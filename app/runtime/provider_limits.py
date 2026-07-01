"""Provider/model global rate limiting and usage settlement."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.core.metrics import Metrics
from app.core.secrets import (
    SecretProvider,
    SecretValue,
    build_secret_provider,
    is_mock_provider,
)
from app.runtime.provider_keys import ProviderKeyPool, build_provider_key_pool

ProviderLimitReason = Literal[
    "ALLOWED",
    "RATE_LIMITED",
    "BACKING_OFF",
    "CONFIG_MISSING",
    "UNAVAILABLE",
]


def _min_retry(current: int | None, candidate: int) -> int:
    if current is None:
        return candidate
    return min(current, candidate)


class ProviderRateLimitError(RuntimeError):
    """Raised when a model call cannot proceed under provider quota."""

    def __init__(
        self,
        reason: ProviderLimitReason,
        *,
        retry_after_ms: int | None = None,
    ) -> None:
        self.reason = reason
        self.retry_after_ms = retry_after_ms
        super().__init__(reason)


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    mock: bool = False

    @property
    def key(self) -> str:
        return provider_key(self.provider, self.model)


@dataclass(frozen=True)
class ProviderLimitConfig:
    rpm: int
    tpm: int

    def __post_init__(self) -> None:
        if self.rpm <= 0 or self.tpm <= 0:
            raise ValueError("provider limit rpm/tpm must be positive")


@dataclass(frozen=True)
class ProviderLimitRequest:
    provider: str
    model: str
    estimated_input_tokens: int
    max_output_tokens: int
    route_type: str
    agent_run_id: str | None = None
    user_id: str | None = None

    @property
    def reserved_tokens(self) -> int:
        return max(0, self.estimated_input_tokens) + max(0, self.max_output_tokens)


@dataclass(frozen=True)
class ProviderLimitDecision:
    allowed: bool
    reason: ProviderLimitReason
    retry_after_ms: int | None = None
    provider_limit_key: str | None = None
    reserved_tokens: int = 0
    remaining_rpm: float | None = None
    remaining_tpm: float | None = None
    provider_key_id: str | None = None
    provider_key_secret: SecretValue | None = None
    provider_key_scope: str | None = None


@dataclass(frozen=True)
class ProviderUsageSettlement:
    provider: str
    model: str
    reserved_tokens: int
    actual_input_tokens: int | None
    actual_output_tokens: int | None
    route_type: str
    agent_run_id: str | None = None
    provider_key_id: str | None = None

    @property
    def actual_total_tokens(self) -> int | None:
        if self.actual_input_tokens is None or self.actual_output_tokens is None:
            return None
        return max(0, self.actual_input_tokens) + max(0, self.actual_output_tokens)


@dataclass(frozen=True)
class ProviderUsageDecision:
    settled: bool
    usage_missing: bool = False
    debit_tokens: int = 0
    remaining_tpm: float | None = None
    provider_key_id: str | None = None


_SAFE_KEY_RE = re.compile(r"[^a-z0-9_.:-]+")


def canonical_name(value: str) -> str:
    normalized = _SAFE_KEY_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or "unknown"


def provider_key(provider: str, model: str) -> str:
    return f"ratelimit:provider:{canonical_name(provider)}:{canonical_name(model)}"


def provider_backoff_key(provider: str, model: str) -> str:
    return f"backoff:provider:{canonical_name(provider)}:{canonical_name(model)}"


def provider_key_slot_key(provider: str, model: str, key_id: str) -> str:
    return (
        f"ratelimit:provider:{canonical_name(provider)}:"
        f"{canonical_name(model)}:key:{canonical_name(key_id)}"
    )


def provider_key_slot_backoff_key(provider: str, model: str, key_id: str) -> str:
    return (
        f"backoff:provider:{canonical_name(provider)}:"
        f"{canonical_name(model)}:key:{canonical_name(key_id)}"
    )


def provider_aggregate_key(provider: str, model: str) -> str:
    return f"ratelimit:provider:{canonical_name(provider)}:{canonical_name(model)}:aggregate"


def provider_aggregate_backoff_key(provider: str, model: str) -> str:
    return f"backoff:provider:{canonical_name(provider)}:{canonical_name(model)}:aggregate"


def provider_key_pool_cursor_key(provider: str, model: str) -> str:
    return f"ratelimit:provider:{canonical_name(provider)}:{canonical_name(model)}:keypool:cursor"


def provider_identity_from_settings(settings: Settings) -> ProviderIdentity:
    provider = (getattr(settings, "llm_provider", "mock") or "mock").strip().lower()
    if is_mock_provider(provider):
        return ProviderIdentity("mock", "mock", mock=True)
    model = {
        "openai": getattr(settings, "openai_model", "openai"),
        "qwen": getattr(settings, "qwen_model", "qwen"),
        "zai": getattr(settings, "zai_model", "glm-5.2"),
        "anthropic": getattr(settings, "anthropic_model", "anthropic"),
        "gemini": getattr(settings, "gemini_model", "gemini"),
    }.get(provider, provider)
    return ProviderIdentity(canonical_name(provider), canonical_name(model), mock=False)


def estimate_input_tokens(text: str) -> int:
    """Cheap conservative-ish estimate for admission before provider usage exists."""
    return max(1, math.ceil(len(text or "") / 3))


def load_provider_limit_config(settings: Settings) -> dict[tuple[str, str], ProviderLimitConfig]:
    raw = getattr(settings, "provider_rate_limits_json", "{}") or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid provider_rate_limits_json") from exc
    configs: dict[tuple[str, str], ProviderLimitConfig] = {}
    if not isinstance(parsed, dict):
        raise ValueError("provider_rate_limits_json must be an object")
    for key, value in parsed.items():
        if not isinstance(value, dict):
            raise ValueError("provider limit entry must be an object")
        provider, _, model = str(key).partition(":")
        if not model:
            provider, _, model = str(key).partition("/")
        if not model:
            raise ValueError("provider limit key must be provider:model")
        configs[(canonical_name(provider), canonical_name(model))] = ProviderLimitConfig(
            rpm=int(value.get("rpm", getattr(settings, "provider_default_rpm", 100000))),
            tpm=int(value.get("tpm", getattr(settings, "provider_default_tpm", 1000000))),
        )
    return configs


class InMemoryProviderRateLimiter:
    """Deterministic limiter used by tests and as a local no-Redis fallback."""

    def __init__(
        self,
        configs: dict[tuple[str, str], ProviderLimitConfig] | None = None,
        *,
        default_config: ProviderLimitConfig | None = None,
        key_pool: ProviderKeyPool | None = None,
        metrics: Metrics | None = None,
        now_ms: Any | None = None,
    ) -> None:
        self._configs = configs or {}
        self._default = default_config or ProviderLimitConfig(rpm=100000, tpm=1000000)
        self._metrics = metrics or Metrics()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._key_pool = key_pool
        self._buckets: dict[str, dict[str, float]] = {}
        self._backoff_until: dict[str, int] = {}
        self._pool_cursors: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        async with self._lock:
            return self._acquire_locked(request, reserve=False)

    async def acquire(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        async with self._lock:
            return self._acquire_locked(request, reserve=True)

    async def settle_usage(self, settlement: ProviderUsageSettlement) -> ProviderUsageDecision:
        actual = settlement.actual_total_tokens
        labels = {
            "provider": canonical_name(settlement.provider),
            "model": canonical_name(settlement.model),
            "route_type": settlement.route_type,
        }
        if actual is None:
            self._metrics.inc_counter("provider_usage_missing_total", labels)
            return ProviderUsageDecision(
                settled=False,
                usage_missing=True,
                provider_key_id=settlement.provider_key_id,
            )
        debit = max(0, actual - max(0, settlement.reserved_tokens))
        async with self._lock:
            remaining_tpm = self._settle_locked(settlement, debit)
        self._metrics.inc_counter(
            "provider_rate_limit_tokens_settled_total", labels, actual
        )
        if debit:
            self._metrics.inc_counter(
                "provider_rate_limit_tokens_debt_total", labels, debit
            )
        return ProviderUsageDecision(
            settled=True,
            debit_tokens=debit,
            remaining_tpm=remaining_tpm,
            provider_key_id=settlement.provider_key_id,
        )

    async def record_provider_error(
        self,
        provider: str,
        model: str,
        status_code: int,
        retry_after_ms: int | None = None,
        provider_key_id: str | None = None,
    ) -> None:
        if status_code < 429:
            return
        retry_after_ms = retry_after_ms or (30000 if status_code == 429 else 5000)
        if provider_key_id:
            backoff_key = provider_key_slot_backoff_key(provider, model, provider_key_id)
        elif self._matches_key_pool_names(provider, model):
            backoff_key = provider_aggregate_backoff_key(provider, model)
        else:
            backoff_key = provider_backoff_key(provider, model)
        self._backoff_until[backoff_key] = (
            self._now_ms() + retry_after_ms
        )
        self._metrics.inc_counter(
            "provider_errors_total",
            {
                "provider": canonical_name(provider),
                "model": canonical_name(model),
                "status_code": str(status_code),
            },
        )

    def _acquire_locked(
        self, request: ProviderLimitRequest, *, reserve: bool
    ) -> ProviderLimitDecision:
        if self._matches_key_pool(request):
            return self._acquire_key_pool_locked(request, reserve=reserve)
        cfg = self._config_for(request.provider, request.model)
        key = provider_key(request.provider, request.model)
        backoff = self._retry_after_backoff_ms(request.provider, request.model)
        if backoff > 0:
            return self._decision(request, False, "BACKING_OFF", backoff, key)
        bucket = self._bucket(key, cfg)
        self._refill(bucket, cfg)
        need_rpm = 1
        need_tpm = max(1, request.reserved_tokens)
        rpm_wait = self._wait_ms(bucket["rpm_tokens"], need_rpm, cfg.rpm)
        tpm_wait = self._wait_ms(bucket["tpm_tokens"], need_tpm, cfg.tpm)
        retry_after = max(rpm_wait, tpm_wait)
        if retry_after > 0:
            return self._decision(
                request, False, "RATE_LIMITED", retry_after, key, bucket
            )
        if reserve:
            bucket["rpm_tokens"] -= need_rpm
            bucket["tpm_tokens"] -= need_tpm
            self._metrics.inc_counter(
                "provider_rate_limit_tokens_reserved_total",
                {
                    "provider": canonical_name(request.provider),
                    "model": canonical_name(request.model),
                    "route_type": request.route_type,
                },
                need_tpm,
            )
        return self._decision(request, True, "ALLOWED", None, key, bucket)

    def _matches_key_pool(self, request: ProviderLimitRequest) -> bool:
        pool = self._key_pool
        if pool is None or not pool.enabled_slots:
            return False
        return (
            pool.provider == canonical_name(request.provider)
            and pool.model == canonical_name(request.model)
        )

    def _matches_key_pool_names(self, provider: str, model: str) -> bool:
        pool = self._key_pool
        if pool is None or not pool.enabled_slots:
            return False
        return (
            pool.provider == canonical_name(provider)
            and pool.model == canonical_name(model)
        )

    def _acquire_key_pool_locked(
        self, request: ProviderLimitRequest, *, reserve: bool
    ) -> ProviderLimitDecision:
        pool = self._key_pool
        if pool is None:
            return self._decision(
                request, False, "CONFIG_MISSING", None, provider_key(request.provider, request.model)
            )
        aggregate_cfg = ProviderLimitConfig(
            rpm=int(pool.aggregate_rpm or self._default.rpm),
            tpm=int(pool.aggregate_tpm or self._default.tpm),
        )
        aggregate_key = provider_aggregate_key(request.provider, request.model)
        aggregate_backoff = self._backoff_until.get(
            provider_aggregate_backoff_key(request.provider, request.model),
            0,
        )
        retry_after = max(0, aggregate_backoff - self._now_ms())
        if retry_after > 0:
            return self._decision(
                request,
                False,
                "BACKING_OFF",
                retry_after,
                aggregate_key,
            )
        aggregate_bucket = self._bucket(aggregate_key, aggregate_cfg)
        self._refill(aggregate_bucket, aggregate_cfg)
        aggregate_wait = max(
            self._wait_ms(aggregate_bucket["rpm_tokens"], 1, aggregate_cfg.rpm),
            self._wait_ms(
                aggregate_bucket["tpm_tokens"],
                max(1, request.reserved_tokens),
                aggregate_cfg.tpm,
            ),
        )
        if aggregate_wait > 0:
            return self._decision(
                request,
                False,
                "RATE_LIMITED",
                aggregate_wait,
                aggregate_key,
                aggregate_bucket,
            )

        candidates = self._ordered_pool_slots(pool)
        best_retry_after: int | None = None
        best_reason: ProviderLimitReason = "RATE_LIMITED"
        for slot in candidates:
            slot_key = provider_key_slot_key(request.provider, request.model, slot.key_id)
            slot_backoff = self._backoff_until.get(
                provider_key_slot_backoff_key(request.provider, request.model, slot.key_id),
                0,
            )
            retry_after = max(0, slot_backoff - self._now_ms())
            if retry_after > 0:
                best_retry_after = _min_retry(best_retry_after, retry_after)
                best_reason = "BACKING_OFF" if best_retry_after == retry_after else best_reason
                continue
            slot_cfg = ProviderLimitConfig(rpm=slot.rpm, tpm=slot.tpm)
            slot_bucket = self._bucket(slot_key, slot_cfg)
            self._refill(slot_bucket, slot_cfg)
            wait = max(
                self._wait_ms(slot_bucket["rpm_tokens"], 1, slot_cfg.rpm),
                self._wait_ms(
                    slot_bucket["tpm_tokens"],
                    max(1, request.reserved_tokens),
                    slot_cfg.tpm,
                ),
            )
            if wait > 0:
                best_retry_after = _min_retry(best_retry_after, wait)
                continue
            if reserve:
                aggregate_bucket["rpm_tokens"] -= 1
                aggregate_bucket["tpm_tokens"] -= max(1, request.reserved_tokens)
                slot_bucket["rpm_tokens"] -= 1
                slot_bucket["tpm_tokens"] -= max(1, request.reserved_tokens)
                self._advance_pool_cursor(pool, slot.key_id)
                self._metrics.inc_counter(
                    "provider_key_tokens_reserved_total",
                    {
                        "provider": canonical_name(request.provider),
                        "model": canonical_name(request.model),
                        "route_type": request.route_type,
                    },
                    max(1, request.reserved_tokens),
                )
            decision = self._decision(
                request,
                True,
                "ALLOWED",
                None,
                slot_key,
                slot_bucket,
            )
            return ProviderLimitDecision(
                allowed=decision.allowed,
                reason=decision.reason,
                retry_after_ms=decision.retry_after_ms,
                provider_limit_key=decision.provider_limit_key,
                reserved_tokens=decision.reserved_tokens,
                remaining_rpm=decision.remaining_rpm,
                remaining_tpm=decision.remaining_tpm,
                provider_key_id=slot.key_id,
                provider_key_secret=slot.secret,
                provider_key_scope=pool.scope,
            )

        self._metrics.inc_counter(
            "provider_key_pool_exhausted_total",
            {
                "provider": canonical_name(request.provider),
                "model": canonical_name(request.model),
                "route_type": request.route_type,
                "reason": best_reason,
            },
        )
        return self._decision(
            request,
            False,
            best_reason,
            best_retry_after,
            aggregate_key,
            aggregate_bucket,
        )

    def _ordered_pool_slots(self, pool: ProviderKeyPool) -> list[Any]:
        slots = pool.enabled_slots
        if not slots:
            return []
        cursor_key = f"{pool.provider}:{pool.model}"
        cursor = self._pool_cursors.get(cursor_key, 0) % len(slots)
        return slots[cursor:] + slots[:cursor]

    def _advance_pool_cursor(self, pool: ProviderKeyPool, selected_key_id: str) -> None:
        slots = pool.enabled_slots
        if not slots:
            return
        cursor_key = f"{pool.provider}:{pool.model}"
        for index, slot in enumerate(slots):
            if slot.key_id == selected_key_id:
                self._pool_cursors[cursor_key] = (index + 1) % len(slots)
                return

    def _settle_locked(
        self,
        settlement: ProviderUsageSettlement,
        debit: int,
    ) -> float:
        if self._key_pool is not None and settlement.provider_key_id:
            pool = self._key_pool
            slot = pool.slot_by_id(settlement.provider_key_id)
            if slot is not None:
                slot_cfg = ProviderLimitConfig(rpm=slot.rpm, tpm=slot.tpm)
                slot_bucket = self._bucket(
                    provider_key_slot_key(settlement.provider, settlement.model, slot.key_id),
                    slot_cfg,
                )
                self._refill(slot_bucket, slot_cfg)
                if debit:
                    slot_bucket["tpm_tokens"] -= debit
                aggregate_cfg = ProviderLimitConfig(
                    rpm=int(pool.aggregate_rpm or self._default.rpm),
                    tpm=int(pool.aggregate_tpm or self._default.tpm),
                )
                aggregate_bucket = self._bucket(
                    provider_aggregate_key(settlement.provider, settlement.model),
                    aggregate_cfg,
                )
                self._refill(aggregate_bucket, aggregate_cfg)
                if debit:
                    aggregate_bucket["tpm_tokens"] -= debit
                return slot_bucket["tpm_tokens"]
        key = provider_key(settlement.provider, settlement.model)
        cfg = self._config_for(settlement.provider, settlement.model)
        bucket = self._bucket(key, cfg)
        self._refill(bucket, cfg)
        if debit:
            bucket["tpm_tokens"] -= debit
        return bucket["tpm_tokens"]

    def _config_for(self, provider: str, model: str) -> ProviderLimitConfig:
        return self._configs.get(
            (canonical_name(provider), canonical_name(model)), self._default
        )

    def _bucket(self, key: str, cfg: ProviderLimitConfig) -> dict[str, float]:
        now = self._now_ms()
        return self._buckets.setdefault(
            key,
            {"rpm_tokens": float(cfg.rpm), "tpm_tokens": float(cfg.tpm), "last_ms": now},
        )

    def _refill(self, bucket: dict[str, float], cfg: ProviderLimitConfig) -> None:
        now = self._now_ms()
        elapsed = max(0, now - int(bucket.get("last_ms", now)))
        if elapsed:
            bucket["rpm_tokens"] = min(
                float(cfg.rpm), bucket["rpm_tokens"] + cfg.rpm * elapsed / 60000.0
            )
            bucket["tpm_tokens"] = min(
                float(cfg.tpm), bucket["tpm_tokens"] + cfg.tpm * elapsed / 60000.0
            )
            bucket["last_ms"] = now

    def _retry_after_backoff_ms(self, provider: str, model: str) -> int:
        until = self._backoff_until.get(provider_backoff_key(provider, model), 0)
        return max(0, until - self._now_ms())

    @staticmethod
    def _wait_ms(available: float, needed: int, per_minute: int) -> int:
        if available >= needed:
            return 0
        missing = needed - available
        return max(1, math.ceil(missing * 60000.0 / max(1, per_minute)))

    def _decision(
        self,
        request: ProviderLimitRequest,
        allowed: bool,
        reason: ProviderLimitReason,
        retry_after_ms: int | None,
        key: str,
        bucket: dict[str, float] | None = None,
    ) -> ProviderLimitDecision:
        labels = {
            "provider": canonical_name(request.provider),
            "model": canonical_name(request.model),
            "route_type": request.route_type,
            "reason": reason,
        }
        self._metrics.inc_counter("provider_rate_limit_decisions_total", labels)
        return ProviderLimitDecision(
            allowed=allowed,
            reason=reason,
            retry_after_ms=retry_after_ms,
            provider_limit_key=key,
            reserved_tokens=request.reserved_tokens,
            remaining_rpm=None if bucket is None else bucket["rpm_tokens"],
            remaining_tpm=None if bucket is None else bucket["tpm_tokens"],
        )


class RedisProviderRateLimiter:
    """Redis Lua-backed provider limiter shared by API and worker replicas."""

    _KEY_POOL_ACQUIRE_SCRIPT = """
