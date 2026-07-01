from __future__ import annotations

import json

from app.core.config import Settings
from app.core.secrets import build_secret_provider
from app.runtime.provider_keys import (
    ProviderKeyPool,
    ProviderKeySlot,
    build_provider_key_pool,
)
from app.runtime.provider_limits import (
    build_provider_limiter,
    InMemoryProviderRateLimiter,
    ProviderLimitDecision,
    ProviderLimitRequest,
    ProviderUsageSettlement,
    RedisProviderRateLimiter,
    provider_aggregate_key,
    provider_key_slot_backoff_key,
    provider_key_slot_key,
)


def _settings(**overrides):
    values = {
        "llm_provider": "zai",
        "provider_default_rpm": 60,
        "provider_default_tpm": 60000,
        "provider_default_max_output_tokens": 8192,
    }
    values.update(overrides)
    return Settings(
        _env_file=None,
        **values,
    )


def _request(tokens: int = 10) -> ProviderLimitRequest:
    return ProviderLimitRequest(
        provider="zai",
        model="glm-5.2",
        estimated_input_tokens=1,
        max_output_tokens=tokens - 1,
        route_type="realtime",
    )


def test_single_zai_key_becomes_one_implicit_pool_slot():
    settings = _settings(zai_api_key="zai-single-secret")

    pool = build_provider_key_pool(settings, build_secret_provider(settings))

    assert pool.status == "single"
    assert pool.scope == "account"
    assert pool.provider == "zai"
    assert pool.model == "glm-5.2"
    assert [slot.key_id for slot in pool.enabled_slots] == ["zai-k001"]
    assert pool.enabled_slots[0].secret.reveal() == "zai-single-secret"
    assert "zai-single-secret" not in repr(pool)


def test_zai_api_keys_file_parses_newline_slots_without_secret_labels(tmp_path):
    path = tmp_path / "zai_keys"
    path.write_text(
        "\n# staging keys\nzai-first-secret\n\nzai-second-secret\n",
        encoding="utf-8",
    )
    settings = _settings(zai_api_keys_file=str(path), provider_key_pool_scope="key")

    pool = build_provider_key_pool(settings, build_secret_provider(settings))

    assert pool.status == "configured"
    assert pool.scope == "key"
    assert [slot.key_id for slot in pool.enabled_slots] == ["zai-k001", "zai-k002"]
    assert [slot.secret.reveal() for slot in pool.enabled_slots] == [
        "zai-first-secret",
        "zai-second-secret",
    ]
    assert "zai-first-secret" not in repr(pool)
    assert "zai-second-secret" not in repr(pool)


def test_provider_key_pool_file_parses_provider_scoped_json(tmp_path):
    path = tmp_path / "provider-key-pool.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "zai:glm-5.2": {
                        "scope": "key",
                        "aggregate_rpm": 120,
                        "aggregate_tpm": 120000,
                        "keys": [
                            {
                                "id": "zai-slot-01",
                                "api_key": "zai-json-one",
                                "rpm": 60,
                                "tpm": 60000,
                            },
                            {
                                "id": "zai-slot-02",
                                "api_key": "zai-json-two",
                                "rpm": 60,
                                "tpm": 60000,
                                "enabled": False,
                            },
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    settings = _settings(provider_key_pool_file=str(path))

    pool = build_provider_key_pool(settings, build_secret_provider(settings))

    assert pool.status == "configured"
    assert pool.scope == "key"
    assert pool.aggregate_rpm == 120
    assert pool.aggregate_tpm == 120000
    assert [slot.key_id for slot in pool.enabled_slots] == ["zai-slot-01"]
    assert [slot.key_id for slot in pool.slots] == ["zai-slot-01", "zai-slot-02"]


async def test_key_pool_limiter_uses_independent_key_buckets_for_capacity():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=1, tpm=10),
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k002", "secret-2", rpm=1, tpm=10),
        ],
        aggregate_rpm=2,
        aggregate_tpm=20,
    )
    limiter = InMemoryProviderRateLimiter(key_pool=pool, now_ms=lambda: 0)

    first = await limiter.acquire(_request(tokens=10))
    second = await limiter.acquire(_request(tokens=10))
    third = await limiter.acquire(_request(tokens=1))

    assert first.allowed is True
    assert second.allowed is True
    assert {first.provider_key_id, second.provider_key_id} == {"zai-k001", "zai-k002"}
    assert first.provider_key_secret is not None
    assert first.provider_key_secret.reveal() in {"secret-1", "secret-2"}
    assert third.allowed is False
    assert third.reason == "RATE_LIMITED"


