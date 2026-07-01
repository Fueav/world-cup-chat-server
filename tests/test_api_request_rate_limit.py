from __future__ import annotations

import inspect
from types import SimpleNamespace

import httpx

from app.api.ratelimit import RateLimiter
from app.core.metrics import InMemoryMetrics


_CHAT_ROUTE = "/api/v1/wc2026/chat"
_OTHER_ROUTE = "/api/v1/wc2026/admin-action"


class _CountingPipeline:
    def __init__(self, redis: _CountingRedis) -> None:
        self._redis = redis
        self._key: str | None = None

    def zremrangebyscore(self, key: str, *_args) -> None:
        self._key = key
        self._redis.commands.append(("zremrangebyscore", key))

    def zadd(self, key: str, *_args) -> None:
        self._redis.commands.append(("zadd", key))

    def zcard(self, key: str) -> None:
        self._redis.commands.append(("zcard", key))

    def expire(self, key: str, *_args) -> None:
        self._redis.commands.append(("expire", key))

    async def execute(self):
        assert self._key is not None
        self._redis.executed_keys.append(self._key)
        self._redis.counts[self._key] = self._redis.counts.get(self._key, 0) + 1
        return [0, 1, self._redis.counts[self._key], True]


class _CountingRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.commands: list[tuple[str, str]] = []
        self.executed_keys: list[str] = []

    def pipeline(self) -> _CountingPipeline:
        return _CountingPipeline(self)


class _FailingPipeline:
    def zremrangebyscore(self, *_args) -> None:
        return None

    def zadd(self, *_args) -> None:
        return None

    def zcard(self, *_args) -> None:
        return None

    def expire(self, *_args) -> None:
        return None

    async def execute(self):
        raise RuntimeError("redis unavailable")


class _FailingRedis:
    def pipeline(self) -> _FailingPipeline:
        return _FailingPipeline()


def _require_route_aware_limiter() -> None:
    signature = inspect.signature(RateLimiter.check)
    route = signature.parameters.get("route")
    assert route is not None, "RateLimiter.check must accept a route scope"
    assert route.kind is inspect.Parameter.KEYWORD_ONLY


def _require_metrics_injection() -> None:
    signature = inspect.signature(RateLimiter.__init__)
    assert "metrics" in signature.parameters, "RateLimiter must accept metrics"


async def test_api_rate_limiter_buckets_by_route_and_hashed_identity():
    _require_route_aware_limiter()
    redis = _CountingRedis()
    limiter = RateLimiter(redis, limit_per_min=1)

    first_chat = await limiter.check("sensitive-user-token", route=_CHAT_ROUTE)
    second_chat = await limiter.check("sensitive-user-token", route=_CHAT_ROUTE)
    other_route = await limiter.check("sensitive-user-token", route=_OTHER_ROUTE)

    assert first_chat.allowed is True
    assert second_chat.allowed is False
    assert other_route.allowed is True

    unique_keys = set(redis.executed_keys)
    assert len(unique_keys) == 2
    assert all("sensitive-user-token" not in key for key in unique_keys)
    assert any("api:v1:wc2026:chat" in key for key in unique_keys)
    assert any("api:v1:wc2026:admin-action" in key for key in unique_keys)


async def test_api_rate_limiter_fail_open_is_metriced_by_route():
    _require_route_aware_limiter()
    _require_metrics_injection()
    metrics = InMemoryMetrics()
    limiter = RateLimiter(_FailingRedis(), limit_per_min=1, metrics=metrics)

    result = await limiter.check("sensitive-user-token", route=_CHAT_ROUTE)

    assert result.allowed is True
    assert metrics.counters["api_rate_limit_fail_open_total"] == [
        (1.0, {"route": "api:v1:wc2026:chat"})
    ]


async def test_rate_limit_middleware_passes_request_path_as_route_scope():
    from app.api.main import create_app

    class _RecordingLimiter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        async def check(self, user_id: str, *, route: str | None = None):
            self.calls.append((user_id, route))
            return SimpleNamespace(
                allowed=False,
                limit=1,
                remaining=0,
                retry_after=60,
            )

    limiter = _RecordingLimiter()
    app = create_app()
    app.state.rate_limiter = limiter

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"{_CHAT_ROUTE}?user_uuid=route-user",
            json={"message": "hello"},
        )

    assert response.status_code == 429
    assert limiter.calls == [("route-user", _CHAT_ROUTE)]