local aggregate_bucket_key = KEYS[1]
local aggregate_backoff_key = KEYS[2]
local cursor_key = KEYS[3]
local now = tonumber(ARGV[1])
local reserve = tonumber(ARGV[2])
local need_tpm = tonumber(ARGV[3])
local aggregate_rpm = tonumber(ARGV[4])
local aggregate_tpm = tonumber(ARGV[5])
local slot_count = tonumber(ARGV[6])

local function refill(bucket_key, rpm, tpm)
  local rpm_tokens = tonumber(redis.call('HGET', bucket_key, 'rpm_tokens') or tostring(rpm))
  local tpm_tokens = tonumber(redis.call('HGET', bucket_key, 'tpm_tokens') or tostring(tpm))
  local last_ms = tonumber(redis.call('HGET', bucket_key, 'last_refill_ms') or ARGV[1])
  local elapsed = math.max(0, now - last_ms)
  rpm_tokens = math.min(rpm, rpm_tokens + (rpm * elapsed / 60000))
  tpm_tokens = math.min(tpm, tpm_tokens + (tpm * elapsed / 60000))
  return rpm_tokens, tpm_tokens
end

local function wait_ms(rpm_tokens, tpm_tokens, rpm, tpm)
  local rpm_wait = 0
  local tpm_wait = 0
  if rpm_tokens < 1 then rpm_wait = math.ceil((1 - rpm_tokens) * 60000 / rpm) end
  if tpm_tokens < need_tpm then tpm_wait = math.ceil((need_tpm - tpm_tokens) * 60000 / tpm) end
  return math.max(rpm_wait, tpm_wait)