async def test_key_pool_limiter_honors_aggregate_cap_even_when_slots_have_capacity():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=1, tpm=10),
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k002", "secret-2", rpm=1, tpm=10),
        ],
        aggregate_rpm=1,
        aggregate_tpm=10,
    )
    limiter = InMemoryProviderRateLimiter(key_pool=pool, now_ms=lambda: 0)

    first = await limiter.acquire(_request(tokens=10))
    second = await limiter.acquire(_request(tokens=1))

    assert first.allowed is True
    assert second.allowed is False
    assert second.provider_key_id is None
    assert second.retry_after_ms is not None


async def test_key_level_backoff_does_not_block_other_key_slots():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=10, tpm=100),
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k002", "secret-2", rpm=10, tpm=100),
        ],
    )
    limiter = InMemoryProviderRateLimiter(key_pool=pool, now_ms=lambda: 0)

    await limiter.record_provider_error(
        "zai",
        "glm-5.2",
        429,
        retry_after_ms=30000,
        provider_key_id="zai-k001",
    )
    decision = await limiter.acquire(_request(tokens=10))

    assert decision.allowed is True
    assert decision.provider_key_id == "zai-k002"


async def test_key_usage_settlement_debits_same_selected_key():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=10, tpm=20),
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k002", "secret-2", rpm=10, tpm=20),
        ],
        aggregate_rpm=20,
        aggregate_tpm=40,
    )
    limiter = InMemoryProviderRateLimiter(key_pool=pool, now_ms=lambda: 0)
    decision = await limiter.acquire(_request(tokens=10))

    settlement = await limiter.settle_usage(
        ProviderUsageSettlement(
            provider="zai",
            model="glm-5.2",
            provider_key_id=decision.provider_key_id,
            reserved_tokens=decision.reserved_tokens,
            actual_input_tokens=1,
            actual_output_tokens=30,
            route_type="realtime",
        )
    )

    assert settlement.settled is True
    assert settlement.debit_tokens == 21
    assert settlement.provider_key_id == decision.provider_key_id


async def test_build_provider_limiter_loads_zai_key_pool_file(tmp_path):
    path = tmp_path / "zai_keys"
    path.write_text("zai-first-secret\nzai-second-secret\n", encoding="utf-8")
    settings = _settings(
        zai_api_keys_file=str(path),
        provider_key_pool_scope="key",
        provider_default_rpm=1,
        provider_default_tpm=10,
    )
    limiter = build_provider_limiter(settings)

    first = await limiter.acquire(_request(tokens=10))
    second = await limiter.acquire(_request(tokens=10))

    assert first.allowed is True
    assert second.allowed is True
    assert {first.provider_key_id, second.provider_key_id} == {"zai-k001", "zai-k002"}


