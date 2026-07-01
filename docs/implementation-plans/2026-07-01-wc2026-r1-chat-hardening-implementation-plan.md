# 2026-07-01 WC2026 R1 Chat Hardening Implementation Plan

## Plan Header

- Specification: `SPEC-WC2026-R1-CHAT-HARDENING-001` in `docs/specifications/2026-07-01-wc2026-r1-chat-hardening-specification.md`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- Target branch/baseline: current `world-cup-chat-server` checkout on 2026-07-01.
- Scope summary: Focused R1 QA hardening for WC2026 Agent behavior, realtime run convergence, request/provider admission limits, guardrail depth, safe-response visibility, owner checks, stream connection limiting, and locked-answer information hygiene.
- Out of scope:
  - `SEAM-1` inbound service-to-service `X-API-Key` enforcement.
  - Proving `<=1.5s` live TTFT.
  - Database schema changes, unlock-policy changes, or real-money execution workflow changes.

## Change Steps

### 1. Lock In R1 Behavior Regressions

- Files/modules:
  - `tests/test_agent_factory.py`
  - `tests/test_chat_behavior_policy.py`
  - `tests/test_orchestrator.py`
  - `tests/test_realtime_runner.py`
  - `tests/test_chat_routing.py`
  - `tests/test_provider_rate_limits.py`
  - `tests/test_wc2026_agent_data.py`
  - `tests/test_conversations_match_index.py`
  - `tests/test_stream_replay.py`
  - `tests/test_runs_routing.py`
- Behavior change:
  - Add assertions for no `web_search`, visible output safe-response tokens, terminal runner failure events, request-size rejection, structured token estimates, null-owner denial, stream connection limit release, and locked payload information hygiene.
- Data contract impact:
  - None.
- Tests to add/update:
  - Focused pytest listed above.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_chat_behavior_policy.py tests/test_orchestrator.py tests/test_realtime_runner.py tests/test_chat_routing.py tests/test_provider_rate_limits.py tests/test_wc2026_agent_data.py tests/test_conversations_match_index.py tests/test_stream_replay.py tests/test_runs_routing.py -q`
- Rollback or compatibility note:
  - Tests can be reverted with the corresponding runtime changes.

### 2. Remove Custom Web Search From WC2026 Agent

- Files/modules:
  - `app/runtime/agent_factory.py`
  - `app/runtime/orchestrator.py`
  - `tests/live_eval/run_wc2026_live_effect_eval.py`
- Behavior change:
  - Do not register custom `web_search`; remove it from run-plan tool names and live-eval tool summaries.
- Data contract impact:
  - Agent still has `search_knowledge`, calculator, clock, and WC2026 current-match/methodology tools.
- Tests to add/update:
  - `test_wc2026_agent_does_not_register_web_search_tool`.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_orchestrator.py -q`
- Rollback or compatibility note:
  - Re-adding `web_search` requires a product/security decision because the R1 report confirmed answer pollution.

### 3. Harden Request Admission And Provider Reservation

- Files/modules:
  - `app/core/config.py`
  - `app/api/routers/chat.py`
  - `app/runtime/provider_limits.py`
  - `app/runtime/orchestrator.py`
- Behavior change:
  - Add configurable bounds for message, metadata, WC2026 context, and total structured request size.
  - Estimate provider input tokens from message plus metadata and WC2026 context.
  - Treat missing provider usage as settled with already reserved tokens retained.
- Data contract impact:
  - New additive 422 detail codes for oversized inputs.
- Tests to add/update:
  - `test_wc2026_chat_rejects_oversized_*`
  - `test_provider_preflight_counts_metadata_and_wc2026_context_tokens`
  - `test_structured_input_token_estimate_counts_context_not_only_message`
  - `test_provider_usage_missing_keeps_reserved_tokens_settled`
- Verification command:
  - `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_provider_rate_limits.py -q`
- Rollback or compatibility note:
  - Limits are settings-backed and can be tuned without code changes.

### 4. Converge Realtime Failures And Conversation Locks

- Files/modules:
  - `app/runtime/runner.py`
  - `app/api/routers/chat.py`
  - `app/core/config.py`
