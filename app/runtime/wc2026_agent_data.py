"""WC2026 centralized agent data client and current-match service."""

from __future__ import annotations

import logging
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings
from app.core.logging import get_logger, log_with_fields
from app.runtime.wc2026_permissions import (
    current_match_id,
    is_current_match_unlocked,
    mask_match_context_payload,
)

logger = get_logger(__name__)

_CENTRAL_UNAVAILABLE_MESSAGE = "WC2026_AGENT_DATA_UNAVAILABLE"


class Wc2026CentralDataClient:
    """HTTP client for the internal WC2026 agent-data API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = (api_key or "").strip()
        self._timeout_s = timeout_s

    async def fetch_match_context(
        self, match_id: str, *, locale: str = "zh-Hans"
    ) -> dict[str, Any]:
        """Fetch full current-match context from the central service."""
        if not self._base_url:
            raise RuntimeError("WC2026_AGENT_API_NOT_CONFIGURED")
        path_match_id = quote(str(match_id), safe="")
        url = self._url(f"/wc2026/agent/match-context/{path_match_id}")
        return await self._get(url, locale=locale)

    async def fetch_methodology(self, *, locale: str = "zh-Hans") -> dict[str, Any]:
        """Fetch public model methodology from the central service."""
        if not self._base_url:
            raise RuntimeError("WC2026_AGENT_API_NOT_CONFIGURED")
        return await self._get(self._url("/wc2026/agent/methodology"), locale=locale)

    def _url(self, api_path: str) -> str:
        base_path = urlsplit(self._base_url).path.rstrip("/")
        if base_path.endswith("/api/v1"):
            return f"{self._base_url}{api_path}"
        return f"{self._base_url}/api/v1{api_path}"

    async def _get(self, url: str, *, locale: str) -> dict[str, Any]:
        headers = {}
        if self._api_key:
            headers["wc-api-key"] = self._api_key
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(
                url,
                params={"locale": locale},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("WC2026_AGENT_API_INVALID_RESPONSE")
        return data


class Wc2026AgentDataService:
    """Agent-facing service that only exposes the trusted current match."""

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client

    async def get_current_match_context(
        self,
        wc2026_context: dict[str, Any] | None,
        *,
        locale: str = "zh-Hans",
    ) -> dict[str, Any]:
        """Return a masked current-match payload safe for the Agent."""
        match_id = current_match_id(wc2026_context)
        if not match_id:
            return {
                "ok": False,
                "status": "missing_context",
                "answer_format": _short_answer_contract(locale),
                "message": "WC2026_CONTEXT_REQUIRED",
            }
        if not is_current_match_unlocked(wc2026_context):
            payload = mask_match_context_payload(
                _locked_current_match_payload(match_id, wc2026_context),
                wc2026_context,
            )
            return {
                "ok": True,
                "status": "locked",
                "match_id": match_id,
                "answer_format": _short_answer_contract(locale),
                "payload": _agent_visible_locked_payload(payload),
            }
        if self._client is None:
            return {
                "ok": False,
                "status": "central_unavailable",
                "match_id": match_id,
                "answer_format": _short_answer_contract(locale),
                "message": "WC2026_AGENT_DATA_CLIENT_UNAVAILABLE",
            }
        try:
            envelope = await self._client.fetch_match_context(match_id, locale=locale)
        except Exception as exc:  # noqa: BLE001 central data must fail closed
            log_with_fields(
                logger,
                logging.WARNING,
                "WC2026 central match-context unavailable",
                match_id=match_id,
                locale=locale,
                error_type=type(exc).__name__,
            )
            return {
                "ok": False,
                "status": "central_unavailable",
                "match_id": match_id,
                "answer_format": _short_answer_contract(locale),
                "message": _CENTRAL_UNAVAILABLE_MESSAGE,
            }
        payload_or_error = _payload_from_envelope(envelope)
        if not payload_or_error["ok"]:
            return {
                "ok": False,
                "status": "central_error",
                "match_id": match_id,
                "answer_format": _short_answer_contract(locale),
                "message": payload_or_error["message"],
            }
        payload = payload_or_error["payload"]
        returned_match_id = _payload_match_id(payload)
        if returned_match_id and returned_match_id != match_id:
            return {
                "ok": False,
                "status": "match_mismatch",
                "match_id": match_id,
                "answer_format": _short_answer_contract(locale),
                "message": "central match-context returned another match_id",
            }
        return {
            "ok": True,
            "status": "ok",
            "match_id": match_id,
            "answer_format": _short_answer_contract(locale),
            "payload": mask_match_context_payload(payload, wc2026_context),
        }

    async def get_methodology(self, *, locale: str = "zh-Hans") -> dict[str, Any]:
        """Return central public WC2026 model methodology."""
        if self._client is None:
            return {
                "ok": True,
                "status": "local_fallback",
                "payload": _public_methodology_payload(locale=locale),
            }
        try:
            envelope = await self._client.fetch_methodology(locale=locale)
        except Exception as exc:  # noqa: BLE001 central data must fail closed
            log_with_fields(
                logger,
                logging.WARNING,
                "WC2026 central methodology unavailable",
                locale=locale,
                error_type=type(exc).__name__,
            )
            return {
                "ok": False,
                "status": "central_unavailable",
                "message": _CENTRAL_UNAVAILABLE_MESSAGE,
            }
        payload_or_error = _payload_from_envelope(envelope)
        if not payload_or_error["ok"]:
            return {
                "ok": False,
                "status": "central_error",
                "message": payload_or_error["message"],
            }
        return {
            "ok": True,
            "status": "ok",
            "payload": payload_or_error["payload"],
        }


def build_wc2026_agent_data_service(settings: Settings) -> Wc2026AgentDataService:
    """Build the WC2026 current-match data service from app settings."""
    if not settings.wc2026_agent_api_base_url.strip():
        return Wc2026AgentDataService(client=None)
    client = Wc2026CentralDataClient(
        base_url=settings.wc2026_agent_api_base_url,
        api_key=settings.wc2026_agent_api_key,
        timeout_s=settings.wc2026_agent_api_timeout_s,
    )
    return Wc2026AgentDataService(client=client)


def _payload_from_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    code = envelope.get("code")
    if code is not None and str(code) not in {"0", "200"}:
        return {
            "ok": False,
            "message": str(envelope.get("message") or envelope.get("msg") or code),
        }
    payload = envelope.get("data", envelope)
    if not isinstance(payload, dict):
        return {"ok": False, "message": "WC2026_AGENT_API_EMPTY_DATA"}
    return {"ok": True, "payload": payload}


def _payload_match_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("match_id") or payload.get("id")
    if value is None:
        match = payload.get("match")
        if isinstance(match, Mapping):
            value = match.get("match_id") or match.get("id")
    if value is None:
        current_match = payload.get("current_match")
        if isinstance(current_match, Mapping):
            value = current_match.get("id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _locked_current_match_payload(
    match_id: str, wc2026_context: dict[str, Any] | None
) -> dict[str, Any]:
    current_match = {}
    if isinstance(wc2026_context, dict):
        match = wc2026_context.get("current_match")
        if isinstance(match, dict):
            current_match = match
    return {
        "match_id": match_id,
        "current_match": current_match,
        "access": {"viewer_scope": "locked", "blocks": {}},
        "summary": {"active_message_values": {}},
        "probability_model": {
            "wdl_probability": {"home": None, "draw": None, "away": None},
            "expected_goals": {"home": None, "away": None},
            "score_grid_summary": [],
        },
        "recommendation": {"status": "locked"},
        "strength_index": {"dimensions": []},
    }


def _agent_visible_locked_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove internal mask implementation fields from locked Agent tool data."""
    current_match = payload.get("current_match")
    summary = payload.get("summary")
    probability_model = payload.get("probability_model")
    recommendation = payload.get("recommendation")
    strength_index = payload.get("strength_index")
    return {
        "match_id": payload.get("match_id"),
        "current_match": current_match if isinstance(current_match, Mapping) else {},
        "permission": {
            "state": "locked",
            "paid_values_visible": False,
            "message": (
                "当前权限下不可展示模型概率、预期进球、比分网格、推荐方向、EV 或实力分具体数值。"
                "可以解释公开计算口径和解锁后可见的数据类型。"
            ),
        },
        "summary": summary if isinstance(summary, Mapping) else {},
        "probability_model": (
            probability_model if isinstance(probability_model, Mapping) else {}
        ),
        "recommendation": (
            recommendation if isinstance(recommendation, Mapping) else {"status": "locked"}
        ),
        "strength_index": (
            strength_index if isinstance(strength_index, Mapping) else {}
        ),
    }


