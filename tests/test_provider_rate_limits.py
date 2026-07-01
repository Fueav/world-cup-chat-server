from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.runtime.provider_limits import (
    InMemoryProviderRateLimiter,
    ProviderLimitConfig,
    ProviderLimitRequest,
    ProviderRateLimitError,
    ProviderUsageSettlement,
    estimate_structured_input_tokens,
    provider_identity_from_settings,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0
        self.sleeps: list[float] = []

    def now_ms(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += int(seconds * 1000)


def _request(tokens: int = 10) -> ProviderLimitRequest:
    return ProviderLimitRequest(
        provider="openai",
        model="gpt-test",
        estimated_input_tokens=1,
        max_output_tokens=tokens - 1,
        route_type="realtime",
    )


async def test_provider_bucket_denies_over_tpm_with_retry_after():
    limiter = InMemoryProviderRateLimiter(
        default_config=ProviderLimitConfig(rpm=10, tpm=10),
        now_ms=lambda: 0,
    )

    first = await limiter.acquire(_request(tokens=10))
    second = await limiter.acquire(_request(tokens=2))

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "RATE_LIMITED"
    assert second.retry_after_ms is not None


async def test_provider_usage_settlement_debits_underestimated_output_and_creates_debt():
    limiter = InMemoryProviderRateLimiter(
        default_config=ProviderLimitConfig(rpm=100, tpm=100),
        now_ms=lambda: 0,
    )
    decision = await limiter.acquire(_request(tokens=40))

    settlement = await limiter.settle_usage(
        ProviderUsageSettlement(
            provider="openai",
            model="gpt-test",
            reserved_tokens=decision.reserved_tokens,
            actual_input_tokens=20,
            actual_output_tokens=100,
            route_type="realtime",
        )
    )
    denied = await limiter.acquire(_request(tokens=1))

    assert settlement.debit_tokens == 80
    assert settlement.remaining_tpm is not None
    assert settlement.remaining_tpm < 0
    assert denied.allowed is False


async def test_provider_usage_settlement_does_not_refund_over_reserved_tokens():
    limiter = InMemoryProviderRateLimiter(
        default_config=ProviderLimitConfig(rpm=100, tpm=100),
        now_ms=lambda: 0,
    )
    decision = await limiter.acquire(_request(tokens=80))

    settlement = await limiter.settle_usage(
        ProviderUsageSettlement(
            provider="openai",
            model="gpt-test",
            reserved_tokens=decision.reserved_tokens,
            actual_input_tokens=1,
            actual_output_tokens=1,
            route_type="realtime",
        )
    )
    denied = await limiter.acquire(_request(tokens=30))

    assert settlement.debit_tokens == 0
    assert denied.allowed is False


async def test_provider_usage_missing_keeps_reserved_tokens_settled():
    limiter = InMemoryProviderRateLimiter(
        default_config=ProviderLimitConfig(rpm=100, tpm=100),
        now_ms=lambda: 0,
    )
    decision = await limiter.acquire(_request(tokens=80))

    settlement = await limiter.settle_usage(
        ProviderUsageSettlement(
            provider="openai",
            model="gpt-test",
            reserved_tokens=decision.reserved_tokens,
            actual_input_tokens=None,
            actual_output_tokens=None,
            route_type="realtime",
        )
    )
    denied = await limiter.acquire(_request(tokens=30))

    assert settlement.settled is True
    assert settlement.usage_missing is True
    assert settlement.debit_tokens == 0
    assert denied.allowed is False


def test_structured_input_token_estimate_counts_context_not_only_message():
    message_only = estimate_structured_input_tokens("hi")
    with_context = estimate_structured_input_tokens(
        "hi",
        {"metadata": "m" * 300},
        {"wc2026_context": "c" * 300},
    )

    assert with_context > message_only + 100


async def test_realtime_accepted_gate_waits_once_when_retry_after_within_budget(monkeypatch):
    from app.bus.event_bus import InMemoryEventBus
    from app.runtime.deps import RuntimeDeps
    from app.runtime.orchestrator import AgentOrchestrator
    from tests.test_orchestrator import _FakeMessageRepo, _FakeRetriever, _FakeRunRepo, _FakeToolRouter

    clock = _Clock()

    class _Limiter:
        calls = 0

        async def acquire(self, request):
            self.calls += 1
            if self.calls == 1:
                return type(
                    "Decision",
                    (),
                    {
                        "allowed": False,
                        "reason": "RATE_LIMITED",
                        "retry_after_ms": 25,
                        "reserved_tokens": request.reserved_tokens,
                    },
                )()
            return type(
                "Decision",
                (),
                {
                    "allowed": True,
                    "reason": "ALLOWED",
                    "retry_after_ms": None,
                    "reserved_tokens": request.reserved_tokens,
                },
            )()

        async def settle_usage(self, settlement):
            return None

    monkeypatch.setattr("app.runtime.orchestrator.asyncio.sleep", clock.sleep)
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        openai_api_key="sk-test",
        provider_realtime_gate_wait_budget_ms=50,
    )
    deps = RuntimeDeps(
        retriever=_FakeRetriever([]),
        tool_router=_FakeToolRouter(),
        event_bus=InMemoryEventBus(),
        message_repo=_FakeMessageRepo(),
        run_repo=_FakeRunRepo(),
        settings=settings,
        provider_limiter=_Limiter(),
    )
    orchestrator = AgentOrchestrator(deps)

    await orchestrator._acquire_provider_quota("run-1", "hello", "realtime")

    assert clock.sleeps == [0.025]
    assert deps.provider_limiter.calls == 2


async def test_realtime_accepted_gate_fails_when_retry_after_exceeds_budget():
    from app.bus.event_bus import InMemoryEventBus
    from app.runtime.deps import RuntimeDeps
    from app.runtime.orchestrator import AgentOrchestrator
    from tests.test_orchestrator import _FakeMessageRepo, _FakeRetriever, _FakeRunRepo, _FakeToolRouter

    class _Limiter:
        async def acquire(self, request):
            return type(
                "Decision",
                (),
                {
                    "allowed": False,
                    "reason": "RATE_LIMITED",
                    "retry_after_ms": 5000,
                    "reserved_tokens": request.reserved_tokens,
                },
            )()

    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        openai_api_key="sk-test",
        provider_realtime_gate_wait_budget_ms=10,
    )
    deps = RuntimeDeps(
        retriever=_FakeRetriever([]),
        tool_router=_FakeToolRouter(),
        event_bus=InMemoryEventBus(),
        message_repo=_FakeMessageRepo(),
        run_repo=_FakeRunRepo(),
        settings=settings,
        provider_limiter=_Limiter(),
    )
    orchestrator = AgentOrchestrator(deps)

    with pytest.raises(ProviderRateLimitError) as exc:
        await orchestrator._acquire_provider_quota("run-1", "hello", "realtime")

    assert exc.value.retry_after_ms == 5000