- Behavior change:
  - Runner pre-run failures and timeouts publish sanitized `ERROR` and failed `RUN_COMPLETED`.
  - Conversation lock TTL becomes configurable and defaults above max runtime.
- Data contract impact:
  - Existing event types reused; no schema change.
- Tests to add/update:
  - `test_realtime_runner_pre_run_failure_emits_terminal_event`
  - timeout terminal event assertions
  - realtime lock TTL assertion
- Verification command:
  - `.venv/bin/python -m pytest tests/test_realtime_runner.py tests/test_chat_routing.py -q`
- Rollback or compatibility note:
  - Error event payloads intentionally use stable codes, not raw exception strings.

### 5. Deepen Guardrails And Output Hygiene

- Files/modules:
  - `app/runtime/chat_behavior.py`
  - `app/runtime/wc2026_agent_data.py`
  - `app/runtime/orchestrator.py`
- Behavior change:
  - Normalize zero-width/full-width variants and compact spacing for deterministic guardrails.
  - Add guaranteed-outcome, direct-betting, locked-paid-content, and internal-field synonyms.
  - Make model-scope safe context phrase-based rather than any mention of "模型".
  - Locked WC2026 tool payload exposes public permission guidance instead of internal access-control fields.
  - Guardrail safe response remains visible in `TOKEN`, persisted answer, and `RUN_COMPLETED.content`.
- Data contract impact:
  - Locked Agent tool payload shape changes from internal mask fields to user-facing permission guidance; unlocked central payload remains unchanged.
- Tests to add/update:
  - `test_input_guardrail_refuses_*`
  - `test_output_guardrail_blocks_internal_permission_field_names`
  - `test_output_guardrail_safe_response_is_visible_token_for_guaranteed_claim`
  - locked payload hygiene assertion.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_orchestrator.py tests/test_wc2026_agent_data.py -q`
- Rollback or compatibility note:
  - If frontend or eval tooling depended on internal locked payload fields, it should use explicit API contracts rather than Agent tool output.

### 6. Close Owner And Stream Resource Gaps

- Files/modules:
  - `app/api/routers/conversations.py`
  - `app/api/routers/runs.py`
  - `app/api/routers/stream.py`
  - `app/api/lifespan.py`
  - `app/core/config.py`
- Behavior change:
  - Null-owner conversation/run/stream resources are forbidden.
  - Add settings-backed per-run in-process SSE/WS connection limit and release counters on close.
- Data contract impact:
  - Additive `429 STREAM_CONNECTION_LIMIT` for abusive stream fan-out.
- Tests to add/update:
  - null-owner tests for conversation, run, stream.
  - stream connection limit acquire/release test.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_conversations_match_index.py tests/test_runs_routing.py tests/test_stream_replay.py -q`
- Rollback or compatibility note:
  - Global multi-replica connection limits remain a future Redis/gateway enhancement.

## Risk Controls

- Public contract risks:
  - New validation failures are limited to oversized inputs or excess stream connections.
  - Existing event names and accepted response fields are unchanged.
- Money/accounting/security risks:
  - `SEAM-1` is explicitly out of scope and remains a known residual risk.
  - Usage-missing settlement keeps reserved tokens consumed.
- Migration/rebuild risks:
  - No migrations or projections.
- Performance risks:
  - JSON size checks and structured token estimates add bounded local serialization before side effects.
  - Stream connection limits are in-process; live multi-replica behavior should be monitored separately.
- Deployment/test-branch risks:
  - Intermittent 502 still needs live environment evidence; this plan only removes code-level hanging and resource-exhaustion contributors.
- Unrelated local changes to avoid:
  - Do not stage or alter unrelated DockerHost template/local changes.

## Completion Criteria

- All planned files changed or explicitly deferred.
- Specification still matches implementation.
- Focused tests pass.
- Full pytest passes.
- `AI_BOUNDARY_APPROVED=1 scripts/check_ai_boundaries.sh` passes.
- `scripts/check_spec_contract.sh` passes.
- `scripts/check_harness_workflows.sh` passes.
- `AI_BOUNDARY_APPROVED=1 make verify-release` passes or blocker is reported.
- Residual risks are called out: `SEAM-1`, live TTFT, and production 502 root-cause confirmation.
