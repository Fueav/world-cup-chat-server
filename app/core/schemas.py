"""API 出入参 Schema(Pydantic v2)。

定义 HTTP 接口的请求体与响应体模型,与 ORM 模型解耦,作为前后端契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
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


class ChatRequest(BaseModel):
    """提交一次对话/任务的请求体。"""

    conversation_id: str | None = None
    match_id: str | None = None
    message: str
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    wc2026_context: "Wc2026Context | None" = None


class Wc2026TeamRef(BaseModel):
    """WC2026 upstream team reference."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    short_name: str | None = None


class Wc2026Match(BaseModel):
    """Trusted current-match descriptor injected by the upstream proxy."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    fd_match_id: str | int | None = None
    description: str | None = None
    kickoff_utc: str | None = None
    match_date: str | None = None
    stage: str | None = None
    stage_label: str | None = None
    group_label: str | None = None
    status: str | None = None
    raw_status: str | None = None
    home: Wc2026TeamRef
    away: Wc2026TeamRef
    is_unlocked: bool = False


class Wc2026Entitlements(BaseModel):
    """Trusted user entitlement summary for the current WC2026 chat."""

    model_config = ConfigDict(extra="allow")

    has_all: bool = False
    unlocked_matches: list[Any] = Field(default_factory=list)
    locked_matches: list[Any] = Field(default_factory=list)


class Wc2026Context(BaseModel):
    """Trusted WC2026 context injected by moss-api/match API."""

    model_config = ConfigDict(extra="allow")

    current_match_id: str = Field(min_length=1)
    current_match: Wc2026Match
    entitlements: Wc2026Entitlements = Field(default_factory=Wc2026Entitlements)

    @field_validator("current_match_id", mode="before")
    @classmethod
    def _strip_current_match_id(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("current_match_id 不能为空")
        return text


class ChatAccepted(BaseModel):
    """POST /runs 受理后的 202 响应体。"""

    conversation_id: str
    agent_run_id: str
    trace_id: str
    status: RunStatus
    stream_url: str
    ws_url: str
    route_type: str | None = None


class MessageOut(BaseModel):
    """消息出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    agent_run_id: str | None = None
    role: MessageRole
    content: str
    token_count: int
    created_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class ConversationOut(BaseModel):
    """会话出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    title: str | None = None
    wc2026_match_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ToolCallOut(BaseModel):
    """工具调用日志出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_run_id: str
    tool_name: str
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    attempt: int = 0
    latency_ms: int
    status: TaskStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class AgentRunOut(BaseModel):
    """Agent 运行出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    trace_id: str
    status: RunStatus
    intent: IntentType | None = None
    plan: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunStatusOut(BaseModel):
    """GET /runs/{id} 的轻量状态出参。"""

    agent_run_id: str
    status: RunStatus
    intent: IntentType | None = None
    error: str | None = None


class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("name 不能为空")
        return text


class KnowledgeBaseOut(BaseModel):
    """知识库响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_user_id: str
    name: str
    description: str | None = None
    status: KnowledgeBaseStatus
    created_at: datetime
    updated_at: datetime


class RAGDocumentCreate(BaseModel):
    """导入 RAG 文档请求。Phase 1 仅接受文本/Markdown。"""

    knowledge_base_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=512)
    content: str = Field(min_length=1)
    source_type: Literal["manual", "api"] = "manual"
    source_uri: str | None = Field(default=None, max_length=2048)
    mime_type: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", mode="before")
    @classmethod
    def _strip_content(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("content 不能为空")
        return text


class RAGDocumentAccepted(BaseModel):
    """文档导入受理响应。"""

    document_id: str
    job_id: str
    status: RAGDocumentStatus
    replayed: bool = False


class RAGDocumentOut(BaseModel):
    """RAG 文档状态响应。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    knowledge_base_id: str
    owner_user_id: str
    title: str | None = None
    source_type: str
    source_uri: str | None = None
    mime_type: str | None = None
    status: RAGDocumentStatus
    error_message: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="meta",
        serialization_alias="metadata",
    )
    created_at: datetime
    updated_at: datetime


class RAGIngestionJobOut(BaseModel):
    """RAG ingestion job 状态响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    knowledge_base_id: str
    owner_user_id: str
    status: RAGIngestionJobStatus
    attempts: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class RAGQueryRequest(BaseModel):
    """RAG 查询请求。"""

    knowledge_base_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: dict[str, Any] | None = None
    strict: bool = False

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("query 不能为空")
        return text


class CitationOut(BaseModel):
    """RAG citation 元数据。"""

    source_uri: str | None = None
    page: int | None = None
    section: str | None = None
    chunk_index: int


class KnowledgeSearchResult(BaseModel):
    """单条 RAG 检索命中。"""

    chunk_id: str
    document_id: str
    knowledge_base_id: str
    title: str | None = None
    content: str
    score: float
    citation: CitationOut
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGQueryResponse(BaseModel):
    """RAG 查询响应。"""

    chunks: list[KnowledgeSearchResult]
    degraded: bool
    reason: str | None = None
    latency_ms: int
    query_id: str | None = None
