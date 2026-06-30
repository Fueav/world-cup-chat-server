# 2026-06-30 WC2026 URL User UUID Chat API Implementation Plan

## Plan Header

- Specification: `docs/specifications/2026-06-30-wc2026-url-user-uuid-chat-api-specification.md` (`SPEC-WC2026-URL-USER-UUID-CHAT-API-001`)
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: `main`
- Scope summary: Break the old chat-flow API contract and move World Cup chat submission, stream, run status, and conversation recovery to `/api/v1/wc2026/*` routes that use URL query `user_uuid` as the internal user id.
- Out of scope: RAG/admin auth changes, DB schema changes, formal auth service, forecasting behavior changes, and legacy `/chat` compatibility.

## Change Steps

1. Tests for WC2026 URL contract.
   - Files/modules: `tests/test_chat_routing.py`, `tests/test_cors.py`, `tests/test_stream_replay.py`, `tests/test_dockerhost_release_cli.py`.
   - Behavior change: assert new route accepts URL `user_uuid`, old route is not available, returned stream/ws URLs are versioned, and release smoke commands stop sending auth headers.
   - Data contract impact: none.
   - Tests to add/update: focused pytest cases for chat accept, missing user_uuid, old route removal, owner mismatch, CORS preflight, and DockerHost command shape.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_cors.py tests/test_stream_replay.py tests/test_dockerhost_release_cli.py -q`.
   - Rollback or compatibility note: tests intentionally reject old chat-flow compatibility.

2. Middleware and dependency identity extraction.
   - Files/modules: `app/api/middleware.py`, `app/api/deps.py`.
   - Behavior change: extract `user_uuid` from query for protected WC2026 chat-flow routes; remove header fallback for those routes; rate limit `/api/v1/wc2026/chat`.
   - Data contract impact: internal `user_id` stores the query `user_uuid`.
   - Tests to add/update: missing/blank `user_uuid` and no-header accepted chat coverage.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_cors.py -q`.
   - Rollback or compatibility note: revert to header extraction and old route prefixes.

3. Route migration.
   - Files/modules: `app/api/routers/chat.py`, `app/api/routers/stream.py`, `app/api/routers/runs.py`, `app/api/routers/conversations.py`.
   - Behavior change: move chat-flow endpoints to `/api/v1/wc2026/*`; build returned stream/ws URLs with `user_uuid`.
   - Data contract impact: response field names unchanged; URL values change.
   - Tests to add/update: accepted response URL assertions; old `/chat` 404; stream/runs/conversations owner checks with query identity.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_stream_replay.py -q`.
   - Rollback or compatibility note: no old route alias.

4. Release tools and docs.
   - Files/modules: `scripts/dockerhost_release.py`, `scripts/benchmark_realtime_ttft.py`, `README.md`, relevant existing specs/plans that describe current route examples.
   - Behavior change: DockerHost smoke and benchmark use `?user_uuid=` URLs and no chat-flow auth headers.
   - Data contract impact: operational command contract changes.
   - Tests to add/update: `tests/test_dockerhost_release_cli.py`.
   - Verification command: `.venv/bin/python -m pytest tests/test_dockerhost_release_cli.py -q`.
   - Rollback or compatibility note: old smoke commands are no longer accepted.

5. Harness verification.
   - Files/modules: all changed files.
   - Behavior change: none beyond verified implementation.
   - Data contract impact: none.
   - Tests to add/update: none unless focused failures expose gaps.
   - Verification command: `AI_BOUNDARY_APPROVED=1 make verify-release`.
   - Rollback or compatibility note: report any harness blocker with exact failing command.

## Suggested Step Order

1. Write failing tests for the new WC2026 URL contract.
2. Implement middleware/dependency extraction.
3. Move routes and response URLs.
4. Update release tooling and docs.
5. Run focused tests.
6. Run py_compile.
7. Run release gate with owner-approved AI boundary flag.
8. Review diff for accidental old-route compatibility or secret exposure.

## Risk Controls

- Public contract risks: old chat-flow clients break intentionally; docs and release smoke must use the new path.
- Money/accounting/security risks: no order execution or provider-secret behavior changes; do not log header/token values.
- Migration/rebuild risks: no DB migration.
- Performance risks: query extraction is constant-time and adds no IO.
- Deployment/test-branch risks: release smoke must validate the new path against a deployed ref before claiming readiness.
- Unrelated local changes to avoid: do not touch RAG auth, provider API Authorization, DB schema, or forecasting policy.

## Completion Criteria

- Specification still matches implementation.
- Focused tests pass.
- Py_compile passes for changed Python modules/scripts.
- Required harness gates pass or blocker is reported.
- Old `/chat` submission route is not available.
- DockerHost smoke and README show the URL `user_uuid` contract.