end

local function persist(bucket_key, rpm_tokens, tpm_tokens)
  redis.call('HSET', bucket_key, 'rpm_tokens', rpm_tokens, 'tpm_tokens', tpm_tokens, 'last_refill_ms', now, 'updated_at_ms', now)
  redis.call('PEXPIRE', bucket_key, 120000)
end

local aggregate_backoff_until = tonumber(redis.call('GET', aggregate_backoff_key) or '0')
if aggregate_backoff_until > now then
  return {0, 'BACKING_OFF', aggregate_backoff_until - now, 0, 0, ''}
end

local aggregate_rpm_tokens, aggregate_tpm_tokens = refill(aggregate_bucket_key, aggregate_rpm, aggregate_tpm)
local aggregate_wait = wait_ms(aggregate_rpm_tokens, aggregate_tpm_tokens, aggregate_rpm, aggregate_tpm)
if aggregate_wait > 0 then
  persist(aggregate_bucket_key, aggregate_rpm_tokens, aggregate_tpm_tokens)
  return {0, 'RATE_LIMITED', aggregate_wait, aggregate_rpm_tokens, aggregate_tpm_tokens, ''}
end

local cursor = tonumber(redis.call('GET', cursor_key) or '0')
if cursor < 0 then cursor = 0 end
local best_retry_after = 0
local best_reason = 'RATE_LIMITED'

