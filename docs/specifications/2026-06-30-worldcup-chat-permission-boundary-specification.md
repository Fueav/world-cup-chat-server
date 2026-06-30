# 2026-06-30 World Cup Chat Permission Boundary Specification

- Spec ID: `SPEC-WORLDCUP-CHAT-PERMISSION-BOUNDARY-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: WC2026 Agent chat must use trusted `wc2026_context` from the upstream moss-api proxy and the teammate-provided central data APIs while preventing cross-match access and paid-content leakage.
- Target baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`, after `SPEC-WORLDCUP-AGENT-EFFECT-GUARDRAILS-001` and `SPEC-WORLDCUP-CENTRALIZED-AGENT-DATA-CONTRACT-001`.
- Current behavior:
  - Chat requests accept `message`, `stream`, `conversation_id`, and `metadata`.
  - Runtime behavior policy already says current-match numeric values must come from centralized match data and must not be fabricated.
  - There is no server-side `wc2026_context` schema, no conversation-to-match binding check, no WC2026 central-data client, no current-match-only tool, and no paid snapshot masking.
- Problem:
  - The central `match-context` API returns full internal payload for a match.
  - The upstream proxy provides current match and unlock context, but the Chat Server must enforce that only the current match can be queried and that paid data is only exposed when `current_match.is_unlocked=true`.
- Non-goals:
  - No wallet/account/real-money execution.
  - No frontend implementation.
  - Add a DB-backed conversation-match binding; `AgentRun.plan` remains only a legacy fallback/audit copy.
  - No center-service masking support required in this slice.

## Product Semantics

- User/operator workflow:
  - Frontend calls moss-api.
  - moss-api validates the user, drops any frontend-provided `wc2026_context`, and injects trusted `wc2026_context`.
  - Chat Server accepts the request only when `wc2026_context.current_match_id` is present.
  - A conversation is bound to its first `current_match_id`; later requests on the same `conversation_id` with another match are rejected.
  - When a chat request omits `conversation_id`, Chat Server first looks for an existing conversation owned by the same `user_uuid` and bound to `wc2026_context.current_match_id`; if found, it reuses that conversation instead of creating a duplicate.
  - Frontend can recover the same mapping through the conversations API: `user_uuid + match_id -> conversation_id`.
  - Agent tools only query the current match from trusted context, never a match id inferred from user text.
- State model:
  - `current_match.is_unlocked=true` means Block B model probability, Block D recommendation, and Power Index 9D numeric values are all unlocked for the current match.
  - `entitlements.has_all=true` also unlocks all three current-match paid blocks.
  - Methodology remains public in product terms, is fetched through the central methodology endpoint, and still requires service API key.
  - Central recommendation `market_side` is limited to 1X2 and exact-score best-edge markets: `home_win`, `away_win`, `draw`, `score_0_0` through `score_3_3`, and `score_any_other`.
  - Chat Server must not expect handicap, over, or under recommendation markets from the central match-context payload; missing odds for a selected best-edge market is represented as `missing_market_price`.
- Ownership and identity rules:
  - moss-api owns user authentication and trusted context injection.
  - Chat Server owns current-match-only enforcement, paid-block masking, and LLM/tool exposure.
  - Central data service owns raw model snapshots.
- Permissions/authentication:
  - Chat Server trusts `wc2026_context` only because it is not public and is called by moss-api over trusted infrastructure.
  - The central-data client must fail closed if base URL or API key is absent.
  - Raw full paid payload must not enter LLM context or persistent tool logs.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - Missing `wc2026_context` or `current_match_id` is rejected for WC2026 chat.
  - Same conversation with a different current match is rejected with a conflict.
  - Unlocked=false should not call paid `match-context`; methodology/public explanation remains allowed.
  - Central API errors produce structured tool errors, not fabricated values.
- Compatibility and migration expectations:
  - Existing non-WC2026 tests should continue to pass.
  - No schema migration is required.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - Existing `POST /api/v1/wc2026/chat` accepts a new top-level `wc2026_context` field.
  - Existing `GET /api/v1/wc2026/conversations` accepts optional query `match_id`.
  - Existing `GET /api/v1/wc2026/conversations/{conversation_id}` includes the conversation's bound WC2026 match id when present.
