from __future__ import annotations

from types import SimpleNamespace


def test_metrics_registry_renders_prometheus_text():
    from app.core.metrics import Metrics, reset_default_metrics_registry

    reset_default_metrics_registry()
    metrics = Metrics()
    metrics.inc_counter("requests_total", {"route": "/chat"}, 2)
    metrics.set_gauge("runner_active_runs", 3, {"runner_id": "runner-1"})
    metrics.observe_histogram("chat_ttft_seconds", 0.25, {"route": "/chat"})

    text = metrics.render_prometheus()

    assert 'requests_total{route="/chat"} 2' in text
    assert 'runner_active_runs{runner_id="runner-1"} 3' in text
    assert 'chat_ttft_seconds_count{route="/chat"} 1' in text
    assert 'chat_ttft_seconds_sum{route="/chat"} 0.25' in text


async def test_metrics_endpoint_returns_prometheus_text(monkeypatch):
    from app.api.routers import health
    from app.core.config import Settings
    from app.core.metrics import Metrics, reset_default_metrics_registry

    reset_default_metrics_registry()
    metrics = Metrics()
    metrics.inc_counter("chat_requests_total", {"route": "/chat"})
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: Settings(_env_file=None, metrics_enabled=True),
    )

    response = await health.metrics(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(metrics=metrics)))
    )

    assert response.status_code == 200
    assert b'chat_requests_total{route="/chat"} 1' in response.body
    assert response.media_type.startswith("text/plain")


async def test_readyz_reports_mock_provider_and_reaper(monkeypatch):
    from app.api.routers import health
    from app.core.config import Settings

    class _Redis:
        async def ping(self):
            return True

    async def _ok_db(checks):
        checks["db"] = "ok"
        return True

    monkeypatch.setattr(health, "_check_db", _ok_db)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="mock", reaper_enabled=True),
    )

    response = await health.readyz(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    redis=_Redis(),
                    event_bus=object(),
                    provider_limiter=object(),
                )
            )
        )
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert '"provider_secret":"mock"' in body
    assert '"provider_limiter":"ok"' in body
    assert '"reaper":"configured"' in body


async def test_readyz_fails_for_missing_real_provider_secret(monkeypatch):
    from app.api.routers import health
    from app.core.config import Settings

    class _Redis:
        async def ping(self):
            return True

    async def _ok_db(checks):
        checks["db"] = "ok"
        return True

    monkeypatch.setattr(health, "_check_db", _ok_db)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="openai", openai_api_key=""),
    )

    response = await health.readyz(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    redis=_Redis(),
                    event_bus=object(),
                    provider_limiter=object(),
                )
            )
        )
    )

    assert response.status_code == 503
    assert '"provider_secret":"missing"' in response.body.decode()


async def test_readyz_accepts_provider_key_pool_without_single_secret(monkeypatch):
    from app.api.routers import health
    from app.core.config import Settings
    from app.runtime.provider_keys import ProviderKeyPool, ProviderKeySlot

    class _Redis:
        async def ping(self):
            return True

    async def _ok_db(checks):
        checks["db"] = "ok"
        return True

    pool = ProviderKeyPool(
        provider="zai",
        model="glm-5.2",
        scope="key",
        slots=[
            ProviderKeySlot.for_test(
                "zai", "glm-5.2", "zai-k001", "secret-1", rpm=60, tpm=60000
            )
        ],
    )
    monkeypatch.setattr(health, "_check_db", _ok_db)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="zai", zai_api_key=""),
    )

    response = await health.readyz(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    redis=_Redis(),
                    event_bus=object(),
                    provider_limiter=object(),
                    provider_key_pool=pool,
                )
            )
        )
    )

    body = response.body.decode()
    assert response.status_code == 200
    assert '"provider_secret":"key_pool"' in body
    assert '"provider_key_pool":"configured:1"' in body


async def test_readyz_fails_when_redis_ping_fails(monkeypatch):
    from app.api.routers import health
    from app.core.config import Settings

    class _Redis:
        async def ping(self):
            raise RuntimeError("redis down")

    async def _ok_db(checks):
        checks["db"] = "ok"
        return True

    monkeypatch.setattr(health, "_check_db", _ok_db)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="mock"),
    )

    response = await health.readyz(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    redis=_Redis(),
                    event_bus=object(),
                    provider_limiter=object(),
                )
            )
        )
    )

    assert response.status_code == 503
    assert '"redis":"error"' in response.body.decode()


def test_dockerhost_production_config_check_passes():
    from scripts.check_dockerhost_production_config import main

    assert main() == 0
