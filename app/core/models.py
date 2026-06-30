"""SQLAlchemy 2.0 ORM 模型。

定义业务持久化表:Conversation / Message / AgentRun / TaskState / ToolCallLog。
使用 DeclarativeBase + Mapped 类型注解风格。JSON 列采用可移植的 JSON 类型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from app.core.enums import (
    IntentType,
    KnowledgeBaseStatus,
    MessageRole,
    RAGDocumentStatus,
    RAGIngestionJobStatus,
    RunStatus,
    TaskStatus,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


class Conversation(Base):
    """会话:一组消息与运行的容器。"""

    __tablename__ = "conversation"
    __table_args__ = (
        Index("ix_conversation_user_wc2026_match", "user_id", "wc2026_match_id"),
        Index(
            "uq_conversation_user_wc2026_match",
            "user_id",
            "wc2026_match_id",
            unique=True,
            postgresql_where=text("wc2026_match_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wc2026_match_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """会话内的单条消息。"""

    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="SET NULL"), index=True, nullable=True
    )
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class AgentRun(Base):
    """一次 Agent 执行的状态记录。"""

    __tablename__ = "agent_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    intent: Mapped[IntentType | None] = mapped_column(String(32), nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="runs")
    tasks: Mapped[list["TaskState"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["ToolCallLog"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class IdempotencyRecord(Base):
    """按用户隔离的幂等键记录。"""

    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_idempotency_record_user_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "agent_run.id",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
    )
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TaskState(Base):
    """运行内某个子任务(intent/rag/tool/llm)的状态。"""

    __tablename__ = "task_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[TaskStatus] = mapped_column(String(16), default=TaskStatus.QUEUED)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    run: Mapped["AgentRun"] = relationship(back_populates="tasks")


class ToolCallLog(Base):
    """一次工具调用的审计日志。"""

    __tablename__ = "tool_call_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[TaskStatus] = mapped_column(String(16), default=TaskStatus.DONE)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["AgentRun"] = relationship(back_populates="tool_calls")


class KnowledgeBase(Base):
    """用户拥有的知识库。"""

    __tablename__ = "knowledge_base"
    __table_args__ = (
        Index("ix_knowledge_base_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[KnowledgeBaseStatus] = mapped_column(
        String(16), default=KnowledgeBaseStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list["RAGDocument"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class RAGDocument(Base):
    """导入到知识库的一份文本/Markdown 文档。"""

    __tablename__ = "rag_document"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "content_hash",
            name="uq_rag_document_kb_content_hash",
        ),
        Index("ix_rag_document_kb_status", "knowledge_base_id", "status"),
        Index("ix_rag_document_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RAGDocumentStatus] = mapped_column(
        String(16), default=RAGDocumentStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="documents")
    chunks: Mapped[list["RAGDocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list["RAGIngestionJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class RAGDocumentChunk(Base):
    """文档分块及其 embedding。SQLite 单测下 embedding 用 JSON 表达。"""

    __tablename__ = "rag_document_chunk"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "index_version",
            "chunk_index",
            name="uq_rag_chunk_doc_index_chunk",
        ),
        Index("ix_rag_chunk_kb_index", "knowledge_base_id", "index_version"),
        Index("ix_rag_chunk_document_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("rag_document.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["RAGDocument"] = relationship(back_populates="chunks")


class RAGIngestionJob(Base):
    """RAG 文档摄取任务状态。"""

    __tablename__ = "rag_ingestion_job"
    __table_args__ = (Index("ix_rag_ingestion_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("rag_document.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RAGIngestionJobStatus] = mapped_column(
        String(16), default=RAGIngestionJobStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["RAGDocument"] = relationship(back_populates="ingestion_jobs")


class RAGRetrievalLog(Base):
    """一次 RAG 检索的审计/诊断日志。"""

    __tablename__ = "rag_retrieval_log"
    __table_args__ = (
        Index("ix_rag_retrieval_agent_run_id", "agent_run_id"),
        Index("ix_rag_retrieval_conversation_id", "conversation_id"),
        Index("ix_rag_retrieval_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    query_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    scores: Mapped[list[float]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