- Request fields and validation:
  - `wc2026_context.current_match_id`: required, string.
  - `wc2026_context.current_match`: required object with at least `id`, `description`, `home`, `away`, and `is_unlocked`.
  - `wc2026_context.entitlements.has_all`: optional boolean, default false.
  - `wc2026_context.entitlements.unlocked_matches` and `locked_matches`: optional arrays.
  - Client-supplied `match_id` is informational only and must not drive tools.
- Response/envelope fields and types:
  - `ChatAccepted` remains unchanged.
  - `ConversationOut` and `ConversationDetailOut` include nullable `wc2026_match_id`.
  - `GET /api/v1/wc2026/conversations?match_id=<id>` returns only conversations for the current `user_uuid` whose persisted `conversation.wc2026_match_id` matches that `match_id`, with run-plan binding used only as a legacy fallback.
- Status/error codes:
  - Missing or malformed `wc2026_context`: 422.
  - Existing conversation bound to another match: 409 `WC2026_CONVERSATION_MATCH_CONFLICT`.
- Backward compatibility:
  - This is a WC2026-specific route; clients must send upstream-injected context.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - Add nullable `conversation.wc2026_match_id`.
  - Add an index for `(user_id, wc2026_match_id)` and a partial unique index for non-null `wc2026_match_id` so one user can have at most one active WC2026 conversation per match.
  - Existing databases apply `app/db/migrations/2026-06-30-wc2026-conversation-match-binding.sql`; new DockerHost databases get the same shape from `app/db/init.sql`.
- Read models, projections, snapshots, caches:
  - Conversation binding is read from `conversation.wc2026_match_id`.
  - `AgentRun.plan.wc2026_context.current_match_id` remains an audit/legacy fallback for historical rows that predate the column.
  - `wc2026_match_id` in conversation responses is a read-only projection from the conversation row.
- Historical data behavior:
  - Conversations created before this spec without binding can receive their first binding on next accepted WC2026 chat request.
  - Conversations without a WC2026 binding return `wc2026_match_id=null` and are excluded from `match_id` filtered list queries.
- Performance-sensitive queries or write paths:
  - Binding check reads the conversation row directly.
  - `user_uuid + match_id` recovery uses the `(user_id, wc2026_match_id)` index; if multiple legacy historical conversations exist for one user and match, Chat Server returns/reuses the most recently updated fallback row.
  - DB uniqueness on `(user_id, wc2026_match_id)` is the final duplicate-create guard for same-user same-match conversations.
  - Realtime requests without an existing `conversation_id` acquire a pre-create lock by `user_uuid + current_match_id`.
  - Central-data calls must be time-bounded.

## Architecture

- Modules/files expected to change:
  - `app/core/schemas.py`: `Wc2026Context` request schema.
  - `app/core/models.py`, `app/db/init.sql`, and `app/db/migrations/2026-06-30-wc2026-conversation-match-binding.sql`: persisted conversation-match binding.
  - `app/api/idempotency.py`: include `wc2026_context` in request hash.
  - `app/api/repos.py`, `app/api/routers/chat.py`, and `app/api/routers/conversations.py`: validate context, enforce conversation-match binding, recover `user_uuid + match_id -> conversation_id`, and project `wc2026_match_id`.
  - `app/core/schemas.py`: expose nullable `wc2026_match_id` on conversation responses.
  - `app/runtime/wc2026_permissions.py`: current-match unlock calculation, masking, and cross-match helpers.
  - `app/runtime/wc2026_agent_data.py`: central API client, methodology adapter, and current-match-only tool adapter.
  - `app/runtime/deps.py`, `app/runtime/adapters.py`, `app/runtime/agent_factory.py`: inject context-aware WC2026 tool path.
  - Focused tests for schema, API routing, masking, adapter, and agent tool exposure.
- Data flow:
  - Chat request -> trusted `wc2026_context` -> DB conversation binding check -> run metadata -> runtime deps -> current-match-only WC2026 tool -> central data client -> masked tool result -> LLM.
  - Methodology question -> central methodology tool -> public method payload -> LLM.
  - Conversation recovery -> `user_uuid` and optional `match_id` -> indexed conversation lookup -> frontend receives `conversation_id` and `wc2026_match_id`.
- Transaction/concurrency boundaries:
  - Binding is checked before run creation.
  - Locking uses the existing conversation lock for active run exclusivity and a pre-create `user_uuid + current_match_id` lock for realtime requests before the conversation row exists.