for offset = 0, slot_count - 1 do
  local idx = ((cursor + offset) % slot_count) + 1
  local key_offset = 4 + ((idx - 1) * 2)
  local slot_bucket_key = KEYS[key_offset]
  local slot_backoff_key = KEYS[key_offset + 1]
  local arg_offset = 6 + ((idx - 1) * 3)
  local key_id = ARGV[arg_offset + 1]
  local slot_rpm = tonumber(ARGV[arg_offset + 2])
  local slot_tpm = tonumber(ARGV[arg_offset + 3])
  local slot_backoff_until = tonumber(redis.call('GET', slot_backoff_key) or '0')
  if slot_backoff_until > now then
    local retry_after = slot_backoff_until - now
    if best_retry_after == 0 or retry_after < best_retry_after then
      best_retry_after = retry_after
      best_reason = 'BACKING_OFF'
    end
  else
    local slot_rpm_tokens, slot_tpm_tokens = refill(slot_bucket_key, slot_rpm, slot_tpm)
    local slot_wait = wait_ms(slot_rpm_tokens, slot_tpm_tokens, slot_rpm, slot_tpm)
    if slot_wait > 0 then
      if best_retry_after == 0 or slot_wait < best_retry_after then
        best_retry_after = slot_wait
        best_reason = 'RATE_LIMITED'
      end
      persist(slot_bucket_key, slot_rpm_tokens, slot_tpm_tokens)
    else
      if reserve == 1 then
        aggregate_rpm_tokens = aggregate_rpm_tokens - 1
        aggregate_tpm_tokens = aggregate_tpm_tokens - need_tpm
        slot_rpm_tokens = slot_rpm_tokens - 1
        slot_tpm_tokens = slot_tpm_tokens - need_tpm
        redis.call('SET', cursor_key, idx % slot_count, 'PX', 120000)
      end
      persist(aggregate_bucket_key, aggregate_rpm_tokens, aggregate_tpm_tokens)
      persist(slot_bucket_key, slot_rpm_tokens, slot_tpm_tokens)
      return {1, 'ALLOWED', 0, slot_rpm_tokens, slot_tpm_tokens, key_id}
    end
  end