async def test_provider_usage_settlement_failure_fails_closed():
    from app.bus.event_bus import InMemoryEventBus
    from app.runtime.deps import RuntimeDeps
    from app.runtime.orchestrator import AgentOrchestrator
    from tests.test_orchestrator import _FakeMessageRepo, _FakeRetriever, _FakeRunRepo, _FakeToolRouter

    class _Limiter:
        async def settle_usage(self, settlement):
            raise RuntimeError("redis down")

    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        openai_api_key="sk-test",
    )
    deps = RuntimeDeps(
        retriever=_FakeRetriever([]),
        tool_router=_FakeToolRouter(),
        event_bus=InMemoryEventBus(),
        message_repo=_FakeMessageRepo(),
        run_repo=_FakeRunRepo(),
        settings=settings,
        provider_limiter=_Limiter(),
    )
    orchestrator = AgentOrchestrator(deps)
    decision = type("Decision", (), {"reserved_tokens": 10})()

    with pytest.raises(ProviderRateLimitError) as exc:
        await orchestrator._settle_provider_usage(decision, object(), "realtime")

    assert exc.value.reason == "UNAVAILABLE"


async def test_provider_error_mapper_extracts_retry_after_and_sets_backoff():
    from app.llm.providers import map_provider_error

    response = httpx.Response(429, headers={"retry-after": "2"})
    request = httpx.Request("POST", "https://provider.test")
    exc = httpx.HTTPStatusError("too many", request=request, response=response)
    limiter = InMemoryProviderRateLimiter(now_ms=lambda: 0)

    info = map_provider_error(exc)
    assert info is not None
    await limiter.record_provider_error("openai", "gpt-test", info.status_code, info.retry_after_ms)
    denied = await limiter.acquire(_request(tokens=1))

    assert info.retry_after_ms == 2000
    assert denied.reason == "BACKING_OFF"


def test_mock_provider_bypass_identity_is_centralized():
    assert provider_identity_from_settings(Settings(_env_file=None, llm_provider="mock")).mock
    assert provider_identity_from_settings(Settings(_env_file=None, llm_provider="")).mock


def test_zai_provider_identity_uses_glm52_model():
    identity = provider_identity_from_settings(
        Settings(_env_file=None, llm_provider="zai")
    )

    assert identity.provider == "zai"
    assert identity.model == "glm-5.2"
    assert identity.key == "ratelimit:provider:zai:glm-5.2"
    assert identity.mock is False
