# 2026-07-01 WC2026 R1 Chat Hardening Specification

## Context

- Spec ID: `SPEC-WC2026-R1-CHAT-HARDENING-001`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- PRD/source request: 2026-07-01 R1 WC2026 chat comprehensive QA report and user triage: skip `SEAM-1`; evaluate and fix `F-DYN-2`; provide a TTFT plan for `F-DYN-1`; fix `ROB-2`, `ROB-3`, guardrail depth, output safe-response visibility, lock/owner/stream robustness, and locked-answer information hygiene.
- Target baseline: current `world-cup-chat-server` checkout on 2026-07-01.
- Current behavior:
  - WC2026 Agent registers a custom `web_search` tool even though product policy forbids external lookup for model-input-outside information.
  - Realtime runner failures before orchestration can leave accepted runs without terminal events.
  - Chat request size and provider admission estimate cover the message but not all structured metadata/context.
  - Deterministic guardrails miss common synonyms, spacing/zero-width variants, traditional Chinese variants, and some internal field leaks.
  - Output guardrail safe responses are intended to stream, but the report requires explicit regression coverage for visible `TOKEN`/`RUN_COMPLETED` content.
  - WC2026 read routes allow null-owner conversations in some paths.
  - SSE/WS streams have no per-run connection cap.
- Problem: The R1 report found non-blocking but release-relevant behavior, robustness, and defense-in-depth gaps that should be closed without changing the upstream paid-wall decision boundary.
- Non-goals:
  - Do not implement `SEAM-1` service-to-service `X-API-Key` enforcement in this change.
  - Do not claim the PRD `<=1.5s` first-token target is met; this change only removes an erroneous tool path and hanging failure modes.
  - Do not change paid unlock semantics, direct order execution workflows, central data ownership, or database schema.

## Product Semantics

- User/operator workflow:
  - Normal WC2026 chat remains asynchronous: POST accepted, then SSE/WS stream and run-status recovery.
  - If deterministic policy blocks generated output, the user must see the safe response in normal answer content, not only in an `ERROR` event payload.
  - Oversized message, metadata, or `wc2026_context` is rejected before DB writes, locks, queues, or provider checks.
- State model:
  - Realtime runner failures outside orchestration converge to `FAILED` with sanitized `ERROR` and `RUN_COMPLETED` events.
  - Provider usage missing keeps the already reserved quota consumed and records `usage_missing`; it is not treated as a free successful refund.
- Ownership and identity rules:
  - WC2026 conversation, run, SSE, and WS resources require an explicit owner matching `user_uuid`; null owner is forbidden.
- Permissions/authentication:
  - No new service-to-service auth in this slice by user decision.
  - Existing `user_uuid` route authentication remains unchanged.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - Oversized request fields return stable `422` detail codes.
  - Per-run stream connection limit returns `429 STREAM_CONNECTION_LIMIT` for SSE and closes WS with policy violation.
  - Runner timeout and pre-run failure both publish terminal failure state.
- Compatibility and migration expectations:
  - No schema migration.
  - Existing event names and response fields remain compatible.
  - New validation errors are additive failure modes for oversized or abusive inputs.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - `/api/v1/wc2026/chat` keeps the same accepted response shape.
  - `/api/v1/wc2026/chat/stream/{run_id}` and WS equivalent keep event payload shape and add per-run connection limiting.
- Request fields and validation:
  - `message`, `metadata`, `wc2026_context`, and total structured request size are bounded by settings.
  - Provider admission estimates include message, metadata, and WC2026 context.
- Response/envelope fields and types:
  - Guardrail-refused output final content is visible through `TOKEN` concatenation and `RUN_COMPLETED.data.content`.
  - Runner-level failures use sanitized error codes such as `REALTIME_RUNNER_FAILED` or `RUN_TIMEOUT`.
- Status/error codes:
  - `422 MESSAGE_TOO_LARGE`, `METADATA_TOO_LARGE`, `WC2026_CONTEXT_TOO_LARGE`, or `CHAT_REQUEST_TOO_LARGE`.
  - `429 STREAM_CONNECTION_LIMIT`.
- Pagination/sorting/filtering:
  - Not applicable.
- Backward compatibility:
  - Valid existing clients are unchanged.
  - Clients already handling `ERROR` and `RUN_COMPLETED` continue to work.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - None.
- Read models, projections, snapshots, caches:
  - None.
- Rebuild or cleanup operators:
  - None.
- Historical data behavior:
  - Historical runs/conversations are unchanged; null-owner reads are no longer allowed through WC2026 routes.
- Performance-sensitive queries or write paths:
  - Request size checks run before side effects using bounded JSON serialization.
  - Stream connection counters are in-process only and do not add Redis round trips.

