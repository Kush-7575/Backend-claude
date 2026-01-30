-- ============================================================
-- Migration 001: Fix Schema for Railway Deployment
-- ============================================================
-- Run this in Supabase SQL Editor to update existing tables
-- Fixes errors:
--   - notes.content does not exist
--   - chat_sessions.compaction_count missing
--   - search_notes_text RPC function not found
-- ============================================================

-- 1. Add content column to notes table
ALTER TABLE notes ADD COLUMN IF NOT EXISTS content TEXT;

-- 2. Add messages, compaction_count, and metadata columns to chat_sessions
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS messages JSONB DEFAULT '[]';
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS compaction_count INT DEFAULT 0;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- 3. Add full-text search index on notes
CREATE INDEX IF NOT EXISTS idx_notes_fts ON notes
    USING gin(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, '')));

-- 4. Create search_notes_text RPC function
CREATE OR REPLACE FUNCTION search_notes_text(
    search_query TEXT,
    match_user_id TEXT DEFAULT 'default',
    match_count INT DEFAULT 10
)
RETURNS TABLE (
    id UUID,
    note_id UUID,
    user_id TEXT,
    title VARCHAR(255),
    content TEXT,
    type VARCHAR(50),
    rank FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        n.id,
        n.id as note_id,
        n.user_id,
        n.title,
        n.content,
        n.type,
        ts_rank(
            to_tsvector('english', COALESCE(n.title, '') || ' ' || COALESCE(n.content, '')),
            plainto_tsquery('english', search_query)
        )::FLOAT as rank
    FROM notes n
    WHERE n.user_id = match_user_id
      AND n.deleted = FALSE
      AND to_tsvector('english', COALESCE(n.title, '') || ' ' || COALESCE(n.content, ''))
          @@ plainto_tsquery('english', search_query)
    ORDER BY rank DESC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Done! Your schema is now updated.
-- ============================================================
