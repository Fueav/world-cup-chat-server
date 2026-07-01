# 2026-07-01 API Request Guard Boundary Implementation Plan

## Plan Header

- Specification: `docs/specifications/2026-07-01-api-request-guard-boundary-specification.md`
- Spec ID: `SPEC-API-REQUEST-GUARD-001`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- Target branch/baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`
- Scope summary: Make best-effort API request limiting route-aware, secret-safe in Redis keys, and observable on fail-open while preserving provider limiter fail-closed behavior.
- Out of scope:
  - Provider/model limiter changes, key-pool changes, gateway/IP limiters, schema changes, deployment changes.

## Change Steps

### Step 1: Add Failing API Request Guard Tests

- Files/modules:
  - `tests/test_api_request_rate_limit.py`
- Behavior change:
  - None; tests express desired contract before implementation.
- Data contract impact:
  - None.
- Tests to add/update:
  - `RateLimiter.check(identity, route=...)` creates distinct Redis buckets for the same identity on different routes.
  - Redis bucket keys include route labels and do not include raw identity values.
  - Redis failure fails open and increments `api_rate_limit_fail_open_total`.
  - `RateLimitMiddleware` passes the request path as route scope to the limiter.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_api_request_rate_limit.py -q`
- Rollback or compatibility note:
  - Expected to fail before implementation because `RateLimiter.check()` has no `route` argument.

### Step 2: Implement Route-Aware Request Limit Buckets

- Files/modules:
  - `app/api/ratelimit.py`
- Behavior change:
  - Add route label normalization and identity hashing.
  - Change Redis key from raw user-only to `ratelimit:api:{route_label}:{identity_hash}`.
  - Keep the existing sliding-window sorted-set algorithm and TTL.
- Data contract impact:
  - Redis key shape changes only; public API is unchanged.
- Tests to add/update:
  - Step 1 unit tests.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_api_request_rate_limit.py -q`
- Rollback or compatibility note:
  - Old keys expire naturally after the existing window.

### Step 3: Wire Middleware and Metrics

- Files/modules:
  - `app/api/middleware.py`
  - `app/api/lifespan.py`
- Behavior change:
  - Middleware calls `limiter.check(user_id, route=request.url.path)`.
  - Lifespan injects the app metrics object into the limiter.
  - Rate-limit logs use sanitized route/identity hash where the limiter owns key construction.
- Data contract impact:
  - None.
- Tests to add/update:
  - Middleware route-scope test.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_api_request_rate_limit.py tests/test_stream_replay.py -q`
- Rollback or compatibility note:
  - No persistent data rollback.

### Step 4: Document Operator Boundary

- Files/modules:
  - `docs/API.md`
- Behavior change:
  - Clarify that request limiting is route plus user scoped and best-effort fail-open, while provider quota remains separate.
- Data contract impact:
  - Documentation only.
- Tests to add/update:
  - None.
- Verification command:
  - `scripts/check_spec_contract.sh`
- Rollback or compatibility note:
  - Documentation-only.

### Step 5: Harness Verification and Review

- Files/modules:
  - All changed files.
- Behavior change:
  - None beyond implementation.
- Data contract impact:
  - None.
- Tests to add/update:
  - Fix only request-guard-related failures.
- Verification command:
  - `.venv/bin/python -m pytest tests/test_api_request_rate_limit.py tests/test_stream_replay.py tests/test_lifespan_runtime_wiring.py -q`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_ai_boundaries.sh`
  - `scripts/check_harness_workflows.sh`
  - `AI_BOUNDARY_APPROVED=1 scripts/verify_release.sh`
- Rollback or compatibility note:
  - If full release verification fails for unrelated environment reasons, preserve focused evidence and report blocker.

## Risk Controls

- Public contract risks:
  - No route, schema, envelope, status code, or header removals.
- Money/accounting/security risks:
  - Provider limiter remains separate and fail-closed by default.
  - Raw identities and tokens must not enter Redis keys.
- Migration/rebuild risks:
  - None; Redis keys expire.
- Performance risks:
  - SHA-256 and route normalization are bounded; no new I/O.
- Deployment/test-branch risks:
  - `app/api/` is approval-required under `.ai-boundaries.yml`; user approval in this thread authorizes the focused change.
- Unrelated local changes to avoid:
  - Do not alter provider limiter defaults, key-pool semantics, release scripts, or deployment runbooks.

## Completion Criteria

- Specification still matches implementation.
- Focused request-guard tests pass after failing red first.
- Stream replay and lifespan wiring regressions pass.
- Spec, AI-boundary, harness workflow, and release verification gates pass or a concrete blocker is reported.
- Review finds no provider limiter fail-open regression and no raw credential storage in request-limit keys.