## Architecture

- Modules/files expected to change:
  - `app/runtime/agent_factory.py`, `app/runtime/orchestrator.py`, `app/runtime/chat_behavior.py`, `app/runtime/runner.py`, `app/runtime/provider_limits.py`, `app/runtime/wc2026_agent_data.py`.
  - `app/api/routers/chat.py`, `app/api/routers/stream.py`, `app/api/routers/conversations.py`, `app/api/routers/runs.py`, `app/api/lifespan.py`.
  - `app/core/config.py`.
  - Focused tests under `tests/`.
- Data flow:
  - Chat request validation rejects oversized fields before idempotency, conversation lookup, locks, DB writes, queues, or provider preflight.
  - Provider limiter receives a structured token estimate covering message and trusted context.
  - Agent tool list excludes custom `web_search`; current-match values remain available only through WC2026 data tools.
  - Locked WC2026 tool payloads expose user-facing permission guidance rather than internal mask implementation fields.
  - Runner catches failures outside orchestration and publishes sanitized terminal events.
- Transaction/concurrency boundaries:
  - Conversation lock TTL is configurable and defaults above the max realtime runtime.
  - Stream connection counts are released when SSE/WS generators close.
- Observability/logging/metrics:
  - Existing runner timeout and provider usage missing metrics remain.
  - No raw secrets, API keys, or raw internal exception strings are emitted to stream events.
- Rollback strategy:
  - Revert the focused runtime/API/test/docs changes; no DB rollback required.

## Harness Classification

- Expected gate(s):
  - `HARNESS-FOCUSED-CHANGE`
  - Focused pytest for chat routing, behavior policy, orchestrator, runner, provider limits, WC2026 data, conversations, streams, and runs.
  - `scripts/check_ai_boundaries.sh`, `scripts/check_spec_contract.sh`, `scripts/check_harness_workflows.sh`.
- Performance-sensitive class:
  - Yes, streaming and request admission hot paths are touched, but no blocking external calls are added.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Unit coverage proves `web_search` is removed and safe tokens stream. Live TTFT p95 remains a follow-up measurement.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_chat_behavior_policy.py tests/test_orchestrator.py tests/test_realtime_runner.py tests/test_chat_routing.py tests/test_provider_rate_limits.py tests/test_wc2026_agent_data.py tests/test_conversations_match_index.py tests/test_stream_replay.py tests/test_runs_routing.py -q`
- Prerelease-grade verification commands:
  - `.venv/bin/python -m pytest -q`
  - `AI_BOUNDARY_APPROVED=1 scripts/check_ai_boundaries.sh`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - WC2026 Agent no longer registers custom `web_search`.
  - Realtime pre-run failure and timeout converge to failed terminal events.
  - Request size limits reject oversized message, metadata, and WC2026 context before side effects.
  - Provider token reservation includes structured runtime context.
  - Output guardrail safe response appears in `TOKEN`, persisted answer, and `RUN_COMPLETED.content`.
  - Deterministic guardrails catch the R1 synonym/traditional/spacing classes.
  - Null-owner conversation/run/stream access is forbidden.
  - SSE/WS per-run connection limits exist.
  - Locked WC2026 tool payloads and output guardrails avoid internal names such as `viewer_scope`, `mask_policy`, `block_b`, and `block_d`.
- Edge cases:
  - Benign API-key setup documentation questions remain allowed.
  - Methodology questions about whether the model includes transfer rumors remain allowed; requests to look up transfer rumors remain refused.
  - Stream connection counters release after close.
- Compatibility:
  - No event enum, DB schema, accepted response, or central-data unlocked payload contract break.
- Operational:
  - `SEAM-1` remains an explicit residual risk by user decision.
  - Intermittent 502 root cause still requires production log or deployment evidence.
- Evidence artifacts:
  - Specification and implementation plan.
  - Focused pytest output.
  - Full pytest output.
  - Harness script output.

## Review Notes

- Open questions:
  - Whether to enforce downstream `X-API-Key` for `SEAM-1` later.
  - Whether the product keeps `<=1.5s` TTFT or revises it after live latency breakdown.
- Accepted assumptions:
  - In-process stream connection limiting is sufficient for this focused hardening slice; global multi-replica throttling can be added at Redis or gateway later.
  - Retaining reserved provider tokens when usage is missing is safer than treating missing usage as a successful refund.
- Rejected alternatives:
  - Rejected adding network search allowlists; WC2026 product policy forbids this Agent from live-searching outside model inputs.
  - Rejected failing the whole request after output was already safely replaced by deterministic guardrail text.
- Reviewer findings and resolution:
  - Pending code review.
