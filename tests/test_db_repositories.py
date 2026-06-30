from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.exc import IntegrityError

from app.api.repos import Repos
from app.core.enums import RunStatus
from app.core.models import Conversation, IdempotencyRecord, Message, ToolCallLog
from app.core.schemas import ChatAccepted


class _MemorySession:
    def __init__(self) -> None:
        self.rows = {}
        self.added = []

    async def get(self, model, key):
        return self.rows.get(key)

    def add(self, entity) -> None:
        self.added.append(entity)
        self.rows[entity.id] = entity

    async def flush(self) -> None:
        return None

    async def refresh(self, entity) -> None:
        return None


def test_message_model_binds_assistant_message_to_agent_run():
    assert "agent_run_id" in Message.__table__.columns
    assert Message.__table__.columns["agent_run_id"].nullable is True


def test_conversation_model_persists_wc2026_match_binding():
    columns = Conversation.__table__.columns
    model_indexes = {
        index.name: index
        for index in Conversation.__table__.indexes
        if isinstance(index, Index)
    }
    indexes = {
        name: tuple(index.columns.keys()) for name, index in model_indexes.items()
    }

    assert "wc2026_match_id" in columns
    assert columns["wc2026_match_id"].nullable is True
    assert indexes["ix_conversation_user_wc2026_match"] == (
        "user_id",
        "wc2026_match_id",
    )
    assert indexes["uq_conversation_user_wc2026_match"] == (
        "user_id",
        "wc2026_match_id",
    )
    assert model_indexes["uq_conversation_user_wc2026_match"].unique is True
    assert (
        str(model_indexes["uq_conversation_user_wc2026_match"].dialect_options["postgresql"]["where"])
        == "wc2026_match_id IS NOT NULL"
    )


def test_idempotency_record_has_unique_user_key_contract():
    columns = IdempotencyRecord.__table__.columns

    assert {"user_id", "idempotency_key", "agent_run_id", "request_hash"}.issubset(
        set(columns.keys())
    )
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in IdempotencyRecord.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("user_id", "idempotency_key") in unique_columns


def test_idempotency_agent_run_fk_is_deferrable_for_pre_lock_claim():
    agent_run_fk = next(
        fk
        for fk in IdempotencyRecord.__table__.columns["agent_run_id"].foreign_keys
        if fk.column.table.name == "agent_run"
    )

    assert agent_run_fk.deferrable is True
    assert agent_run_fk.initially == "DEFERRED"


def test_chat_accepted_supports_optional_route_type():
    accepted = ChatAccepted(
        conversation_id="conv-1",
        agent_run_id="run-1",
        trace_id="trace-1",
        status=RunStatus.PENDING,
        stream_url="/stream/run-1",
        ws_url="/ws/run-1",
        route_type="realtime",
    )

    assert accepted.model_dump()["route_type"] == "realtime"


def test_tool_call_log_has_attempt_and_timing_fields():
    columns = ToolCallLog.__table__.columns

    assert "attempt" in columns
    assert "started_at" in columns
    assert "finished_at" in columns


async def test_api_repos_ensure_conversation_creates_with_requested_id():
    session = _MemorySession()
    repos = Repos(session)

    conversation = await repos.ensure_conversation(
        "conv_requested", "user-1", wc2026_match_id="75"
    )

    assert conversation.id == "conv_requested"
    assert conversation.wc2026_match_id == "75"
    assert await repos.get_conversation("conv_requested") is conversation


async def test_api_repos_ensure_conversation_recovers_wc2026_unique_conflict():
    existing = Conversation(
        id="conv-existing-75",
        user_id="user-1",
        wc2026_match_id="75",
    )

    class _ConflictSession(_MemorySession):
        def __init__(self) -> None:
            super().__init__()
            self.rolled_back = False

        async def flush(self) -> None:
            raise IntegrityError(
                "insert conversation",
                {},
                Exception("duplicate user match"),
            )

        async def rollback(self) -> None:
            self.rolled_back = True

    class _Repos(Repos):
        async def get_wc2026_conversation_id_by_match(self, user_id, match_id):
            assert user_id == "user-1"
            assert match_id == "75"
            return "conv-existing-75"

        async def get_conversation(self, conversation_id):
            if conversation_id == "conv-racing-request":
                return None
            assert conversation_id == "conv-existing-75"
            return existing

    session = _ConflictSession()
    repos = _Repos(session)

    conversation = await repos.ensure_conversation(
        "conv-racing-request", "user-1", wc2026_match_id="75"
    )

    assert session.rolled_back is True
    assert conversation is existing
