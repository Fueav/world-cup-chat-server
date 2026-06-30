"""核心对话入口路由。

POST /api/v1/wc2026/chat?user_uuid=<uuid> 流程:
1. URL user_uuid 鉴权/限流由中间件完成,此处校验请求体(Pydantic)。
2. 创建或复用 conversation,写入用户消息。
3. 生成 agent_run_id + trace_id,落库 AgentRun(PENDING) + TaskState(QUEUED)。
4. 按 route_type 分发:默认 realtime 交给常驻 Async Runner,慢任务/批任务投递 Celery。
5. 立即返回 ChatAccepted(含 stream_url / ws_url)。

API 层无状态:不在内存保存会话,全部经 DB/Redis。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, ReposDep
from app.api.idempotency import chat_request_hash
from app.api.runner_gateway import (
    RunnerUnavailableError,
    enqueue_run,
)
from app.core.config import get_settings
from app.core.enums import MessageRole, RunStatus
from app.core.ids import _new_id, new_conversation_id, new_run_id, new_trace_id
from app.core.logging import get_logger, log_with_fields
from app.core.schemas import ChatAccepted, ChatRequest
from app.runtime.locks import ConversationLock
from app.runtime.provider_limits import (
    ProviderLimitRequest,
    estimate_input_tokens,
    provider_identity_from_settings,
)
from app.runtime.runner import (
    RealtimeCapacitySlot,
    RealtimeRunRequest,
    RealtimeRunner,
    now_seconds,
)

logger = get_logger(__name__)

_WC2026_PREFIX = "/api/v1/wc2026"

router = APIRouter(prefix=_WC2026_PREFIX, tags=["chat"])

# 投递任务的子任务类型(对应 task_state.task_type)
_RUN_TASK_TYPE = "run"
_BATCH_TASK_TYPES = {
    "file",
    "file_analysis",
    "long",
    "long_rag",
    "slow",
    "slow_tool",
    "batch",
}

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@router.post("/chat", status_code=status.HTTP_202_ACCEPTED)
async def create_chat(
    body: ChatRequest,
    request: Request,
    user: CurrentUser,
    repos: ReposDep,
) -> Any:
    """受理一次对话/任务请求。"""
    _validate_message(body.message)
    _validate_async_only(body.stream)
    wc2026_context = _validate_wc2026_context(body)
    settings = get_settings()
    trace_id = getattr(request.state, "trace_id", None) or new_trace_id()
    run_id = new_run_id()
    route_type = select_route_type(
        body.metadata,
        runtime_mode=settings.chat_runtime_mode,
    )
    idempotency_key = request.headers.get("idempotency-key")
    request_hash = chat_request_hash(
        message=body.message,
        conversation_id=body.conversation_id,
        metadata=body.metadata,
        wc2026_context=wc2026_context,
    )
    if idempotency_key:
        replay = await _try_idempotency_replay(
            repos, user, idempotency_key, request_hash
        )
        if replay is not None:
            return replay

    conversation_id, conversation_is_existing = await _resolve_wc2026_conversation_id(
        repos,
        user,
        body.conversation_id,
        wc2026_context["current_match_id"],
    )
    route_type = await _apply_provider_preflight(
        body,
        request,
        route_type,
        settings=settings,
        user_id=user,
    )
    accepted = _accepted(
        conversation_id,
        run_id,
        trace_id,
        user_uuid=user,
        route_type=route_type,
    )
    if idempotency_key:
        replay = await _claim_idempotency_or_replay(
            repos,
            user_id=user,
            idempotency_key=idempotency_key,
            run_id=run_id,
            request_hash=request_hash,
            accepted=accepted,
        )
        if replay is not None:
            return replay

    conversation_lease = None
    capacity_slot: RealtimeCapacitySlot | None = None
    try:
        if conversation_is_existing:
            existing_conversation = await repos.get_conversation(conversation_id)
            if (
                existing_conversation is not None
                and existing_conversation.user_id != user
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权访问该会话",
                )
            await _ensure_wc2026_match_binding(
                repos,
                conversation_id,
                wc2026_context["current_match_id"],
            )
        if route_type == "realtime":
            runner = _get_realtime_runner(request)
            capacity_slot = runner.try_acquire_capacity()
            if capacity_slot is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="REALTIME_RUNNER_BUSY",
                )
            lock = getattr(request.app.state, "conversation_lock", None) or ConversationLock()
            lock_key = (
                conversation_id
                if conversation_is_existing
                else _new_wc2026_conversation_lock_key(
                    user, wc2026_context["current_match_id"]
                )
            )
            conversation_lease = await lock.acquire(lock_key, run_id, ttl_s=120)
            if conversation_lease is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="CONVERSATION_BUSY",
                )

        conversation = await repos.ensure_conversation(
            conversation_id,
            user,
            wc2026_match_id=wc2026_context["current_match_id"],
        )
        if conversation.id != conversation_id:
            conversation_id = conversation.id
            accepted = _accepted(
                conversation_id,
                run_id,
                trace_id,
                user_uuid=user,
                route_type=route_type,
            )
        await repos.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=body.message,
        )
        await repos.create_run(
            run_id,
            conversation.id,
            trace_id,
            plan={
                "route_type": route_type,
                "metadata": body.metadata,
                "wc2026_context": wc2026_context,
            },
        )
        payload = _build_payload(run_id, conversation.id, trace_id, body)
        payload["user_id"] = user
        payload["route_type"] = route_type
        if route_type == "batch":
            await repos.create_queued_task(
                task_id=_new_id("task_"),
                agent_run_id=run_id,
                task_type=_RUN_TASK_TYPE,
                payload=payload,
            )
        await repos.commit()

        if route_type == "batch":
            _dispatch(payload)
        else:
            _dispatch_realtime(
                request,
                payload,
                user,
                conversation_lease,
                capacity_slot=capacity_slot,
            )
            conversation_lease = None
            capacity_slot = None

        return accepted
    finally:
        if capacity_slot is not None:
            await capacity_slot.release()
        if conversation_lease is not None:
            await conversation_lease.release()


def select_route_type(
    metadata: dict[str, Any] | None,
    *,
    runtime_mode: str = "auto",
) -> str:
    """Return realtime or batch for a chat request."""
    metadata = metadata or {}
    forced_runtime = runtime_mode.lower()
    requested = str(metadata.get("mode") or "auto").lower()
    task_type = str(metadata.get("task_type") or "chat").lower()

    if forced_runtime == "celery":
        return "batch"
    if forced_runtime == "realtime":
        return "realtime"
    if requested == "batch":
        return "batch"
    if requested == "realtime":
        return "realtime"
    if task_type in _BATCH_TASK_TYPES or any(
        marker in task_type for marker in ("file", "long", "slow", "batch")
    ):
        return "batch"
    return "realtime"


async def _apply_provider_preflight(
    body: ChatRequest,
    request: Request,
    route_type: str,
    *,
    settings: Any,
    user_id: str,
) -> str:
    """Advisory provider check before realtime run creation."""
    if route_type != "realtime":
        return route_type
    identity = provider_identity_from_settings(settings)
    if identity.mock:
        return route_type
    limiter = getattr(request.app.state, "provider_limiter", None)
    if limiter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_LIMITER_UNAVAILABLE",
        )
    req = ProviderLimitRequest(
        provider=identity.provider,
        model=identity.model,
        estimated_input_tokens=estimate_input_tokens(body.message),
        max_output_tokens=settings.provider_default_max_output_tokens,
        route_type="realtime",
        user_id=user_id,
    )
    try:
        decision = await asyncio.wait_for(
            limiter.check(req),
            timeout=settings.provider_realtime_preflight_timeout_ms / 1000,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_LIMITER_UNAVAILABLE",
        ) from exc
    if decision.allowed:
        return route_type
    requested = str((body.metadata or {}).get("mode") or "auto").lower()
    if requested != "realtime" and settings.provider_realtime_degrade_to_batch:
        body.metadata = {
            **(body.metadata or {}),
            "degraded": True,
            "degraded_reason": "provider_rate_limited",
            "retry_after_ms": decision.retry_after_ms,
        }
        return "batch"
    headers = {}
    if decision.retry_after_ms is not None:
        headers["Retry-After"] = str(max(1, math.ceil(decision.retry_after_ms / 1000)))
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if decision.reason == "UNAVAILABLE"
        else status.HTTP_429_TOO_MANY_REQUESTS
    )
    raise HTTPException(
        status_code=status_code,
        detail=decision.reason if decision.reason != "UNAVAILABLE" else "PROVIDER_LIMITER_UNAVAILABLE",
        headers=headers,
    )


def _validate_message(message: str) -> None:
    """校验消息非空,空白消息直接 422。"""
    if not message or not message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message 不能为空",
        )


def _validate_async_only(stream: bool) -> None:
    """Chat API only accepts asynchronous streaming runs."""
    if stream is False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="STREAM_FALSE_NOT_SUPPORTED",
        )


def _validate_wc2026_context(body: ChatRequest) -> dict[str, Any]:
    """Return trusted WC2026 context or reject the WC2026 chat request."""
    context = body.wc2026_context
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WC2026_CONTEXT_REQUIRED",
        )
    current_match_id = str(context.current_match_id).strip()
    if not current_match_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WC2026_CONTEXT_REQUIRED",
        )
    if body.match_id is not None and str(body.match_id).strip() != current_match_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WC2026_MATCH_ID_MISMATCH",
        )
    if str(context.current_match.id).strip() != current_match_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WC2026_MATCH_ID_MISMATCH",
        )
    return context.model_dump(mode="json", exclude_none=True)


async def _resolve_wc2026_conversation_id(
    repos: ReposDep,
    user_id: str,
    requested_conversation_id: str | None,
    current_match_id: str,
) -> tuple[str, bool]:
    """Resolve explicit, match-bound existing, or new conversation id."""
    if requested_conversation_id:
        return requested_conversation_id, True
    getter = getattr(repos, "get_wc2026_conversation_id_by_match", None)
    if callable(getter):
        existing_id = await getter(user_id, current_match_id)
        if existing_id:
            return str(existing_id), True
    return new_conversation_id(), False


async def _ensure_wc2026_match_binding(
    repos: ReposDep, conversation_id: str, current_match_id: str
) -> None:
    """Reject attempts to reuse a WC2026 conversation for another match."""
    getter = getattr(repos, "get_conversation_wc2026_match_id", None)
    if not callable(getter):
        return
    bound_match_id = await getter(conversation_id)
    if bound_match_id and str(bound_match_id) != str(current_match_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="WC2026_CONVERSATION_MATCH_CONFLICT",
        )


def _new_wc2026_conversation_lock_key(user_id: str, current_match_id: str) -> str:
    """Return the pre-create lock key for one user and one WC2026 match."""
    return f"new_wc2026:{user_id}:{current_match_id}"


def _build_payload(
    run_id: str, conversation_id: str, trace_id: str, body: ChatRequest
) -> dict[str, Any]:
    """组装投递给 Celery 的任务载荷。"""
    return {
        "agent_run_id": run_id,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "message": body.message,
        "metadata": body.metadata,
        "match_id": body.match_id,
        "wc2026_context": body.wc2026_context.model_dump(
            mode="json", exclude_none=True
        )
        if body.wc2026_context is not None
        else None,
    }


def _dispatch(payload: dict[str, Any]) -> None:
    """投递任务,队列不可用时转换为 503。"""
    try:
        enqueue_run(payload)
    except RunnerUnavailableError as exc:
        log_with_fields(
            logger,
            logging.ERROR,
            "任务队列不可用",
            agent_run_id=payload["agent_run_id"],
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务队列暂不可用,请稍后重试",
        ) from exc


def _dispatch_realtime(
    request: Request,
    payload: dict[str, Any],
    user_id: str,
    conversation_lease: Any | None,
    *,
    capacity_slot: RealtimeCapacitySlot | None = None,
) -> asyncio.Task[Any]:
    """Dispatch realtime run in the resident event loop."""
    runner = _get_realtime_runner(request)
    realtime_request = RealtimeRunRequest(
        agent_run_id=payload["agent_run_id"],
        conversation_id=payload["conversation_id"],
        user_id=user_id,
        trace_id=payload["trace_id"],
        message=payload["message"],
        metadata=payload.get("metadata") or {},
        wc2026_context=payload.get("wc2026_context"),
        accepted_at=now_seconds(),
        route_type=str(payload.get("route_type") or "realtime"),
    )
    task = asyncio.create_task(
        runner.run_chat(
            realtime_request,
            conversation_lease=conversation_lease,
            capacity_slot=capacity_slot,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _get_realtime_runner(request: Request) -> RealtimeRunner:
    runner = getattr(request.app.state, "realtime_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="REALTIME_RUNNER_NOT_READY",
        )
    return runner


async def _try_idempotency_replay(
    repos: ReposDep,
    user_id: str,
    idempotency_key: str,
    request_hash: str,
) -> ChatAccepted | None:
    record = await repos.get_idempotency_record(user_id, idempotency_key)
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IDEMPOTENCY_CONFLICT",
        )
    response = dict(record.response)
    run = await repos.get_run(record.agent_run_id)
    if run is not None:
        response["status"] = run.status
    return ChatAccepted.model_validate(response)


async def _claim_idempotency_or_replay(
    repos: ReposDep,
    *,
    user_id: str,
    idempotency_key: str,
    run_id: str,
    request_hash: str,
    accepted: ChatAccepted,
) -> ChatAccepted | None:
    record, created = await repos.claim_idempotency_record(
        record_id=_new_id("idem_"),
        user_id=user_id,
        idempotency_key=idempotency_key,
        agent_run_id=run_id,
        request_hash=request_hash,
        response=accepted.model_dump(mode="json"),
    )
    if created:
        return None
    return await _replay_idempotency_record(repos, record, request_hash)


async def _replay_idempotency_record(
    repos: ReposDep,
    record: Any,
    request_hash: str,
) -> ChatAccepted:
    if record.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IDEMPOTENCY_CONFLICT",
        )
    response = dict(record.response)
    run = await repos.get_run(record.agent_run_id)
    if run is not None:
        response["status"] = run.status
    return ChatAccepted.model_validate(response)


def _accepted(
    conversation_id: str,
    run_id: str,
    trace_id: str,
    *,
    user_uuid: str,
    route_type: str | None = None,
) -> ChatAccepted:
    """构造 202 受理响应。"""
    user_query = urlencode({"user_uuid": user_uuid})
    return ChatAccepted(
        conversation_id=conversation_id,
        agent_run_id=run_id,
        trace_id=trace_id,
        status=RunStatus.PENDING,
        stream_url=f"{_WC2026_PREFIX}/stream/{run_id}?{user_query}",
        ws_url=f"{_WC2026_PREFIX}/ws/{run_id}?{user_query}",
        route_type=route_type,
    )
