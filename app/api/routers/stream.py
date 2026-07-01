"""流式网关路由:SSE 与 WebSocket。

- GET /api/v1/wc2026/chat/stream/{agent_run_id}: 用 sse-starlette 订阅 EventBus 频道 run:{id},
  将 AgentEvent 逐条以 SSE 推送,收到 RUN_COMPLETED/ERROR 后结束。
- WS /api/v1/wc2026/chat/ws/{agent_run_id}: 等价的 WebSocket 推送。

两者都从同一事件总线读取,API 层不缓存事件,断线后客户端可重连重订阅。
WebSocket 不经 HTTP 中间件,故在握手阶段自行做轻量鉴权。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import AsyncIterator

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, ReposDep, get_event_bus
from app.api.repos import Repos
from app.core.config import get_settings
from app.core.events import AgentEvent, EventType
from app.core.ids import new_trace_id
from app.core.interfaces import EventBus
from app.core.logging import get_logger, log_with_fields, set_trace_id
from app.core.metrics import Metrics
from app.db.session import async_session_factory

logger = get_logger(__name__)

_WC2026_PREFIX = "/api/v1/wc2026"
_WC2026_USER_QUERY = "user_uuid"
_SSE_PING_SECONDS = 5

router = APIRouter(prefix=_WC2026_PREFIX, tags=["stream"])

# 终止事件:收到后结束流
_TERMINAL_TYPES = {EventType.RUN_COMPLETED, EventType.ERROR}


def _channel(agent_run_id: str) -> str:
    """运行对应的事件总线频道名。"""
    return f"run:{agent_run_id}"


@router.get("/chat/stream/{agent_run_id}")
async def stream_sse(
    agent_run_id: str,
    request: Request,
    user: CurrentUser,
    repos: ReposDep,
    bus: EventBus = Depends(get_event_bus),
) -> EventSourceResponse:
    """SSE 端点:逐条推送事件直至终止或客户端断开。"""
    await _assert_run_owner(agent_run_id, user, repos)
    last_event_id = request.headers.get("last-event-id")
    connection_lease = await _acquire_stream_connection(request.app.state, agent_run_id)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in _iter_events(bus, agent_run_id, last_event_id):
                if await request.is_disconnected():
                    break
                yield event.to_sse()
        except Exception as exc:  # 订阅异常:发一条 error 事件后收尾
            log_with_fields(
                logger,
                logging.ERROR,
                "SSE 订阅异常",
                agent_run_id=agent_run_id,
                error=str(exc),
            )
            yield {"event": EventType.ERROR.value, "data": str(exc)}
        finally:
            await connection_lease.release()

    return EventSourceResponse(event_generator(), ping=_SSE_PING_SECONDS)


@router.websocket("/chat/ws/{agent_run_id}")
async def stream_ws(websocket: WebSocket, agent_run_id: str) -> None:
    """WebSocket 端点:等价于 SSE 的事件推送。"""
    bus = getattr(websocket.app.state, "event_bus", None)
    if bus is None:
        await websocket.close(code=1011, reason="事件总线未就绪")
        return
    user_id = _ws_user_id(websocket)
    if not user_id:
        await websocket.close(code=1008, reason="缺少鉴权凭证")
        return
    async with async_session_factory() as session:
        repos = Repos(session)
        try:
            await _assert_run_owner(agent_run_id, user_id, repos)
        except HTTPException:
            await websocket.close(code=1008, reason="无权订阅该运行")
            return
    try:
        connection_lease = await _acquire_stream_connection(
            websocket.app.state,
            agent_run_id,
        )
    except HTTPException:
        await websocket.close(code=1008, reason="连接数过多")
        return
    await websocket.accept()
    try:
        await _pump_ws(
            websocket,
            bus,
            agent_run_id,
            websocket.query_params.get("last_event_id"),
        )
    finally:
        await connection_lease.release()


def _ws_user_id(websocket: WebSocket) -> str | None:
    """WebSocket 轻量鉴权:从 URL query user_uuid 提取 user id。"""
    user_uuid = websocket.query_params.get(_WC2026_USER_QUERY)
    if user_uuid and user_uuid.strip():
        return user_uuid.strip()
    return None


async def _pump_ws(
    websocket: WebSocket,
    bus: EventBus,
    agent_run_id: str,
    last_event_id: str | None = None,
) -> None:
    """从事件总线读取并通过 WS 推送,处理断开与异常。"""
    try:
        async for event in _iter_events(bus, agent_run_id, last_event_id):
            await websocket.send_text(_ws_payload(event))
    except WebSocketDisconnect:
        log_with_fields(
            logger, logging.INFO, "WS 客户端断开", agent_run_id=agent_run_id
        )
    except Exception as exc:
        log_with_fields(
            logger,
            logging.ERROR,
            "WS 推送异常",
            agent_run_id=agent_run_id,
            error=str(exc),
        )
    finally:
        set_trace_id(None)
        await _safe_close(websocket)


def _ws_payload(event: AgentEvent) -> str:
    """WS 帧采用与 Pub/Sub 一致的事件 JSON。"""
    return event.to_json()


async def _safe_close(websocket: WebSocket) -> None:
    """安全关闭 WS,忽略已关闭导致的异常。"""
    try:
        await websocket.close()
    except Exception:  # 已关闭:忽略
        pass


async def _iter_events(
    bus: EventBus,
    agent_run_id: str,
    last_event_id: str | None,
) -> AsyncIterator[AgentEvent]:
    """Iterate events from StreamBus with replay, or fallback to old EventBus."""
    if hasattr(bus, "replay"):
        try:
            async for event in bus.subscribe(agent_run_id, last_event_id):  # type: ignore[call-arg]
                yield event
                if _is_terminal(event):
                    break
        except Exception as exc:
            if _is_stream_gap(exc):
                yield _stream_gap_event(agent_run_id, last_event_id)
                return
            raise
        return

    last_seq = _parse_legacy_seq(last_event_id)
    async for event in bus.subscribe(_channel(agent_run_id)):
        if last_seq is not None and event.seq <= last_seq:
            continue
        yield event
        if _is_terminal(event):
            break


async def _assert_run_owner(agent_run_id: str, user_id: str, repos: Repos) -> None:
    """Ensure the current user owns the run before streaming events."""
    run = await repos.get_run(agent_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="运行记录不存在",
        )
    conversation = await repos.get_conversation(run.conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在",
        )
    owner = getattr(conversation, "user_id", None)
    if owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权订阅该运行",
        )


def _parse_legacy_seq(last_event_id: str | None) -> int | None:
    if last_event_id is None or "-" in last_event_id:
        return None
    try:
        return int(last_event_id)
    except ValueError:
        return None


def _is_terminal(event: AgentEvent) -> bool:
    event_type = event.type.value if hasattr(event.type, "value") else event.type
    return event_type in {item.value for item in _TERMINAL_TYPES}


def _is_stream_gap(exc: Exception) -> bool:
    return exc.__class__.__name__ == "StreamGapError"


def _stream_gap_event(agent_run_id: str, last_event_id: str | None) -> AgentEvent:
    Metrics().inc_counter("stream_replay_gap_total", {"route": "stream"})
    return AgentEvent(
        agent_run_id=agent_run_id,
        trace_id=new_trace_id(),
        type=EventType.ERROR,
        seq=0,
        data={
            "stage": "stream_replay",
            "error": "STREAM_GAP",
            "message": "stream replay cursor is outside retention",
            "last_event_id": last_event_id,
        },
    )


@dataclass
class _StreamConnectionLease:
    state: object
    agent_run_id: str
    enabled: bool = True
    released: bool = False

    async def release(self) -> None:
        if self.released or not self.enabled:
            return
        self.released = True
        lock = _stream_connection_lock(self.state)
        async with lock:
            counts = _stream_connection_counts(self.state)
            current = counts.get(self.agent_run_id, 0)
            if current <= 1:
                counts.pop(self.agent_run_id, None)
            else:
                counts[self.agent_run_id] = current - 1


async def _acquire_stream_connection(
    state: object, agent_run_id: str
) -> _StreamConnectionLease:
    limit = int(getattr(get_settings(), "stream_max_connections_per_run", 20) or 0)
    if limit <= 0:
        return _StreamConnectionLease(state, agent_run_id, enabled=False)
    lock = _stream_connection_lock(state)
    async with lock:
        counts = _stream_connection_counts(state)
        current = counts.get(agent_run_id, 0)
        if current >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="STREAM_CONNECTION_LIMIT",
            )
        counts[agent_run_id] = current + 1
    return _StreamConnectionLease(state, agent_run_id)


def _stream_connection_lock(state: object) -> asyncio.Lock:
    lock = getattr(state, "stream_connection_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(state, "stream_connection_lock", lock)
    return lock


def _stream_connection_counts(state: object) -> dict[str, int]:
    counts = getattr(state, "stream_connection_counts", None)
    if counts is None:
        counts = {}
        setattr(state, "stream_connection_counts", counts)
    return counts
