# 2026-07-01 WC2026 Answer Quality Briefing Implementation Plan

## Plan Header

- Specification: `SPEC-WC2026-ANSWER-QUALITY-001`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- Scope summary: add a detailed-answer mode for WC2026 match analysis while preserving default concise side-panel answers and existing safety boundaries.

## Change Steps

### 1. Lock In Behavior Tests

- Files/modules:
  - `tests/test_chat_behavior_policy.py`
  - `tests/test_wc2026_agent_data.py`
- Behavior change:
  - Assert the new quality spec appears in the behavior prompt.
  - Assert detailed requests map to professional pre-match briefing instructions.
  - Assert expanded streaming style allows a materially longer answer than the default concise cap.
  - Assert current-match answer metadata exposes expanded briefing mode.
- Verification:
  - `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_wc2026_agent_data.py -q`

### 2. Add Detailed Answer Mode

- Files/modules:
  - `app/runtime/chat_behavior.py`
  - `app/runtime/orchestrator.py`
- Behavior change:
  - Detect explicit depth requests such as `详细`, `展开`, `深入`, `分析一下`, `deep dive`, or `in-depth`.
  - Inject expanded briefing format only for those requests.
  - Keep default side-panel instructions and default stream cap unchanged.
- Rollback note:
  - Revert the detail detector and expanded stream cap wiring; default concise behavior remains.

### 3. Align Agent Data Metadata

- Files/modules:
  - `app/runtime/wc2026_agent_data.py`
- Behavior change:
  - Add expanded briefing metadata under `answer_format`.
  - Do not alter unlocked central payload shape or paid-content masking rules.

## Completion Criteria

- Focused tests pass.
- Spec-contract check passes.
- Existing R1 hardening changes remain untouched except for the minimal integration points needed by this behavior update.
