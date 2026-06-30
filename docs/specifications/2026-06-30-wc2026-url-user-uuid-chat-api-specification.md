# 2026-06-30 WC2026 URL User UUID Chat API Specification

- Spec ID: `SPEC-WC2026-URL-USER-UUID-CHAT-API-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: expose the World Cup chat submission URL under a longer versioned path and pass the caller user uid as `user_uuid` in the URL instead of request headers.
- Target baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`.
- Current behavior: chat submission is `POST /chat`; protected routes derive `user_id` from `Authorization: Bearer <token>` or `X-API-Key`; accepted chat responses return `/stream/{agent_run_id}` and `/ws/{agent_run_id}`.
- Problem: the upstream caller needs a URL-only identity contract and a product-specific route namespace such as `/api/v1/wc2026/chat`.
- Non-goals: no new formal auth service, no DB schema migration, no real-money execution workflow, no change to World Cup forecasting behavior or event payload semantics.

## Product Semantics

- User/operator workflow:
  - Caller submits chat with `POST /api/v1/wc2026/chat?user_uuid=<uuid>`.
  - API returns `202 ChatAccepted` with versioned `stream_url` and `ws_url` that already include the same `user_uuid`.
  - Caller consumes output through returned SSE/WebSocket URLs and can recover through versioned run status or conversation history URLs with the same `user_uuid`.
- State model:
  - The URL `user_uuid` becomes the internal `user_id` for conversations, idempotency records, rate limiting, provider preflight, task payloads, realtime runner requests, stream ownership checks, run status checks, and conversation ownership checks.
- Ownership and identity rules:
  - `user_uuid` is required on WC2026 chat-flow routes.
  - Header-derived identity is not accepted for WC2026 chat-flow routes.
  - Owner mismatch still returns `403`.
- Permissions/authentication:
  - WC2026 chat-flow routes use URL `user_uuid` as the current placeholder identity.
  - Existing RAG/admin and non-WC2026 operational routes are out of scope for this contract.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - `stream=false` remains rejected with `422 STREAM_FALSE_NOT_SUPPORTED`.
  - Missing or blank `user_uuid` returns `401` before chat side effects.
  - Idempotency conflicts remain scoped by `(user_uuid, Idempotency-Key)`.
  - Provider limiter, realtime capacity, conversation lock, stream replay, and reaper behavior remain unchanged.
- Compatibility and migration expectations:
  - This is a breaking chat-flow API change.
  - `POST /chat`, `/stream/{agent_run_id}`, `/ws/{agent_run_id}`, `/runs/{agent_run_id}`, and `/conversations` are not retained as the primary World Cup chat-flow integration surface.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - `POST /api/v1/wc2026/chat?user_uuid=<uuid>`
  - `GET /api/v1/wc2026/stream/{agent_run_id}?user_uuid=<uuid>`
  - `WS /api/v1/wc2026/ws/{agent_run_id}?user_uuid=<uuid>`
  - `GET /api/v1/wc2026/runs/{agent_run_id}?user_uuid=<uuid>`
  - `GET /api/v1/wc2026/conversations/{conversation_id}?user_uuid=<uuid>`
  - `GET /api/v1/wc2026/conversations?user_uuid=<uuid>`
  - `POST /api/v1/wc2026/conversations?user_uuid=<uuid>`
- Request fields and validation:
  - Chat body remains `message`, optional `conversation_id`, optional `stream`, and optional `metadata`.
  - Query `user_uuid` is required and must be non-blank.
  - `stream=false` remains unsupported.
- Response/envelope fields and types:
  - `ChatAccepted` field names and types remain unchanged.
  - `stream_url` and `ws_url` are versioned relative URLs including `user_uuid`.
  - SSE event names and payloads remain unchanged.
- Status/error codes:
  - Missing/blank `user_uuid`: `401`.
  - Old chat-flow route usage: `404`.
  - Owner mismatch: `403`.
  - Idempotency conflict: `409`.
  - `stream=false`: `422 STREAM_FALSE_NOT_SUPPORTED`.
  - Rate/provider/capacity failure semantics remain unchanged.
- Pagination/sorting/filtering:
  - Conversation list `limit` and `offset` behavior remains unchanged.
- Backward compatibility:
  - No old chat-flow compatibility path is preserved.
  - Docs, release smoke, and benchmark scripts must use the new WC2026 URL contract.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - No schema migration.
- Read models, projections, snapshots, caches:
  - No projection changes.
