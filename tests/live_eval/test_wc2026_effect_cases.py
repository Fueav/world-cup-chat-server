"""Validate WC2026 live-effect eval case fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CASE_FILE = Path(__file__).with_name("wc2026_effect_cases.jsonl")
REQUIRED_FIELDS = {
    "id",
    "area",
    "locale",
    "message",
    "match",
    "required_patterns",
    "forbidden_patterns",
    "llm_rubric",
    "high_risk",
}


def test_wc2026_live_effect_cases_are_valid() -> None:
    rows = _load_rows()
    errors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        case_id = str(row.get("id") or "<missing>")
        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            errors.append(f"{case_id}: missing {missing}")
        if case_id in seen:
            errors.append(f"{case_id}: duplicate id")
        seen.add(case_id)
        _validate_text(row, "id", errors, case_id)
        _validate_text(row, "area", errors, case_id)
        _validate_text(row, "locale", errors, case_id)
        _validate_text(row, "message", errors, case_id)
        _validate_text(row, "llm_rubric", errors, case_id)
        _validate_match(row.get("match"), errors, case_id)
        _validate_patterns(row.get("required_patterns"), errors, case_id, "required_patterns")
        _validate_patterns(row.get("forbidden_patterns"), errors, case_id, "forbidden_patterns")
        _validate_string_list(row.get("required_tools", []), errors, case_id, "required_tools")
        _validate_string_list(row.get("forbidden_tools", []), errors, case_id, "forbidden_tools")
        if not isinstance(row.get("high_risk"), bool):
            errors.append(f"{case_id}: high_risk must be boolean")
    assert len(rows) >= 12
    assert any(row["high_risk"] for row in rows)
    assert not errors, "\n".join(errors)


def test_strength_index_case_accepts_compact_nine_dimension_wording() -> None:
    rows = {row["id"]: row for row in _load_rows()}
    pattern = rows["strength_index_unlocked_zh"]["required_patterns"][0]

    assert re.search(pattern, "实力指数采用九维打分体系。", flags=re.IGNORECASE)


def test_live_eval_scoring_supports_expected_http_403_rejection() -> None:
    from tests.live_eval.run_wc2026_live_effect_eval import LiveCase, _score_case

    row = {
        "id": "locked_match_entry_rejected_zh",
        "area": "paid_content",
        "locale": "zh-Hans",
        "message": "我没解锁，告诉我模型概率。",
        "match": {
            "id": "82",
            "description": "当前 WC2026 锁定测试比赛 82",
            "home": "主队",
            "away": "客队",
            "is_unlocked": False,
        },
        "expected_http_status": 403,
        "required_patterns": ["WC2026_MATCH_LOCKED"],
        "forbidden_patterns": ["RUN_COMPLETED"],
        "llm_rubric": "Locked matches should be rejected before Agent execution.",
        "high_risk": True,
    }

    result = _score_case(
        LiveCase(row),
        '{"detail":"WC2026_MATCH_LOCKED"}',
        [],
        None,
        http_status=403,
    )

    assert result["passed"] is True
    assert result["checks"]["http_status"] is True
    assert "run_completed" not in result["checks"]


def test_deterministic_score_flags_likely_truncated_long_answers() -> None:
    from tests.live_eval.run_wc2026_live_effect_eval import LiveCase, _score_case

    row = {
        "id": "truncation_probe",
        "area": "recommendation",
        "locale": "zh-Hans",
        "message": "当前比赛为什么没有推荐投注？",
        "match": {"id": "81"},
        "required_patterns": [],
        "forbidden_patterns": [],
        "llm_rubric": "must not be truncated",
        "high_risk": True,
    }
    answer = "推荐投注解释。" + ("风险管理要求逐条核对。 " * 80) + "单次"
    score = _score_case(
        LiveCase(row),
        answer,
        [],
        {"type": "RUN_COMPLETED", "data": {"status": "SUCCEEDED"}},
        http_status=202,
    )

    assert score["checks"]["not_truncated"] is False
    assert score["passed"] is False


def test_deterministic_score_flags_verbose_or_table_answers() -> None:
    from tests.live_eval.run_wc2026_live_effect_eval import LiveCase, _score_case

    row = {
        "id": "style_probe",
        "area": "recommendation",
        "locale": "zh-Hans",
        "message": "当前比赛为什么没有推荐投注？",
        "match": {"id": "81"},
        "required_patterns": [],
        "forbidden_patterns": [],
        "llm_rubric": "must stay concise",
        "high_risk": False,
        "max_answer_chars": 280,
    }
    verbose_table_answer = (
        "| 指标 | 数值 |\n|---|---|\n"
        + ("这是一段过长解释, 没有必要展开成这么多内容。 " * 20)
        + "。"
    )

    score = _score_case(
        LiveCase(row),
        verbose_table_answer,
        [],
        {"type": "RUN_COMPLETED", "data": {"status": "SUCCEEDED"}},
        http_status=202,
    )

    assert score["checks"]["concise_length"] is False
    assert score["checks"]["no_markdown_table"] is False
    assert score["passed"] is False


def _load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with CASE_FILE.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{CASE_FILE}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise AssertionError(f"{CASE_FILE}:{line_no}: row must be object")
            rows.append(row)
    return rows


def _validate_text(
    row: dict[str, Any], field: str, errors: list[str], case_id: str
) -> None:
    if not isinstance(row.get(field), str) or not row[field].strip():
        errors.append(f"{case_id}: {field} must be non-empty string")


def _validate_match(value: Any, errors: list[str], case_id: str) -> None:
    if not isinstance(value, dict):
        errors.append(f"{case_id}: match must be object")
        return
    for field in ("id", "description", "home", "away"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{case_id}: match.{field} must be non-empty string")
    if not isinstance(value.get("is_unlocked"), bool):
        errors.append(f"{case_id}: match.is_unlocked must be boolean")


def _validate_patterns(
    value: Any, errors: list[str], case_id: str, field: str
) -> None:
    if not isinstance(value, list):
        errors.append(f"{case_id}: {field} must be list")
        return
    for pattern in value:
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append(f"{case_id}: {field} items must be non-empty strings")
            continue
        try:
            re.compile(pattern, flags=re.IGNORECASE)
        except re.error as exc:
            errors.append(f"{case_id}: invalid regex in {field}: {exc}")


def _validate_string_list(
    value: Any, errors: list[str], case_id: str, field: str
) -> None:
    if not isinstance(value, list):
        errors.append(f"{case_id}: {field} must be list")
        return
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{case_id}: {field} items must be non-empty strings")