def test_orchestrator_construction_does_not_require_single_key_when_pool_is_configured(tmp_path):
    from app.bus.event_bus import InMemoryEventBus
    from app.runtime.deps import RuntimeDeps
    from app.runtime.orchestrator import AgentOrchestrator
    from tests.test_orchestrator import _FakeMessageRepo, _FakeRetriever, _FakeRunRepo, _FakeToolRouter

    path = tmp_path / "zai_keys"
    path.write_text("zai-selected-secret\n", encoding="utf-8")
    settings = _settings(zai_api_key="", zai_api_keys_file=str(path))
    deps = RuntimeDeps(
        retriever=_FakeRetriever([]),
        tool_router=_FakeToolRouter(),
        event_bus=InMemoryEventBus(),
        message_repo=_FakeMessageRepo(),
        run_repo=_FakeRunRepo(),
        settings=settings,
        provider_limiter=InMemoryProviderRateLimiter(),
        secret_provider=build_secret_provider(settings),
    )

    orchestrator = AgentOrchestrator(deps)

    assert orchestrator is not None


def test_provider_limit_decision_can_carry_selected_key_secret():
    decision = ProviderLimitDecision(
        allowed=True,
        reason="ALLOWED",
        provider_key_id="zai-k001",
        provider_key_secret=ProviderKeySlot.for_test(
            "zai", "glm-5.2", "zai-k001", "selected-secret", rpm=1, tpm=10
        ).secret,
        provider_key_scope="key",
    )

    assert decision.provider_key_secret is not None
    assert decision.provider_key_secret.reveal() == "selected-secret"
    assert "selected-secret" not in repr(decision.provider_key_secret)


class _ScriptedRedis:
    def __init__(self, results):
        self.results = list(results)
        self.eval_calls = []
        self.psetex_calls = []

    async def eval(self, script, numkeys, *args):
        self.eval_calls.append((script, numkeys, args))
        return self.results.pop(0)

    async def psetex(self, key, ttl_ms, value):
        self.psetex_calls.append((key, ttl_ms, value))


async def test_redis_key_pool_acquire_maps_selected_key_secret():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=1, tpm=10),
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k002", "secret-2", rpm=1, tpm=10),
        ],
        aggregate_rpm=2,
        aggregate_tpm=20,
    )
    redis = _ScriptedRedis([[1, "ALLOWED", 0, 0, 0, "zai-k002"]])
    limiter = RedisProviderRateLimiter(redis, _settings(), key_pool=pool)

    decision = await limiter.acquire(_request(tokens=10))

    assert decision.allowed is True
    assert decision.provider_key_id == "zai-k002"
    assert decision.provider_key_secret is not None
    assert decision.provider_key_secret.reveal() == "secret-2"
    assert redis.eval_calls[0][1] == 7


async def test_redis_key_level_backoff_targets_only_selected_slot():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=1, tpm=10),
        ],
    )
    redis = _ScriptedRedis([])
    limiter = RedisProviderRateLimiter(redis, _settings(), key_pool=pool)

    await limiter.record_provider_error(
        "zai",
        "glm-5.2",
        429,
        retry_after_ms=30000,
        provider_key_id="zai-k001",
    )

    assert redis.psetex_calls
    assert redis.psetex_calls[0][0] == provider_key_slot_backoff_key(
        "zai", "glm-5.2", "zai-k001"
    )


async def test_redis_key_usage_settlement_debits_slot_and_aggregate():
    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test("zai", "glm-5.2", "zai-k001", "secret-1", rpm=1, tpm=10),
        ],
        aggregate_rpm=1,
        aggregate_tpm=10,
    )
    redis = _ScriptedRedis([[5, 5], [5, 5]])
    limiter = RedisProviderRateLimiter(redis, _settings(), key_pool=pool)

    settlement = await limiter.settle_usage(
        ProviderUsageSettlement(
            provider="zai",
            model="glm-5.2",
            provider_key_id="zai-k001",
            reserved_tokens=10,
            actual_input_tokens=1,
            actual_output_tokens=14,
            route_type="realtime",
        )
    )

    touched_keys = [call[2][0] for call in redis.eval_calls]
    assert settlement.provider_key_id == "zai-k001"
    assert provider_key_slot_key("zai", "glm-5.2", "zai-k001") in touched_keys
    assert provider_aggregate_key("zai", "glm-5.2") in touched_keys
