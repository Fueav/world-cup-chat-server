from __future__ import annotations

from copy import deepcopy


def _wc_context(match_id: str = "75", *, unlocked: bool = False, has_all: bool = False):
    return {
        "current_match_id": match_id,
        "current_match": {
            "id": match_id,
            "fd_match_id": "fd-75",
            "description": "阿根廷 vs 法国",
            "stage": "final",
            "stage_label": "决赛",
            "home": {"name": "阿根廷", "short_name": "ARG"},
            "away": {"name": "法国", "short_name": "FRA"},
            "is_unlocked": unlocked,
        },
        "entitlements": {
            "has_all": has_all,
            "unlocked_matches": [match_id] if unlocked else [],
            "locked_matches": [] if unlocked else [match_id],
        },
    }


def _central_payload(match_id: str = "75"):
    return {
        "match_id": match_id,
        "access": {
            "viewer_scope": "internal",
            "blocks": {
                "methodology": {"allowed": True},
                "block_b_model_probability": {"unlocked": True, "mask_policy": "full"},
                "block_d_recommendation": {"unlocked": True, "mask_policy": "full"},
                "block_power_index_9d": {"unlocked": True, "mask_policy": "full"},
            },
        },
        "summary": {
            "active_message_values": {
                "home_win_probability": 62.1,
                "expected_goals_home": 1.82,
                "expected_goals_away": 1.04,
                "recommendation_summary": "阿根廷 -0.25",
            }
        },
        "probability_model": {
            "wdl_probability": {"home": 62.1, "draw": 21.3, "away": 16.6},
            "expected_goals": {"home": 1.82, "away": 1.04},
            "score_grid_summary": [
                {"score": "2-1", "probability": 12.4},
                {"score": "1-0", "probability": 10.1},
            ],
        },
        "recommendation": {
            "status": "active",
            "recommendation_label": "主胜",
            "market_side": "home_win",
            "model_probability": 62.1,
            "polymarket_implied_probability": 54.0,
            "probability_gap_pp": 8.1,
            "decimal_odds": 1.75,
            "break_even_probability": 57.1,
            "ev_estimate": 0.08,
            "expected_value": 0.08,
        },
        "strength_index": {
            "total_score_home": 77.2,
            "total_score_away": 62.5,
            "dimensions": [
                {
                    "key": "elo",
                    "label": "Elo strength",
                    "weight": 0.18,
                    "public_explanation": "长期强度与近期稳定性。",
                    "home_score": 8.1,
                    "away_score": 6.2,
                }
            ],
        },
    }


def test_current_match_unlock_uses_match_flag_as_authority():
    from app.runtime.wc2026_permissions import is_current_match_unlocked

    assert is_current_match_unlocked(_wc_context(unlocked=True)) is True
    assert is_current_match_unlocked(_wc_context(has_all=True)) is False
    assert is_current_match_unlocked(_wc_context(unlocked=False, has_all=False)) is False


def test_locked_match_masks_paid_blocks_b_d_and_9d_scores():
    from app.runtime.wc2026_permissions import mask_match_context_payload

    masked = mask_match_context_payload(
        deepcopy(_central_payload()),
        _wc_context(unlocked=False),
    )

    access = masked["access"]
    assert access["viewer_scope"] == "locked"
    assert access["blocks"]["block_b_model_probability"]["unlocked"] is False
    assert access["blocks"]["block_b_model_probability"]["mask_policy"] == "omit_values"
    assert access["blocks"]["block_d_recommendation"]["unlocked"] is False
    assert access["blocks"]["block_power_index_9d"]["unlocked"] is False

    active_values = masked["summary"]["active_message_values"]
    assert active_values["home_win_probability"] is None
    assert active_values["expected_goals_home"] is None
    assert active_values["expected_goals_away"] is None
    assert active_values["recommendation_summary"] is None

    model = masked["probability_model"]
    assert model["wdl_probability"] == {"home": None, "draw": None, "away": None}
    assert model["expected_goals"] == {"home": None, "away": None}
    assert model["score_grid_summary"] == []

    recommendation = masked["recommendation"]
    assert recommendation["status"] == "locked"
    assert recommendation["recommendation_label"] is None
    assert recommendation["market_side"] is None
    assert recommendation["model_probability"] is None
    assert recommendation["polymarket_implied_probability"] is None
    assert recommendation["probability_gap_pp"] is None
    assert recommendation["decimal_odds"] is None
    assert recommendation["break_even_probability"] is None
    assert recommendation["ev_estimate"] is None
    assert recommendation["expected_value"] is None

    strength = masked["strength_index"]
    assert strength["total_score_home"] is None
    assert strength["total_score_away"] is None
    dimension = strength["dimensions"][0]
    assert dimension["label"] == "Elo strength"
    assert dimension["weight"] == 0.18
    assert dimension["public_explanation"] == "长期强度与近期稳定性。"
    assert dimension["home_score"] is None
    assert dimension["away_score"] is None


def test_unlocked_match_preserves_paid_values_after_sanitization():
    from app.runtime.wc2026_permissions import mask_match_context_payload

    payload = _central_payload()
    sanitized = mask_match_context_payload(payload, _wc_context(unlocked=True))

    assert sanitized["access"]["viewer_scope"] == "unlocked"
    assert sanitized["probability_model"]["wdl_probability"]["home"] == 62.1
    assert sanitized["recommendation"]["model_probability"] == 62.1
    assert sanitized["strength_index"]["dimensions"][0]["home_score"] == 8.1


def test_context_instruction_names_current_match_and_blocks_cross_match_tooling():
    from app.runtime.wc2026_permissions import build_wc2026_context_instruction

    instruction = build_wc2026_context_instruction(_wc_context(unlocked=True))

    assert "阿根廷 vs 法国" in instruction
    assert "current_match_id=75" in instruction
    assert "只能" in instruction
    assert "其他比赛" in instruction
