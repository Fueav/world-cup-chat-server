# 2026-06-30 World Cup Chat Permission Boundary Implementation Plan

- Spec ID: `SPEC-WORLDCUP-CHAT-PERMISSION-BOUNDARY-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Goal

Implement the Chat Server side of the WC2026 permission boundary:

- accept trusted upstream `wc2026_context`;
- reject locked current matches before any chat side effects;
- bind one `conversation_id` to one `current_match_id`;
- expose only a current-match WC2026 data tool to the Agent;
- expose and recover `user_uuid + match_id -> conversation_id`;
- avoid paid `match-context` calls for locked matches;
- mask paid blocks before any central payload reaches the LLM.

## Constraints

- Persist WC2026 conversation binding in the `conversation` table and keep run-plan binding only as a legacy fallback/audit copy.
- Do not expose a generic `get_match_context(match_id)` tool.
- Treat `wc2026_context.current_match.is_unlocked` as the authoritative chat entry permission.
- Do not hard-code central API keys.
- Do not persist raw full paid payloads in `ToolCallLog`.
- Preserve existing `ChatAccepted` response shape and route selection behavior.
- Add DB model/init SQL support for `conversation.wc2026_match_id` with a user+match index and partial uniqueness.

## Steps

1. Add focused RED tests.
   - `tests/test_wc2026_permissions.py`: unlock semantics and paid block masking.
   - `tests/test_wc2026_agent_data.py`: current-match-only central data adapter, locked no-call behavior, central API error fallback.
   - `tests/test_chat_routing.py`: request context preservation, locked-match entry rejection before side effects, idempotency hash, run plan context, conversation match conflict.
   - `tests/test_conversations_match_index.py` or conversation route tests: persisted `wc2026_match_id`, indexed `match_id` filtering, and chat auto-reuse by same user/match.
   - `tests/test_agent_factory.py` / orchestrator coverage: WC2026 tool registration and context injection.

2. Add request/context contracts.
   - Extend `ChatRequest` with optional `match_id` and `wc2026_context`.
   - Keep route-level validation strict: WC2026 chat rejects missing `wc2026_context.current_match_id`.
   - Reject `wc2026_context.current_match.is_unlocked=false` with 403 `WC2026_MATCH_LOCKED` before idempotency, conversation lookup, provider preflight, capacity reservation, run creation, or task enqueue.
   - Include `wc2026_context` in idempotency hashing.

3. Enforce API-side conversation binding.
   - Add `conversation.wc2026_match_id` to ORM, init SQL, and a standalone migration SQL file for existing databases.
   - Add repository helpers that read/write `conversation.wc2026_match_id`, with `AgentRun.plan.wc2026_context.current_match_id` as a legacy fallback.
   - Add repository helpers for user-scoped indexed `match_id -> conversation_id` lookup and conversation list projection.
   - Reject same-conversation match switches with 409 `WC2026_CONVERSATION_MATCH_CONFLICT`.
   - When no `conversation_id` is supplied, reuse the existing same-user same-match conversation before allocating a new id.
   - Use `user_uuid + match_id` pre-create locking for realtime first requests and DB unique-conflict recovery as the final duplicate-create guard.
   - Store `wc2026_match_id` on the conversation and sanitized `wc2026_context` in new run plans and dispatch payloads.

4. Extend conversation response/query contracts.
   - Add nullable `wc2026_match_id` to `ConversationOut`.
   - Include `wc2026_match_id` in `GET /api/v1/wc2026/conversations/{id}`.
   - Add optional `match_id` query to `GET /api/v1/wc2026/conversations`.
   - Keep filtering scoped to URL-derived `user_uuid`.

5. Implement permission and masking helpers.
   - Calculate unlock from `current_match.is_unlocked`; `entitlements.has_all` does not override a locked current match.
   - Keep public methodology/dimension labels/explanations/weights.
   - Mask match-specific paid values for Block B, Block D, and 9D scores.
   - Keep the defensive Block D mask aligned with central contract fields, including `polymarket_implied_probability`, `probability_gap_pp`, `decimal_odds`, and `expected_value`.

6. Implement central data adapter.
   - Add settings for central base URL, optional API key, and timeout.
   - Accept either origin-style base URLs such as `http://viki-api:8080` or API-base URLs such as `http://viki-api:8080/api/v1`.
   - Send `wc-api-key` only when `WC2026_AGENT_API_KEY` is configured.
   - Locked current match remains fail-closed and returns a structured locked result without calling `match-context` if the service is invoked directly; normal chat requests are rejected earlier.
   - Unlocked current match calls only `/agent/match-context/{current_match_id}`.
   - Add central methodology fetch for public method questions.
   - Central API failures return structured errors, never fabricate numbers, and never expose raw transport exception strings to the Agent.

7. Wire runtime and Agent.
   - Extend `RuntimeDeps` and `AgentDeps` with the WC2026 data client and context.
   - Add a current-match-only Agent tool with no arbitrary `match_id` argument.
   - Add a methodology Agent tool with no `match_id` argument.
   - Inject a per-run instruction describing the current match and current-match-only boundary.
   - Include the WC2026 tool in plan snapshots.

8. Update integration docs.
   - Update `docs/INTEGRATION_GUIDE.md` with required `wc2026_context`, current-match permission behavior, central methodology/match-context configuration, `wc2026_match_id`, `match_id` filtering, and chat auto-reuse semantics.

9. Verify.
   - Run focused pytest for new and touched tests.
   - Run spec/harness checks.
   - Run `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`.

## Expected Evidence

- RED/GREEN test output for focused tests.
- `scripts/check_spec_contract.sh` passes.
- `scripts/check_harness_workflows.sh` passes.
- `make verify-release` passes with required approval environment variables.
