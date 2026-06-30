"""WC2026 current-match permission and masking helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

_PAID_BLOCKS = (
    "block_b_model_probability",
    "block_d_recommendation",
    "block_power_index_9d",
)
_LEGACY_PAID_BLOCKS = ("model_probability", "recommendation", "power_index_9d")
_ACTIVE_VALUE_KEYS = (
    "home_win_probability",
    "draw_probability",
    "away_win_probability",
    "expected_goals_home",
    "expected_goals_away",
    "recommendation_summary",
)
_RECOMMENDATION_PAID_KEYS = (
    "recommendation_label",
    "market_side",
    "model_probability",
    "market_probability",
    "polymarket_implied_probability",
    "probability_gap",
    "probability_gap_pp",
    "break_even",
    "break_even_probability",
    "ev",
    "ev_estimate",
    "expected_value",
    "decimal_odds",
)


def is_current_match_unlocked(wc2026_context: Any) -> bool:
    """Return whether all current-match paid blocks are unlocked."""
    context = _as_mapping(wc2026_context)
    current_match = _as_mapping(context.get("current_match"))
    entitlements = _as_mapping(context.get("entitlements"))
    return bool(current_match.get("is_unlocked")) or bool(
        entitlements.get("has_all")
    )


def mask_match_context_payload(payload: Mapping[str, Any], wc2026_context: Any) -> dict:
    """Return a copy of a central match payload safe for Agent consumption."""
    output = deepcopy(dict(payload))
    unlocked = is_current_match_unlocked(wc2026_context)
    _apply_access_mask(output, unlocked=unlocked)
    if unlocked:
        return output
    _mask_summary(output)
    _mask_probability_model(output)
    _mask_recommendation(output)
    _mask_strength_index(output)
    return output


def build_wc2026_context_instruction(wc2026_context: Any) -> str:
    """Build per-run instructions that bind the Agent to the current match."""
    context = _as_mapping(wc2026_context)
    match_id = _clean_text(context.get("current_match_id")) or "unknown"
    current_match = _as_mapping(context.get("current_match"))
    description = _clean_text(current_match.get("description"))
    if not description:
        home = _team_name(current_match.get("home")) or "主队"
        away = _team_name(current_match.get("away")) or "客队"
        description = f"{home} vs {away}"
    lock_state = "已解锁" if is_current_match_unlocked(context) else "未解锁"
    return (
        "WC2026 当前比赛边界: "
        f"current_match_id={match_id}, 当前比赛={description}, 权限={lock_state}。"
        "只能使用当前比赛上下文和当前比赛工具回答。"
        "如果用户询问其他比赛、要求切换比赛或暗示另一个 match id, "
        "必须说明当前对话无法访问其他比赛信息, 不得自行查询或编造。"
    )


def current_match_id(wc2026_context: Any) -> str | None:
    """Extract the trusted current match id."""
    text = _clean_text(_as_mapping(wc2026_context).get("current_match_id"))
    return text or None


def _apply_access_mask(payload: dict[str, Any], *, unlocked: bool) -> None:
    access = _ensure_dict(payload, "access")
    access["viewer_scope"] = "unlocked" if unlocked else "locked"
    blocks = _ensure_dict(access, "blocks")
    for key in (*_PAID_BLOCKS, *_LEGACY_PAID_BLOCKS):
        block = blocks.get(key)
        if not isinstance(block, dict):
            block = {}
            blocks[key] = block
        block["unlocked"] = unlocked
        block["mask_policy"] = "full" if unlocked else "omit_values"
        block["allowed"] = unlocked
        block["masked"] = not unlocked


def _mask_summary(payload: dict[str, Any]) -> None:
    summary = _ensure_dict(payload, "summary")
    values = _ensure_dict(summary, "active_message_values")
    for key in _ACTIVE_VALUE_KEYS:
        if key in values:
            values[key] = None


def _mask_probability_model(payload: dict[str, Any]) -> None:
    model = _ensure_dict(payload, "probability_model")
    wdl = model.get("wdl_probability")
    if isinstance(wdl, dict):
        for key in list(wdl.keys()):
            wdl[key] = None
    else:
        model["wdl_probability"] = {"home": None, "draw": None, "away": None}
    expected_goals = model.get("expected_goals")
    if isinstance(expected_goals, dict):
        for key in list(expected_goals.keys()):
            expected_goals[key] = None
    else:
        model["expected_goals"] = {"home": None, "away": None}
    model["score_grid_summary"] = []


def _mask_recommendation(payload: dict[str, Any]) -> None:
    recommendation = _ensure_dict(payload, "recommendation")
    recommendation["status"] = "locked"
    for key in _RECOMMENDATION_PAID_KEYS:
        if key in recommendation:
            recommendation[key] = None


def _mask_strength_index(payload: dict[str, Any]) -> None:
    strength = _ensure_dict(payload, "strength_index")
    for key in ("total_score_home", "total_score_away"):
        if key in strength:
            strength[key] = None
    dimensions = strength.get("dimensions")
    if not isinstance(dimensions, list):
        return
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            continue
        for key in ("home_score", "away_score", "home_value", "away_value"):
            if key in dimension:
                dimension[key] = None


def _ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if isinstance(value, dict):
        return value
    value = {}
    parent[key] = value
    return value


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _team_name(value: Any) -> str | None:
    team = _as_mapping(value)
    return _clean_text(team.get("name")) or _clean_text(team.get("short_name"))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