end

persist(aggregate_bucket_key, aggregate_rpm_tokens, aggregate_tpm_tokens)
if best_retry_after == 0 then best_retry_after = 1000 end
return {0, best_reason, best_retry_after, aggregate_rpm_tokens, aggregate_tpm_tokens, ''}
"""

    _ACQUIRE_SCRIPT = """
local bucket_key = KEYS[1]
local backoff_key = KEYS[2]
local now = tonumber(ARGV[1])
local rpm = tonumber(ARGV[2])
local tpm = tonumber(ARGV[3])
local need_tpm = tonumber(ARGV[4])
local reserve = tonumber(ARGV[5])
local backoff_until = tonumber(redis.call('GET', backoff_key) or '0')
if backoff_until > now then
  return {0, 'BACKING_OFF', backoff_until - now, 0, 0}
end
local rpm_tokens = tonumber(redis.call('HGET', bucket_key, 'rpm_tokens') or ARGV[2])
local tpm_tokens = tonumber(redis.call('HGET', bucket_key, 'tpm_tokens') or ARGV[3])
local last_ms = tonumber(redis.call('HGET', bucket_key, 'last_refill_ms') or ARGV[1])
local elapsed = math.max(0, now - last_ms)
rpm_tokens = math.min(rpm, rpm_tokens + (rpm * elapsed / 60000))
tpm_tokens = math.min(tpm, tpm_tokens + (tpm * elapsed / 60000))
local rpm_wait = 0
local tpm_wait = 0
if rpm_tokens < 1 then rpm_wait = math.ceil((1 - rpm_tokens) * 60000 / rpm) end
if tpm_tokens < need_tpm then tpm_wait = math.ceil((need_tpm - tpm_tokens) * 60000 / tpm) end
local wait = math.max(rpm_wait, tpm_wait)
if wait > 0 then
  redis.call('HSET', bucket_key, 'rpm_tokens', rpm_tokens, 'tpm_tokens', tpm_tokens, 'last_refill_ms', now, 'updated_at_ms', now)
  redis.call('PEXPIRE', bucket_key, 120000)
  return {0, 'RATE_LIMITED', wait, rpm_tokens, tpm_tokens}
