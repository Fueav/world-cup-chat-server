from __future__ import annotations

import inspect

import pytest


def _wc_context(match_id: str = "75", *, unlocked: bool = False):
    return {
        "current_match_id": match_id,
        "current_match": {
            "id": match_id,
            "description": "阿根廷 vs 法国",
            "home": {"name": "阿根廷"},
            "away": {"name": "法国"},
            "is_unlocked": unlocked,
        },
        "entitlements": {"has_all": False},
    }


def _central_payload(match_id: str = "75"):
    return {
        "match_id": match_id,
        "access": {"viewer_scope": "internal", "blocks": {}},
        "probability_model": {
            "wdl_probability": {"home": 62.1, "draw": 21.3, "away": 16.6}
        },
        "recommendation": {"status": "active", "model_probability": 62.1},
        "strength_index": {
            "total_score_home": 77.2,
            "total_score_away": 62.5,
            "dimensions": [],
        },
    }


class _FakeCentralClient:
    def __init__(self, envelope=None, *, error: Exception | None = None) -> None:
        self.envelope = envelope if envelope is not None else {
            "code": 0,
            "data": _central_payload(),
        }
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def fetch_match_context(self, match_id: str, *, locale: str):
        self.calls.append((match_id, locale))
        if self.error is not None:
            raise self.error
        return self.envelope

    async def fetch_methodology(self, *, locale: str):
        self.calls.append(("methodology", locale))
        if self.error is not None:
            raise self.error
        return self.envelope


async def test_locked_context_does_not_call_paid_match_context():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient()
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context(unlocked=False))

    assert client.calls == []
    assert result["ok"] is True
    assert result["status"] == "locked"
    assert result["match_id"] == "75"
    assert result["answer_format"]["mode"] == "concise_side_panel"
    assert result["answer_format"]["fields"] == ["结论", "关键数据", "依据", "状态/风险"]
    assert result["payload"]["access"]["viewer_scope"] == "locked"


async def test_unlocked_context_calls_current_match_only_and_masks_payload():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient()
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context(unlocked=True))

    assert client.calls == [("75", "zh-Hans")]
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["match_id"] == "75"
    assert result["answer_format"]["mode"] == "concise_side_panel"
    assert result["answer_format"]["default_max_chars"] == 420
    assert "Markdown 表格" in result["answer_format"]["forbidden_by_default"]
    assert result["payload"]["probability_model"]["wdl_probability"]["home"] == 62.1


def test_current_match_tool_interface_does_not_accept_arbitrary_match_id():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    params = inspect.signature(
        Wc2026AgentDataService.get_current_match_context
    ).parameters

    assert "match_id" not in params
    assert "requested_match_id" not in params


async def test_build_service_without_base_url_returns_client_unavailable():
    from app.core.config import Settings
    from app.runtime.wc2026_agent_data import build_wc2026_agent_data_service

    service = build_wc2026_agent_data_service(
        Settings(_env_file=None, wc2026_agent_api_base_url="")
    )

    result = await service.get_current_match_context(_wc_context(unlocked=True))

    assert result["ok"] is False
    assert result["status"] == "central_unavailable"
    assert result["answer_format"]["mode"] == "concise_side_panel"
    assert result["message"] == "WC2026_AGENT_DATA_CLIENT_UNAVAILABLE"


async def test_methodology_without_base_url_returns_public_fallback():
    from app.core.config import Settings
    from app.runtime.wc2026_agent_data import build_wc2026_agent_data_service

    service = build_wc2026_agent_data_service(
        Settings(_env_file=None, wc2026_agent_api_base_url="")
    )

    result = await service.get_methodology()

    assert result["ok"] is True
    assert result["status"] == "local_fallback"
    payload = result["payload"]
    assert payload["recommendation_triggers"]["probability_gap_threshold_pp"] == 4
    assert payload["recommendation_triggers"]["decimal_odds_range"] == [1.70, 2.40]
    assert payload["strength_index"]["dimensions_count"] == 9
    assert payload["strength_index"]["score_scale"] == "0-100"
    assert payload["coefficients"]["total_goals_scale_k"] == 0.943
    assert payload["coefficients"]["low_score_rho"] == -0.15
    assert "group_stage_confidence_contraction" in payload["stage_calibration"]
    assert "knockout_draw_weight_adjustment" in payload["stage_calibration"]


async def test_central_match_context_error_returns_structured_failure():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient({"code": 5001, "message": "snapshot missing"})
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context(unlocked=True))

    assert result["ok"] is False
    assert result["status"] == "central_error"
    assert result["match_id"] == "75"
    assert "snapshot missing" in result["message"]
    assert "payload" not in result


