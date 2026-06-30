"""运行时依赖容器与装配工厂。

RuntimeDeps 聚合 Orchestrator 运行所需的全部协作者(检索器、工具路由、
LLM 路由、事件总线、仓储)。这里同时声明协作者的最小 Protocol 契约,
使编排核心仅依赖抽象;build_deps() 负责按配置装配真实实现,缺失时安全降级。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.events import AgentEvent
from app.core.logging import get_logger, log_with_fields
from app.core.metrics import Metrics
from app.core.secrets import SecretProvider, build_secret_provider
from app.runtime.provider_limits import build_provider_limiter

logger = get_logger(__name__)


# --- 协作者最小契约(由各兄弟模块实现,这里仅声明形状) ---


@runtime_checkable
class RAGRetriever(Protocol):
    """检索器:给定查询返回若干文档片段。"""

    async def retrieve(self, query: str, top_k: int) -> Any:
        """返回 RAG 查询响应 dict,或旧式文档列表。"""
        ...


@runtime_checkable
class ToolRouter(Protocol):
    """工具路由:挑选并执行合适的工具。"""

    async def route(
        self,
        query: str,
        tool_name: str | None = None,
        *,
        agent_run_id: str = "",
    ) -> dict[str, Any]:
        """执行工具并返回结构化结果,至少含 tool_name/result/status。"""
        ...


@runtime_checkable
class EventBusLike(Protocol):
    """事件总线:发布事件到指定 channel。"""

    async def publish(self, channel: str, event: AgentEvent) -> AgentEvent | None:
        """发布一条事件。"""
        ...


@runtime_checkable
class MessageRepository(Protocol):
    """消息仓储:读取历史与写入新消息。"""

    async def list_by_conversation(
        self, conversation_id: str, limit: int
    ) -> list[Any]:
        """按时间升序返回会话内消息(ORM 对象,含 role/content)。"""
        ...

    async def add(
        self,
        conversation_id: str,
        role: Any,
        content: str,
        token_count: int = 0,
        meta: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> Any:
        """新增一条消息并返回。"""
        ...


@runtime_checkable
class RunRepository(Protocol):
    """运行仓储:更新 AgentRun 的状态与结果字段。"""

    async def mark_running(
        self, agent_run_id: str, intent: Any | None = None
    ) -> None:
        """置为运行中,可选写入 intent。"""
        ...

    async def set_plan(self, agent_run_id: str, plan: dict[str, Any]) -> None:
        """写入计划快照。"""
        ...

    async def mark_running_with_plan(
        self, agent_run_id: str, intent: Any | None, plan: dict[str, Any]
    ) -> None:
        """置为运行中并写入计划快照。"""
        ...

    async def mark_succeeded(self, agent_run_id: str) -> None:
        """置为成功并记录结束时间。"""
        ...

    async def mark_succeeded_with_answer(
        self,
        agent_run_id: str,
        conversation_id: str,
        answer: str,
        token_count: int,
    ) -> None:
        """写入最终 assistant 消息并置为成功。"""
        ...

    async def mark_failed(self, agent_run_id: str, error: str) -> None:
        """置为失败并记录错误。"""
        ...


@dataclass
class RuntimeDeps:
    """编排所需依赖的聚合容器。

    由 task 层用 build_deps() 装配后注入 Orchestrator;测试可用假实现直接构造。
    """

    retriever: RAGRetriever
    tool_router: ToolRouter
    event_bus: EventBusLike
    message_repo: MessageRepository
    run_repo: RunRepository
    settings: Settings
    metrics: Metrics | None = None
    provider_limiter: Any | None = None
    secret_provider: SecretProvider | None = None
    wc2026_agent_data: Any | None = None


def build_deps(
    session: Any | None = None,
    *,
    event_bus: EventBusLike | None = None,
    redis_client: Any | None = None,
    provider_limiter: Any | None = None,
    secret_provider: SecretProvider | None = None,
    metrics: Metrics | None = None,
) -> RuntimeDeps:
    """装配真实依赖。

    参数:
        session: 可选的 AsyncSession,用于仓储持久化;为 None 时仓储层
            自行从 session 工厂获取(由各仓储实现决定)。

    任何兄弟模块尚未就位时抛出清晰的 ImportError,便于集成期定位缺失。
    """
    settings = get_settings()
    if redis_client is None:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        retriever = _build_retriever(settings)
        tool_router = _build_tool_router(settings, session)
        event_bus = event_bus or _build_event_bus(settings, redis_client)
        message_repo, run_repo = _build_repos(session)
        secret_provider = secret_provider or build_secret_provider(settings)
        wc2026_agent_data = _build_wc2026_agent_data(settings)
        provider_limiter = provider_limiter or build_provider_limiter(
            settings, redis_client=redis_client, metrics=metrics
        )
    except ImportError as exc:
        log_with_fields(
            logger,
            logging.ERROR,
            "build_deps failed: a collaborator module is missing",
            error=str(exc),
        )
        raise

    return RuntimeDeps(
        retriever=retriever,
        tool_router=tool_router,
        event_bus=event_bus,
        message_repo=message_repo,
        run_repo=run_repo,
        settings=settings,
        metrics=metrics or Metrics(),
        provider_limiter=provider_limiter,
        secret_provider=secret_provider,
        wc2026_agent_data=wc2026_agent_data,
    )


def _build_retriever(settings: Settings) -> RAGRetriever:
    """装配检索器。

    这里用薄适配器把查询服务对齐到 Agent tool 需要的 retrieve 契约。
    """
    from app.runtime.adapters import RetrieverAdapter

    return RetrieverAdapter()


def _build_tool_router(settings: Settings, session: Any | None = None) -> ToolRouter:
    """装配工具路由。

    app.tools.router.ToolRouter.route(plan_step) 返回 Tool 且 execute 分离;
    用薄适配器对齐到 route(query, tool_name) -> dict 契约。
    """
    from app.runtime.adapters import ToolRouterAdapter

    return ToolRouterAdapter(log_sink=_build_tool_log_sink(session))


def _build_event_bus(
    settings: Settings, redis_client: Any | None = None
) -> EventBusLike:
    """装配 Redis Stream 事件总线。"""
    from app.bus.stream_bus import StreamBus

    return StreamBus(settings.redis_url, redis_client=redis_client)


def _build_repos(
    session: Any | None,
) -> tuple[MessageRepository, RunRepository]:
    """装配消息与运行仓储。

    真实仓储为 MessageRepository / AgentRunRepository(需 AsyncSession),
    且方法名/签名与本模块契约不同(create vs add、update_status vs mark_*),
    故用薄适配器对齐。
    """
    from app.runtime.adapters import (
        MessageRepoAdapter,
        RunRepoAdapter,
    )

    return MessageRepoAdapter(session), RunRepoAdapter(session)


def _build_tool_log_sink(session: Any | None):
    """Build a ToolCallLog sink for ToolRouter audit writes."""

    async def sink(log: Any) -> None:
        if session is not None:
            session.add(log)
            await session.commit()
            return
        from app.db.session import async_session_factory

        async with async_session_factory() as scoped:
            scoped.add(log)
            await scoped.commit()

    return sink


def _build_wc2026_agent_data(settings: Settings) -> Any:
    from app.runtime.wc2026_agent_data import build_wc2026_agent_data_service

    return build_wc2026_agent_data_service(settings)
