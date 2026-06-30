"""Agent 编排核心(系统大脑,PydanticAI 驱动)。

AgentOrchestrator.run() 是单次 Agent 运行的异步主控:加载多轮历史 -> 启动
PydanticAI agentic loop(由 LLM 自主决定检索 / 调用工具 / 收尾)-> 把框架事件
流映射为本平台的 AgentEvent 并经 EventBus 广播 -> 落库 -> 收尾。

设计要点:
- 控制流由 LLM 在 loop 中自主决定,而非规则计划;工具与知识检索均以
  @agent.tool 暴露(见 app.runtime.agent_factory)。
- 对外契约保持不变:run() 签名、生命周期事件(RUN_STARTED/RUN_COMPLETED)、
  逐 token 的 TOKEN 事件、最终 assistant 落库,均与既有 tasks/api 层兼容。
- 韧性:任一阶段失败均尽力降级,保证总能给出一个 assistant 回答并把运行
  状态正确收敛;UsageLimits 提供轮数护栏,防止 loop 失控。
"""

from __future__ import annotations

import logging
import time
import asyncio
from typing import Any

from pydantic_ai import (
    Agent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from app.core.enums import MessageRole, RunStatus
from app.core.events import AgentEvent, EventType
from app.core.logging import get_logger, log_with_fields, set_trace_id
from app.runtime.agent_factory import (
    TOOL_WC2026_CURRENT_MATCH_CONTEXT,
    TOOL_WC2026_METHODOLOGY,
    TOOL_SEARCH_KNOWLEDGE,
    AgentDeps,
    build_agent,
    build_model,
)
from app.runtime.chat_behavior import (
    DEFAULT_CHAT_BEHAVIOR_POLICY,
    GuardrailAction,
    GuardrailDecision,
    StreamingOutputGuardrail,
    build_language_instruction,
    detect_target_language,
    evaluate_user_message,
    finalize_assistant_answer,
    is_likely_truncated_answer,
)
from app.runtime.deps import RuntimeDeps
from app.runtime.provider_limits import (
    ProviderLimitDecision,
    ProviderLimitRequest,
    ProviderRateLimitError,
    ProviderUsageSettlement,
    estimate_input_tokens,
    provider_identity_from_settings,
)
from app.runtime.token_stream import TokenAggregator
from app.runtime.wc2026_permissions import build_wc2026_context_instruction

logger = get_logger(__name__)

# 读取多轮历史时的最大条数
_HISTORY_LIMIT = 20
# 事件 channel 前缀,与 bus 约定 run:{agent_run_id}
_CHANNEL_PREFIX = "run:"
# 顶层兜底文案
_FATAL_ANSWER = "抱歉,处理过程中发生了内部错误,请稍后重试。"
# 计划快照中记录的工具清单(供回放/观测)
_TOOL_NAMES = (
    TOOL_SEARCH_KNOWLEDGE,
    "calculator",
    "clock",
    "web_search",
    TOOL_WC2026_CURRENT_MATCH_CONTEXT,
    TOOL_WC2026_METHODOLOGY,
)


class TruncatedOutputError(RuntimeError):
    """Raised after a run is converged failed for likely provider output cutoff."""

    def __init__(self) -> None:
        super().__init__("TRUNCATED_OUTPUT")


class _EventEmitter:
    """单次运行内的事件发射器,负责 seq 自增与 channel 拼装。"""

    def __init__(
        self,
        bus: Any,
        agent_run_id: str,
        trace_id: str,
        *,
        metrics: Any = None,
        accepted_at: float | None = None,
    ) -> None:
        self._bus = bus
        self._agent_run_id = agent_run_id
        self._trace_id = trace_id
        self._channel = f"{_CHANNEL_PREFIX}{agent_run_id}"
        self._seq = 0
        self._metrics = metrics
        self._accepted_at = accepted_at
        self._first_token_observed = False

    async def emit(
        self, type_: EventType, data: dict[str, Any] | None = None
    ) -> None:
        """构造并发布一条事件;发布失败仅记录,不中断主流程。"""
        self._seq += 1
        data = data or {}
        self._observe_event(type_, data)
        event = AgentEvent(
            agent_run_id=self._agent_run_id,
            trace_id=self._trace_id,
            type=type_,
            seq=self._seq,
            ts=time.time(),
            data=data,
        )
        try:
            await self._bus.publish(self._channel, event)
        except Exception as exc:  # 事件总线故障不应影响业务执行
            log_with_fields(
                logger,
                logging.WARNING,
                "event publish failed",
                event_type=type_.value,
                error=str(exc),
            )

    def _observe_event(self, type_: EventType, data: dict[str, Any]) -> None:
        if self._metrics is None:
            return
        labels = {"agent_run_id": self._agent_run_id}
        if type_ is EventType.TOKEN and not self._first_token_observed:
            self._first_token_observed = True
            if self._accepted_at is not None:
                self._metrics.observe_ttft(time.time() - self._accepted_at, labels)
        if type_ is EventType.ERROR:
            stage = str(data.get("stage", "unknown"))
            if stage == "output_guardrail":
                return
            self._metrics.inc_counter(
                "provider_errors_total",
                {"stage": stage},
            )


async def run_orchestration(
    *,
    agent_run_id: str,
    conversation_id: str,
    trace_id: str,
    user_message: str,
    emit: Any = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    wc2026_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """tasks 层集成入口(薄适配)。

    装配 RuntimeDeps、构造 AgentOrchestrator 并运行,把最终文本归一化为
    ``{"content", "intent"}`` dict。``emit`` 参数为兼容 tasks 契约而保留;
    AgentOrchestrator 通过自身注入的 event_bus 在同一频道发布事件,故此处不
    复用外部 emit,避免重复发射。
    """
    from app.runtime.deps import build_deps

    deps = build_deps()
    orchestrator = AgentOrchestrator(deps)
    answer = await orchestrator.run(
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        user_message=user_message,
        route_type="batch",
        user_id=user_id,
        metadata=metadata,
        wc2026_context=wc2026_context,
    )
    return {"content": answer, "intent": None}


class AgentOrchestrator:
    """编排器。依赖通过 RuntimeDeps 注入;PydanticAI Agent 在构造时装配。"""

    def __init__(
        self, deps: RuntimeDeps, agent: Agent[AgentDeps, str] | None = None
    ) -> None:
        """构造编排器。

        参数:
            deps: 运行依赖容器(检索器 / 工具路由 / 事件总线 / 仓储 / 配置)。
            agent: 可选注入的 PydanticAI Agent(测试用);缺省按配置构建。
        """
        self._deps = deps
        self._agent = agent or build_agent(
            build_model(deps.settings, deps.secret_provider)
        )

    async def run(
        self,
        agent_run_id: str,
        conversation_id: str,
        trace_id: str,
        user_message: str,
        accepted_at: float | None = None,
        route_type: str = "realtime",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        wc2026_context: dict[str, Any] | None = None,
    ) -> str:
        """执行一次完整运行,返回最终 assistant 文本。"""
        set_trace_id(trace_id)
        emitter = _EventEmitter(
            self._deps.event_bus,
            agent_run_id,
            trace_id,
            metrics=self._deps.metrics,
            accepted_at=accepted_at,
        )
        await emitter.emit(EventType.RUN_STARTED, {"message": user_message})
        try:
            target_language = detect_target_language(user_message)
            input_decision = evaluate_user_message(user_message)
            if input_decision.action is GuardrailAction.REFUSE:
                return await self._handle_guardrail_refusal(
                    agent_run_id,
                    conversation_id,
                    emitter,
                    route_type,
                    metadata or {},
                    input_decision,
                    target_language,
                    wc2026_context,
                )
            return await self._execute(
                agent_run_id,
                conversation_id,
                user_message,
                emitter,
                route_type,
                user_id,
                metadata or {},
                target_language,
                wc2026_context,
            )
        except ProviderRateLimitError as exc:
            await self._handle_provider_rate_limit(agent_run_id, emitter, exc)
            raise
        except TruncatedOutputError:
            raise
        except Exception as exc:  # 顶层兜底,保证状态收敛为 FAILED
            return await self._handle_fatal(agent_run_id, emitter, exc)

    async def _execute(
        self,
        agent_run_id: str,
        conversation_id: str,
        user_message: str,
        emitter: _EventEmitter,
        route_type: str,
        user_id: str | None,
        metadata: dict[str, Any],
        target_language: str,
        wc2026_context: dict[str, Any] | None,
    ) -> str:
        """主控制流:历史 -> agentic loop -> 落库 -> 成功收尾。"""
        history = await self._load_history(conversation_id)
        await self._safe_run_repo(
            "mark_running_with_plan",
            agent_run_id,
            None,
            self._plan_snapshot(
                route_type,
                metadata,
                self._deps.settings,
                target_language=target_language,
                wc2026_context=wc2026_context,
            ),
        )
        await emitter.emit(EventType.PLANNING_STARTED, {})

        answer = await self._run_agent(
            agent_run_id,
            conversation_id,
            user_message,
            history,
            emitter,
            route_type,
            user_id,
            metadata,
            target_language,
            wc2026_context,
        )

        await emitter.emit(EventType.RESULT_COMPOSED, {"length": len(answer)})
        completed = await self._try_run_repo(
            "mark_succeeded_with_answer",
            agent_run_id,
            conversation_id,
            answer,
            len(answer),
        )
        if not completed:
            await self._persist_answer(conversation_id, agent_run_id, answer)
            await self._safe_run_repo("mark_succeeded", agent_run_id)
        # 终止事件携带 status 与答案内容,供流式消费者和恢复接口兜底校验。
        await emitter.emit(
            EventType.RUN_COMPLETED,
            {"status": RunStatus.SUCCEEDED.value, "content": answer},
        )
        return answer

    async def _run_agent(
        self,
        agent_run_id: str,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, Any]],
        emitter: _EventEmitter,
        route_type: str,
        user_id: str | None,
        metadata: dict[str, Any],
        target_language: str,
        wc2026_context: dict[str, Any] | None,
    ) -> str:
        """运行 PydanticAI agentic loop,映射事件流,返回最终文本。

        LLM 在 loop 中自主决定是否检索 / 调用工具;失败时降级为兜底文案,
        保证总有回答产出。
        """
        knowledge_base_id = _knowledge_base_id(self._deps.settings, metadata)
        knowledge_base_owner_user_id = _knowledge_base_owner_user_id(
            self._deps.settings, user_id
        )
        deps = AgentDeps(
            retriever=self._contextual_retriever(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_owner_user_id=knowledge_base_owner_user_id,
            ),
            tool_router=self._deps.tool_router,
            agent_run_id=agent_run_id,
            user_id=user_id or "anonymous",
            conversation_id=conversation_id,
            knowledge_base_id=knowledge_base_id,
            retrieval_top_k=self._deps.settings.retrieval_top_k,
            target_language=target_language,
            language_instruction=build_language_instruction(target_language),
            wc2026_context=_clean_wc2026_context(wc2026_context),
            wc2026_agent_data=self._deps.wc2026_agent_data,
            wc2026_context_instruction=build_wc2026_context_instruction(
                wc2026_context
            )
            if wc2026_context
            else "",
        )
        limits = UsageLimits(request_limit=self._deps.settings.max_turns)
        message_history = _to_message_history(history)
        emitted_chunks: list[str] = []
        raw_chunks: list[str] = []
        llm_started = False
        quota_decision = await self._acquire_provider_quota(
            agent_run_id, user_message, route_type
        )
        try:
            async with self._agent.iter(
                user_message,
                deps=deps,
                message_history=message_history or None,
                usage_limits=limits,
            ) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        llm_started = await self._handle_model_request(
                            node,
                            run,
                            emitter,
                            llm_started,
                            emitted_chunks,
                            raw_chunks,
                            target_language,
                        )
                    elif Agent.is_call_tools_node(node):
                        await self._handle_tool_calls(node, run, emitter)
            await self._settle_provider_usage(quota_decision, run, route_type)
            if not emitted_chunks and run.result and run.result.output:
                await self._emit_guarded_final_output(
                    emitter,
                    str(run.result.output),
                    emitted_chunks,
                    raw_chunks,
                    target_language,
                )
            answer = "".join(emitted_chunks)
            raw_answer = "".join(raw_chunks)
            if not answer.strip():
                answer = self._empty_answer(user_message)
            return await self._finalize_answer_integrity(
                agent_run_id,
                answer,
                emitter,
                target_language,
                raw_answer=raw_answer or answer,
            )
        except ProviderRateLimitError:
            raise
        except TruncatedOutputError:
            raise
        except Exception as exc:
            provider_error = await self._record_provider_exception(exc)
            if provider_error is not None:
                reason = (
                    "RATE_LIMITED"
                    if provider_error.status_code == 429
                    else "BACKING_OFF"
                )
                raise ProviderRateLimitError(
                    reason, retry_after_ms=provider_error.retry_after_ms
                ) from exc
            await self._emit_error(emitter, "agent", exc)
            answer = "".join(emitted_chunks)
            raw_answer = "".join(raw_chunks)
            if not answer.strip():
                answer = self._empty_answer(user_message)
            return await self._finalize_answer_integrity(
                agent_run_id,
                answer,
                emitter,
                target_language,
                raw_answer=raw_answer or answer,
            )

    async def _finalize_answer_integrity(
        self,
        agent_run_id: str,
        answer: str,
        emitter: _EventEmitter,
        target_language: str,
        *,
        raw_answer: str | None = None,
    ) -> str:
        """Apply post-generation integrity and deterministic product-safety rules."""
        integrity_answer = raw_answer if raw_answer is not None else answer
        if is_likely_truncated_answer(integrity_answer):
            await emitter.emit(
                EventType.ERROR,
                {
                    "stage": "output_integrity",
                    "error": "TRUNCATED_OUTPUT",
                },
            )
            await self._safe_run_repo("mark_failed", agent_run_id, "TRUNCATED_OUTPUT")
            await emitter.emit(
                EventType.RUN_COMPLETED,
                {"status": RunStatus.FAILED.value, "error": "TRUNCATED_OUTPUT"},
            )
            raise TruncatedOutputError()
        final_answer = finalize_assistant_answer(
            answer, target_language=target_language
        )
        if final_answer != answer:
            suffix = final_answer[len(answer) :]
            await emitter.emit(EventType.TOKEN, {"token": suffix})
        return final_answer

    async def _acquire_provider_quota(
        self, agent_run_id: str, user_message: str, route_type: str
    ) -> ProviderLimitDecision | None:
        """Gate real provider calls before entering the Pydantic AI loop."""
        identity = provider_identity_from_settings(self._deps.settings)
        limiter = self._deps.provider_limiter
        if identity.mock or limiter is None:
            return None
        request = ProviderLimitRequest(
            provider=identity.provider,
            model=identity.model,
            estimated_input_tokens=estimate_input_tokens(user_message),
            max_output_tokens=self._deps.settings.provider_default_max_output_tokens,
            route_type=route_type,
            agent_run_id=agent_run_id,
        )
        try:
            decision = await limiter.acquire(request)
        except Exception as exc:
            raise ProviderRateLimitError("UNAVAILABLE", retry_after_ms=1000) from exc
        if decision.allowed:
            return decision
        budget_ms = self._deps.settings.provider_realtime_gate_wait_budget_ms
        if (
            route_type == "realtime"
            and decision.retry_after_ms is not None
            and decision.retry_after_ms <= budget_ms
        ):
            await asyncio.sleep(decision.retry_after_ms / 1000)
            try:
                decision = await limiter.acquire(request)
            except Exception as exc:
                raise ProviderRateLimitError("UNAVAILABLE", retry_after_ms=1000) from exc
            if decision.allowed:
                return decision
        raise ProviderRateLimitError(
            decision.reason, retry_after_ms=decision.retry_after_ms
        )

    async def _settle_provider_usage(
        self,
        decision: ProviderLimitDecision | None,
        run: Any,
        route_type: str,
    ) -> None:
        """Settle actual provider usage after output usage becomes available."""
        if decision is None or self._deps.provider_limiter is None:
            return
        identity = provider_identity_from_settings(self._deps.settings)
        usage = _extract_usage(run)
        try:
            await self._deps.provider_limiter.settle_usage(
                ProviderUsageSettlement(
                    provider=identity.provider,
                    model=identity.model,
                    reserved_tokens=decision.reserved_tokens,
                    actual_input_tokens=usage.get("input_tokens"),
                    actual_output_tokens=usage.get("output_tokens"),
                    route_type=route_type,
                )
            )
        except Exception as exc:
            raise ProviderRateLimitError("UNAVAILABLE", retry_after_ms=1000) from exc

    async def _record_provider_exception(self, exc: Exception) -> Any | None:
        from app.llm.providers import map_provider_error

        info = map_provider_error(exc)
        if info is None or self._deps.provider_limiter is None:
            return info
        identity = provider_identity_from_settings(self._deps.settings)
        if identity.mock:
            return info
        await self._deps.provider_limiter.record_provider_error(
            identity.provider,
            identity.model,
            info.status_code,
            info.retry_after_ms,
        )
        return info

    async def _handle_model_request(
        self,
        node: Any,
        run: Any,
        emitter: _EventEmitter,
        llm_started: bool,
        emitted_chunks: list[str],
        raw_chunks: list[str],
        target_language: str,
    ) -> bool:
        """处理模型请求节点:首次发 LLM_GENERATING,最终结果阶段流式 TOKEN。"""
        aggregator = TokenAggregator()
        guardrail = StreamingOutputGuardrail(target_language=target_language)
        output_guardrail_reported = False
        if not llm_started:
            await emitter.emit(EventType.LLM_GENERATING, {})
            llm_started = True
        async with node.stream(run.ctx) as request_stream:
            final_found = False
            async for event in request_stream:
                if isinstance(event, FinalResultEvent):
                    final_found = True
                    break
            if final_found:
                async for token in request_stream.stream_text(delta=True):
                    if token:
                        raw_chunks.append(token)
                        safe_chunk = guardrail.push(token)
                        if safe_chunk:
                            output_guardrail_reported = (
                                await self._emit_output_guardrail_blocked_once(
                                    emitter,
                                    guardrail,
                                    output_guardrail_reported,
                                )
                            )
                            await self._emit_aggregated_token(
                                emitter, aggregator, emitted_chunks, safe_chunk
                            )
                safe_tail = guardrail.finish()
                if safe_tail:
                    output_guardrail_reported = (
                        await self._emit_output_guardrail_blocked_once(
                            emitter,
                            guardrail,
                            output_guardrail_reported,
                        )
                    )
                    await self._emit_aggregated_token(
                        emitter, aggregator, emitted_chunks, safe_tail
                    )
                await self._flush_aggregated_tokens(
                    emitter, aggregator, emitted_chunks
                )
        return llm_started

    @staticmethod
    async def _emit_output_guardrail_blocked_once(
        emitter: _EventEmitter,
        guardrail: StreamingOutputGuardrail,
        already_reported: bool,
    ) -> bool:
        """Emit a sanitized output-guardrail ERROR once per model request."""
        if already_reported or not guardrail.blocked:
            return already_reported
        decision = guardrail.decision
        await emitter.emit(
            EventType.ERROR,
            {
                "stage": "output_guardrail",
                "category": decision.category.value,
                "reason_code": decision.reason_code,
                "safe_response": decision.safe_response,
            },
        )
        return True

    async def _handle_tool_calls(
        self, node: Any, run: Any, emitter: _EventEmitter
    ) -> None:
        """处理工具调用节点:把 LLM 的工具调用/结果映射为检索或工具事件。"""
        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, FunctionToolCallEvent):
                    await self._emit_tool_started(emitter, event)
                elif isinstance(event, FunctionToolResultEvent):
                    await self._emit_tool_finished(emitter, event)

    @staticmethod
    async def _emit_tool_started(
        emitter: _EventEmitter, event: FunctionToolCallEvent
    ) -> None:
        """工具调用开始:search_knowledge 映射为检索事件,其余为工具事件。"""
        name = event.part.tool_name
        if name == TOOL_SEARCH_KNOWLEDGE:
            query = ""
            args = event.part.args
            if isinstance(args, dict):
                query = str(args.get("query", ""))
            await emitter.emit(EventType.RETRIEVAL_STARTED, {"query": query})
        else:
            await emitter.emit(
                EventType.TOOL_CALL_STARTED, {"tool_name": name}
            )

    @staticmethod
    async def _emit_tool_finished(
        emitter: _EventEmitter, event: FunctionToolResultEvent
    ) -> None:
        """工具结果返回:对应发出检索完成或工具完成事件。"""
        # 新版用 event.part(ToolReturnPart),旧版用 event.result,做兼容回退。
        part = getattr(event, "part", None) or getattr(event, "result", None)
        name = getattr(part, "tool_name", None)
        if name == TOOL_SEARCH_KNOWLEDGE:
            await emitter.emit(EventType.RETRIEVAL_FINISHED, {})
        else:
            await emitter.emit(
                EventType.TOOL_CALL_FINISHED, {"tool_name": name}
            )

    @staticmethod
    def _empty_answer(user_message: str) -> str:
        """LLM 未产出任何内容时的兜底文案。"""
        return f"抱歉,我暂时无法生成关于「{user_message}」的回答。"

    async def _load_history(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        """读取多轮历史并规范为 {role, content};失败降级为空。"""
        try:
            rows = await self._deps.message_repo.list_by_conversation(
                conversation_id, _HISTORY_LIMIT
            )
        except Exception as exc:
            log_with_fields(
                logger,
                logging.WARNING,
                "load history failed",
                conversation_id=conversation_id,
                error=str(exc),
            )
            return []
        return [self._row_to_msg(r) for r in rows]

    @staticmethod
    def _row_to_msg(row: Any) -> dict[str, Any]:
        """把 Message ORM 行转为 {role, content}。"""
        role = getattr(row, "role", MessageRole.USER)
        role_value = role.value if isinstance(role, MessageRole) else str(role)
        return {
            "role": role_value.lower(),
            "content": getattr(row, "content", ""),
        }

    def _contextual_retriever(
        self,
        *,
        user_id: str | None,
        conversation_id: str | None,
        agent_run_id: str | None,
        knowledge_base_id: str | None,
        knowledge_base_owner_user_id: str | None,
    ) -> Any:
        """Return a per-run retriever when the adapter supports context."""
        retriever = self._deps.retriever
        with_context = getattr(retriever, "with_context", None)
        if callable(with_context):
            return with_context(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_owner_user_id=knowledge_base_owner_user_id,
            )
        return retriever

    @staticmethod
    def _plan_snapshot(
        route_type: str,
        metadata: dict[str, Any],
        settings: Any,
        *,
        target_language: str | None = None,
        wc2026_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return an engine snapshot for run audit/debugging."""
        plan: dict[str, Any] = {
            "engine": "pydantic-ai",
            "tools": list(_TOOL_NAMES),
            "route_type": route_type,
            "metadata": _plan_metadata(settings, metadata),
        }
        kb_id = _knowledge_base_id(settings, metadata)
        if kb_id:
            plan["knowledge_base_id"] = kb_id
        plan["policy_version"] = DEFAULT_CHAT_BEHAVIOR_POLICY.version
        if target_language:
            plan["target_language"] = target_language
        clean_context = _clean_wc2026_context(wc2026_context)
        if clean_context:
            plan["wc2026_context"] = clean_context
        return plan

    async def _handle_guardrail_refusal(
        self,
        agent_run_id: str,
        conversation_id: str,
        emitter: _EventEmitter,
        route_type: str,
        metadata: dict[str, Any],
        decision: GuardrailDecision,
        target_language: str,
        wc2026_context: dict[str, Any] | None,
    ) -> str:
        """Converge a deterministic policy refusal without calling the model."""
        plan = self._plan_snapshot(
            route_type,
            metadata,
            self._deps.settings,
            target_language=target_language,
            wc2026_context=wc2026_context,
        )
        plan["guardrail"] = decision.as_plan_metadata()
        await self._safe_run_repo(
            "mark_running_with_plan",
            agent_run_id,
            None,
            plan,
        )
        await emitter.emit(EventType.PLANNING_STARTED, {})
        answer = decision.safe_response
        await emitter.emit(EventType.RESULT_COMPOSED, {"length": len(answer)})
        completed = await self._try_run_repo(
            "mark_succeeded_with_answer",
            agent_run_id,
            conversation_id,
            answer,
            len(answer),
        )
        if not completed:
            await self._persist_answer(conversation_id, agent_run_id, answer)
            await self._safe_run_repo("mark_succeeded", agent_run_id)
        await emitter.emit(
            EventType.RUN_COMPLETED,
            {"status": RunStatus.SUCCEEDED.value, "content": answer},
        )
        return answer

    async def _emit_guarded_final_output(
        self,
        emitter: _EventEmitter,
        output: str,
        emitted_chunks: list[str],
        raw_chunks: list[str],
        target_language: str,
    ) -> None:
        """Fallback for completed model output that did not stream text deltas."""
        raw_chunks.append(output)
        aggregator = TokenAggregator()
        guardrail = StreamingOutputGuardrail(target_language=target_language)
        safe_chunk = guardrail.push(output)
        if safe_chunk:
            await self._emit_aggregated_token(
                emitter, aggregator, emitted_chunks, safe_chunk
            )
        safe_tail = guardrail.finish()
        if safe_tail:
            await self._emit_aggregated_token(
                emitter, aggregator, emitted_chunks, safe_tail
            )
        await self._flush_aggregated_tokens(emitter, aggregator, emitted_chunks)

    @staticmethod
    async def _emit_aggregated_token(
        emitter: _EventEmitter,
        aggregator: TokenAggregator,
        emitted_chunks: list[str],
        text: str,
    ) -> None:
        chunk = aggregator.push(text)
        if chunk:
            await emitter.emit(EventType.TOKEN, {"token": chunk})
            emitted_chunks.append(chunk)

    @staticmethod
    async def _flush_aggregated_tokens(
        emitter: _EventEmitter,
        aggregator: TokenAggregator,
        emitted_chunks: list[str],
    ) -> None:
        chunk = aggregator.flush()
        if chunk:
            await emitter.emit(EventType.TOKEN, {"token": chunk})
            emitted_chunks.append(chunk)

    async def _persist_answer(
        self, conversation_id: str, agent_run_id: str, answer: str
    ) -> None:
        """把 assistant 回答落库(失败仅记录,不影响返回)。"""
        try:
            await self._deps.message_repo.add(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=answer,
                token_count=len(answer),
                agent_run_id=agent_run_id,
            )
        except Exception as exc:
            log_with_fields(
                logger,
                logging.ERROR,
                "persist answer failed",
                conversation_id=conversation_id,
                error=str(exc),
            )

    async def _handle_fatal(
        self, agent_run_id: str, emitter: _EventEmitter, exc: Exception
    ) -> str:
        """顶层异常处理:发 ERROR、置 FAILED,返回兜底文案。"""
        await self._emit_error(emitter, "fatal", exc)
        await self._safe_run_repo("mark_failed", agent_run_id, str(exc))
        await emitter.emit(
            EventType.RUN_COMPLETED, {"status": RunStatus.FAILED.value}
        )
        return _FATAL_ANSWER

    async def _handle_provider_rate_limit(
        self,
        agent_run_id: str,
        emitter: _EventEmitter,
        exc: ProviderRateLimitError,
    ) -> None:
        """Provider quota denial after run acceptance: fail fast and converge."""
        data: dict[str, Any] = {
            "stage": "provider_rate_limit",
            "error": exc.reason,
        }
        if exc.retry_after_ms is not None:
            data["retry_after_ms"] = exc.retry_after_ms
        await emitter.emit(EventType.ERROR, data)
        await self._safe_run_repo("mark_failed", agent_run_id, exc.reason)
        await emitter.emit(
            EventType.RUN_COMPLETED, {"status": RunStatus.FAILED.value}
        )

    @staticmethod
    async def _emit_error(
        emitter: _EventEmitter, stage: str, exc: Exception
    ) -> None:
        """发布一条 ERROR 事件并记录日志。"""
        log_with_fields(
            logger, logging.ERROR, "stage error", stage=stage, error=str(exc)
        )
        await emitter.emit(EventType.ERROR, {"stage": stage, "error": str(exc)})

    async def _safe_run_repo(self, method: str, *args: Any) -> None:
        """安全调用 run_repo 的状态更新方法,异常仅记录不抛出。"""
        await self._try_run_repo(method, *args)

    async def _try_run_repo(self, method: str, *args: Any) -> bool:
        """Call run_repo and return whether it succeeded."""
        try:
            await getattr(self._deps.run_repo, method)(*args)
            return True
        except Exception as exc:
            log_with_fields(
                logger,
                logging.WARNING,
                "run_repo call failed",
                method=method,
                error=str(exc),
            )
            return False

def _to_message_history(
    history: list[dict[str, Any]],
) -> list[ModelMessage]:
    """把 {role, content} 历史转为 PydanticAI message_history。

    user -> ModelRequest(UserPromptPart);assistant -> ModelResponse(TextPart);
    system 由 Agent 自带,这里忽略,空内容过滤。
    """
    out: list[ModelMessage] = []
    for item in history:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        role = str(item.get("role", "user")).lower()
        if role == "assistant":
            out.append(ModelResponse(parts=[TextPart(content=content)]))
        elif role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=content)]))
    return out


def _extract_usage(run: Any) -> dict[str, int | None]:
    """Best-effort Pydantic AI usage extraction without binding to one version."""
    candidates: list[Any] = []
    result = getattr(run, "result", None)
    if result is not None:
        candidates.extend(
            [
                getattr(result, "usage", None),
                getattr(result, "usage_data", None),
            ]
        )
    candidates.extend([getattr(run, "usage", None), getattr(run, "usage_data", None)])
    for candidate in candidates:
        if callable(candidate):
            try:
                candidate = candidate()
            except TypeError:
                continue
        if candidate is None:
            continue
        input_tokens = _usage_field(
            candidate,
            "input_tokens",
            "request_tokens",
            "prompt_tokens",
        )
        output_tokens = _usage_field(
            candidate,
            "output_tokens",
            "response_tokens",
            "completion_tokens",
        )
        if input_tokens is not None or output_tokens is not None:
            return {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return {"input_tokens": None, "output_tokens": None}


def _knowledge_base_id(settings: Any, metadata: dict[str, Any] | None) -> str | None:
    default_value = _clean_text(
        getattr(settings, "rag_default_knowledge_base_id", "")
    )
    if default_value:
        return default_value
    if not bool(getattr(settings, "rag_allow_client_knowledge_base_id", False)):
        return None
    value = (metadata or {}).get("knowledge_base_id")
    return _clean_text(value)


def _knowledge_base_owner_user_id(settings: Any, user_id: str | None) -> str | None:
    configured = _clean_text(getattr(settings, "rag_internal_owner_user_id", ""))
    return configured or user_id


def _plan_metadata(settings: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    output = dict(metadata)
    if not bool(getattr(settings, "rag_allow_client_knowledge_base_id", False)):
        output.pop("knowledge_base_id", None)
    for reserved_key in (
        "policy_version",
        "guardrail",
        "disable_guardrails",
        "disable_guardrail",
        "behavior_policy",
        "target_language",
        "language_policy",
        "disable_language_guardrail",
        "wc2026_context",
    ):
        output.pop(reserved_key, None)
    return output


def _clean_wc2026_context(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _usage_field(obj: Any, *names: str) -> int | None:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