async def test_central_match_id_mismatch_is_rejected():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient({"code": 0, "data": _central_payload("76")})
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context("75", unlocked=True))

    assert result["ok"] is False
    assert result["status"] == "match_mismatch"
    assert result["match_id"] == "75"


async def test_nested_central_match_id_mismatch_is_rejected():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    payload = _central_payload("75")
    payload.pop("match_id")
    payload["match"] = {"match_id": "76"}
    client = _FakeCentralClient({"code": 200, "data": payload})
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context("75", unlocked=True))

    assert result["ok"] is False
    assert result["status"] == "match_mismatch"
    assert result["match_id"] == "75"


async def test_transport_exception_returns_structured_failure():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient(
        error=TimeoutError(
            "GET http://viki-api:8080/api/v1/wc2026/agent/match-context/75 timed out"
        )
    )
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(_wc_context(unlocked=True))

    assert result["ok"] is False
    assert result["status"] == "central_unavailable"
    assert result["match_id"] == "75"
    assert result["message"] == "WC2026_AGENT_DATA_UNAVAILABLE"
    assert "viki-api" not in result["message"]
    assert "http://" not in result["message"]


async def test_missing_current_match_context_fails_closed():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    service = Wc2026AgentDataService(client=_FakeCentralClient())

    result = await service.get_current_match_context({})

    assert result["ok"] is False
    assert result["status"] == "missing_context"


@pytest.mark.anyio
async def test_has_all_does_not_override_locked_current_match():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    context = _wc_context("75", unlocked=False)
    context["entitlements"] = {
        "has_all": True,
        "unlocked_matches": ["76"],
        "locked_matches": ["75"],
    }
    client = _FakeCentralClient()
    service = Wc2026AgentDataService(client=client)

    result = await service.get_current_match_context(context)

    assert client.calls == []
    assert result["ok"] is True
    assert result["status"] == "locked"


async def test_methodology_calls_central_public_endpoint():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient({"code": 200, "data": {"schema_version": "2026-06-29"}})
    service = Wc2026AgentDataService(client=client)

    result = await service.get_methodology(locale="en")

    assert client.calls == [("methodology", "en")]
    assert result == {
        "ok": True,
        "status": "ok",
        "payload": {"schema_version": "2026-06-29"},
    }


async def test_methodology_transport_exception_is_sanitized():
    from app.runtime.wc2026_agent_data import Wc2026AgentDataService

    client = _FakeCentralClient(
        error=ConnectionError(
            "GET http://viki-api:8080/api/v1/wc2026/agent/methodology failed"
        )
    )
    service = Wc2026AgentDataService(client=client)

    result = await service.get_methodology()

    assert result["ok"] is False
    assert result["status"] == "central_unavailable"
    assert result["message"] == "WC2026_AGENT_DATA_UNAVAILABLE"
    assert "viki-api" not in result["message"]
    assert "http://" not in result["message"]


async def test_central_client_accepts_origin_or_api_v1_base_url(monkeypatch):
    from app.runtime import wc2026_agent_data
    from app.runtime.wc2026_agent_data import Wc2026CentralDataClient

    captured_urls: list[str] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "data": {"match_id": "75"}}

    class _Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, params, headers):
            captured_urls.append(url)
            assert params == {"locale": "zh-Hans"}
            assert headers == {"wc-api-key": "secret"}
            return _Response()

    monkeypatch.setattr(wc2026_agent_data.httpx, "AsyncClient", _Client)

    await Wc2026CentralDataClient(
        base_url="http://viki-api:8080", api_key="secret"
    ).fetch_match_context("75")
    await Wc2026CentralDataClient(
        base_url="http://viki-api:8080/api/v1", api_key="secret"
    ).fetch_match_context("75")

    assert captured_urls == [
        "http://viki-api:8080/api/v1/wc2026/agent/match-context/75",
        "http://viki-api:8080/api/v1/wc2026/agent/match-context/75",
    ]


async def test_central_client_omits_api_key_header_when_unset(monkeypatch):
    from app.runtime import wc2026_agent_data
    from app.runtime.wc2026_agent_data import Wc2026CentralDataClient

    captured_headers: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "data": {"schema_version": "2026-06-29"}}

    class _Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, params, headers):
            captured_headers.append(dict(headers))
            assert url == "http://viki-api:8080/api/v1/wc2026/agent/methodology"
            assert params == {"locale": "zh-Hans"}
            return _Response()

    monkeypatch.setattr(wc2026_agent_data.httpx, "AsyncClient", _Client)

    await Wc2026CentralDataClient(
        base_url="http://viki-api:8080",
        api_key="",
    ).fetch_methodology()

    assert captured_headers == [{}]
