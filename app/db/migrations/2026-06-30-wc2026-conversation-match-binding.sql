-- Add persistent WC2026 conversation-to-match binding for existing databases.

ALTER TABLE IF EXISTS conversation
    ADD COLUMN IF NOT EXISTS wc2026_match_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_conversation_user_wc2026_match
    ON conversation (user_id, wc2026_match_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_user_wc2026_match
    ON conversation (user_id, wc2026_match_id)
    WHERE wc2026_match_id IS NOT NULL;
