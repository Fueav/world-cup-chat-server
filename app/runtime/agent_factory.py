"""PydanticAI Agent 工厂。

把本平台的运行时内核切换为 PydanticAI 驱动的 agentic loop:由 LLM 自主决定
是否检索知识库、调用哪个工具、何时收尾。这里负责三件事:

1. build_model(settings):按 settings.llm_provider 选择 PydanticAI 原生 model
   (mock / openai / qwen / zai / anthropic / gemini),其中 mock 用 FunctionModel
   实现零 key、确定性、可演示一次「检索->回答」的离线行为。
2. AgentDeps:通过依赖注入把检索器与工具路由传给各 @agent.tool,工具实现
   仅做薄转发,复用既有 RetrieverAdapter / ToolRouterAdapter 契约。
3. build_agent(model):构造 Agent 并注册 4 个工具(知识检索 + 计算/时钟/搜索)。

设计原则:Agent 自身无可变状态、可复用;运行所需的协作者经 deps 在每次
run 时注入,便于 task 装配与测试替身。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from app.core.config import Settings, get_settings
from app.core.secrets import SecretProvider, build_secret_provider, is_mock_provider
from app.runtime.chat_behavior import (
    DEFAULT_CHAT_BEHAVIOR_POLICY,
    build_system_prompt,
)

# mock 流式回答的分片长度(按字符切分,模拟逐 token 产出)
_MOCK_CHUNK_SIZE = 12

# 知识检索工具名(mock 模型据此判断是否先检索一轮)
TOOL_SEARCH_KNOWLEDGE = "search_knowledge"
TOOL_WC2026_CURRENT_MATCH_CONTEXT = "get_current_wc2026_match_context"
TOOL_WC2026_METHODOLOGY = "get_wc2026_model_methodology"

# Agent 的系统提示词由版本化行为策略构造,便于审计和回归。
_SYSTEM_PROMPT = build_system_prompt(DEFAULT_CHAT_BEHAVIOR_POLICY)


@dataclass
class AgentDeps:
    """注入给各 @agent.tool 的运行期依赖。

    字段:
        retriever: 满足 retrieve(query, top_k) -> dict/list 契约的检索器。
        tool_router: 满足 route(query, tool_name) -> dict 契约的工具路由。
        agent_run_id: 当前 run id,用于工具审计。
        user_id/conversation_id/knowledge_base_id: server-side RAG 上下文。
        retrieval_top_k: 检索返回条数上限。
    """

    retriever: Any
    tool_router: Any
    agent_run_id: str = ""
    user_id: str = "anonymous"
    conversation_id: str = ""
    knowledge_base_id: str | None = None
    retrieval_top_k: int = 5
    target_language: str = "unknown"
    language_instruction: str = ""
    answer_format_instruction: str = ""
    wc2026_context: dict[str, Any] | None = None
    wc2026_agent_data: Any | None = None
    wc2026_context_instruction: str = ""


def build_agent(model: Model) -> Agent[AgentDeps, str]:
    """构造并返回注册好工具的 Agent。

    参数:
        model: PydanticAI model 实例(由 build_model 产出或测试注入)。
    """
    agent: Agent[AgentDeps, str] = Agent(
        model,
        deps_type=AgentDeps,
        output_type=str,
        system_prompt=_SYSTEM_PROMPT,
    )

    @agent.instructions
    def run_language_policy(ctx: RunContext[AgentDeps]) -> str:
        """Inject per-run language policy without mutating user messages."""
        return ctx.deps.language_instruction

    @agent.instructions
    def run_wc2026_context_policy(ctx: RunContext[AgentDeps]) -> str:
        """Inject current-match permission context for WC2026 runs."""
        return ctx.deps.wc2026_context_instruction

    @agent.instructions
    def run_answer_format_policy(ctx: RunContext[AgentDeps]) -> str:
        """Inject the side-panel answer contract close to each run."""
        return ctx.deps.answer_format_instruction

    @agent.tool
    async def search_knowledge(
        ctx: RunContext[AgentDeps], query: str
    ) -> Any:
        """检索知识库,返回与查询最相关的若干文档片段。

        参数:
            query: 检索关键词或问题。
        """
        return await ctx.deps.retriever.retrieve(query, ctx.deps.retrieval_top_k)

    @agent.tool
    async def calculator(
        ctx: RunContext[AgentDeps], expression: str
    ) -> dict[str, Any]:
        """对数学表达式求值,支持加减乘除、取模、整除、幂与括号。

        参数:
            expression: 待求值的数学表达式,如 '2 * (3 + 4)'。
        """
        return await ctx.deps.tool_router.route(
            expression, "calculator", agent_run_id=ctx.deps.agent_run_id
        )

    @agent.tool
    async def clock(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """返回当前的 UTC 与本地时间(ISO 8601 与 Unix 时间戳)。"""
        return await ctx.deps.tool_router.route(
            "", "clock", agent_run_id=ctx.deps.agent_run_id
        )

    @agent.tool
    async def web_search(
        ctx: RunContext[AgentDeps], query: str
    ) -> dict[str, Any]:
        """联网搜索,返回与查询相关的结果列表(当前为离线确定性实现)。

        参数:
            query: 搜索关键词。
        """
        return await ctx.deps.tool_router.route(
            query, "web_search", agent_run_id=ctx.deps.agent_run_id
        )

    @agent.tool
    async def get_current_wc2026_match_context(
        ctx: RunContext[AgentDeps], locale: str = "zh-Hans"
    ) -> dict[str, Any]:
        """读取当前对话绑定比赛的 WC2026 中心化上下文。

        工具不接受 match_id;实际 match_id 只能来自 server 注入的
        wc2026_context.current_match_id。
        """
        if ctx.deps.wc2026_agent_data is None:
            return {
                "ok": False,
                "status": "central_unavailable",
                "message": "WC2026_AGENT_DATA_CLIENT_UNAVAILABLE",
            }
        return await ctx.deps.wc2026_agent_data.get_current_match_context(
            ctx.deps.wc2026_context,
            locale=locale,
        )

    @agent.tool
    async def get_wc2026_model_methodology(
        ctx: RunContext[AgentDeps], locale: str = "zh-Hans"
    ) -> dict[str, Any]:
        """读取 WC2026 模型方法论。

        这是跨场通用公开方法论,不接受 match_id。
        """
        if ctx.deps.wc2026_agent_data is None:
            return {
                "ok": False,
                "status": "central_unavailable",
                "message": "WC2026_AGENT_DATA_CLIENT_UNAVAILABLE",
            }
        return await ctx.deps.wc2026_agent_data.get_methodology(locale=locale)

    return agent


def build_model(
    settings: Settings | None = None,
    secret_provider: SecretProvider | None = None,
) -> Model:
    """按配置选择 PydanticAI 原生 model;未知或 mock 时回退到离线 FunctionModel。"""
    settings = settings or get_settings()
    secret_provider = secret_provider or build_secret_provider(settings)
    provider = (settings.llm_provider or "mock").strip().lower()
    if is_mock_provider(provider):
        return build_mock_model()
    if provider == "openai":
        secret_provider.validate_required("openai", settings.openai_model)
        api_key = secret_provider.get_secret("openai_api_key")
        return _openai_model(
            settings.openai_model,
            settings.openai_base_url,
            api_key.reveal() if api_key else "",
        )
    if provider == "qwen":
        secret_provider.validate_required("qwen", settings.qwen_model)
        api_key = secret_provider.get_secret("dashscope_api_key")
        return _openai_model(
            settings.qwen_model,
            settings.qwen_base_url,
            api_key.reveal() if api_key else "",
        )
    if provider == "zai":
        secret_provider.validate_required("zai", settings.zai_model)
        api_key = secret_provider.get_secret("zai_api_key")
        return _zai_model(settings, api_key.reveal() if api_key else "")
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        secret_provider.validate_required("anthropic", settings.anthropic_model)
        api_key = secret_provider.get_secret("anthropic_api_key")
        return AnthropicModel(
            settings.anthropic_model,
            provider=AnthropicProvider(api_key=api_key.reveal() if api_key else ""),
        )
    if provider == "gemini":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        secret_provider.validate_required("gemini", settings.gemini_model)
        api_key = secret_provider.get_secret("gemini_api_key")
        return GoogleModel(
            settings.gemini_model,
            provider=GoogleProvider(api_key=api_key.reveal() if api_key else ""),
        )
    return build_mock_model()


def _openai_model(model: str, base_url: str, api_key: str) -> Model:
    """构造 OpenAI 兼容 model(OpenAI 官方端点与 DashScope/Qwen 共用)。"""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    if not api_key:
        raise RuntimeError("PROVIDER_SECRET_MISSING provider=openai-compatible")
    return OpenAIChatModel(
        model,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )


def _zai_model(settings: Settings, api_key: str) -> Model:
    """构造 Z.AI GLM OpenAI-compatible model."""
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider

    if not api_key:
        raise RuntimeError("PROVIDER_SECRET_MISSING provider=zai")
    extra_body: dict[str, Any] = {
        "max_tokens": settings.provider_default_max_output_tokens,
        "tool_stream": settings.zai_tool_stream,
    }
    thinking_type = (settings.zai_thinking_type or "").strip().lower()
    if thinking_type:
        extra_body["thinking"] = {"type": thinking_type}
    reasoning_effort = (settings.zai_reasoning_effort or "").strip()
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    return OpenAIChatModel(
        settings.zai_model,
        provider=OpenAIProvider(
            base_url=settings.zai_base_url,
            api_key=api_key,
        ),
        profile=OpenAIModelProfile(
            openai_chat_thinking_field="reasoning_content",
            openai_supports_strict_tool_definition=False,
        ),
        settings={"extra_body": extra_body},
    )


def build_mock_model() -> FunctionModel:
    """离线 mock model:零 key、确定性,演示一次「检索 -> 中文回答」的 agentic 流程。

    行为:首轮在知识检索工具可用且尚无工具结果时,主动调用一次
    search_knowledge;拿到结果后(或工具不可用时)产出基于问题的确定性中文
    回答。保证整个平台在无任何外部模型 / API key 时仍可端到端跑通。

    同时提供非流式 function 与流式 stream_function:orchestrator 走逐 token
    流式路径(stream_function),run_sync 等非流式调用走 function。
    """
    return FunctionModel(
        _mock_model_function, stream_function=_mock_stream_function
    )


def _mock_model_function(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """FunctionModel 非流式回调:据消息历史决定先检索还是直接作答。"""
    question = _last_user_text(messages)
    if _should_mock_retrieve(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=TOOL_SEARCH_KNOWLEDGE,
                    args={"query": question or ""},
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content=_mock_answer(question))])


async def _mock_stream_function(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    """FunctionModel 流式回调:首轮按需流式发起一次检索工具调用,否则流式出文本。"""
    question = _last_user_text(messages)
    if _should_mock_retrieve(messages, info):
        yield {
            0: DeltaToolCall(
                name=TOOL_SEARCH_KNOWLEDGE,
                json_args=json.dumps(
                    {"query": question or ""}, ensure_ascii=False
                ),
            )
        }
        return
    answer = _mock_answer(question)
    for i in range(0, len(answer), _MOCK_CHUNK_SIZE):
        yield answer[i : i + _MOCK_CHUNK_SIZE]


def _should_mock_retrieve(
    messages: list[ModelMessage], info: AgentInfo
) -> bool:
    """mock 是否应在本轮发起检索:工具已注册且历史中尚无任何工具返回。"""
    has_search = any(
        getattr(t, "name", None) == TOOL_SEARCH_KNOWLEDGE
        for t in info.function_tools
    )
    if not has_search:
        return False
    return not _has_tool_result(messages)


def _has_tool_result(messages: list[ModelMessage]) -> bool:
    """历史消息中是否已存在工具返回(ToolReturnPart)。"""
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    return True
    return False


def _last_user_text(messages: list[ModelMessage]) -> str:
    """提取最后一条用户输入文本(UserPromptPart),取不到返回空串。"""
    from pydantic_ai.messages import UserPromptPart

    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    if isinstance(content, str):
                        return content.strip()
                    return str(content).strip()
    return ""


def _mock_answer(question: str) -> str:
    """生成确定性中文回答(供离线演示)。"""
    if not question:
        return "你好,我是世界杯预测 Chat Server 的内置离线助手(mock),请告诉我要分析的比赛。"
    return (
        f"你问的是「{question}」。这是世界杯预测 Chat Server 的确定性离线回答,"
        "用于在无外部模型与 API key 时演示检索、工具调用、流式输出和运行状态收敛链路。"
        "真实赛前结论需要补齐证据账本、比分概率分布、可执行 CLOB 价格和 no-bet 条件。"
    )
