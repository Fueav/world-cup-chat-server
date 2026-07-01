# 2026-07-01 Provider API Key Pool Concurrency Specification

## Context

- Spec ID: `SPEC-PROVIDER-KEY-POOL-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Related specifications:
  - `SPEC-PROVIDER-RATELIMIT-001`: provider/model Redis RPM/TPM admission control.
  - `SPEC-SECRET-MANAGEMENT-001`: provider secrets are deployment-injected and never committed, logged, or persisted.
  - `SPEC-ZAI-GLM52-001`: first-class Z.AI GLM-5.2 provider identity and DockerHost configuration.
- PRD/source request: Increase real-model chat concurrency by using multiple authorized provider API keys instead of one `ZAI_API_KEY`.
- Target baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server` at current DockerHost shape: one API process, realtime chat default, Z.AI `glm-5.2`, provider limiter enabled.
- Current behavior:
  - Runtime accepts one provider key per provider, for example `ZAI_API_KEY` or `ZAI_API_KEY_FILE`.
  - `SettingsSecretProvider.get_secret()` returns a single `SecretValue`.
  - `AgentOrchestrator.__init__()` builds a Pydantic AI `Agent` and model before a run-specific provider quota decision is made.
  - `ProviderLimitRequest` and Redis limiter bucket are keyed by `(provider, model)` only, e.g. `ratelimit:provider:zai:glm-5.2`.
  - Current DockerHost Z.AI limits are operator-configured as `PROVIDER_DEFAULT_RPM=60` and `PROVIDER_DEFAULT_TPM=60000`, so `8192` output-token reservations cap concurrent cold-start realtime calls to roughly seven even though the API runner limit is higher.
- Problem:
  - Adding more raw keys to deployment config would not improve throughput unless key selection, quota admission, model construction, error backoff, and usage settlement all operate on the same selected key slot.
  - If limiter remains provider/model-wide, multiple keys still behave like one shared bucket.
  - If model construction remains process-level or orchestrator-level, requests cannot safely use different keys per run.
  - If provider account limits are account-wide rather than key-level, naive key pooling can create more 429s without increasing real capacity.
- Non-goals:
  - Do not bypass provider terms, account-level limits, or anti-abuse controls.
  - Do not store raw provider keys in Postgres, Redis, logs, metrics, docs, tests, release artifacts, or run plans.
  - Do not expose key values, key prefixes, key hashes, or mounted secret file contents through API responses.
  - Do not implement billing or per-user paid quota.
  - Do not implement an exact tokenizer in this change.
  - Do not make frontend changes.
  - Do not require a database migration.

## Product Semantics

- User/operator workflow:
  - Existing single-key deployment remains valid: `ZAI_API_KEY` or `ZAI_API_KEY_FILE` creates one implicit key slot.
  - To enable key pooling, the operator supplies a mounted secret file with multiple keys, preferably `ZAI_API_KEYS_FILE` for Z.AI or the provider-neutral `PROVIDER_KEY_POOL_FILE`.
  - The operator configures whether those keys have independent provider quota. If key-level independence is not confirmed, the service must keep an aggregate provider/model cap and must not claim higher capacity.
  - The operator may disable a specific key slot by editing the secret file and redeploying, or by setting an optional disabled flag in the JSON form.
  - The API and worker choose a key slot automatically; clients do not pass or see provider keys.
- Key pool file formats:
  - Preferred provider-neutral JSON secret file:

    ```json
    {
      "version": 1,
      "providers": {
        "zai:glm-5.2": {
          "scope": "key",
          "aggregate_rpm": 180,
          "aggregate_tpm": 180000,
          "keys": [
            {"id": "zai-slot-01", "api_key": "<secret>", "rpm": 60, "tpm": 60000},
            {"id": "zai-slot-02", "api_key": "<secret>", "rpm": 60, "tpm": 60000},
            {"id": "zai-slot-03", "api_key": "<secret>", "rpm": 60, "tpm": 60000}
          ]
        }
      }
    }
    ```

  - Provider-specific convenience file `ZAI_API_KEYS_FILE` may be either:
    - a JSON array of strings;
    - a JSON object with `keys`;
    - newline-delimited raw keys, ignoring blank lines and lines starting with `#`.
  - Raw-key convenience files generate stable slot ids from order only, such as `zai-k001`. Slot ids must never be derived from the key value.
  - JSON slot ids must be unique, lower-case safe labels after normalization, and must not include any secret-like material.
