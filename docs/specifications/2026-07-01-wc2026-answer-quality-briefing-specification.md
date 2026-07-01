# 2026-07-01 WC2026 Answer Quality Briefing Specification

## Context

- Spec ID: `SPEC-WC2026-ANSWER-QUALITY-001`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- PRD/source request: improve WC2026 chat answer quality so detailed match questions feel like professional pre-match briefing rather than terse customer-support responses.
- Current behavior:
  - Default answer policy is intentionally concise: four-line side-panel, about 420 Chinese characters.
  - Streaming output guardrail clamps all answers around the default short-answer budget, so explicit "详细/展开/分析" requests can lose structure and depth.
- Non-goals:
  - Do not weaken paid-content masking, no-bet discipline, or real-money refusal boundaries.
  - Do not add new external search or betting execution behavior.
  - Do not change API response envelopes, event names, or database schema.

## Product Semantics

- Default user questions still receive concise side-panel answers.
- If the user explicitly asks for detail, expansion, in-depth analysis, or a full match view, the Agent may produce a longer professional pre-match briefing.
- Normal match-analysis answers may use a small number of World Cup themed emoji
  such as football, trophy, chart, or target markers to improve scanability.
  Emoji must be sparse: at most one per short line or briefing section, never
  stacked, and never used to replace numbers, evidence, no-bet status, or risk
  language.
- Streaming output must not expose Unicode replacement characters produced by
  provider token boundary issues; these characters should be stripped before
  frontend delivery.
- Expanded answers must remain evidence-led and must not fabricate probabilities, expected goals, CLOB prices, liquidity, recommendations, lineup news, or market data.
- Expanded answers should be organized around:
  - conclusion first
  - probability center
  - price discipline / value threshold
  - key evidence
  - risk and cancellation conditions
  - no-bet or paper-watch status

## API / Interface Contract

- No route, event, schema, or status-code changes.
- `answer_format` metadata may expose both default concise mode and expanded professional briefing mode.
- `answer_format` metadata may expose the bounded emoji style contract for the
  Agent, but this is formatting guidance only and does not change response
  envelopes.
- Streaming behavior remains bounded; expanded mode uses a larger deterministic output-style cap than concise mode.

## Architecture

- Expected files:
  - `app/runtime/chat_behavior.py`
  - `app/runtime/orchestrator.py`
  - `app/runtime/wc2026_agent_data.py`
  - focused tests under `tests/`
- Runtime flow:
  - Detect explicit detail requests from the current user message.
  - Inject the expanded answer-format instruction only for those requests.
  - Use an expanded streaming style cap for those requests; keep the concise cap for default turns.

## Harness Classification

- Expected gate: `HARNESS-FOCUSED-CHANGE`
- Focused verification:
  - `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_wc2026_agent_data.py tests/test_agent_factory.py tests/test_orchestrator.py -q`
  - `scripts/check_spec_contract.sh`

## Acceptance Criteria

- Prompt policy references `SPEC-WC2026-ANSWER-QUALITY-001`.
- Default concise answers still use the four-field side-panel contract.
- Default and expanded answer-format instructions allow sparse WC2026-themed
  emoji while keeping risk notes and disclaimers plain.
- Streaming style filtering removes Unicode replacement characters so emoji
  formatting cannot degrade into broken glyphs.
- Explicit detailed requests receive professional briefing instructions and a larger stream cap.
- Expanded answer-format metadata is visible to current-match Agent data.
- Guardrails still block direct betting decisions, guaranteed outcome claims, internal permission-field leaks, and fabricated paid values.
