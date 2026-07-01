# 2026-07-01 Provider API Key Pool Concurrency Implementation Plan

## Plan Header

- Specification: `docs/specifications/2026-07-01-provider-api-key-pool-concurrency-specification.md`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Related specifications:
  - `docs/specifications/2026-06-11-provider-rate-limit-and-secret-management-specification.md`
  - `docs/specifications/2026-06-22-zai-glm52-provider-specification.md`
- Target branch/baseline: `main`
- Scope summary: Add a provider API key pool that can increase real-model concurrency when provider quota is key-level, while preserving single-key compatibility, secret hygiene, provider limiter fail-closed behavior, and DockerHost deployment safety.
- Out of scope:
  - Creating, requesting, or storing real API keys.
  - Proving provider key-level independence without an operator-supplied real-provider benchmark.
  - Public API changes.
  - Database migrations.
  - Frontend work.

## Change Steps

1. Test-first key-pool contract
   - Files/modules:
     - `tests/test_provider_key_pool.py`
     - `tests/test_provider_rate_limits.py`
     - `tests/test_secret_management.py`
     - `tests/test_agent_factory.py`
     - `tests/test_chat_routing.py`
     - `tests/test_worker_provider_limits.py`
   - Behavior change:
     - Express the new expected behavior before implementation.
   - Data contract impact:
     - None.
   - Tests to add/update:
     - Single `ZAI_API_KEY` becomes one implicit slot.
     - `ZAI_API_KEYS_FILE` parses newline, JSON array, and JSON object forms.
     - `PROVIDER_KEY_POOL_FILE` parses provider/model scoped JSON.
     - Duplicate key ids fail sanitized.
     - Disabled slots are ignored.
     - Raw key values never appear in `repr`, exception text, logs captured by tests, or run metadata.
     - Key-level limiter can admit multiple independent slots beyond one slot's TPM/RPM.
     - Aggregate cap blocks total throughput even when individual slots have capacity.
     - One key in backoff does not block other slots.
     - All slots exhausted returns minimum retry-after.
     - Provider usage settlement debits the same selected key id.
     - Provider 429 records backoff for the selected key id.
     - Agent/model construction receives the selected key value and not a random or first slot.
     - Chat realtime preflight remains advisory; runner acquire is authoritative.
     - Batch worker acquires at execution time, not enqueue time.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_provider_rate_limits.py tests/test_secret_management.py -q`
   - Rollback or compatibility note:
     - Tests should keep mock mode and single-key mode green.

2. Settings and secret parsing
   - Files/modules:
     - `app/core/config.py`
     - `app/core/secrets.py`
     - new `app/runtime/provider_keys.py` or equivalent
     - `tests/test_provider_key_pool.py`
     - `tests/test_secret_management.py`
   - Behavior change:
     - Add settings:
       - `provider_key_pool_file: str = ""`
       - `provider_key_pool_scope: str = "account"` or provider/file override.
       - `provider_key_pool_strategy: str = "least_wait_round_robin"`
       - `zai_api_keys_file: str = ""`
     - Add `ProviderKeySlot`, `ProviderKeyPool`, and parsing helpers.
     - Preserve `ZAI_API_KEY` / `ZAI_API_KEY_FILE` as one-slot fallback.
     - Normalize and validate key ids without deriving labels from secret values.
     - Redact every parsed secret through existing `SecretValue` behavior.
   - Data contract impact:
     - Environment/config contract only.
   - Tests to add/update:
     - File parsing variants and validation failures.
     - Backward-compatible single-key behavior.
     - Secret redaction across all parser errors.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_secret_management.py -q`
   - Rollback or compatibility note:
     - If key-pool parsing fails in production, remove the key-pool file env and fall back to single key.

