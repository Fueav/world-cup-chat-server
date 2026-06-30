"""仓储层(Repository Pattern)。

为五张业务表提供异步 CRUD 与领域语义方法。所有写操作遵循"不可变风格":
不在调用方传入的对象上原地修改,而是把字段值写入新建/重新载入的 ORM 实体并
返回提交后(已刷新)的实体副本。所有 DB 异常被 try/except 捕获并转译为
app.db.errors 中的领域错误向上抛出。

每个仓储以一个 AsyncSession 构造,事务边界由仓储方法内部 commit 管理,
便于在 Celery 任务或请求作用域中按需创建/回收 session。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    IntentType,
    KnowledgeBaseStatus,
    MessageRole,
    RAGDocumentStatus,
    RAGIngestionJobStatus,
    RunStatus,
    TaskStatus,
)
from app.core.ids import _new_id, new_conversation_id, new_run_id
from app.core.models import (
    AgentRun,
    Conversation,
    IdempotencyRecord,
    KnowledgeBase,
    Message,
    RAGDocument,
    RAGDocumentChunk,
    RAGIngestionJob,
    RAGRetrievalLog,
    TaskState,
    ToolCallLog,
)
from app.db.errors import (
    DuplicateEntityError,
    EntityNotFoundError,
    PersistenceError,
)
from app.db.state_machine import assert_run_transition, assert_task_transition


def _utcnow() -> datetime:
    """返回带时区的当前 UTC 时间(用于 started_at/finished_at 显式赋值)。"""
    return datetime.now(timezone.utc)


class _BaseRepository:
    """仓储公共基类:持有 session 并集中处理提交与异常转译。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _commit_refresh(self, entity: Any, label: str) -> Any:
        """提交事务并刷新实体;捕获唯一冲突与通用 DB 错误转译为领域错误。"""
        try:
            await self._session.commit()
            await self._session.refresh(entity)
            return entity
        except IntegrityError as exc:
            await self._session.rollback()
            ident = getattr(entity, "id", "?")
            raise DuplicateEntityError(label, str(ident)) from exc
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise PersistenceError(f"persist {label} failed: {exc}") from exc

    async def _scalar(self, stmt: Any, label: str) -> Any:
        """执行查询返回单个标量结果;DB 错误转译为 PersistenceError。"""
        try:
            result = await self._session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError(f"query {label} failed: {exc}") from exc

    async def _scalars(self, stmt: Any, label: str) -> Sequence[Any]:
        """执行查询返回标量列表;DB 错误转译为 PersistenceError。"""
        try:
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as exc:
            raise PersistenceError(f"query {label} failed: {exc}") from exc