- State model:
  - `ProviderKeySlot`:
    - `provider`, `model`, `key_id`, `SecretValue`, `rpm`, `tpm`, `max_output_tokens`, `enabled`, optional `weight`.
  - `ProviderKeyDecision`:
    - `ALLOWED`: a specific `key_id` was selected and quota was reserved.
    - `RATE_LIMITED`: all eligible keys are out of RPM/TPM budget, with minimum retry-after.
    - `BACKING_OFF`: all eligible keys, or the aggregate provider/model, are in backoff.
    - `CONFIG_MISSING`: no enabled key slot is configured for the selected provider/model.
    - `UNAVAILABLE`: Redis limiter or key-pool config is unavailable and production must fail closed.
  - `ProviderKeyLease`:
    - In-memory object containing selected `key_id`, reserved token budget, route type, and a `SecretValue` for model construction.
    - Lease values may be passed within the process and task payload only as `key_id` and reservation metadata; raw secret value must not be serialized.
- Ownership and identity rules:
  - Provider API keys are service credentials, not user credentials.
  - Clients cannot select provider keys, override key ids, or observe exact key values.
  - User-level rate limiting remains separate and still applies.
  - Provider key slots are scoped by canonical `(provider, model, key_id)`.
  - Aggregate provider/model limits still exist when configured, so a deployment can model account-level caps even with many keys.
- Permissions/authentication:
  - No new public auth surface.
  - Only deployment-time secrets and environment configuration control key pool membership.
  - `/readyz` may expose `provider_key_pool=configured|single|missing|mock|partial` and a slot count, but never key material.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - `mock` provider bypasses key pool and provider quota.
  - If no key pool file exists, existing single-key behavior is preserved as a one-slot pool.
  - If a key pool file exists but has zero enabled valid keys for the selected provider/model, startup fails for real providers with sanitized `PROVIDER_KEY_POOL_MISSING`.
  - If a key slot is syntactically invalid or missing `api_key`, startup fails unless the slot is explicitly disabled.
  - If duplicate normalized key ids exist, startup fails.
  - If key-level independence is not confirmed:
    - default `scope` is `account`;
    - the aggregate bucket defaults to existing provider/model RPM/TPM values;
    - multiple keys provide failover/backoff isolation only, not advertised throughput increase.
  - If `scope=key`, each enabled key gets its own bucket and the aggregate bucket defaults to the sum of enabled key limits unless `aggregate_rpm`/`aggregate_tpm` is explicitly lower.
  - Provider 429/5xx after a selected key:
    - records backoff for that `key_id`;
    - may also record aggregate backoff when provider response or repeated cross-key failures indicate account-level throttling.
  - Usage settlement applies to the same selected key slot and the aggregate bucket, using actual usage when available.
  - If a selected key's provider call fails before any provider request is made, no provider error backoff is recorded; existing run error handling applies.
  - If key-pool Redis operations fail in production, fail closed with sanitized `PROVIDER_LIMITER_UNAVAILABLE`.