3. Key-aware provider limiter
   - Files/modules:
     - `app/runtime/provider_limits.py`
     - `app/runtime/provider_keys.py`
     - `tests/test_provider_key_pool.py`
     - `tests/test_provider_rate_limits.py`
   - Behavior change:
     - Extend limiter request/decision models with optional `key_id` and key-pool decisions.
     - Add atomic key-pool acquire path that:
       - checks aggregate backoff and aggregate bucket when configured;
       - checks each enabled key bucket and key backoff;
       - reserves one selected key and aggregate budget in one normal-path Redis operation;
       - rotates among eligible keys to avoid hot-spotting.
     - Add settlement against the same selected key and aggregate bucket.
     - Add key-level `record_provider_error(...)` support.
   - Data contract impact:
     - Redis keys:
       - `ratelimit:provider:{provider}:{model}:key:{key_id}`
       - `ratelimit:provider:{provider}:{model}:aggregate`
       - `backoff:provider:{provider}:{model}:key:{key_id}`
       - `backoff:provider:{provider}:{model}:aggregate`
       - `cursor:provider:{provider}:{model}:keys`
   - Tests to add/update:
     - Concurrent acquisitions cannot oversubscribe a slot.
     - Two or more slots increase admitted reservations when `scope=key`.
     - `scope=account` preserves aggregate cap.
     - Backoff and settlement apply to selected `key_id`.
     - Redis-unavailable behavior remains fail-closed unless explicit fail-open is configured.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_provider_rate_limits.py -q`
   - Rollback or compatibility note:
     - Keep existing provider/model-only limiter behavior as single-slot fallback until release confidence is established.

4. Run-scoped model construction
   - Files/modules:
     - `app/runtime/agent_factory.py`
     - `app/runtime/orchestrator.py`
     - `app/runtime/deps.py`
     - `tests/test_agent_factory.py`
     - `tests/test_provider_key_pool.py`
   - Behavior change:
     - Stop constructing a real provider model in `AgentOrchestrator.__init__()` for production runs.
     - Keep injected test `Agent` support.
     - Acquire provider key quota before building the run's Pydantic AI model.
     - Build `Agent` per run with the selected `ProviderKeyLease`.
     - Ensure selected `key_id` is recorded only as safe metadata.
   - Data contract impact:
     - `agent_run.plan` may add sanitized `provider_key_id` and `provider_key_pool_scope`.
   - Tests to add/update:
     - A fake model factory observes the selected key slot.
     - Runs do not always use the first key.
     - Missing key pool fails before model construction.
     - Existing injected-agent tests continue to work.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_provider_key_pool.py -q`
   - Rollback or compatibility note:
     - If per-run model construction has unacceptable overhead, cache provider/model profile objects but never cache a model bound to one secret across key slots.

5. API, realtime, and worker integration
   - Files/modules:
     - `app/api/lifespan.py`
     - `app/api/routers/chat.py`
     - `app/runtime/deps.py`
     - `app/runtime/orchestrator.py`
     - `app/tasks/agent_tasks.py`
     - `tests/test_chat_routing.py`
     - `tests/test_worker_provider_limits.py`
   - Behavior change:
     - Startup validates at least one key slot for real provider.
     - `/readyz` reports safe key-pool state and enabled slot count.
     - Realtime API preflight asks whether any slot is likely available, without reserving raw secrets.
     - Runner acquire remains authoritative and records selected `key_id`.
     - Batch worker acquires a key during task execution and respects key-level backoff/retry-after.
   - Data contract impact:
     - No public request/response schema change.
   - Tests to add/update:
     - Readyz states for mock, single, configured, missing, partial.
     - Explicit realtime over key-pool limit returns 429 before unnecessary side effects when preflight can determine denial.
     - Accepted run handles post-accept key-pool exhaustion with existing provider rate-limit error path.
     - Worker retry path uses selected key-level retry-after.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_worker_provider_limits.py -q`
   - Rollback or compatibility note:
     - Single-key fallback path remains the first rollback target.

6. Observability and redaction
   - Files/modules:
     - `app/core/metrics.py` if needed
     - `app/runtime/provider_limits.py`
     - `app/runtime/orchestrator.py`
     - `app/core/secrets.py`
     - tests under `tests/`
   - Behavior change:
     - Add key-level metrics with safe `key_id` labels.
     - Add pool-exhaustion counters.
     - Extend redaction tests to cover every parsed key value.
     - Ensure logs and errors never include secret file contents or raw keys.
   - Data contract impact:
     - Prometheus metrics are additive.
   - Tests to add/update:
     - Metrics include safe key ids.
     - Metrics/logs do not contain raw keys, prefixes, suffixes, or value-derived hashes.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_secret_management.py -q`
   - Rollback or compatibility note:
     - Metrics can be ignored by old dashboards; existing provider-level metrics remain.

