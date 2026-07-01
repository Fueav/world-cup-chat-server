"""运行状态查询路由。

- GET /api/v1/wc2026/runs/{id}: 返回某次 Agent 运行的轻量状态(RunStatusOut)。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, ReposDep
from app.core.logging import get_logger
from app.core.schemas import RunStatusOut

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/wc2026/runs", tags=["runs"])


@router.get("/{agent_run_id}", response_model=RunStatusOut)
async def get_run_status(agent_run_id: str, user: CurrentUser, repos: ReposDep) -> Any:
    """查询运行状态;不存在返回 404。"""
    run = await repos.get_run(agent_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="运行记录不存在"
        )
    conversation = await repos.get_conversation(run.conversation_id)
    if conversation is None or conversation.user_id != user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该运行"
        )
    return RunStatusOut(
        agent_run_id=run.id,
        status=run.status,
        intent=run.intent,
        error=run.error,
    )