- Compatibility and migration expectations:
  - Existing single-key env variables remain valid.
  - Existing provider limiter metrics remain available; new key-level metrics are additive.
  - Existing `/api/v1/wc2026/chat` request/response envelopes remain unchanged.
  - Existing run rows are not backfilled.
  - DockerHost may continue to deploy with one `ZAI_API_KEY` until a key-pool secret file is supplied.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - No new public chat request fields.
  - `POST /api/v1/wc2026/chat` keeps existing `202`, `429`, and `503` semantics.
  - `GET /readyz` may include:
    - `provider_key_pool`: `mock|single|configured|missing|partial`.
    - `provider_key_slots`: integer count of enabled slots.
    - `provider_key_scope`: `account|key|hybrid` when real provider is configured.
  - DockerHost environment accepts:
    - `PROVIDER_KEY_POOL_FILE`
    - `ZAI_API_KEYS_FILE`
    - optional `PROVIDER_KEY_POOL_SCOPE`
    - optional `PROVIDER_KEY_POOL_STRATEGY`
- Request fields and validation:
  - No client-supplied key id, provider key, or key-pool override is accepted.
  - Existing `metadata.mode` behavior remains.
- Response/envelope fields and types:
  - `ChatAccepted` remains unchanged.
  - `agent_run.plan` may record sanitized provider metadata:
    - `provider`
    - `model`
    - `provider_key_id`
    - `provider_key_pool_scope`
    - `provider_limit_key`
    - `degraded`
    - `degraded_reason`
  - Raw key values, key prefixes, key hashes, and secret file paths must not be persisted.
- Status/error codes:
  - `429 PROVIDER_RATE_LIMITED`: no key slot can currently admit the request.
  - `503 PROVIDER_KEY_POOL_MISSING`: real provider selected but no usable key slot exists.
  - `503 PROVIDER_LIMITER_UNAVAILABLE`: Redis key-pool limiter unavailable and fail-open is not allowed.
  - Existing `PROVIDER_SECRET_MISSING` remains valid for single-key fallback.
- Events:
  - If a run has already been accepted and all key slots become unavailable, emit existing `ERROR`:
    - `stage="provider_rate_limit"`
    - `error="PROVIDER_RATE_LIMITED"`
    - optional `retry_after_ms`
  - Do not emit provider key values.
- Backward compatibility:
  - No event type is removed.
  - Existing clients continue to read the same stream and run status endpoints.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - None.
- Read models, projections, snapshots, caches:
  - Redis key-level bucket:
    - `ratelimit:provider:{provider}:{model}:key:{key_id}`
  - Redis aggregate bucket:
    - `ratelimit:provider:{provider}:{model}:aggregate`
  - Redis key-level backoff:
    - `backoff:provider:{provider}:{model}:key:{key_id}`
  - Redis aggregate backoff:
    - `backoff:provider:{provider}:{model}:aggregate`
  - Optional round-robin cursor:
    - `cursor:provider:{provider}:{model}:keys`
  - Redis values must contain only quota counters, timestamps, reason labels, and safe key ids.
- Rebuild or cleanup operators:
  - Add or document an operator-safe way to clear limiter/backoff keys for a provider/model or a specific key id after misconfiguration.
  - Cleanup commands must not print secrets.
- Historical data behavior:
  - Existing runs without `provider_key_id` remain readable.
  - Missing key id on historical rows means "pre-key-pool or unknown", not an error.
- Performance-sensitive queries or write paths:
  - Key selection and reservation must be one Redis round trip for the normal path.
  - Selection must not make one Redis call per key under normal load if key count is small-to-moderate.
  - No DB connection may be held while waiting for provider quota.
  - Limiter must run per model request, not per streamed token.

## Architecture

- Modules/files expected to change:
  - `app/core/config.py`
  - `app/core/secrets.py`
  - `app/runtime/provider_keys.py` or equivalent new module
  - `app/runtime/provider_limits.py`
  - `app/runtime/agent_factory.py`
  - `app/runtime/orchestrator.py`
  - `app/runtime/deps.py`
  - `app/api/lifespan.py`
  - `app/api/routers/chat.py`
  - `app/tasks/agent_tasks.py`
  - `app/llm/providers.py`
  - `dockerhost/compose.yaml`
  - `dockerhost/env.example`
  - `docs/DOCKERHOST_RELEASE_RUNBOOK.md`
  - focused tests under `tests/`
