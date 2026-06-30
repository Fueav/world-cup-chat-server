"""Deterministic chat behavior eval fixture loader and validator."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.chat_behavior import GuardrailAction, GuardrailCategory

CASE_FILE = Path(__file__).with_name("golden_cases.jsonl")

_REQUIRED_FIELDS = {
    "id",
    "locale",
    "area",
    "user_message",
    "expected_input_action",
    "expected_input_category",
    "requires_rag",
    "requires_tool",
    "tags",
}
_ALLOWED_ACTIONS = {action.value for action in GuardrailAction}
_ALLOWED_CATEGORIES = {category.value for category in GuardrailCategory}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class ChatBehaviorCase:
    """Validated chat behavior golden case."""

    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def user_message(self) -> str:
        return str(self.raw["user_message"])

    @property
    def expected_input_action(self) -> str:
        return str(self.raw["expected_input_action"])

    @property
    def expected_input_category(self) -> str:
        return str(self.raw["expected_input_category"])

    @property
    def sample_assistant_answer(self) -> str | None:
        value = self.raw.get("sample_assistant_answer")
        return None if value is None else str(value)

    @property
    def target_language(self) -> str:
        return str(self.raw.get("target_language") or "unknown")


def load_cases(path: Path = CASE_FILE) -> list[ChatBehaviorCase]:
    """Load and validate chat behavior golden cases."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise AssertionError(f"{path}:{line_no}: case must be an object")
            rows.append(row)
    errors = validate_cases(rows)
    if errors:
        joined = "\n".join(errors)
        raise AssertionError(f"invalid chat behavior golden cases:\n{joined}")
    return [ChatBehaviorCase(row) for row in rows]


def validate_cases(rows: list[dict[str, Any]]) -> list[str]:
    """Return validation errors for raw case dictionaries."""
    errors: list[str] = []
    ids: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        case_id = str(row.get("id") or f"line_{idx}")
        missing = sorted(_REQUIRED_FIELDS - set(row))
        if missing:
            errors.append(f"{case_id}: missing fields {missing}")
        if case_id in ids:
            errors.append(f"{case_id}: duplicate id")
        ids.add(case_id)
        _validate_scalar(row, "id", errors, case_id)
        _validate_scalar(row, "locale", errors, case_id)
        _validate_scalar(row, "area", errors, case_id)
        _validate_scalar(row, "user_message", errors, case_id)
        _validate_enum(
            row, "expected_input_action", _ALLOWED_ACTIONS, errors, case_id
        )
        _validate_enum(
            row, "expected_input_category", _ALLOWED_CATEGORIES, errors, case_id
        )
        if row.get("expected_input_action") == "refuse":
            _validate_string_list(row, "safe_response_contains", errors, case_id)
        if row.get("expected_input_action") == "allow":
            _validate_string_list(row, "answer_traits", errors, case_id)
            _validate_string_list(row, "forbidden_claims", errors, case_id)
        if "expected_output_action" in row:
            _validate_enum(
                row, "expected_output_action", _ALLOWED_ACTIONS, errors, case_id
            )
            _validate_enum(
                row,
                "expected_output_category",
                _ALLOWED_CATEGORIES,
                errors,
                case_id,
            )
            if not row.get("sample_assistant_answer"):
                errors.append(f"{case_id}: output case needs sample_assistant_answer")
        _validate_string_list(row, "tags", errors, case_id)
        _validate_secret_hygiene(row, errors, case_id)
    if len(rows) < 10:
        errors.append("fixture must contain at least 10 cases")
    return errors


def coverage_summary(cases: list[ChatBehaviorCase]) -> dict[str, int]:
    """Return simple coverage counters used by pytest gates."""
    counts: dict[str, int] = {
        "allow": 0,
        "refuse": 0,
        "rag_required": 0,
        "hidden_instruction": 0,
        "secret_request": 0,
        "real_money_operation": 0,
        "personal_wallet_data": 0,
        "direct_betting_decision": 0,
        "guaranteed_outcome": 0,
        "locked_paid_content": 0,
        "model_scope_out_of_bounds": 0,
        "platform_account_support": 0,
        "output_policy_leak": 0,
        "language_mismatch": 0,
        "false_positive_guard": 0,
        "product_doc_section_2": 0,
        "central_data_pending": 0,
    }
    for case in cases:
        action = case.expected_input_action
        if action in counts:
            counts[action] += 1
        category = case.expected_input_category
        if category in counts:
            counts[category] += 1
        if case.raw.get("requires_rag") is True:
            counts["rag_required"] += 1
        if case.raw.get("expected_output_category") == "output_policy_leak":
            counts["output_policy_leak"] += 1
        if case.raw.get("expected_output_category") == "language_mismatch":
            counts["language_mismatch"] += 1
        if "false_positive_guard" in case.raw.get("tags", []):
            counts["false_positive_guard"] += 1
        if "product_doc_section_2" in case.raw.get("tags", []):
            counts["product_doc_section_2"] += 1
        if "central_data_pending" in case.raw.get("tags", []):
            counts["central_data_pending"] += 1
    return counts


def _validate_scalar(
    row: dict[str, Any], field: str, errors: list[str], case_id: str
) -> None:
    if field in row and not str(row[field]).strip():
        errors.append(f"{case_id}: {field} must be non-empty")


def _validate_enum(
    row: dict[str, Any],
    field: str,
    allowed: set[str],
    errors: list[str],
    case_id: str,
) -> None:
    if field in row and row[field] not in allowed:
        errors.append(f"{case_id}: {field}={row[field]!r} not in {sorted(allowed)}")


def _validate_string_list(
    row: dict[str, Any], field: str, errors: list[str], case_id: str
) -> None:
    value = row.get(field)
    if not isinstance(value, list) or not value:
        errors.append(f"{case_id}: {field} must be a non-empty list")
        return
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{case_id}: {field} items must be non-empty strings")


def _validate_secret_hygiene(
    row: dict[str, Any], errors: list[str], case_id: str
) -> None:
    serialized = json.dumps(row, ensure_ascii=False)
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(serialized):
            errors.append(f"{case_id}: fixture appears to contain a real secret")
