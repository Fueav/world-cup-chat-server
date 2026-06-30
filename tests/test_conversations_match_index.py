from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


def _conversation(conversation_id: str, *, user_id: str = "user-1"):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=conversation_id,
        user_id=user_id,
        title=None,
        created_at=now,
        updated_at=now,
        messages=[],
    )


async def test_list_conversations_includes_wc2026_match_id_and_filters_by_match():
    from app.api.routers.conversations import list_conversations

    class _Repos:
        async def list_conversations_with_wc2026_match_id(
            self, *, user_id, limit, offset, match_id=None
        ):
            assert user_id == "user-1"
            assert limit == 20
            assert offset == 0
            assert match_id == "75"
            return [(_conversation("conv-75"), "75")]

    rows = await list_conversations(
        user="user-1",
        repos=_Repos(),
        limit=20,
        offset=0,
        match_id="75",
    )

    assert len(rows) == 1
    assert rows[0].id == "conv-75"
    assert rows[0].wc2026_match_id == "75"


async def test_get_conversation_detail_includes_wc2026_match_id():
    from app.api.routers.conversations import get_conversation

    class _Repos:
        async def get_conversation_with_messages(self, conversation_id):
            assert conversation_id == "conv-75"
            return _conversation("conv-75")

        async def get_conversation_wc2026_match_id(self, conversation_id):
            assert conversation_id == "conv-75"
            return "75"

    detail = await get_conversation("conv-75", "user-1", _Repos())

    assert detail.id == "conv-75"
    assert detail.wc2026_match_id == "75"
    assert detail.messages == []