- Data flow:
  1. Startup loads settings and builds the secret provider.
  2. Secret provider parses single-key fallback and key-pool files into `ProviderKeySlot` objects.
  3. Startup validates the selected real provider has at least one enabled slot.
  4. API preflight checks whether any slot is likely available without exposing or persisting a secret.
  5. Realtime runner or Celery worker performs authoritative acquire before the Pydantic AI model is constructed.
  6. Acquire selects a key slot and reserves RPM/TPM on that slot and on the aggregate bucket when enabled.
  7. Orchestrator builds the Pydantic AI model for the selected key slot, runs the agent, then settles actual usage against the same slot and aggregate bucket.
  8. Provider errors record key-level or aggregate backoff using the selected `key_id`.
- Key selection strategy:
  - Default strategy: `least_wait_round_robin`.
  - Eligible keys are enabled slots that are not in key-level backoff and whose bucket can satisfy the request.
  - When multiple keys can satisfy the request, rotate by cursor to avoid hot-spotting the first key.
  - When no key can satisfy the request, return the minimum retry-after across key and aggregate candidates.
  - Optional future strategies, such as weighted round-robin or least-used, must keep the same safety semantics.
- Transaction/concurrency boundaries:
  - Provider key selection and token reservation must be atomic across concurrent API and worker processes.
  - The same selected `key_id` must be used for model construction, error backoff, and usage settlement.
  - `AgentOrchestrator` must not eagerly construct a real provider model at object initialization; real model construction must be run-scoped after key acquisition.
  - Tests may still inject a fake `Agent`.
  - API preflight is advisory; authoritative acquire is still in the runner/worker path.
  - A key lease is process-local and short-lived. It must not serialize raw secrets into Celery payloads, Redis, or Postgres.
  - Batch workers must acquire their own key slot when executing the task, not at enqueue time.
- Observability/logging/metrics:
  - Existing metrics remain:
    - `provider_rate_limit_decisions_total{provider,model,route_type,reason}`
    - `provider_rate_limit_tokens_reserved_total{provider,model,route_type}`
    - `provider_rate_limit_tokens_settled_total{provider,model,route_type}`
  - Add key-level metrics:
    - `provider_key_pool_slots{provider,model,scope}`
    - `provider_key_rate_limit_decisions_total{provider,model,key_id,route_type,reason}`
    - `provider_key_tokens_reserved_total{provider,model,key_id,route_type}`
    - `provider_key_tokens_settled_total{provider,model,key_id,route_type}`
    - `provider_key_backoff_total{provider,model,key_id,reason}`
    - `provider_key_pool_exhausted_total{provider,model,route_type,reason}`
  - `key_id` is a safe slot label, never a key prefix, suffix, hash, or value-derived label.
  - Logs may include provider/model/key_id/reason/retry_after_ms, never secret values or secret file content.
- Rollback strategy:
  - Remove `PROVIDER_KEY_POOL_FILE` / `ZAI_API_KEYS_FILE` and keep `ZAI_API_KEY` to return to single-key behavior.
  - Set `LLM_PROVIDER=mock` for provider outage rollback.
  - Set `PROVIDER_KEY_POOL_SCOPE=account` and aggregate limits equal to the old single-key limits if multi-key causes unexpected provider throttling.
  - Existing single-key limiter path must remain available until key-pool smoke and release gates pass.

## Harness Classification

- Expected gate(s):
  - `ai_boundaries`
  - `spec_contract`
  - focused secret/key-pool/provider limiter tests
  - focused chat routing tests
  - DockerHost config validation
  - `make verify-release` before release/deploy claims
- Performance-sensitive class:
  - Provider hot path and realtime TTFT/concurrency path.
- Whether harness mapping must be extended:
  - No new workflow class required.
