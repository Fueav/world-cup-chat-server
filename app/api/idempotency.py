"""Idempotency helpers for chat acceptance."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def chat_request_hash(
    *,
    message: str,
    conversation_id: str | None,
    metadata: dict[str, Any] | None,
    wc2026_context: Any | None = None,
) -> str:
    """Return a stable hash for idempotency replay comparison."""
    payload = {
        "conversation_id": conversation_id,
        "message": message,
        "metadata": metadata or {},
        "wc2026_context": _jsonable(wc2026_context),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", exclude_none=True)
    return value
