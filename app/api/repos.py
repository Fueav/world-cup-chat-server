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
        wc2026_match_id: str | None = None,
    ) -> Conversation:
        """创建会话并刷新以获得 server_default 字段。"""
        conv = Conversation(
            id=conversation_id or new_conversation_id(),
            user_id=user_id,
            wc2026_match_id=wc2026_match_id,
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

    async def list_conversations_with_wc2026_match_id(
        self,
        *,
        user_id: str | None,
        limit: int,
        offset: int,
        match_id: str | None = None,
    ) -> list[tuple[Conversation, str | None]]:
        """List conversations with their WC2026 match binding projection."""
        stmt = select(Conversation).order_by(Conversation.updated_at.desc())
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        if match_id:
            stmt = stmt.where(Conversation.wc2026_match_id == match_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        if match_id and not rows and offset == 0:
            return await self._legacy_list_conversations_by_wc2026_match_id(
                user_id=user_id,
                match_id=match_id,
                limit=limit,
            )
        projected = await self._wc2026_match_ids_by_conversation(
            [row.id for row in rows]
        )
        return [(row, projected.get(row.id)) for row in rows]

    async def get_wc2026_conversation_id_by_match(
        self, user_id: str | None, match_id: str
    ) -> str | None:
        """Return the latest conversation id for this user and WC2026 match."""
        rows = await self.list_conversations_with_wc2026_match_id(
            user_id=user_id,
            limit=1,
            offset=0,
            match_id=match_id,
        )
        if not rows:
            return None
        return rows[0][0].id

    async def ensure_conversation(
        self,
        conversation_id: str | None,
        user_id: str | None,
        wc2026_match_id: str | None = None,
    ) -> Conversation:
        """复用已有会话或新建一个,保证返回有效会话。"""
        if conversation_id:
            existing = await self.get_conversation(conversation_id)
            if existing is not None:
                if wc2026_match_id and existing.wc2026_match_id is None:
                    existing.wc2026_match_id = wc2026_match_id
                    await self._session.flush()
                return existing
        try:
            return await self.create_conversation(
                user_id=user_id,
                title=None,
                conversation_id=conversation_id,
                wc2026_match_id=wc2026_match_id,
            )
        except IntegrityError:
            if not wc2026_match_id:
                raise
            await self._session.rollback()
            existing_id = await self.get_wc2026_conversation_id_by_match(
                user_id, wc2026_match_id
            )
            if existing_id is None:
                raise
            existing = await self.get_conversation(existing_id)
            if existing is None:
                raise
            return existing

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

    async def get_conversation_wc2026_match_id(
        self, conversation_id: str
    ) -> str | None:
        """Return the persisted WC2026 match binding, with run-plan fallback."""
        conversation = await self.get_conversation(conversation_id)
        if conversation is not None and conversation.wc2026_match_id:
            return str(conversation.wc2026_match_id)
        stmt = select(AgentRun.plan).where(AgentRun.conversation_id == conversation_id)
        result = await self._session.execute(stmt)
        for plan in result.scalars().all():
            match_id = _plan_wc2026_match_id(plan)
            if match_id:
                return match_id
        return None

    async def _wc2026_match_ids_by_conversation(
        self, conversation_ids: list[str]
    ) -> dict[str, str]:
        """Project conversation id -> first recorded WC2026 match id."""
        if not conversation_ids:
            return {}
        conv_stmt = select(Conversation.id, Conversation.wc2026_match_id).where(
            Conversation.id.in_(conversation_ids)
        )
        conv_result = await self._session.execute(conv_stmt)
        output = {
            str(conversation_id): str(match_id)
            for conversation_id, match_id in conv_result.all()
            if match_id
        }
        missing_ids = [
            conversation_id
            for conversation_id in conversation_ids
            if str(conversation_id) not in output
        ]
        if not missing_ids:
            return output
        stmt = select(AgentRun.conversation_id, AgentRun.plan).where(
            AgentRun.conversation_id.in_(missing_ids)
        )
        result = await self._session.execute(stmt)
        for conversation_id, plan in result.all():
            if conversation_id in output:
                continue
            match_id = _plan_wc2026_match_id(plan)
            if match_id:
                output[str(conversation_id)] = match_id
        return output

    async def _legacy_list_conversations_by_wc2026_match_id(
        self,
        *,
        user_id: str | None,
        match_id: str,
        limit: int,
    ) -> list[tuple[Conversation, str | None]]:
        """Fallback for conversations created before wc2026_match_id existed."""
        stmt = (
            select(Conversation)
            .join(AgentRun, AgentRun.conversation_id == Conversation.id)
            .where(
                AgentRun.plan["wc2026_context"][
                    "current_match_id"
                ].as_string()
                == match_id
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        result = await self._session.execute(stmt)
        rows = []
        seen: set[str] = set()
        for row in result.scalars().all():
            if row.id in seen:
                continue
            seen.add(row.id)
            rows.append((row, match_id))
        return rows

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


def _plan_wc2026_match_id(plan: dict | None) -> str | None:
    if not isinstance(plan, dict):
        return None
    context = plan.get("wc2026_context")
    if not isinstance(context, dict):
        return None
    match_id = context.get("current_match_id")
    if match_id is None:
        return None
    text = str(match_id).strip()
    return text or None
