-- 初始化建表脚本(对应 app/core/models.py)
-- Postgres 16。可重复执行(IF NOT EXISTS)。
-- 含必要索引:message.conversation_id / agent_run.conversation_id / tool_call_log.agent_run_id

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS conversation (
    id              VARCHAR(64) PRIMARY KEY,
    user_id         VARCHAR(64),
    wc2026_match_id VARCHAR(64),
    title           VARCHAR(512),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE IF EXISTS conversation
    ADD COLUMN IF NOT EXISTS wc2026_match_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_conversation_user_wc2026_match
    ON conversation (user_id, wc2026_match_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_user_wc2026_match
    ON conversation (user_id, wc2026_match_id)
    WHERE wc2026_match_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS message (
    id              VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL
        REFERENCES conversation(id) ON DELETE CASCADE,
    agent_run_id    VARCHAR(64),
    role            VARCHAR(16) NOT NULL,
    content         TEXT NOT NULL,
    token_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_message_conversation_id
    ON message (conversation_id);
CREATE INDEX IF NOT EXISTS ix_message_agent_run_id
    ON message (agent_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_assistant_per_run
    ON message (agent_run_id, role)
    WHERE agent_run_id IS NOT NULL AND role = 'ASSISTANT';

CREATE TABLE IF NOT EXISTS agent_run (
    id              VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL
        REFERENCES conversation(id) ON DELETE CASCADE,
    trace_id        VARCHAR(64) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    intent          VARCHAR(32),
    plan            JSONB,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_agent_run_conversation_id
    ON agent_run (conversation_id);
CREATE INDEX IF NOT EXISTS ix_agent_run_trace_id
    ON agent_run (trace_id);

CREATE TABLE IF NOT EXISTS idempotency_record (
    id              VARCHAR(64) PRIMARY KEY,
    user_id         VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(256) NOT NULL,
    agent_run_id    VARCHAR(64) NOT NULL
        REFERENCES agent_run(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    request_hash    VARCHAR(128) NOT NULL,
    response        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_idempotency_record_user_key
        UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_idempotency_record_agent_run_id
    ON idempotency_record (agent_run_id);

ALTER TABLE IF EXISTS idempotency_record
    DROP CONSTRAINT IF EXISTS idempotency_record_agent_run_id_fkey;
ALTER TABLE IF EXISTS idempotency_record
    ADD CONSTRAINT idempotency_record_agent_run_id_fkey
    FOREIGN KEY (agent_run_id)
    REFERENCES agent_run(id)
    ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS task_state (
    id           VARCHAR(64) PRIMARY KEY,
    agent_run_id VARCHAR(64) NOT NULL
        REFERENCES agent_run(id) ON DELETE CASCADE,
    task_type    VARCHAR(32) NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    attempt      INTEGER NOT NULL DEFAULT 0,
    payload      JSONB,
    result       JSONB,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_task_state_agent_run_id
    ON task_state (agent_run_id);

CREATE TABLE IF NOT EXISTS tool_call_log (
    id           VARCHAR(64) PRIMARY KEY,
    agent_run_id VARCHAR(64) NOT NULL
        REFERENCES agent_run(id) ON DELETE CASCADE,
    tool_name    VARCHAR(128) NOT NULL,
    arguments    JSONB,
    result       JSONB,
    attempt      INTEGER NOT NULL DEFAULT 0,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    status       VARCHAR(16) NOT NULL DEFAULT 'DONE',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tool_call_log_agent_run_id
    ON tool_call_log (agent_run_id);

ALTER TABLE IF EXISTS message
    ADD COLUMN IF NOT EXISTS agent_run_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_message_agent_run_id
    ON message (agent_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_assistant_per_run
    ON message (agent_run_id, role)
    WHERE agent_run_id IS NOT NULL AND role = 'ASSISTANT';

ALTER TABLE IF EXISTS tool_call_log
    ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS tool_call_log
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS tool_call_log
    ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS knowledge_base (
    id            VARCHAR(64) PRIMARY KEY,
    owner_user_id VARCHAR(64) NOT NULL,
    name          VARCHAR(128) NOT NULL,
    description   TEXT,
    status        VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_knowledge_base_owner_status
    ON knowledge_base (owner_user_id, status);

CREATE TABLE IF NOT EXISTS rag_document (
    id                VARCHAR(64) PRIMARY KEY,
    knowledge_base_id VARCHAR(64) NOT NULL
        REFERENCES knowledge_base(id) ON DELETE CASCADE,
    owner_user_id     VARCHAR(64) NOT NULL,
    title             VARCHAR(512),
    source_type       VARCHAR(32) NOT NULL,
    source_uri        TEXT,
    mime_type         VARCHAR(128),
    content_hash      VARCHAR(128) NOT NULL,
    raw_content       TEXT NOT NULL,
    status            VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    error_message     TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rag_document_kb_content_hash
        UNIQUE (knowledge_base_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_rag_document_kb_status
    ON rag_document (knowledge_base_id, status);
CREATE INDEX IF NOT EXISTS ix_rag_document_owner_created
    ON rag_document (owner_user_id, created_at);

CREATE TABLE IF NOT EXISTS rag_document_chunk (
    id                 VARCHAR(64) PRIMARY KEY,
    document_id        VARCHAR(64) NOT NULL
        REFERENCES rag_document(id) ON DELETE CASCADE,
    knowledge_base_id  VARCHAR(64) NOT NULL
        REFERENCES knowledge_base(id) ON DELETE CASCADE,
    owner_user_id      VARCHAR(64) NOT NULL,
    chunk_index        INTEGER NOT NULL,
    content            TEXT NOT NULL,
    content_hash       VARCHAR(128) NOT NULL,
    token_count        INTEGER NOT NULL DEFAULT 0,
    page_number        INTEGER,
    section_title      TEXT,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(256) NOT NULL,
    embedding_provider VARCHAR(64) NOT NULL,
    embedding_model    VARCHAR(128) NOT NULL,
    embedding_dim      INTEGER NOT NULL,
    index_version      VARCHAR(64) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rag_chunk_doc_index_chunk
        UNIQUE (document_id, index_version, chunk_index)
);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_kb_index
    ON rag_document_chunk (knowledge_base_id, index_version);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_document_id
    ON rag_document_chunk (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_metadata
    ON rag_document_chunk USING GIN (metadata);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_embedding_hnsw
    ON rag_document_chunk USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS rag_ingestion_job (
    id                VARCHAR(64) PRIMARY KEY,
    document_id       VARCHAR(64) NOT NULL
        REFERENCES rag_document(id) ON DELETE CASCADE,
    knowledge_base_id VARCHAR(64) NOT NULL,
    owner_user_id     VARCHAR(64) NOT NULL,
    status            VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempts          INTEGER NOT NULL DEFAULT 0,
    payload           JSONB,
    error_message     TEXT,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rag_ingestion_status_created
    ON rag_ingestion_job (status, created_at);

CREATE TABLE IF NOT EXISTS rag_retrieval_log (
    id                VARCHAR(64) PRIMARY KEY,
    agent_run_id      VARCHAR(64),
    conversation_id   VARCHAR(64),
    user_id           VARCHAR(64) NOT NULL,
    knowledge_base_id VARCHAR(64) NOT NULL,
    query_hash        VARCHAR(128) NOT NULL,
    query_preview     TEXT,
    top_k             INTEGER NOT NULL,
    matched_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    scores            JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms        INTEGER NOT NULL,
    degraded          BOOLEAN NOT NULL DEFAULT false,
    reason            VARCHAR(64),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rag_retrieval_agent_run_id
    ON rag_retrieval_log (agent_run_id);
CREATE INDEX IF NOT EXISTS ix_rag_retrieval_conversation_id
    ON rag_retrieval_log (conversation_id);
CREATE INDEX IF NOT EXISTS ix_rag_retrieval_kb_created
    ON rag_retrieval_log (knowledge_base_id, created_at);