- Rebuild or cleanup operators:
  - None.
- Historical data behavior:
  - Existing conversations remain readable only when the supplied `user_uuid` matches their stored `user_id`.
- Performance-sensitive queries or write paths:
  - Rate limiting remains on the same per-user hot path, with `user_uuid` as the key.

## Architecture

- Modules/files expected to change:
  - `app/api/middleware.py`: derive WC2026 current user from URL `user_uuid` and rate limit the versioned chat path.
  - `app/api/deps.py`: prefer request-state identity and remove header fallback for chat-flow dependency behavior.
  - `app/api/routers/chat.py`: move submission route to `/api/v1/wc2026/chat` and build versioned stream/ws URLs.
  - `app/api/routers/stream.py`: move SSE/WS routes to `/api/v1/wc2026/stream` and `/api/v1/wc2026/ws`.
  - `app/api/routers/runs.py`: move run status route to `/api/v1/wc2026/runs`.
  - `app/api/routers/conversations.py`: move conversation routes to `/api/v1/wc2026/conversations`.
  - `tests/`, `scripts/dockerhost_release.py`, `scripts/benchmark_realtime_ttft.py`, `README.md`, and related specs/plans: update route and identity examples.
- Data flow:
  - FastAPI middleware extracts `user_uuid` into `request.state.user_id`.
  - Route dependency returns that current user.
  - Existing repositories, runner, tasks, stream ownership, and status ownership use the same internal `user_id` field.
- Transaction/concurrency boundaries:
  - No change to DB transaction, conversation lock, idempotency claim, capacity reservation, or task dispatch boundaries.
- Observability/logging/metrics:
  - Logs and metrics must not print secret/header tokens.
  - Logging `user_id` continues to refer to the supplied `user_uuid`.
- Rollback strategy:
  - Revert route/auth/script/docs changes and redeploy a previously verified Git ref.

## Harness Classification

- Expected gate(s):
  - `HARNESS-SPEC-FIRST-FEATURE`.
  - Focused API, CORS, stream, release CLI, and benchmark tests.
  - `scripts/check_ai_boundaries.sh`, `scripts/check_spec_contract.sh`, `scripts/check_harness_workflows.sh`, and `scripts/verify_release.sh`.
- Performance-sensitive class:
  - API middleware and chat submission hot path; no new DB/Redis operations.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Focused tests prove no extra sync waiting and no old route fallback.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_cors.py tests/test_stream_replay.py tests/test_dockerhost_release_cli.py -q`
  - `.venv/bin/python -m py_compile app/api/middleware.py app/api/deps.py app/api/routers/chat.py app/api/routers/stream.py app/api/routers/runs.py app/api/routers/conversations.py scripts/dockerhost_release.py scripts/benchmark_realtime_ttft.py`
- Prerelease-grade verification commands:
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - `POST /api/v1/wc2026/chat?user_uuid=<uuid>` accepts a valid chat body without auth headers and returns `202`.
  - Accepted response `stream_url` and `ws_url` include `/api/v1/wc2026/...` and `user_uuid=<uuid>`.
  - Versioned stream, run status, and conversation detail/list routes authorize by URL `user_uuid`.
  - `POST /chat` no longer accepts the chat submission workflow.
- Edge cases:
  - Missing/blank `user_uuid` is rejected before side effects.
  - Header-only identity is rejected for the WC2026 chat-flow routes.
  - Owner mismatch still returns `403`.
  - Idempotency replay remains scoped by `user_uuid`.
- Compatibility:
  - RAG/provider internal Authorization headers are unaffected.
  - Async-only chat and event payload contracts remain unchanged.
- Operational:
  - DockerHost smoke uses URL `user_uuid` and does not send `X-API-Key` for chat-flow checks.
  - Release verification remains the readiness proof.
- Evidence artifacts:
  - Specification and implementation plan.
  - Focused pytest and py_compile output.
  - Release harness output or explicit blocker.

## Review Notes

- Open questions:
  - None; user explicitly chose query `user_uuid` and no old-mode compatibility.
- Accepted assumptions:
  - `user_uuid` is a caller-provided opaque user uid string and is not validated as RFC 4122 UUID in this slice.
  - Existing RAG/admin surfaces may keep their current auth model.
- Rejected alternatives:
  - Keep `/chat` as a compatibility alias: rejected by user.
  - Put `user_uuid` in body or header: rejected by source request.