7. DockerHost and runbook configuration
   - Files/modules:
     - `dockerhost/compose.yaml`
     - `dockerhost/env.example`
     - `docs/DOCKERHOST_RELEASE_RUNBOOK.md`
     - `docs/INTEGRATION_GUIDE.md` only if the public integration docs need capacity wording.
     - `scripts/check_dockerhost_production_config.py`
     - tests or script checks as applicable.
   - Behavior change:
     - API and worker containers receive `PROVIDER_KEY_POOL_FILE` and `ZAI_API_KEYS_FILE`.
     - Examples show secret-file injection, not inline secret values.
     - Production config check verifies key-pool env wiring is present where needed.
   - Data contract impact:
     - DockerHost deployment contract changes.
   - Tests to add/update:
     - DockerHost production config check asserts key-pool file envs are available in API and worker.
     - Runbook text tests, if present, reject inline raw key examples.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_dockerhost_production_config.py -q` if present.
     - `envctl validate-template --dir /Users/chris/AiProject/world-cup-chat-server/dockerhost`
   - Rollback or compatibility note:
     - Existing `ZAI_API_KEY` secret-env deploy remains valid.

8. Focused and harness verification
   - Files/modules:
     - no new runtime files unless previous feedback requires changes.
   - Behavior change:
     - None.
   - Data contract impact:
     - None.
   - Tests to add/update:
     - Fix any coverage holes from review.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_provider_rate_limits.py tests/test_secret_management.py -q`
     - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_chat_routing.py tests/test_worker_provider_limits.py -q`
     - `VERIFY_ARTIFACT_DIR=/tmp/world-cup-chat-server-checks scripts/check_spec_contract.sh`
     - `VERIFY_ARTIFACT_DIR=/tmp/world-cup-chat-server-checks scripts/check_ai_boundaries.sh`
     - `make verify-release`
   - Rollback or compatibility note:
     - Report any skipped DockerHost or real-provider benchmark as a release blocker for capacity claims.

9. DockerHost benchmark after implementation
   - Files/modules:
     - `scripts/benchmark_realtime_ttft.py` if it needs a provider-key-pool label or output field.
     - `docs/evaluations/` only for reviewed benchmark reports, not raw scratch output.
   - Behavior change:
     - Produce real black-box evidence comparing single-key and multi-key deployment.
   - Data contract impact:
     - None.
   - Tests to add/update:
     - None unless benchmark script changes.
   - Verification command:
     - Single-key baseline: bounded run with current `ZAI_API_KEY`.
     - Multi-key run: same request count/concurrency after `ZAI_API_KEYS_FILE` deployment.
     - Inspect `/metrics` for key distribution and error/backoff counts.
   - Rollback or compatibility note:
     - If provider quota is account-level, publish result as failover-only and keep capacity claim at aggregate account limit.

## Risk Controls

- Public contract risks:
  - Do not add client-visible key controls.
  - Keep `/api/v1/wc2026/chat` envelope and stream event types unchanged.
- Money/accounting/security risks:
  - Do not treat multi-key as permission to bypass provider terms or account caps.
  - Do not commit real keys or secret-file contents.
  - Do not derive metrics labels from key values.
  - Keep provider limiter fail-closed in production.
- Migration/rebuild risks:
  - No DB migration; run-plan metadata is additive.
- Performance risks:
  - Per-run model construction may add overhead; measure TTFT after implementation.
  - Key-selection Redis script must be bounded and avoid per-key network round trips on hot path.
  - Aggregate account cap must remain configurable to prevent 429 storms.
- Deployment/test-branch risks:
  - DockerHost deploys from pushed refs; key-pool benchmark requires pushed code and mounted secret file.
  - One-shot DockerHost secrets must be passed again during redeploy/rollback if platform semantics require it.
- Unrelated local changes to avoid:
  - Do not stage `.artifacts/`, `.env`, private runbooks, generated benchmark scratch files, or unrelated evaluation reports.

## Completion Criteria

- Specification still matches implementation.
- Single-key fallback tests pass.
- Multi-key parser, limiter, settlement, model-construction, realtime, and worker tests pass.
- Readyz and metrics expose only safe key-pool status and key ids.
- DockerHost config validates and documents secret-file injection.
- `scripts/check_spec_contract.sh` and `scripts/check_ai_boundaries.sh` pass with artifacts written outside the repository for design/check-only runs.
- `make verify-release` passes before claiming release readiness.
- Real DockerHost benchmark evidence exists before claiming increased production concurrency.
