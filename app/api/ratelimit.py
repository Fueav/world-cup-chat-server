"""基于 redis.asyncio 的滑动窗口限流器。

以 route + identity hash 为维度,在固定时间窗口内统计请求数,超出阈值
即拒绝。采用 Redis sorted set 实现精确滑动窗口:成员为唯一请求标记,
score 为时间戳,按窗口边界裁剪过期成员后统计基数。Redis 不可用时降级
为放行(fail-open),避免入口限流组件故障导致整体不可用,同时记录告警日志
和指标。真实 provider quota 不由本限流器承担。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.logging import get_logger, log_with_fields
from app.core.metrics import Metrics

logger = get_logger(__name__)

# 滑动窗口长度(秒)
_WINDOW_SECONDS = 60
# Redis key 前缀
_KEY_PREFIX = "ratelimit:api:"
_ROUTE_LABEL_RE = re.compile(r"[^a-z0-9_.:\-]+")


@dataclass(frozen=True)
class RateLimitResult:
    """一次限流判定的结果。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    route: str = "default"
    identity_hash: str = ""


class RateLimiter:
    """滑动窗口限流器,key=route + identity hash。"""

    def __init__(
        self,
        redis: Redis,
        limit_per_min: int,
        window_seconds: int = _WINDOW_SECONDS,
        metrics: Metrics | None = None,
    ) -> None:
        """注入 redis 客户端与每分钟阈值。"""
        self._redis = redis
        self._limit = max(1, limit_per_min)
        self._window = window_seconds
        self._metrics = metrics or Metrics()

    async def check(self, user_id: str, *, route: str = "default") -> RateLimitResult:
        """判定指定用户是否允许本次请求。

        使用 Redis pipeline 原子地完成:裁剪过期成员、写入本次标记、
        统计窗口内基数、刷新 TTL。Redis 异常时 fail-open 放行。
        """
        now = time.time()
        route_label = normalize_route_label(route)
        identity_hash = hash_rate_limit_identity(user_id)
        key = f"{_KEY_PREFIX}{route_label}:{identity_hash}"
        window_start = now - self._window
        member = f"{now:.6f}:{uuid.uuid4().hex}"
        try:
            count = await self._run_pipeline(key, window_start, now, member)
        except Exception as exc:  # redis 故障:降级放行
            self._metrics.inc_counter(
                "api_rate_limit_fail_open_total",
                {"route": route_label},
            )
            log_with_fields(
                logger,
                logging.WARNING,
                "限流器降级放行(redis 不可用)",
                route=route_label,
                identity_hash=identity_hash,
                error=str(exc),
            )
            return RateLimitResult(
                allowed=True,
                limit=self._limit,
                remaining=self._limit,
                retry_after=0,
                route=route_label,
                identity_hash=identity_hash,
            )
        return self._build_result(count, route_label, identity_hash)

    async def _run_pipeline(
        self, key: str, window_start: float, now: float, member: str
    ) -> int:
        """执行原子 pipeline,返回当前窗口内的请求计数。"""
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, self._window + 1)
        results = await pipe.execute()
        # zcard 是第 3 条命令(索引 2)的返回
        return int(results[2])

    def _build_result(
        self, count: int, route: str, identity_hash: str
    ) -> RateLimitResult:
        """根据计数生成限流结果。"""
        allowed = count <= self._limit
        remaining = max(0, self._limit - count)
        retry_after = 0 if allowed else self._window
        return RateLimitResult(
            allowed=allowed,
            limit=self._limit,
            remaining=remaining,
            retry_after=retry_after,
            route=route,
            identity_hash=identity_hash,
        )


def normalize_route_label(route: str) -> str:
    """Return a stable, Redis-key-safe label for a request route."""
    raw = (route or "default").split("?", 1)[0].strip().lower().strip("/")
    if not raw:
        return "root"
    label = _ROUTE_LABEL_RE.sub(":", raw.replace("/", ":")).strip(":")
    return label or "root"


def hash_rate_limit_identity(identity: str) -> str:
    """Hash user identity so raw bearer/API keys never appear in Redis keys."""
    value = (identity or "anonymous").strip() or "anonymous"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
