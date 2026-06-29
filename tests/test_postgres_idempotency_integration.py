from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.api.repos import Repos
from app.core.models import AgentRun, Conversation, IdempotencyRecord
from app.db.seed import _ensure_tables
from app.db.session import async_session_factory, engine


pytestmark = pytest.mark.postgres


async def _require_postgres_schema() -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("Postgres-only idempotency concurrency semantics")
    try:
        await asyncio.wait_for(_ensure_tables(), timeout=3)
    except Exception as exc:  # pragma: no cover - depends on local services
        pytest.skip(f"Postgres is not reachable: {exc}")


async def test_postgres_idempotency_claim_blocks_then_replays_duplicate_key():
    """Postgres unique index waits on an uncommitted duplicate insert.

    SQLite unit tests cannot prove this behavior; the C1 fix relies on PG
    blocking the second INSERT until the first transaction commits or rolls back.
    """
    await _require_postgres_schema()
    suffix = uuid4().hex
    user_id = f"user_pg_idem_{suffix}"
    idem_key = f"key_{suffix}"
    conv_id = f"conv_pg_idem_{suffix}"
    run_id = f"run_pg_idem_{suffix}"
    duplicate_run_id = f"run_pg_idem_dup_{suffix}"
    response = {
        "conversation_id": conv_id,
        "agent_run_id": run_id,
        "trace_id": f"trace_{suffix}",
        "status": "PENDING",
        "stream_url": f"/stream/{run_id}",
        "ws_url": f"/ws/{run_id}",
        "route_type": "realtime",
    }

    async def duplicate_claim():
        async with async_session_factory() as session:
            repos = Repos(session)
            return await repos.claim_idempotency_record(
                record_id=f"idem_dup_{suffix}",
                user_id=user_id,
                idempotency_key=idem_key,
                agent_run_id=duplicate_run_id,
                request_hash="hash-1",
                response={**response, "agent_run_id": duplicate_run_id},
            )

    try:
        async with async_session_factory() as first_session:
            repos = Repos(first_session)
            record, created = await repos.claim_idempotency_record(
                record_id=f"idem_{suffix}",
                user_id=user_id,
                idempotency_key=idem_key,
                agent_run_id=run_id,
                request_hash="hash-1",
                response=response,
            )
            assert created is True
            assert record.agent_run_id == run_id

            task = asyncio.create_task(duplicate_claim())
            done, _ = await asyncio.wait({task}, timeout=0.2)
            assert not done, "duplicate INSERT should block on PG unique index"

            conversation = await repos.ensure_conversation(conv_id, user_id)
            assert conversation.id == conv_id
            await repos.create_run(
                run_id,
                conversation.id,
                f"trace_{suffix}",
                plan={"route_type": "realtime"},
            )
            await first_session.commit()

        replay_record, replay_created = await asyncio.wait_for(task, timeout=3)

        assert replay_created is False
        assert replay_record.agent_run_id == run_id
        assert replay_record.response["agent_run_id"] == run_id
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.user_id == user_id,
                    IdempotencyRecord.idempotency_key == idem_key,
                )
            )
            await cleanup.execute(
                delete(AgentRun).where(
                    AgentRun.id.in_([run_id, duplicate_run_id])
                )
            )
            await cleanup.execute(
                delete(Conversation).where(Conversation.id == conv_id)
            )
            await cleanup.commit()
