"""数据库种子脚本(可执行)。

幂等地写入一条示例会话与若干消息,并加载 scripts/sample_knowledge.json
中的示例知识库文档(供 RAG ingest)。可通过以下任一方式运行:

    python -m app.db.seed
    python scripts/seed.py   # scripts/seed.py 转调本模块的 main()

为保证零外部依赖即可演示,运行前会用 Base.metadata.create_all 确保业务表存在,
因此对全新的空库也能直接跑通。所有 DB 操作经仓储层完成并捕获领域错误。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.enums import MessageRole
from app.core.ids import new_conversation_id
from app.core.logging import configure_logging, get_logger
from app.core.models import Base
from app.db.errors import DuplicateEntityError, RepositoryError
from app.db.repositories import ConversationRepository, MessageRepository
from app.db.session import async_session_factory, dispose_engine, engine

logger = get_logger(__name__)

# 示例知识库文件相对项目根的路径
_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "sample_knowledge.json"
)

# 固定的示例会话 ID,使脚本可重复运行而不重复插入会话
_SEED_CONVERSATION_ID = "conv_seed_demo"

_SEED_MESSAGES: list[tuple[MessageRole, str]] = [
    (MessageRole.SYSTEM, "你是一个有用的通用智能助手。"),
    (MessageRole.USER, "什么是 RAG?"),
    (
        MessageRole.ASSISTANT,
        "RAG 是检索增强生成,先检索相关文档再让模型据此作答,可降低幻觉。",
    ),
]


async def _ensure_tables() -> None:
    """确保业务表存在(对空库友好,幂等)。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS conversation
                        ADD COLUMN IF NOT EXISTS wc2026_match_id VARCHAR(64);
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_conversation_user_wc2026_match
                        ON conversation (user_id, wc2026_match_id);
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_user_wc2026_match
                        ON conversation (user_id, wc2026_match_id)
                        WHERE wc2026_match_id IS NOT NULL;
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_message_assistant_per_run
                        ON message (agent_run_id, role)
                        WHERE agent_run_id IS NOT NULL AND role = 'ASSISTANT';
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS idempotency_record
                        DROP CONSTRAINT IF EXISTS idempotency_record_agent_run_id_fkey;
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS idempotency_record
                        ADD CONSTRAINT idempotency_record_agent_run_id_fkey
                        FOREIGN KEY (agent_run_id)
                        REFERENCES agent_run(id)
                        ON DELETE CASCADE
                        DEFERRABLE INITIALLY DEFERRED;
                    """
                )
            )


def load_knowledge_docs() -> list[dict[str, Any]]:
    """读取并校验示例知识库文档列表。

    每条文档必须含 id 与 text 字段(VectorStore.add 的最低要求),
    缺字段则抛出 ValueError 快速失败。
    """
    if not _KNOWLEDGE_PATH.exists():
        raise FileNotFoundError(f"sample knowledge not found: {_KNOWLEDGE_PATH}")
    raw = _KNOWLEDGE_PATH.read_text(encoding="utf-8")
    docs = json.loads(raw)
    if not isinstance(docs, list):
        raise ValueError("sample_knowledge.json 顶层必须是数组")
    for idx, doc in enumerate(docs):
        if not isinstance(doc, dict) or "id" not in doc or "text" not in doc:
            raise ValueError(f"第 {idx} 篇文档缺少 id/text 字段")
    return docs


def _new_message_id() -> str:
    """生成消息主键(复用 uuid 生成器,替换前缀为 msg_)。"""
    return new_conversation_id().replace("conv_", "msg_", 1)


async def _seed_conversation() -> str:
    """幂等写入示例会话与消息,返回会话 ID。"""
    async with async_session_factory() as session:
        conv_repo = ConversationRepository(session)
        msg_repo = MessageRepository(session)
        try:
            await conv_repo.create(
                _SEED_CONVERSATION_ID, user_id="user_demo", title="示例会话"
            )
        except DuplicateEntityError:
            logger.info("示例会话已存在,跳过创建: %s", _SEED_CONVERSATION_ID)
            return _SEED_CONVERSATION_ID
        for role, content in _SEED_MESSAGES:
            await msg_repo.create(
                _new_message_id(), _SEED_CONVERSATION_ID, role, content
            )
        return _SEED_CONVERSATION_ID


async def seed() -> dict[str, Any]:
    """执行完整种子流程,返回汇总信息(会话 ID 与文档数量)。"""
    await _ensure_tables()
    conv_id = await _seed_conversation()
    docs = load_knowledge_docs()
    logger.info("种子完成: conversation=%s, knowledge_docs=%d", conv_id, len(docs))
    return {"conversation_id": conv_id, "knowledge_docs": len(docs)}


async def main() -> None:
    """脚本入口:配置日志、执行种子、清理引擎连接池。"""
    configure_logging("INFO")
    try:
        summary = await seed()
        logger.info("seed summary: %s", summary)
    except (RepositoryError, OSError, ValueError) as exc:
        logger.error("种子失败: %s", exc)
        raise
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