class ConversationRepository(_BaseRepository):
    """会话仓储。"""

    async def create(
        self,
        conversation_id: str,
        user_id: str | None = None,
        title: str | None = None,
        wc2026_match_id: str | None = None,
    ) -> Conversation:
        """新建会话并返回持久化后的实体。"""
        entity = Conversation(
            id=conversation_id,
            user_id=user_id,
            title=title,
            wc2026_match_id=wc2026_match_id,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "Conversation")

    async def get(self, conversation_id: str) -> Conversation | None:
        """按 ID 获取会话,不存在返回 None。"""
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        return await self._scalar(stmt, "Conversation")

    async def get_or_404(self, conversation_id: str) -> Conversation:
        """按 ID 获取会话,不存在抛 EntityNotFoundError。"""
        entity = await self.get(conversation_id)
        if entity is None:
            raise EntityNotFoundError("Conversation", conversation_id)
        return entity

    async def ensure(
        self, conversation_id: str | None, wc2026_match_id: str | None = None
    ) -> Conversation:
        """幂等获取或创建会话:传 None 时生成新会话。"""
        if conversation_id is None:
            return await self.create(
                new_conversation_id(), wc2026_match_id=wc2026_match_id
            )
        existing = await self.get(conversation_id)
        if existing is not None:
            if wc2026_match_id and existing.wc2026_match_id is None:
                existing.wc2026_match_id = wc2026_match_id
                return await self._commit_refresh(existing, "Conversation")
            return existing
        return await self.create(conversation_id, wc2026_match_id=wc2026_match_id)

    async def update_title(self, conversation_id: str, title: str) -> Conversation:
        """更新会话标题,返回更新后的实体(重载后赋值)。"""
        entity = await self.get_or_404(conversation_id)
        entity.title = title
        return await self._commit_refresh(entity, "Conversation")

    async def list(self, limit: int = 50, offset: int = 0) -> list[Conversation]:
        """按更新时间倒序分页列出会话。"""
        stmt = (
            select(Conversation)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(await self._scalars(stmt, "Conversation"))


class MessageRepository(_BaseRepository):
    """消息仓储。"""

    async def create(
        self,
        message_id: str,
        conversation_id: str,
        role: MessageRole,
        content: str,
        token_count: int = 0,
        meta: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> Message:
        """追加一条消息并返回持久化后的实体。"""
        entity = Message(
            id=message_id,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            role=role,
            content=content,
            token_count=token_count,
            meta=meta or {},
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "Message")

    async def get(self, message_id: str) -> Message | None:
        """按 ID 获取消息。"""
        stmt = select(Message).where(Message.id == message_id)
        return await self._scalar(stmt, "Message")

    async def list_by_conversation(
        self, conversation_id: str, limit: int = 100, offset: int = 0
    ) -> list[Message]:
        """按创建时间升序列出某会话的消息(用于上下文拼接)。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(await self._scalars(stmt, "Message"))


class AgentRunRepository(_BaseRepository):
    """Agent 运行仓储,含状态机校验。"""

    async def create(
        self,
        conversation_id: str,
        trace_id: str,
        run_id: str | None = None,
        intent: IntentType | None = None,
        plan: dict[str, Any] | None = None,
    ) -> AgentRun:
        """新建处于 PENDING 状态的运行记录。"""
        entity = AgentRun(
            id=run_id or new_run_id(),
            conversation_id=conversation_id,
            trace_id=trace_id,
            status=RunStatus.PENDING,
            intent=intent,
            plan=plan,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "AgentRun")

    async def get(self, run_id: str) -> AgentRun | None:
        """按 ID 获取运行。"""
        stmt = select(AgentRun).where(AgentRun.id == run_id)
        return await self._scalar(stmt, "AgentRun")

    async def get_or_404(self, run_id: str) -> AgentRun:
        """按 ID 获取运行,不存在抛 EntityNotFoundError。"""
        entity = await self.get(run_id)
        if entity is None:
            raise EntityNotFoundError("AgentRun", run_id)
        return entity

    async def update_status(
        self, run_id: str, status: RunStatus, error: str | None = None
    ) -> AgentRun:
        """幂等地更新运行状态。

        先做状态机校验(自环放行、非法流转抛错),再按目标状态维护
        started_at/finished_at 时间戳,最后提交并返回更新后实体。
        """
        entity = await self.get_or_404(run_id)
        assert_run_transition(entity.status, status)
        entity.status = status
        if status == RunStatus.RUNNING and entity.started_at is None:
            entity.started_at = _utcnow()
        if status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            entity.finished_at = _utcnow()
        if error is not None:
            entity.error = error
        return await self._commit_refresh(entity, "AgentRun")

    async def set_intent(self, run_id: str, intent: IntentType) -> AgentRun:
        """写入意图识别结果。"""
        entity = await self.get_or_404(run_id)
        entity.intent = intent
        return await self._commit_refresh(entity, "AgentRun")

    async def set_plan(self, run_id: str, plan: dict[str, Any]) -> AgentRun:
        """写入执行计划。"""
        entity = await self.get_or_404(run_id)
        entity.plan = plan
        return await self._commit_refresh(entity, "AgentRun")

    async def list_by_conversation(
        self, conversation_id: str, limit: int = 50, offset: int = 0
    ) -> list[AgentRun]:
        """列出某会话的运行。"""
        stmt = (
            select(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
            .limit(limit)
            .offset(offset)
        )
        return list(await self._scalars(stmt, "AgentRun"))


class IdempotencyRepository(_BaseRepository):
    """幂等键仓储。"""

    async def create(
        self,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
        agent_run_id: str,
        response: dict[str, Any],
        record_id: str | None = None,
    ) -> IdempotencyRecord:
        """写入一条幂等记录。"""
        entity = IdempotencyRecord(
            id=record_id or _new_id("idem_"),
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            agent_run_id=agent_run_id,
            response=response,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "IdempotencyRecord")

    async def get(
        self, user_id: str, idempotency_key: str
    ) -> IdempotencyRecord | None:
        """按用户和幂等键读取记录。"""
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        return await self._scalar(stmt, "IdempotencyRecord")


class TaskStateRepository(_BaseRepository):
    """子任务状态仓储,支持重试(attempt 自增)与恢复(payload/result)。"""

    async def create(
        self,
        task_id: str,
        agent_run_id: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        status: TaskStatus = TaskStatus.QUEUED,
    ) -> TaskState:
        """新建子任务,默认 QUEUED、attempt=0。"""
        entity = TaskState(
            id=task_id,
            agent_run_id=agent_run_id,
            task_type=task_type,
            status=status,
            attempt=0,
            payload=payload,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "TaskState")

    async def get(self, task_id: str) -> TaskState | None:
        """按 ID 获取子任务。"""
        stmt = select(TaskState).where(TaskState.id == task_id)
        return await self._scalar(stmt, "TaskState")

    async def get_or_404(self, task_id: str) -> TaskState:
        """按 ID 获取子任务,不存在抛 EntityNotFoundError。"""
        entity = await self.get(task_id)
        if entity is None:
            raise EntityNotFoundError("TaskState", task_id)
        return entity

    async def update_status(
        self, task_id: str, status: TaskStatus, result: dict[str, Any] | None = None
    ) -> TaskState:
        """幂等更新子任务状态,可同时回写 result;状态机校验。"""
        entity = await self.get_or_404(task_id)
        assert_task_transition(entity.status, status)
        entity.status = status
        if result is not None:
            entity.result = result
        return await self._commit_refresh(entity, "TaskState")

    async def increment_attempt(self, task_id: str) -> TaskState:
        """重试计数 +1(用于退避重试与恢复)。"""
        entity = await self.get_or_404(task_id)
        entity.attempt = (entity.attempt or 0) + 1
        return await self._commit_refresh(entity, "TaskState")

    async def update_payload(self, task_id: str, payload: dict[str, Any]) -> TaskState:
        """更新任务输入载荷(用于恢复时改写参数)。"""
        entity = await self.get_or_404(task_id)
        entity.payload = payload
        return await self._commit_refresh(entity, "TaskState")

    async def list_by_run(self, agent_run_id: str) -> list[TaskState]:
        """列出某运行下的全部子任务。"""
        stmt = select(TaskState).where(TaskState.agent_run_id == agent_run_id)
        return list(await self._scalars(stmt, "TaskState"))


class ToolCallLogRepository(_BaseRepository):
    """工具调用审计日志仓储。"""

    async def create(
        self,
        log_id: str,
        agent_run_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        attempt: int = 0,
        latency_ms: int = 0,
        status: TaskStatus = TaskStatus.DONE,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> ToolCallLog:
        """写入一条工具调用日志并返回。"""
        entity = ToolCallLog(
            id=log_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            attempt=attempt,
            latency_ms=latency_ms,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "ToolCallLog")

    async def get(self, log_id: str) -> ToolCallLog | None:
        """按 ID 获取工具调用日志。"""
        stmt = select(ToolCallLog).where(ToolCallLog.id == log_id)
        return await self._scalar(stmt, "ToolCallLog")

    async def list_by_run(self, agent_run_id: str) -> list[ToolCallLog]:
        """按创建时间升序列出某运行的工具调用。"""
        stmt = (
            select(ToolCallLog)
            .where(ToolCallLog.agent_run_id == agent_run_id)
            .order_by(ToolCallLog.created_at.asc())
        )
        return list(await self._scalars(stmt, "ToolCallLog"))


class KnowledgeBaseRepository(_BaseRepository):
    """RAG 知识库仓储。"""

    async def create(
        self,
        *,
        owner_user_id: str,
        name: str,
        description: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> KnowledgeBase:
        entity = KnowledgeBase(
            id=knowledge_base_id or _new_id("kb_"),
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "KnowledgeBase")

    async def get(self, knowledge_base_id: str) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
        return await self._scalar(stmt, "KnowledgeBase")

    async def get_for_user(
        self, knowledge_base_id: str, owner_user_id: str
    ) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.owner_user_id == owner_user_id,
        )
        return await self._scalar(stmt, "KnowledgeBase")

    async def list_for_user(
        self, owner_user_id: str, limit: int = 50, offset: int = 0
    ) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.owner_user_id == owner_user_id)
            .order_by(KnowledgeBase.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(await self._scalars(stmt, "KnowledgeBase"))


class RAGDocumentRepository(_BaseRepository):
    """RAG 文档仓储,含 content hash 幂等回放。"""

    async def create_or_get(
        self,
        *,
        document_id: str,
        knowledge_base_id: str,
        owner_user_id: str,
        title: str | None,
        source_type: str,
        source_uri: str | None,
        mime_type: str | None,
        content_hash: str,
        raw_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[RAGDocument, bool]:
        existing = await self.get_by_content_hash(knowledge_base_id, content_hash)
        if existing is not None:
            return existing, False
        entity = RAGDocument(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            owner_user_id=owner_user_id,
            title=title,
            source_type=source_type,
            source_uri=source_uri,
            mime_type=mime_type,
            content_hash=content_hash,
            raw_content=raw_content,
            status=RAGDocumentStatus.PENDING,
            meta=metadata or {},
        )
        self._session.add(entity)
        try:
            return await self._commit_refresh(entity, "RAGDocument"), True
        except DuplicateEntityError:
            existing = await self.get_by_content_hash(knowledge_base_id, content_hash)
            if existing is None:
                raise
            return existing, False

    async def get(self, document_id: str) -> RAGDocument | None:
        stmt = select(RAGDocument).where(RAGDocument.id == document_id)
        return await self._scalar(stmt, "RAGDocument")

    async def get_for_user(
        self, document_id: str, owner_user_id: str
    ) -> RAGDocument | None:
        stmt = select(RAGDocument).where(
            RAGDocument.id == document_id,
            RAGDocument.owner_user_id == owner_user_id,
        )
        return await self._scalar(stmt, "RAGDocument")

    async def get_by_content_hash(
        self, knowledge_base_id: str, content_hash: str
    ) -> RAGDocument | None:
        stmt = select(RAGDocument).where(
            RAGDocument.knowledge_base_id == knowledge_base_id,
            RAGDocument.content_hash == content_hash,
        )
        return await self._scalar(stmt, "RAGDocument")

    async def update_status(
        self,
        document_id: str,
        status: RAGDocumentStatus,
        *,
        error_message: str | None = None,
    ) -> RAGDocument:
        entity = await self.get(document_id)
        if entity is None:
            raise EntityNotFoundError("RAGDocument", document_id)
        entity.status = status
        entity.error_message = error_message
        return await self._commit_refresh(entity, "RAGDocument")


class RAGIngestionJobRepository(_BaseRepository):
    """RAG ingestion job 仓储。"""

    async def create(
        self,
        *,
        job_id: str,
        document_id: str,
        knowledge_base_id: str,
        owner_user_id: str,
        payload: dict[str, Any] | None = None,
    ) -> RAGIngestionJob:
        entity = RAGIngestionJob(
            id=job_id,
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            owner_user_id=owner_user_id,
            status=RAGIngestionJobStatus.PENDING,
            attempts=0,
            payload=payload,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "RAGIngestionJob")

    async def get(self, job_id: str) -> RAGIngestionJob | None:
        stmt = select(RAGIngestionJob).where(RAGIngestionJob.id == job_id)
        return await self._scalar(stmt, "RAGIngestionJob")

    async def get_for_user(
        self, job_id: str, owner_user_id: str
    ) -> RAGIngestionJob | None:
        stmt = select(RAGIngestionJob).where(
            RAGIngestionJob.id == job_id,
            RAGIngestionJob.owner_user_id == owner_user_id,
        )
        return await self._scalar(stmt, "RAGIngestionJob")

    async def get_latest_for_document(
        self, document_id: str
    ) -> RAGIngestionJob | None:
        stmt = (
            select(RAGIngestionJob)
            .where(RAGIngestionJob.document_id == document_id)
            .order_by(RAGIngestionJob.created_at.desc())
            .limit(1)
        )
        return await self._scalar(stmt, "RAGIngestionJob")

    async def update_status(
        self,
        job_id: str,
        status: RAGIngestionJobStatus,
        *,
        error_message: str | None = None,
    ) -> RAGIngestionJob:
        entity = await self.get(job_id)
        if entity is None:
            raise EntityNotFoundError("RAGIngestionJob", job_id)
        entity.status = status
        if status == RAGIngestionJobStatus.RUNNING:
            entity.started_at = _utcnow()
            entity.attempts = (entity.attempts or 0) + 1
        if status in (
            RAGIngestionJobStatus.SUCCEEDED,
            RAGIngestionJobStatus.FAILED,
            RAGIngestionJobStatus.CANCELLED,
        ):
            entity.finished_at = _utcnow()
        entity.error_message = error_message
        return await self._commit_refresh(entity, "RAGIngestionJob")


class RAGChunkRepository(_BaseRepository):
    """RAG chunk 仓储。"""

    async def replace_for_document(
        self,
        *,
        document_id: str,
        index_version: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        try:
            await self._session.execute(
                delete(RAGDocumentChunk).where(
                    RAGDocumentChunk.document_id == document_id,
                    RAGDocumentChunk.index_version == index_version,
                )
            )
            for item in chunks:
                self._session.add(RAGDocumentChunk(**item))
            await self._session.commit()
            return len(chunks)
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicateEntityError("RAGDocumentChunk", document_id) from exc
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise PersistenceError(f"persist RAGDocumentChunk failed: {exc}") from exc


class RAGRetrievalLogRepository(_BaseRepository):
    """RAG 检索日志仓储。"""

    async def create(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        query_hash: str,
        query_preview: str | None,
        top_k: int,
        matched_chunk_ids: list[str],
        scores: list[float],
        latency_ms: int,
        degraded: bool,
        reason: str | None = None,
        agent_run_id: str | None = None,
        conversation_id: str | None = None,
        log_id: str | None = None,
    ) -> RAGRetrievalLog:
        entity = RAGRetrievalLog(
            id=log_id or _new_id("retrlog_"),
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            query_hash=query_hash,
            query_preview=query_preview,
            top_k=top_k,
            matched_chunk_ids=matched_chunk_ids,
            scores=scores,
            latency_ms=latency_ms,
            degraded=degraded,
            reason=reason,
        )
        self._session.add(entity)
        return await self._commit_refresh(entity, "RAGRetrievalLog")
