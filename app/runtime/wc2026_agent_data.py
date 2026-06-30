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
        api_key: str,
        timeout_s: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def fetch_match_context(
        self, match_id: str, *, locale: str = "zh-Hans"
    ) -> dict[str, Any]:
        """Fetch full current-match context from the central service."""
        if not self._base_url or not self._api_key:
            raise RuntimeError("WC2026_AGENT_API_NOT_CONFIGURED")
        path_match_id = quote(str(match_id), safe="")
        url = self._url(f"/wc2026/agent/match-context/{path_match_id}")
        return await self._get(url, locale=locale)

    async def fetch_methodology(self, *, locale: str = "zh-Hans") -> dict[str, Any]:
        """Fetch public model methodology from the central service."""
        if not self._base_url or not self._api_key:
            raise RuntimeError("WC2026_AGENT_API_NOT_CONFIGURED")
        return await self._get(self._url("/wc2026/agent/methodology"), locale=locale)

    def _url(self, api_path: str) -> str:
        base_path = urlsplit(self._base_url).path.rstrip("/")
        if base_path.endswith("/api/v1"):
            return f"{self._base_url}{api_path}"
        return f"{self._base_url}/api/v1{api_path}"

    async def _get(self, url: str, *, locale: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(
                url,
                params={"locale": locale},
                headers={"wc-api-key": self._api_key},
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
                "payload": payload,
            }
        if self._client is None:
            return {
                "ok": False,
                "status": "central_unavailable",
                "match_id": match_id,
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
                "message": _CENTRAL_UNAVAILABLE_MESSAGE,
            }
        payload_or_error = _payload_from_envelope(envelope)
        if not payload_or_error["ok"]:
            return {
                "ok": False,
                "status": "central_error",
                "match_id": match_id,
                "message": payload_or_error["message"],
            }
        payload = payload_or_error["payload"]
        returned_match_id = _payload_match_id(payload)
        if returned_match_id and returned_match_id != match_id:
            return {
                "ok": False,
                "status": "match_mismatch",
                "match_id": match_id,
                "message": "central match-context returned another match_id",
            }
        return {
            "ok": True,
            "status": "ok",
            "match_id": match_id,
            "payload": mask_match_context_payload(payload, wc2026_context),
        }

    async def get_methodology(self, *, locale: str = "zh-Hans") -> dict[str, Any]:
        """Return central public WC2026 model methodology."""
        if self._client is None:
            return {
                "ok": False,
                "status": "central_unavailable",
                "message": "WC2026_AGENT_DATA_CLIENT_UNAVAILABLE",
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
