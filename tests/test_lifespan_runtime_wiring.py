from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI


async def test_lifespan_wires_shared_redis_runtime_resources(monkeypatch):
    from app.api import lifespan as lifespan_module

    class _FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    redis = _FakeRedis()
    monkeypatch.setattr(
        lifespan_module.Redis,
        "from_url",
        lambda *args, **kwargs: redis,
    )
    monkeypatch.setattr(
        lifespan_module,
        "get_settings",
        lambda: SimpleNamespace(
            redis_url="redis://test/0",
            rate_limit_per_min=1000,
            log_level="WARNING",
            realtime_runner_max_concurrency=123,
            run_max_runtime_s=456,
        ),
    )
    monkeypatch.setattr(lifespan_module, "configure_logging", lambda *args, **kwargs: None)

    app = FastAPI()

    async with lifespan_module.lifespan(app):
        assert app.state.redis is redis
        assert app.state.metrics is not None
        assert app.state.event_bus._client is redis
        assert app.state.conversation_lock._client is redis
        assert app.state.realtime_runner._run_lease._client is redis
        assert app.state.realtime_runner._event_bus is app.state.event_bus
        assert app.state.realtime_runner._max_concurrency == 123
        assert app.state.realtime_runner._max_runtime_s == 456
        orchestrator = app.state.realtime_runner._orchestrator_factory()
        assert orchestrator._deps.event_bus is app.state.event_bus
        assert orchestrator._deps.metrics is app.state.metrics

    assert redis.closed is True


async def test_lifespan_wires_provider_key_pool_without_single_secret(
    monkeypatch, tmp_path
):
    from app.api import lifespan as lifespan_module
    from app.core.config import Settings

    class _FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    key_file = tmp_path / "zai_keys"
    key_file.write_text("zai-first-secret\nzai-second-secret\n", encoding="utf-8")
    redis = _FakeRedis()
    settings = Settings(
        _env_file=None,
        llm_provider="zai",
        zai_api_key="",
        zai_api_keys_file=str(key_file),
        provider_key_pool_scope="key",
        redis_url="redis://test/0",
        rate_limit_per_min=1000,
        log_level="WARNING",
        realtime_runner_max_concurrency=123,
        run_max_runtime_s=456,
    )
    monkeypatch.setattr(
        lifespan_module.Redis,
        "from_url",
        lambda *args, **kwargs: redis,
    )
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: settings)
    monkeypatch.setattr(lifespan_module, "configure_logging", lambda *args, **kwargs: None)

    app = FastAPI()

    async with lifespan_module.lifespan(app):
        assert app.state.provider_key_pool.status == "configured"
        assert len(app.state.provider_key_pool.enabled_slots) == 2
        assert app.state.provider_limiter._key_pool is app.state.provider_key_pool

    assert redis.closed is True
