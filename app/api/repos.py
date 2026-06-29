"""API 层仓储封装。

将 SQLAlchemy 查询集中到一处,保持 router 瘦身且 API 层无状态:
每个仓储仅持有按请求注入的 AsyncSession,不缓存任何会话间状态。
所有写操作返回新建/查询到的 ORM 对象,由调用方负责事务边界(commit)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import MessageRole, RunStatus, TaskStatus
from app.core.ids import _new_id, new_conversation_id
from app.core.models import (
    AgentRun,
    Conversation,
    IdempotencyRecord,
    Message,
    TaskState,
)


class Repos:
    """请求级仓储聚合,封装会话级数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求的数据库会话。"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """暴露底层会话,供需要细粒度控制的调用方使用。"""
        return self._session

    # --- Conversation ---

    async def create_conversation(
        self,
        user_id: str | None,
        title: str | None,
        conversation_id: str | None = None,
    ) -> Conversation:
        """创建会话并刷新以获得 server_default 字段。"""
        conv = Conversation(
            id=conversation_id or new_conversation_id(),
            user_id=user_id,
            title=title,
        )
        self._session.add(conv)
        await self._session.flush()
        await self._session.refresh(conv)
        return conv

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """按主键获取会话,不存在返回 None。"""
        return await self._session.get(Conversation, conversation_id)

    async def get_conversation_with_messages(
        self, conversation_id: str
    ) -> Conversation | None:
        """获取会话并预加载其消息。"""
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_conversations(
        self, user_id: str | None, limit: int, offset: int
    ) -> list[Conversation]:
        """分页列出某用户的会话,按更新时间倒序。"""
        stmt = select(Conversation).order_by(Conversation.updated_at.desc())
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def ensure_conversation(
        self, conversation_id: str | None, user_id: str | None
    ) -> Conversation:
        """复用已有会话或新建一个,保证返回有效会话。"""
        if conversation_id:
            existing = await self.get_conversation(conversation_id)
            if existing is not None:
                return existing
        return await self.create_conversation(
            user_id=user_id,
            title=None,
            conversation_id=conversation_id,
        )

    # --- Message ---

    async def add_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        token_count: int = 0,
        agent_run_id: str | None = None,
    ) -> Message:
        """向会话追加一条消息。"""
        msg = Message(
            id=_new_id("msg_"),
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            role=role,
            content=content,
            token_count=token_count,
        )
        self._session.add(msg)
        await self._session.flush()
        await self._session.refresh(msg)
        return msg

    # --- AgentRun / TaskState ---

    async def create_run(
        self,
        run_id: str,
        conversation_id: str,
        trace_id: str,
        plan: dict | None = None,
    ) -> AgentRun:
        """以 PENDING 状态创建一次 Agent 运行记录。"""
        run = AgentRun(
            id=run_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            status=RunStatus.PENDING,
            plan=plan,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_idempotency_record(
        self, user_id: str, idempotency_key: str
    ) -> IdempotencyRecord | None:
        """读取用户维度的幂等记录。"""
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_idempotency_record(
        self,
        *,
        record_id: str,
        user_id: str,
        idempotency_key: str,
        agent_run_id: str,
        request_hash: str,
        response: dict,
    ) -> IdempotencyRecord:
        """创建幂等记录。"""
        record = IdempotencyRecord(
            id=record_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            agent_run_id=agent_run_id,
            request_hash=request_hash,
            response=response,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def claim_idempotency_record(
        self,
        *,
        record_id: str,
        user_id: str,
        idempotency_key: str,
        agent_run_id: str,
        request_hash: str,
        response: dict,
    ) -> tuple[IdempotencyRecord, bool]:
        """INSERT-first idempotency claim.

        The insert is intentionally flushed before acquiring a conversation lock.
        Concurrent requests with the same key block on the unique index and then
        replay the committed record instead of racing into CONVERSATION_BUSY.
        """
        record = IdempotencyRecord(
            id=record_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            agent_run_id=agent_run_id,
            request_hash=request_hash,
            response=response,
        )
        self._session.add(record)
        try:
            await self._session.flush()
            return record, True
        except IntegrityError:
            await self._session.rollback()
            existing = await self.get_idempotency_record(user_id, idempotency_key)
            if existing is None:
                raise
            return existing, False

    async def get_run(self, run_id: str) -> AgentRun | None:
        """按主键获取运行记录,不存在返回 None。"""
        return await self._session.get(AgentRun, run_id)

    async def create_queued_task(
        self, task_id: str, agent_run_id: str, task_type: str, payload: dict
    ) -> TaskState:
        """为运行创建一条 QUEUED 状态的初始任务。"""
        task = TaskState(
            id=task_id,
            agent_run_id=agent_run_id,
            task_type=task_type,
            status=TaskStatus.QUEUED,
            attempt=0,
            payload=payload,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def commit(self) -> None:
        """提交当前事务。失败由会话依赖统一回滚。"""
        await self._session.commit()


def utcnow() -> datetime:
    """返回带时区的当前时间,统一时间来源。"""
    return datetime.now(timezone.utc)