def _public_methodology_payload(*, locale: str) -> dict[str, Any]:
    """Return public WC2026 methodology constants without match-specific values."""
    return {
        "schema_version": "local-public-2026-06-30",
        "source": "local_public_fallback",
        "locale": locale,
        "scope": "public_methodology_only_no_current_match_values",
        "recommendation_triggers": {
            "probability_gap_threshold_pp": 4,
            "decimal_odds_range": [1.70, 2.40],
            "market_reference": "Polymarket implied probability and executable CLOB odds",
            "statuses": ["value_bet", "probe_bet", "no-bet"],
            "risk_boundary": "explain trigger rules only; not betting advice",
        },
        "strength_index": {
            "dimensions_count": 9,
            "score_scale": "0-100",
            "aggregation": "score nine independent dimensions and combine by weights",
            "adjustments": ["opponent Elo rating", "SOS correction"],
            "current_match_scores_available": False,
        },
        "probability_model": {
            "expected_goals": "expected_goals_lambda enters a Poisson score grid",
            "wdl_probability": "home/draw/away probabilities are aggregated from the score grid",
        },
        "coefficients": {
            "total_goals_scale_k": 0.943,
            "total_goals_scale_k_meaning": (
                "total-goals scaling/calibration coefficient fitted from historical international matches"
            ),
            "low_score_rho": -0.15,
            "low_score_rho_meaning": (
                "low-score score-correlation correction affecting 0-0, 1-0, 0-1, and 1-1"
            ),
        },
        "stage_calibration": {
            "group_stage_confidence_contraction": "group-stage probabilities use confidence contraction",
            "knockout_draw_weight_adjustment": "knockout-stage modeling adjusts draw weight for stage dynamics",
        },
    }


