# 2026-06-29 Chat Conversation ID Consistency Implementation Plan

- Specification: `docs/specifications/2026-06-29-chat-conversation-id-consistency-specification.md`
- Spec ID: `SPEC-CHAT-CONVERSATION-ID-CONSISTENCY-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: current `main` worktree.
- Scope summary: Ensure new chat conversation IDs are stable across `POST /chat`, persisted conversation records, messages, runs, and `/conversations/{id}` reads.
- Out of scope: migrations, historical bad idempotency response repair, SSE/WS protocol changes, provider/runtime scheduling changes.

## Change Steps

1. Add repository contract coverage.
   - Files/modules: `tests/test_db_repositories.py`.
   - Behavior change: express that `Repos.ensure_conversation(requested_id, user)` creates `requested_id` when missing.
   - Data contract impact: none beyond conversation primary-key consistency.
   - Verification command: `.venv/bin/python -m pytest tests/test_db_repositories.py::test_api_repos_ensure_conversation_creates_with_requested_id -q`.
   - Rollback note: removing this test would reopen the original bug class.

2. Fix API repository creation behavior.
   - Files/modules: `app/api/repos.py`.
   - Behavior change: `create_conversation()` accepts optional `conversation_id`; `ensure_conversation()` passes through explicit missing IDs.
   - Data contract impact: new rows now use the caller-intended ID instead of an unrelated generated ID.
   - Verification command: `.venv/bin/python -m pytest tests/test_db_repositories.py -q`.
   - Rollback note: revert this file and the tests together only if an alternate contract-preserving fix replaces it.

3. Add API-level and idempotency regression coverage.
   - Files/modules: `tests/test_chat_routing.py`, `tests/test_postgres_idempotency_integration.py`.
   - Behavior change: prove `POST /chat` returned ID can read `/conversations/{id}` in database-backed environments; assert PG idempotency setup uses the requested conversation ID.
   - Data contract impact: none.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_postgres_idempotency_integration.py -q`.
   - Rollback note: keep at least one database-backed regression before changing the repository contract again.

4. Run release verification.
   - Files/modules: release harness only.
   - Behavior change: none.
   - Verification command: `AI_BOUNDARY_APPROVED=1 make verify-release`.
   - Rollback note: if release gate fails for behavior, fix before submit; if it fails for unavailable local optional tools, report explicitly.

## Risk Controls

- Public contract risk: response field names and status codes are unchanged.
- Persistence risk: no schema change; the primary-key value for newly created explicit conversations changes to the intended ID.
- Idempotency risk: new idempotency records cache the correct ID; old cached bad responses require separate data cleanup if needed.
- Performance risk: no additional query path or transaction boundary.
- Unrelated local changes to avoid: do not stage `.artifacts/`, local env files, or release logs.

## Completion Criteria

- `SPEC-CHAT-CONVERSATION-ID-CONSISTENCY-001` matches implementation.
- Focused regression tests pass.
- `make test` passes.
- `AI_BOUNDARY_APPROVED=1 make verify-release` passes or any blocker is reported with log evidence.