end
if reserve == 1 then
  rpm_tokens = rpm_tokens - 1
  tpm_tokens = tpm_tokens - need_tpm
end
redis.call('HSET', bucket_key, 'rpm_tokens', rpm_tokens, 'tpm_tokens', tpm_tokens, 'last_refill_ms', now, 'updated_at_ms', now)
redis.call('PEXPIRE', bucket_key, 120000)
return {1, 'ALLOWED', 0, rpm_tokens, tpm_tokens}
"""

    _SETTLE_SCRIPT = """
local bucket_key = KEYS[1]
local now = tonumber(ARGV[1])
local tpm = tonumber(ARGV[2])
local debit = tonumber(ARGV[3])
local tpm_tokens = tonumber(redis.call('HGET', bucket_key, 'tpm_tokens') or ARGV[2])
local last_ms = tonumber(redis.call('HGET', bucket_key, 'last_refill_ms') or ARGV[1])
local elapsed = math.max(0, now - last_ms)
tpm_tokens = math.min(tpm, tpm_tokens + (tpm * elapsed / 60000))
tpm_tokens = tpm_tokens - debit
redis.call('HSET', bucket_key, 'tpm_tokens', tpm_tokens, 'last_refill_ms', now, 'updated_at_ms', now)
redis.call('PEXPIRE', bucket_key, 120000)
return {debit, tpm_tokens}
"""

    def __init__(
        self,
        redis_client: Any,
        settings: Settings,
        *,
        key_pool: ProviderKeyPool | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        self._metrics = metrics or Metrics()
        self._key_pool = key_pool
        self._configs = load_provider_limit_config(settings)
        self._default = ProviderLimitConfig(
            rpm=getattr(settings, "provider_default_rpm", 100000),
            tpm=getattr(settings, "provider_default_tpm", 1000000),
        )

    async def check(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        return await self._acquire(request, reserve=False)

    async def acquire(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        return await self._acquire(request, reserve=True)

    async def settle_usage(self, settlement: ProviderUsageSettlement) -> ProviderUsageDecision:
        actual = settlement.actual_total_tokens
        labels = {
            "provider": canonical_name(settlement.provider),
            "model": canonical_name(settlement.model),
            "route_type": settlement.route_type,
        }
        if actual is None:
            self._metrics.inc_counter("provider_usage_missing_total", labels)
            return ProviderUsageDecision(
                settled=False,
                usage_missing=True,
                provider_key_id=settlement.provider_key_id,
            )
        debit = max(0, actual - max(0, settlement.reserved_tokens))
        if self._matches_key_pool_names(settlement.provider, settlement.model):
            pool = self._key_pool
            slot = pool.slot_by_id(settlement.provider_key_id) if pool else None
            if slot is not None:
                slot_result = await self._redis.eval(
                    self._SETTLE_SCRIPT,
                    1,
                    provider_key_slot_key(settlement.provider, settlement.model, slot.key_id),
                    int(time.time() * 1000),
                    slot.tpm,
                    debit,
                )
                aggregate_cfg = ProviderLimitConfig(
                    rpm=int(pool.aggregate_rpm or self._default.rpm),
                    tpm=int(pool.aggregate_tpm or self._default.tpm),
                )
                await self._redis.eval(
                    self._SETTLE_SCRIPT,
                    1,
                    provider_aggregate_key(settlement.provider, settlement.model),
                    int(time.time() * 1000),
                    aggregate_cfg.tpm,
                    debit,
                )
                self._record_settlement_metrics(labels, actual, debit)
                return ProviderUsageDecision(
                    settled=True,
                    debit_tokens=int(slot_result[0]),
                    remaining_tpm=float(slot_result[1]),
                    provider_key_id=slot.key_id,
                )
        cfg = self._config_for(settlement.provider, settlement.model)
        result = await self._redis.eval(
            self._SETTLE_SCRIPT,
            1,
            provider_key(settlement.provider, settlement.model),
            int(time.time() * 1000),
            cfg.tpm,
            debit,
        )
        self._record_settlement_metrics(labels, actual, debit)
        return ProviderUsageDecision(
            settled=True,
            debit_tokens=int(result[0]),
            remaining_tpm=float(result[1]),
            provider_key_id=settlement.provider_key_id,
        )

    async def record_provider_error(
        self,
        provider: str,
        model: str,
        status_code: int,
        retry_after_ms: int | None = None,
        provider_key_id: str | None = None,
    ) -> None:
        if status_code < 429:
            return
        retry_after_ms = retry_after_ms or (30000 if status_code == 429 else 5000)
        if provider_key_id:
            backoff_key = provider_key_slot_backoff_key(provider, model, provider_key_id)
        elif self._matches_key_pool_names(provider, model):
            backoff_key = provider_aggregate_backoff_key(provider, model)
        else:
            backoff_key = provider_backoff_key(provider, model)
        await self._redis.psetex(
            backoff_key,
            retry_after_ms,
            int(time.time() * 1000) + retry_after_ms,
        )
        self._metrics.inc_counter(
            "provider_errors_total",
            {
                "provider": canonical_name(provider),
                "model": canonical_name(model),
                "status_code": str(status_code),
            },
        )

    async def _acquire(
        self, request: ProviderLimitRequest, *, reserve: bool
    ) -> ProviderLimitDecision:
        if self._matches_key_pool(request):
            return await self._acquire_key_pool(request, reserve=reserve)
        cfg = self._config_for(request.provider, request.model)
        started = time.perf_counter()
        try:
            result = await self._redis.eval(
                self._ACQUIRE_SCRIPT,
                2,
                provider_key(request.provider, request.model),
                provider_backoff_key(request.provider, request.model),
                int(time.time() * 1000),
                cfg.rpm,
                cfg.tpm,
                max(1, request.reserved_tokens),
                1 if reserve else 0,
            )
        except Exception:
            if getattr(self._settings, "provider_rate_limit_fail_open", False):
                return ProviderLimitDecision(
                    True, "ALLOWED", reserved_tokens=request.reserved_tokens
                )
            return ProviderLimitDecision(False, "UNAVAILABLE", retry_after_ms=1000)
        self._metrics.observe_histogram(
            "provider_rate_limit_lua_seconds",
            time.perf_counter() - started,
            {
                "provider": canonical_name(request.provider),
                "model": canonical_name(request.model),
                "route_type": request.route_type,
            },
        )
        allowed = bool(int(result[0]))
        reason = str(result[1])
        retry_after_ms = int(float(result[2] or 0)) or None
        self._metrics.inc_counter(
            "provider_rate_limit_decisions_total",
            {
                "provider": canonical_name(request.provider),
                "model": canonical_name(request.model),
                "route_type": request.route_type,
                "reason": reason,
            },
        )
        if allowed and reserve:
            self._metrics.inc_counter(
                "provider_rate_limit_tokens_reserved_total",
                {
                    "provider": canonical_name(request.provider),
                    "model": canonical_name(request.model),
                    "route_type": request.route_type,
                },
                max(1, request.reserved_tokens),
            )
        return ProviderLimitDecision(
            allowed=allowed,
            reason=reason,  # type: ignore[arg-type]
            retry_after_ms=retry_after_ms,
            provider_limit_key=provider_key(request.provider, request.model),
            reserved_tokens=request.reserved_tokens,
            remaining_rpm=float(result[3]),
            remaining_tpm=float(result[4]),
        )

    async def _acquire_key_pool(
        self,
        request: ProviderLimitRequest,
        *,
        reserve: bool,
    ) -> ProviderLimitDecision:
        pool = self._key_pool
        if pool is None or not pool.enabled_slots:
            return ProviderLimitDecision(
                False,
                "CONFIG_MISSING",
                provider_limit_key=provider_aggregate_key(
                    request.provider, request.model
                ),
                reserved_tokens=request.reserved_tokens,
            )
        aggregate_cfg = ProviderLimitConfig(
            rpm=int(pool.aggregate_rpm or self._default.rpm),
            tpm=int(pool.aggregate_tpm or self._default.tpm),
        )
        keys: list[str] = [
            provider_aggregate_key(request.provider, request.model),
            provider_aggregate_backoff_key(request.provider, request.model),
            provider_key_pool_cursor_key(request.provider, request.model),
        ]
        args: list[Any] = [
            int(time.time() * 1000),
            1 if reserve else 0,
            max(1, request.reserved_tokens),
            aggregate_cfg.rpm,
            aggregate_cfg.tpm,
            len(pool.enabled_slots),
        ]
        for slot in pool.enabled_slots:
            keys.extend(
                [
                    provider_key_slot_key(request.provider, request.model, slot.key_id),
                    provider_key_slot_backoff_key(
                        request.provider, request.model, slot.key_id
                    ),
                ]
            )
            args.extend([slot.key_id, slot.rpm, slot.tpm])
        started = time.perf_counter()
        try:
            result = await self._redis.eval(
                self._KEY_POOL_ACQUIRE_SCRIPT,
                len(keys),
                *keys,
                *args,
            )
        except Exception:
            if getattr(self._settings, "provider_rate_limit_fail_open", False):
                return ProviderLimitDecision(
                    True, "ALLOWED", reserved_tokens=request.reserved_tokens
                )
            return ProviderLimitDecision(False, "UNAVAILABLE", retry_after_ms=1000)
        self._metrics.observe_histogram(
            "provider_rate_limit_lua_seconds",
            time.perf_counter() - started,
            {
                "provider": canonical_name(request.provider),
                "model": canonical_name(request.model),
                "route_type": request.route_type,
            },
        )
        allowed = bool(int(result[0]))
        reason = str(result[1])
        retry_after_ms = int(float(result[2] or 0)) or None
        key_id = str(result[5] or "")
        slot = pool.slot_by_id(key_id) if key_id else None
        self._metrics.inc_counter(
            "provider_rate_limit_decisions_total",
            {
                "provider": canonical_name(request.provider),
                "model": canonical_name(request.model),
                "route_type": request.route_type,
                "reason": reason,
            },
        )
        if allowed and reserve:
            self._metrics.inc_counter(
                "provider_key_tokens_reserved_total",
                {
                    "provider": canonical_name(request.provider),
                    "model": canonical_name(request.model),
                    "route_type": request.route_type,
                },
                max(1, request.reserved_tokens),
            )
        return ProviderLimitDecision(
            allowed=allowed,
            reason=reason,  # type: ignore[arg-type]
            retry_after_ms=retry_after_ms,
            provider_limit_key=provider_key_slot_key(
                request.provider, request.model, key_id
            )
            if key_id
            else provider_aggregate_key(request.provider, request.model),
            reserved_tokens=request.reserved_tokens,
            remaining_rpm=float(result[3]),
            remaining_tpm=float(result[4]),
            provider_key_id=slot.key_id if slot else None,
            provider_key_secret=slot.secret if slot else None,
            provider_key_scope=pool.scope,
        )

    def _matches_key_pool(self, request: ProviderLimitRequest) -> bool:
        return self._matches_key_pool_names(request.provider, request.model)

    def _matches_key_pool_names(self, provider: str, model: str) -> bool:
        pool = self._key_pool
        if pool is None or not pool.enabled_slots:
            return False
        return (
            pool.provider == canonical_name(provider)
            and pool.model == canonical_name(model)
        )

    def _record_settlement_metrics(
        self,
        labels: dict[str, str],
        actual: int,
        debit: int,
    ) -> None:
        self._metrics.inc_counter(
            "provider_rate_limit_tokens_settled_total", labels, actual
        )
        if debit:
            self._metrics.inc_counter(
                "provider_rate_limit_tokens_debt_total", labels, debit
            )

    def _config_for(self, provider: str, model: str) -> ProviderLimitConfig:
        return self._configs.get(
            (canonical_name(provider), canonical_name(model)), self._default
        )


class DisabledProviderRateLimiter(InMemoryProviderRateLimiter):
    """Fail-open limiter used only when explicitly disabled for local/mock work."""

    async def check(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        return ProviderLimitDecision(True, "ALLOWED", reserved_tokens=request.reserved_tokens)

    async def acquire(self, request: ProviderLimitRequest) -> ProviderLimitDecision:
        return ProviderLimitDecision(True, "ALLOWED", reserved_tokens=request.reserved_tokens)

    async def settle_usage(self, settlement: ProviderUsageSettlement) -> ProviderUsageDecision:
        return ProviderUsageDecision(settled=True)


def build_provider_limiter(
    settings: Settings,
    *,
    redis_client: Any | None = None,
    metrics: Metrics | None = None,
    secret_provider: SecretProvider | None = None,
    key_pool: ProviderKeyPool | None = None,
) -> InMemoryProviderRateLimiter | RedisProviderRateLimiter | DisabledProviderRateLimiter:
    if not getattr(settings, "provider_rate_limit_enabled", True):
        return DisabledProviderRateLimiter(metrics=metrics)
    if key_pool is None:
        secret_provider = secret_provider or build_secret_provider(settings)
        key_pool = build_provider_key_pool(settings, secret_provider)
    if redis_client is None:
        return InMemoryProviderRateLimiter(
            default_config=ProviderLimitConfig(
                rpm=getattr(settings, "provider_default_rpm", 100000),
                tpm=getattr(settings, "provider_default_tpm", 1000000),
            ),
            configs=load_provider_limit_config(settings),
            key_pool=key_pool,
            metrics=metrics,
        )
    return RedisProviderRateLimiter(
        redis_client,
        settings,
        key_pool=key_pool,
        metrics=metrics,
    )
