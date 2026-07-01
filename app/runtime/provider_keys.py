"""Provider API key pool parsing and in-memory secret slots."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.secrets import (
    SecretProvider,
    SecretValue,
    is_mock_provider,
    required_secret_name,
)

_SAFE_ID_RE = re.compile(r"[^a-z0-9_.:-]+")
_VALID_SCOPES = {"account", "key"}


class ProviderKeyPoolConfigError(RuntimeError):
    """Raised when a provider key pool file is malformed."""


def _clean_id(value: Any) -> str:
    text = _SAFE_ID_RE.sub("-", str(value or "").strip().lower()).strip("-")
    if not text:
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID key_id")
    return text


def _canonical(value: Any) -> str:
    text = _SAFE_ID_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return text or "unknown"


def _provider_model(settings: Settings) -> tuple[str, str, bool]:
    provider = (getattr(settings, "llm_provider", "mock") or "mock").strip().lower()
    if is_mock_provider(provider):
        return "mock", "mock", True
    model = {
        "openai": getattr(settings, "openai_model", "openai"),
        "qwen": getattr(settings, "qwen_model", "qwen"),
        "zai": getattr(settings, "zai_model", "glm-5.2"),
        "anthropic": getattr(settings, "anthropic_model", "anthropic"),
        "gemini": getattr(settings, "gemini_model", "gemini"),
    }.get(provider, provider)
    return _canonical(provider), _canonical(model), False


@dataclass(frozen=True)
class ProviderKeySlot:
    provider: str
    model: str
    key_id: str
    secret: SecretValue
    rpm: int
    tpm: int
    max_output_tokens: int
    enabled: bool = True
    weight: int = 1

    @classmethod
    def for_test(
        cls,
        provider: str,
        model: str,
        key_id: str,
        secret: str,
        *,
        rpm: int,
        tpm: int,
        max_output_tokens: int = 8192,
        enabled: bool = True,
    ) -> "ProviderKeySlot":
        return cls(
            provider=_canonical(provider),
            model=_canonical(model),
            key_id=_clean_id(key_id),
            secret=SecretValue(secret),
            rpm=rpm,
            tpm=tpm,
            max_output_tokens=max_output_tokens,
            enabled=enabled,
        )

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return (
            "ProviderKeySlot("
            f"provider={self.provider!r}, model={self.model!r}, "
            f"key_id={self.key_id!r}, rpm={self.rpm}, tpm={self.tpm}, "
            f"state={state})"
        )


@dataclass(frozen=True)
class ProviderKeyPool:
    provider: str
    model: str
    scope: str = "account"
    slots: list[ProviderKeySlot] = field(default_factory=list)
    aggregate_rpm: int | None = None
    aggregate_tpm: int | None = None
    strategy: str = "least_wait_round_robin"
    status: str = "configured"

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _canonical(self.provider))
        object.__setattr__(self, "model", _canonical(self.model))
        scope = (self.scope or "account").strip().lower()
        if scope not in _VALID_SCOPES:
            raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID scope")
        object.__setattr__(self, "scope", scope)

    @property
    def enabled_slots(self) -> list[ProviderKeySlot]:
        return [slot for slot in self.slots if slot.enabled]

    def slot_by_id(self, key_id: str | None) -> ProviderKeySlot | None:
        if not key_id:
            return None
        clean = _clean_id(key_id)
        for slot in self.slots:
            if slot.key_id == clean:
                return slot
        return None

    def __repr__(self) -> str:
        return (
            "ProviderKeyPool("
            f"provider={self.provider!r}, model={self.model!r}, "
            f"scope={self.scope!r}, status={self.status!r}, "
            f"slots={[slot.key_id for slot in self.slots]!r})"
        )


def build_provider_key_pool(
    settings: Settings,
    secret_provider: SecretProvider,
) -> ProviderKeyPool:
    """Build the configured key pool, preserving single-key compatibility."""
    provider, model, mock = _provider_model(settings)
    if mock:
        return ProviderKeyPool(provider="mock", model="mock", status="mock", slots=[])

    pool_file = (getattr(settings, "provider_key_pool_file", "") or "").strip()
    if pool_file:
        return _from_provider_pool_file(settings, provider, model, Path(pool_file))

    provider_file = _provider_specific_key_file(settings, provider)
    if provider_file:
        return _from_provider_specific_file(settings, provider, model, Path(provider_file))

    secret_name = required_secret_name(provider)
    secret = secret_provider.get_secret(secret_name) if secret_name else None
    if not secret:
        return ProviderKeyPool(
            provider=provider,
            model=model,
            status="missing",
            scope=_scope(getattr(settings, "provider_key_pool_scope", "account")),
            slots=[],
        )
    slot = ProviderKeySlot(
        provider=provider,
        model=model,
        key_id=f"{provider}-k001",
        secret=secret,
        rpm=getattr(settings, "provider_default_rpm", 100000),
        tpm=getattr(settings, "provider_default_tpm", 1000000),
        max_output_tokens=getattr(settings, "provider_default_max_output_tokens", 8192),
    )
    return _pool(
        settings,
        provider,
        model,
        slots=[slot],
        scope="account",
        status="single",
    )


def _provider_specific_key_file(settings: Settings, provider: str) -> str:
    if provider == "zai":
        return getattr(settings, "zai_api_keys_file", "")
    return ""


def _from_provider_specific_file(
    settings: Settings,
    provider: str,
    model: str,
    path: Path,
) -> ProviderKeyPool:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_MISSING keys")
    parsed = _parse_raw(raw)
    scope = _scope(getattr(settings, "provider_key_pool_scope", "account"))
    entries: list[Any]
    aggregate_rpm = None
    aggregate_tpm = None
    if isinstance(parsed, dict):
        entries = _expect_list(parsed.get("keys"), "keys")
        scope = _scope(parsed.get("scope", scope))
        aggregate_rpm = _optional_int(parsed.get("aggregate_rpm"))
        aggregate_tpm = _optional_int(parsed.get("aggregate_tpm"))
    else:
        entries = _expect_list(parsed, "keys")
    slots = _slots_from_entries(settings, provider, model, entries)
    return _pool(
        settings,
        provider,
        model,
        slots=slots,
        scope=scope,
        aggregate_rpm=aggregate_rpm,
        aggregate_tpm=aggregate_tpm,
        status="configured",
    )


def _from_provider_pool_file(
    settings: Settings,
    provider: str,
    model: str,
    path: Path,
) -> ProviderKeyPool:
    raw = path.read_text(encoding="utf-8").strip()
    parsed = _parse_raw(raw)
    if not isinstance(parsed, dict):
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID root")
    providers = parsed.get("providers")
    if not isinstance(providers, dict):
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID providers")
    entry = _provider_entry(providers, provider, model)
    if not isinstance(entry, dict):
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_MISSING provider")
    scope = _scope(
        entry.get("scope", getattr(settings, "provider_key_pool_scope", "account"))
    )
    aggregate_rpm = _optional_int(entry.get("aggregate_rpm"))
    aggregate_tpm = _optional_int(entry.get("aggregate_tpm"))
    slots = _slots_from_entries(
        settings,
        provider,
        model,
        _expect_list(entry.get("keys"), "keys"),
    )
    return _pool(
        settings,
        provider,
        model,
        slots=slots,
        scope=scope,
        aggregate_rpm=aggregate_rpm,
        aggregate_tpm=aggregate_tpm,
        status="configured",
    )


def _provider_entry(providers: dict[Any, Any], provider: str, model: str) -> Any:
    wanted = f"{provider}:{model}"
    for key, value in providers.items():
        parts = str(key).split(":", 1)
        if (
            len(parts) == 2
            and f"{_canonical(parts[0])}:{_canonical(parts[1])}" == wanted
        ):
            return value
    return None


def _parse_raw(raw: str) -> Any:
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID json") from exc
    return [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _slots_from_entries(
    settings: Settings,
    provider: str,
    model: str,
    entries: list[Any],
) -> list[ProviderKeySlot]:
    slots: list[ProviderKeySlot] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        slot = _slot_from_entry(settings, provider, model, entry, index)
        if slot.key_id in seen:
            raise ProviderKeyPoolConfigError(
                "PROVIDER_KEY_POOL_INVALID duplicate_key_id"
            )
        seen.add(slot.key_id)
        slots.append(slot)
    return slots


def _slot_from_entry(
    settings: Settings,
    provider: str,
    model: str,
    entry: Any,
    index: int,
) -> ProviderKeySlot:
    if isinstance(entry, str):
        key_id = f"{provider}-k{index:03d}"
        api_key = entry.strip()
        enabled = True
        rpm = getattr(settings, "provider_default_rpm", 100000)
        tpm = getattr(settings, "provider_default_tpm", 1000000)
        weight = 1
    elif isinstance(entry, dict):
        enabled = bool(entry.get("enabled", True))
        key_id = _clean_id(entry.get("id") or f"{provider}-k{index:03d}")
        api_key = str(entry.get("api_key") or "").strip()
        rpm = int(
            entry.get("rpm") or getattr(settings, "provider_default_rpm", 100000)
        )
        tpm = int(
            entry.get("tpm") or getattr(settings, "provider_default_tpm", 1000000)
        )
        weight = int(entry.get("weight") or 1)
    else:
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID key_entry")
    if enabled and not api_key:
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID missing_api_key")
    return ProviderKeySlot(
        provider=provider,
        model=model,
        key_id=key_id,
        secret=SecretValue(api_key),
        rpm=rpm,
        tpm=tpm,
        max_output_tokens=getattr(settings, "provider_default_max_output_tokens", 8192),
        enabled=enabled,
        weight=weight,
    )


def _pool(
    settings: Settings,
    provider: str,
    model: str,
    *,
    slots: list[ProviderKeySlot],
    scope: str,
    status: str,
    aggregate_rpm: int | None = None,
    aggregate_tpm: int | None = None,
) -> ProviderKeyPool:
    enabled = [slot for slot in slots if slot.enabled]
    if scope == "key":
        aggregate_rpm = (
            aggregate_rpm
            if aggregate_rpm is not None
            else sum(slot.rpm for slot in enabled)
        )
        aggregate_tpm = (
            aggregate_tpm
            if aggregate_tpm is not None
            else sum(slot.tpm for slot in enabled)
        )
    else:
        aggregate_rpm = (
            aggregate_rpm
            if aggregate_rpm is not None
            else getattr(settings, "provider_default_rpm", 100000)
        )
        aggregate_tpm = (
            aggregate_tpm
            if aggregate_tpm is not None
            else getattr(settings, "provider_default_tpm", 1000000)
        )
    return ProviderKeyPool(
        provider=provider,
        model=model,
        scope=scope,
        slots=slots,
        aggregate_rpm=aggregate_rpm,
        aggregate_tpm=aggregate_tpm,
        strategy=getattr(
            settings, "provider_key_pool_strategy", "least_wait_round_robin"
        ),
        status=status,
    )


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderKeyPoolConfigError(f"PROVIDER_KEY_POOL_INVALID {label}")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _scope(value: Any) -> str:
    scope = str(value or "account").strip().lower()
    if scope not in _VALID_SCOPES:
        raise ProviderKeyPoolConfigError("PROVIDER_KEY_POOL_INVALID scope")
    return scope
