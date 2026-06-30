"""FastAPI 依赖注入。

集中提供请求级依赖:当前用户、仓储、事件总线、限流器。
所有有状态资源(redis/db)均按请求获取或经 app.state 暴露的单例访问,
保证 API 层本身无状态。
"""

from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.repos import Repos
from app.api.ratelimit import RateLimiter
from app.core.interfaces import EventBus
from app.core.logging import get_logger
from app.db.session import get_session

logger = get_logger(__name__)

# 鉴权 header 约定:Authorization: Bearer <token>,或 X-API-Key
_BEARER_PREFIX = "Bearer "
_WC2026_PREFIX = "/api/v1/wc2026"
# 缺失鉴权信息时使用的匿名用户(demo 模式可放行)
_ANON_USER = "anonymous"


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """从请求头解析 user_id。

    优先使用中间件已写入 request.state.user_id(避免重复解析),WC2026 chat-flow
    路由的该值来自 URL query user_uuid;
    否则从 Authorization Bearer 或 X-API-Key 提取 token 作为 user_id。
    demo 模式下缺失时降级为匿名用户而非直接 401(401 由中间件统一兜底)。
    """
    state_user = getattr(request.state, "user_id", None)
    if state_user:
        return state_user
    if request.url.path.startswith(_WC2026_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 user_uuid")
    token = _extract_token(authorization, x_api_key)
    return token or _ANON_USER


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    """从两类鉴权头中提取 token,均不存在返回 None。"""
    if authorization and authorization.startswith(_BEARER_PREFIX):
        candidate = authorization[len(_BEARER_PREFIX) :].strip()
        if candidate:
            return candidate
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    return None


async def get_repos(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[Repos]:
    """提供绑定到当前请求会话的仓储聚合。"""
    yield Repos(session)


def get_event_bus(request: Request) -> EventBus:
    """返回 app.state 上的事件总线单例。

    事件总线在应用 lifespan 启动时初始化;缺失说明应用未就绪。
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="事件总线尚未就绪",
        )
    return bus


def get_rate_limiter(request: Request) -> RateLimiter:
    """返回 app.state 上的限流器单例。"""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="限流器尚未就绪",
        )
    return limiter


CurrentUser = Annotated[str, Depends(get_current_user)]
ReposDep = Annotated[Repos, Depends(get_repos)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
