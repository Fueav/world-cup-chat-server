from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.enums import RunStatus


async def test_get_run_status_rejects_null_owner_conversation():
    from app.api.routers.runs import get_run_status

    class _Repos:
        async def get_run(self, agent_run_id):
            return SimpleNamespace(
                id=agent_run_id,
                conversation_id="conv-null-owner",
                status=RunStatus.RUNNING,
                intent=None,
                error=None,
            )

        async def get_conversation(self, conversation_id):
            return SimpleNamespace(user_id=None)

    with pytest.raises(HTTPException) as exc:
        await get_run_status("run-1", "user-1", _Repos())

    assert exc.value.status_code == 403
