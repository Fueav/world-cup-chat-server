# 2026-07-01 API Request Guard Boundary Specification

## Context

- Spec ID: `SPEC-API-REQUEST-GUARD-001`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
- Related specifications:
  - `SPEC-PROVIDER-RATELIMIT-001`: provider/model RPM/TPM admission remains the hard quota and cost guard.
  - `SPEC-PROVIDER-KEY-POOL-001`: provider key-pool admission and settlement remain separate from API request throttling.
- PRD/source request:
  - A tester warned that an `api_request_guard` style limiter may be global, per-process, reset-window based, and fail-open. The accepted product decision is to use API request limiting only as best-effort entry abuse protection, not as provider quota protection.
- Target baseline:
  - `main` in `/Users/chris/AiProject/world-cup-chat-server`.
- Current behavior:
  - `RateLimitMiddleware` limits only `/api/v1/wc2026/chat`.
  - `RateLimiter` uses Redis sorted sets and fails open when Redis is unavailable.
  - The Redis bucket is keyed by raw `user_id` only.
  - Provider/model rate limiting is handled separately by `RedisProviderRateLimiter` and defaults to fail-closed.
- Problem:
  - A raw user-only bucket can cause all write routes for the same identity to share one quota if more write routes are added.
  - Redis keys should not store raw bearer tokens or API keys if the same limiter is later reused for header-authenticated routes.
  - Fail-open behavior is acceptable only if it is explicit, observable, and not confused with provider quota protection.
- Non-goals:
  - Do not change provider/model limiter behavior or `provider_rate_limit_fail_open`.
  - Do not add paid quota, per-plan limits, IP reputation, captcha, or gateway/service-mesh configuration.
  - Do not change WC2026 chat request/response schemas.
  - Do not add database migrations.

## Product Semantics

- User/operator workflow:
  - Operators may enable API request limiting as best-effort protection against client retry storms, accidental loops, or obvious abuse.
  - Operators must continue to rely on provider/model limiter for real provider quota and cost protection.
- State model:
  - API request limiter state is a short-lived Redis sorted set per `(route, identity)` bucket.
  - Bucket keys include a stable route label and a hash of the identity.
  - Bucket keys do not contain raw user ids, bearer tokens, API keys, provider secrets, or query strings.
- Ownership and identity rules:
  - WC2026 chat identity remains `user_uuid` from the URL query.
  - Future protected write routes may use the authenticated user id extracted by `AuthMiddleware`.
  - API request throttling is user-facing fairness protection, not provider credential ownership.
- Permissions/authentication:
  - Authentication still runs before rate limiting for protected business requests.
  - Public health, metrics, docs, stream, websocket, and read endpoints remain outside this limiter.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - Over-limit requests return `429` with `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining`.
  - Redis failure in the API request limiter fails open, increments a fail-open metric, and logs a sanitized warning.
  - Missing limiter or missing authenticated identity continues downstream so auth or route validation owns the final error.
  - Provider limiter unavailable continues to fail closed through existing `503 PROVIDER_LIMITER_UNAVAILABLE` behavior.
- Compatibility and migration expectations:
  - Existing configured limit value and response headers remain compatible.
  - Existing old Redis keys may expire naturally; no cleanup is required.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - `POST /api/v1/wc2026/chat` remains the only request-limited route in this slice.
- Request fields and validation:
  - No client request shape changes.
- Response/envelope fields and types:
  - Normal accepted responses keep existing `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers.
  - Over-limit responses keep existing `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining` headers.
- Status/error codes:
  - API request limiter returns `429`.
  - Provider quota/limiter paths keep existing `429` or `503` semantics.
- Pagination/sorting/filtering:
  - Unchanged.
- Backward compatibility:
  - Clients do not observe Redis key shape changes.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - None.
- Read models, projections, snapshots, caches:
  - Redis request-limit keys change from user-only to route plus identity hash.
- Rebuild or cleanup operators:
  - None; old keys expire after the existing window TTL.
- Historical data behavior:
  - Unchanged.
- Performance-sensitive queries or write paths:
  - Adds only bounded string normalization and SHA-256 hashing before the existing Redis pipeline.

## Architecture

- Modules/files expected to change:
  - `app/api/ratelimit.py`
  - `app/api/middleware.py`
  - `app/api/lifespan.py`
  - `tests/test_api_request_rate_limit.py`
  - `docs/API.md`
- Data flow:
  1. `AuthMiddleware` extracts `request.state.user_id`.
  2. `RateLimitMiddleware` checks only configured write paths and passes both `user_id` and request path to `RateLimiter`.
  3. `RateLimiter` builds `ratelimit:api:{route_label}:{identity_hash}` and runs the existing Redis sliding-window pipeline.
  4. Redis success returns allowed or over-limit.
  5. Redis failure logs a sanitized warning, increments `api_rate_limit_fail_open_total`, and allows the request.
- Transaction/concurrency boundaries:
  - Redis remains the shared multi-process state for API request buckets.
  - No per-process in-memory fallback is introduced for production request limiting.
- Observability/logging/metrics:
  - Fail-open warnings include route label and identity hash, not raw identity.
  - Metrics add `api_rate_limit_fail_open_total{route=...}`.
- Rollback strategy:
  - Revert code and docs; old and new Redis keys expire naturally.

## Harness Classification

- Expected gate(s):
  - `HARNESS-FOCUSED-CHANGE`
  - Focused pytest
  - `scripts/check_ai_boundaries.sh`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
  - `scripts/verify_release.sh`
- Performance-sensitive class:
  - API middleware hot path; bounded CPU-only key construction plus existing Redis work.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Focused tests prove key scope and fail-open behavior.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_api_request_rate_limit.py tests/test_stream_replay.py -q`
- Prerelease-grade verification commands:
  - `make test`
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - API request limiter buckets are scoped by route and identity hash.
  - Same identity on different routes does not share the same API request quota.
  - Redis request-limit keys do not contain raw identity values.
  - `RateLimitMiddleware` passes a stable route scope into the limiter.
- Edge cases:
  - Redis failure remains fail-open for API request limiting and records `api_rate_limit_fail_open_total`.
  - Missing limiter or missing identity remains non-blocking in middleware.
  - SSE/WS stream routes are not request-limited.
- Compatibility:
  - Existing response headers and `429` body remain compatible.
  - Provider/model limiter behavior remains unchanged and fail-closed by default.
- Operational:
  - No secrets or raw tokens are persisted into Redis keys, logs, metrics, docs, or tests.
  - Docs clearly distinguish best-effort API request limiting from provider quota enforcement.
- Evidence artifacts:
  - Specification and implementation plan.
  - Red/green focused pytest output.
  - Release harness output or explicit blocker.

## Review Notes

- Open questions:
  - Gateway-level IP-based rate limiting can be added later if abuse patterns require it.
- Accepted assumptions:
  - `user_uuid + route` is the right bucket for current WC2026 chat traffic.
  - API request limiter fail-open is acceptable only as best-effort entry protection.
- Rejected alternatives:
  - One global API bucket: rejected because one traffic source can starve all clients.
  - Per-process in-memory production request limiting: rejected because limits scale with replica count.
  - Provider quota through API request guard: rejected because provider admission and settlement must stay fail-closed.
