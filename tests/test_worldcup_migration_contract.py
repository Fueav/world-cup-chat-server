"""World Cup project migration contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.api.main import create_app
from app.runtime.agent_factory import _SYSTEM_PROMPT


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_identity_is_worldcup_not_source_agent_detail_product():
    assert "World Cup Match Forecast Chat Server" in _SYSTEM_PROMPT
    assert "世界杯比赛预测" in _SYSTEM_PROMPT
    assert "Polymarket" in _SYSTEM_PROMPT
    assert "比分概率" in _SYSTEM_PROMPT
    assert "Ask this Agent" not in _SYSTEM_PROMPT
    assert "Top Holders" not in _SYSTEM_PROMPT
    assert "Mint" not in _SYSTEM_PROMPT
    assert "Redeem" not in _SYSTEM_PROMPT


def test_app_title_and_dockerhost_template_use_worldcup_project_name():
    app = create_app()
    assert app.title == "World Cup Chat Server"

    template = (ROOT / "dockerhost" / "template.yaml").read_text(encoding="utf-8")
    assert "name: world-cup-chat-server" in template
    assert "general-agent-ai" not in template


def test_sample_knowledge_is_worldcup_seed_not_moss_seed():
    rows = json.loads((ROOT / "scripts" / "sample_knowledge.json").read_text(encoding="utf-8"))
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "世界杯预测" in serialized
    assert "Polymarket" in serialized
    assert "no-bet" in serialized
    assert "MOSS" not in serialized
    assert "Ask this Agent" not in serialized


def test_init_sql_persists_wc2026_conversation_match_binding():
    sql = (ROOT / "app" / "db" / "init.sql").read_text(encoding="utf-8").lower()
    migration = (
        ROOT
        / "app"
        / "db"
        / "migrations"
        / "2026-06-30-wc2026-conversation-match-binding.sql"
    ).read_text(encoding="utf-8").lower()

    assert "wc2026_match_id" in sql
    assert "alter table if exists conversation" in sql
    assert "ix_conversation_user_wc2026_match" in sql
    assert "uq_conversation_user_wc2026_match" in sql
    assert "wc2026_match_id" in migration
    assert "alter table if exists conversation" in migration
    assert "uq_conversation_user_wc2026_match" in migration