- Observability/logging/metrics:
  - Tool logs may include `match_id`, `data_status`, `missing_fields`, and masked block states.
  - Tool logs must not include raw full paid payload for locked users.
- Rollback strategy:
  - Revert schema, API, runtime adapter, tool, and tests.
  - DB rollback, if needed, drops `uq_conversation_user_wc2026_match`,
    `ix_conversation_user_wc2026_match`, and `conversation.wc2026_match_id`
    after confirming no active release depends on match recovery.

## Harness Classification

- Expected gate(s):
  - `HARNESS-SPEC-FIRST-FEATURE`
  - `scripts/check_ai_boundaries.sh`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
  - Focused pytest and `make verify-release`
- Performance-sensitive class:
  - API acceptance path and tool read path.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Focused tests must verify no central paid call is made when current match is locked.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_wc2026_permissions.py tests/test_wc2026_agent_data.py tests/test_chat_routing.py tests/test_agent_factory.py -q`
  - `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
- Prerelease-grade verification commands:
  - `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - `ChatRequest` preserves and validates server-injected `wc2026_context`.
  - Idempotency hash includes `wc2026_context`.
  - Same conversation cannot switch `current_match_id`.
  - Conversation-match binding is persisted on `conversation.wc2026_match_id` for new WC2026 conversations.
  - When no `conversation_id` is supplied, Chat Server reuses an existing same-user same-match conversation before creating a new one.
  - Concurrent same-user same-match creation cannot produce duplicate active WC2026 conversations.
  - Conversation list/detail responses expose `wc2026_match_id`.
  - Conversation list supports `match_id` filtering scoped to the current `user_uuid`.
  - Agent-facing WC2026 match-context tool accepts no arbitrary match id and always uses the current context match id.
  - Agent-facing WC2026 methodology tool accepts no match id and fetches the central methodology endpoint.
  - Locked current match does not call paid `match-context`.
  - Unlocked current match may call paid `match-context`.
  - Full central payload is masked before returning to the LLM or tool logs.
  - Locked recommendation masking covers central contract fields including `polymarket_implied_probability`, `probability_gap_pp`, `decimal_odds`, and `expected_value`.
  - Power Index 9D numeric scores are masked when locked; dimensions/labels/weights remain public.
- Edge cases:
  - `entitlements.has_all=true` unlocks paid blocks.
  - `current_match.is_unlocked=true` unlocks all paid blocks for the current match.
  - Missing or malformed context is rejected.
  - Central API unavailable produces no fabricated match numbers.
  - Central API transport failures return fixed `WC2026_AGENT_DATA_UNAVAILABLE` tool messages instead of raw exception strings.
  - Cross-match user prompts cannot force another match id into tool calls.
- Compatibility:
  - `ChatAccepted` response shape remains unchanged.
  - Existing route selection and provider preflight behavior remain intact.
  - `ConversationOut` adds a nullable field; existing clients that ignore unknown JSON fields remain compatible.
- Operational:
  - No raw full paid payload is written to `ToolCallLog`.
  - DockerHost api/worker services can receive `WC2026_AGENT_API_BASE_URL`, `WC2026_AGENT_API_KEY`, and `WC2026_AGENT_API_TIMEOUT_S`.
  - API key is read from configuration, never hard-coded.
- Evidence artifacts:
  - This specification and matching implementation plan.
  - RED/GREEN focused tests.
  - Release harness summary.

## Review Notes

- Open questions:
  - None for recommendation market enums: upstream confirmed no handicap, over, under, `unsupported_market`, or `negative_or_out_of_scope_handicap` values in the current full-payload interface.
- Accepted assumptions:
  - Chat Server is not directly public and only receives moss-api-injected context.
  - One match unlock unlocks Block B, Block D, and Power Index 9D for that match.
  - One conversation id binds to one match id.
  - 9D dimension names, explanations, and weight table are public; match-specific 9D scores are paid.
- Rejected alternatives:
  - Do not expose `get_match_context(match_id)` to the LLM.
  - Do not rely on prompt-only paid-content masking.
  - Do not keep `AgentRun.plan` as the primary binding source once indexed conversation lookup is required by the frontend recovery flow.
- Reviewer findings and resolution:
  - Pending implementation review.