- Required performance evidence:
  - Unit evidence proving multiple key buckets allow higher admitted concurrency than single bucket when `scope=key`.
  - DockerHost black-box benchmark comparing single-key and multi-key deployments with bounded request counts.
  - Metrics evidence showing requests distributed across key ids and no secret leakage.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_provider_key_pool.py tests/test_provider_rate_limits.py tests/test_secret_management.py -q`
  - `.venv/bin/python -m pytest tests/test_agent_factory.py tests/test_chat_routing.py tests/test_worker_provider_limits.py -q`
  - `VERIFY_ARTIFACT_DIR=/tmp/world-cup-chat-server-checks scripts/check_spec_contract.sh`
  - `VERIFY_ARTIFACT_DIR=/tmp/world-cup-chat-server-checks scripts/check_ai_boundaries.sh`
- Prerelease-grade verification commands:
  - `make verify-release`
  - `envctl validate-template --dir /Users/chris/AiProject/world-cup-chat-server/dockerhost`
  - bounded DockerHost live concurrency benchmark with real provider keys.

## Acceptance Criteria

- Functional:
  - Single-key deployment remains compatible and behaves as a one-slot pool.
  - `ZAI_API_KEYS_FILE` or `PROVIDER_KEY_POOL_FILE` can configure two or more Z.AI key slots without committing secrets.
  - Startup fails sanitized when real provider has no valid key slot.
  - Realtime and batch paths share the same key-pool limiter.
  - A run uses the selected `key_id` for quota acquire, model construction, provider error backoff, and usage settlement.
  - With three independent `60 RPM / 60000 TPM` slots and `scope=key`, the limiter can admit roughly three times the single-key reservation budget, subject to aggregate cap.
- Edge cases:
  - Duplicate key ids fail startup.
  - Disabled slots are ignored.
  - One slot in backoff does not block other healthy slots unless aggregate backoff is active.
  - All slots exhausted returns `429 PROVIDER_RATE_LIMITED` with bounded retry-after semantics.
  - Provider key pool with `scope=account` does not advertise increased throughput.
  - Secret parsing supports JSON and newline convenience files without logging raw values.
- Compatibility:
  - Existing `ZAI_API_KEY` / `ZAI_API_KEY_FILE` deployments still work.
  - Existing public chat API and stream events remain unchanged.
  - Mock provider remains zero-key.
- Operational:
  - DockerHost examples show how to pass a key-pool secret file without inline secret values.
  - `/readyz` shows safe key-pool status and slot count.
  - Metrics show key-id distribution without secret-derived labels.
  - Release runbook explains that provider key-level independence must be confirmed before claiming higher capacity.
- Evidence artifacts:
  - Focused test output.
  - Spec/AI boundary gate outputs outside the repository artifact path when running design-only checks.
  - Future DockerHost benchmark report after implementation.

## Review Notes

- Open questions:
  - Confirm whether Z.AI quota is enforced per API key or per account/project. If it is account-level, multi-key only improves failover/backoff isolation, not throughput.
  - Confirm DockerHost `--secret-file` runtime mapping for `ZAI_API_KEYS_FILE` before implementation.
  - Decide whether initial implementation should support only Z.AI or all OpenAI-compatible providers through the provider-neutral file. This spec favors a provider-neutral core with Z.AI as the first exercised path.
- Accepted assumptions:
  - Multiple authorized keys can be mounted in one secret file outside the repository.
  - Slot ids can be operator-defined safe labels or generated from file order.
  - Conservative token reservation remains acceptable until tokenizer precision improves.
  - API preflight can remain advisory as long as runner/worker acquire is authoritative.
- Rejected alternatives:
  - Rejected comma-separated raw keys in a normal environment variable because it is harder to rotate safely and more likely to leak through shell/process inspection.
  - Rejected deriving `key_id` from a hash or prefix of the secret because value-derived labels are unnecessary and can become sensitive.
  - Rejected provider/model-only limiter with multiple keys because it cannot increase admitted concurrency.
  - Rejected constructing one global Pydantic AI model at startup because it fixes one key for all runs.
  - Rejected client-selected key ids because provider keys are service credentials, not user-visible routing controls.
