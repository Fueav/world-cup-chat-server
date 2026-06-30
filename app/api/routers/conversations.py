"""会话管理路由。

- POST /api/v1/wc2026/conversations: 创建新会话。
- GET /api/v1/wc2026/conversations/{id}: 获取会话详情(含消息)。
- GET /api/v1/wc2026/conversations: 分页列出当前用户的会话。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, ReposDep
from app.core.logging import get_logger
from app.core.schemas import ConversationOut, MessageOut

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/wc2026/conversations", tags=["conversations"])

# 列表分页上限,防止无界查询
_MAX_PAGE_SIZE = 100


class CreateConversationRequest(BaseModel):
    """创建会话请求体。"""

    title: str | None = None


class ConversationDetailOut(ConversationOut):
    """会话详情:在基础出参上附带消息列表。"""

    messages: list[MessageOut] = Field(default_factory=list)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest, user: CurrentUser, repos: ReposDep
) -> Any:
    """创建并持久化一个新会话。"""
    conv = await repos.create_conversation(user_id=user, title=body.title)
    await repos.commit()
    return ConversationOut.model_validate(conv)


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: str, user: CurrentUser, repos: ReposDep
) -> Any:
    """获取会话详情及其全部消息。"""
    conv = await repos.get_conversation_with_messages(conversation_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    _assert_owner(conv.user_id, user)
    messages = [MessageOut.model_validate(m) for m in conv.messages]
    return ConversationDetailOut(
        id=conv.id,
        user_id=conv.user_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    user: CurrentUser,
    repos: ReposDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    """分页列出当前用户的会话。"""
    rows = await repos.list_conversations(user_id=user, limit=limit, offset=offset)
    return [ConversationOut.model_validate(c) for c in rows]


def _assert_owner(owner_id: str | None, user: str) -> None:
    """校验资源归属;归属不符返回 403。匿名归属(None)放行。"""
    if owner_id is not None and owner_id != user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话"
        )
