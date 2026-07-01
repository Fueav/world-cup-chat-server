"""健康检查路由。

- /healthz: 存活探针,进程在即返回 200。
- /readyz: 就绪探针,校验数据库与事件总线是否可用,任一失败返回 503。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import Metrics
from app.core.secrets import ProviderSecretMissingError, build_secret_provider
from app.db.session import async_session_factory
from app.runtime.provider_keys import (
    ProviderKeyPoolConfigError,
    build_provider_key_pool,
)
from app.runtime.provider_limits import provider_identity_from_settings

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:只要进程响应即视为存活。"""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """就绪探针:校验 DB、Redis、事件总线和 provider guardrails。"""
    checks: dict[str, str] = {}
    db_ok = await _check_db(checks)
    redis_ok = await _check_redis(request, checks)
    bus_ok = _check_bus(request, checks)
    secret_ok = _check_provider_secret(request, checks)
    limiter_ok = _check_provider_limiter(request, checks)
    _check_reaper(checks)
    ready = db_ok and redis_ok and bus_ok and secret_ok and limiter_ok
    code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus text metrics endpoint."""
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    metrics_obj = getattr(request.app.state, "metrics", None) or Metrics()
    body = metrics_obj.render_prometheus()
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


async def _check_db(checks: dict[str, str]) -> bool:
    """执行 SELECT 1 验证数据库可用。"""
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
        return True
    except Exception:
        checks["db"] = "error"
        return False


async def _check_redis(request: Request, checks: dict[str, str]) -> bool:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        checks["redis"] = "not_initialized"
        return False
    try:
        response = await redis.ping()
    except Exception:
        checks["redis"] = "error"
        return False
    checks["redis"] = "ok" if response else "error"
    return bool(response)


def _check_bus(request: Request, checks: dict[str, str]) -> bool:
    """校验事件总线单例是否已初始化。"""
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        checks["event_bus"] = "not_initialized"
        return False
    checks["event_bus"] = "ok"
    return True


def _check_provider_secret(request: Request, checks: dict[str, str]) -> bool:
    settings = get_settings()
    identity = provider_identity_from_settings(settings)
    if identity.mock:
        checks["provider_secret"] = "mock"
        checks["provider_key_pool"] = "mock"
        return True
    provider = getattr(request.app.state, "secret_provider", None)
    if provider is None:
        provider = build_secret_provider(settings)
    pool = getattr(request.app.state, "provider_key_pool", None)
    if pool is None:
        try:
            pool = build_provider_key_pool(settings, provider)
        except (OSError, ProviderKeyPoolConfigError):
            checks["provider_secret"] = "missing"
            checks["provider_key_pool"] = "invalid"
            return False
    if pool.enabled_slots:
        checks["provider_secret"] = (
            "key_pool" if pool.status != "single" else "configured"
        )
        checks["provider_key_pool"] = f"{pool.status}:{len(pool.enabled_slots)}"
        return True
    try:
        provider.validate_required(identity.provider, identity.model)
    except ProviderSecretMissingError:
        checks["provider_secret"] = "missing"
        checks["provider_key_pool"] = "missing"
        return False
    checks["provider_secret"] = "configured"
    checks["provider_key_pool"] = "single:1"
    return True


def _check_provider_limiter(request: Request, checks: dict[str, str]) -> bool:
    settings = get_settings()
    if not settings.provider_rate_limit_enabled:
        checks["provider_limiter"] = "disabled"
        return True
    limiter = getattr(request.app.state, "provider_limiter", None)
    if limiter is None:
        checks["provider_limiter"] = "unavailable"
        return False
    checks["provider_limiter"] = "ok"
    return True


def _check_reaper(checks: dict[str, str]) -> None:
    settings = get_settings()
    checks["reaper"] = "configured" if settings.reaper_enabled else "disabled"