def _short_answer_contract(locale: str) -> dict[str, Any]:
    """Return the agent-facing default answer format for current-match tools."""
    if str(locale or "").lower().startswith("en"):
        return {
            "mode": "concise_side_panel",
            "default_max_lines": 4,
            "default_max_chars": 650,
            "expanded_mode": "professional_pre_match_briefing",
            "expanded_max_chars": 2200,
            "expanded_sections": [
                "Conclusion",
                "Probability center",
                "Price discipline",
                "Evidence",
                "Risk/cancel conditions",
                "No-bet or paper-watch status",
            ],
            "fields": ["Conclusion", "Key data", "Basis", "Status/Risk"],
            "expand_only_if_user_asks": [
                "detail",
                "expand",
                "full",
                "table",
                "all dimensions",
                "Top",
                "item-by-item",
                "comparison table",
            ],
            "forbidden_by_default": [
                "Markdown tables",
                "long headings",
                "full 9D lists",
                "Top5 score lists",
                "market-depth dumps",
                "step-by-step report prose",
            ],
        }
    return {
        "mode": "concise_side_panel",
        "default_max_lines": 4,
        "default_max_chars": 420,
        "expanded_mode": "professional_pre_match_briefing",
        "expanded_max_chars": 1800,
        "expanded_sections": [
            "结论先行",
            "概率中枢",
            "价值门槛",
            "关键证据",
            "风险与取消条件",
            "no-bet/纸面观察状态",
        ],
        "fields": ["结论", "关键数据", "依据", "状态/风险"],
        "expand_only_if_user_asks": [
            "详细",
            "展开",
            "完整",
            "全量",
            "表格",
            "全部维度",
            "Top",
            "逐项",
            "对比表",
        ],
        "forbidden_by_default": [
            "Markdown 表格",
            "长标题",
            "一/二/三式报告章节",
            "全量 9 个维度列表",
            "Top5 比分列表",
            "完整市场深度",
            "逐项流水账",
        ],
    }
