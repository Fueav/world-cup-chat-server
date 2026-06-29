# 2026-06-29 Chat Conversation ID Consistency Specification

- Spec ID: `SPEC-CHAT-CONVERSATION-ID-CONSISTENCY-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- PRD/source request: Fix the severe bug where a fresh `POST /chat` response can return a `conversation_id` that is not the persisted conversation ID, causing `GET /conversations/{id}` to return 404.
- Target baseline: current `main` worktree.

## Context

- Current behavior: `POST /chat` pre-generates a `conversation_id` for `ChatAccepted`, but `Repos.ensure_conversation()` creates a missing conversation through `create_conversation()`, which generates another ID.
- Problem: clients that start a new chat and immediately fetch `/conversations/{returned_id}` can receive 404 even though the chat was accepted and persisted under another hidden conversation ID.
- Non-goals: changing SSE, WebSocket, run IDs, auth rules, idempotency key semantics, schemas, migrations, or conversation ownership rules.

## Product Semantics

- When `POST /chat` creates a new conversation, the response `conversation_id` is the same ID persisted in `conversation.id`.
- When `POST /chat` receives an existing accessible `conversation_id`, the existing conversation is reused unchanged.
- When repository code is asked to ensure a non-existent explicit conversation ID, it creates that exact ID instead of replacing it.
- Existing bad historical idempotency responses are not repaired by this code change; operational cleanup may backfill them from `agent_run.conversation_id` separately.

## API / Interface Contract

- `POST /chat` continues to return `202 ChatAccepted` with the same field names and types.
- For newly accepted chats, `GET /conversations/{conversation_id}` using the returned ID must return 200 for the same authenticated user after the acceptance transaction commits.
- `stream_url`, `ws_url`, and run status lookup remain keyed by `agent_run_id`.
- Existing `POST /conversations` behavior remains compatible and may still generate a new server-side conversation ID when no explicit ID is supplied by repository callers.

## Data / Schema / Projection Impact

- No migration or schema change.
- New rows in `conversation`, `message`, and `agent_run` must reference the same conversation ID for a chat acceptance.
- Historical rows are unchanged.

## Architecture

- `app/api/repos.py` owns the fix:
  - `create_conversation()` accepts an optional explicit `conversation_id`.
  - `ensure_conversation()` passes its requested ID into `create_conversation()` when creating a missing conversation.
- `app/api/routers/chat.py` does not need a response-shape change because the pre-generated ID becomes the persisted ID.
- Transaction behavior remains unchanged: user message, run, optional queued task, and conversation creation commit together.

## Harness Classification

- Expected gate: `HARNESS-SPEC-FIRST-FEATURE`.
- Harness mapping extension: not required.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_db_repositories.py -q`
  - `.venv/bin/python -m pytest tests/test_chat_routing.py -q`
  - `.venv/bin/python -m pytest tests/test_postgres_idempotency_integration.py -q`
- Prerelease-grade verification commands:
  - `make test`
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- `Repos.ensure_conversation("conv_requested", user)` persists and returns `id == "conv_requested"` when missing.
- A database-backed `POST /chat` response conversation ID can be used to fetch `/conversations/{id}`.
- Existing idempotency concurrency behavior remains covered.
- Release gate passes with explicit owner approval for the touched `app/api` path.

## Review Notes

- Accepted assumption: user approval in the chat authorizes the `app/api/repos.py` change required by `.ai-boundaries.yml`.
- Rejected alternative: rebuilding `ChatAccepted` from the post-create conversation object only, because the idempotency claim intentionally stores the accepted response before the conversation lock path and should retain the pre-generated ID as the single intended ID.
