-- ============================================================
-- SUPABASE-CLAUDE: Complete Database Schema for Backend-claude
-- ============================================================
-- Run this ENTIRE file in Supabase SQL Editor to set up all tables
--
-- Project: BrainMap Backend-claude
-- Purpose: Voice-enabled AI assistant with persistent memory
-- Deployment: Railway (ephemeral containers - all data in Supabase)
-- ============================================================

-- ============================================================
-- EXTENSIONS
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable vector similarity search (for semantic search)
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. NOTES (User-saved content)
-- ============================================================
-- Notes are containers that hold multiple items
-- Example: "Coffee Preferences" note with items like "likes oat milk", "dark roast"

CREATE TABLE IF NOT EXISTS notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    title VARCHAR(255) NOT NULL,
    content TEXT,  -- Optional direct content (for simple notes without items)
    type VARCHAR(50) DEFAULT 'collection',  -- 'collection' or 'list'
    pinned BOOLEAN DEFAULT FALSE,
    archived BOOLEAN DEFAULT FALSE,
    deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for notes
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_deleted ON notes(deleted);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);

-- Full-text search index for notes
CREATE INDEX IF NOT EXISTS idx_notes_fts ON notes
    USING gin(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, '')));

-- ============================================================
-- 2. NOTE ITEMS (Content within notes)
-- ============================================================
-- Each note can have multiple items (facts, list entries, etc.)
-- For 'list' type notes, items can be checked/unchecked

CREATE TABLE IF NOT EXISTS note_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    note_id UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL DEFAULT 'default',
    content TEXT NOT NULL,
    checked BOOLEAN DEFAULT FALSE,  -- For checklist items
    metadata JSONB DEFAULT '{}',
    added_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for note_items
CREATE INDEX IF NOT EXISTS idx_note_items_note ON note_items(note_id);
CREATE INDEX IF NOT EXISTS idx_note_items_user ON note_items(user_id);

-- ============================================================
-- 3. NOTE EMBEDDINGS (Vector search for notes)
-- ============================================================
-- Stores vector embeddings for semantic search across notes

CREATE TABLE IF NOT EXISTS note_embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    note_id UUID UNIQUE NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL DEFAULT 'default',
    embedding vector(1536),  -- OpenAI text-embedding-3-small dimensions
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Vector similarity index (HNSW for fast search)
CREATE INDEX IF NOT EXISTS idx_note_embeddings_vector ON note_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 4. ACTION ITEMS (Tasks, Reminders, To-dos)
-- ============================================================
-- Single table for all task-like items
-- Note: There is NO separate "reminders" table - this handles everything

CREATE TABLE IF NOT EXISTS action_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending',  -- 'pending', 'completed', 'cancelled'
    priority VARCHAR(20) DEFAULT 'medium',  -- 'low', 'medium', 'high'
    completed BOOLEAN DEFAULT FALSE,
    due_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for action_items
CREATE INDEX IF NOT EXISTS idx_action_items_user ON action_items(user_id);
CREATE INDEX IF NOT EXISTS idx_action_items_status ON action_items(status);
CREATE INDEX IF NOT EXISTS idx_action_items_due ON action_items(due_at);
CREATE INDEX IF NOT EXISTS idx_action_items_completed ON action_items(completed);

-- ============================================================
-- 5. CHAT SESSIONS (Conversation containers)
-- ============================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    title VARCHAR(255),
    messages JSONB DEFAULT '[]',  -- Array of message objects
    compaction_count INT DEFAULT 0,  -- Number of times session was compacted
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for chat_sessions
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON chat_sessions(active);

-- ============================================================
-- 6. DAILY LOGS (Conversation History)
-- ============================================================
-- Stores ALL conversation exchanges for searchability
-- Replaces local daily/*.md files (ephemeral on Railway)

CREATE TABLE IF NOT EXISTS daily_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    log_date DATE NOT NULL DEFAULT CURRENT_DATE,
    entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    category TEXT DEFAULT 'conversation',  -- 'conversation', 'auto_capture', 'compaction_flush'
    session_id TEXT,
    content TEXT NOT NULL,  -- Full "User: ...\nAssistant: ..." exchange
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for daily_logs
CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date ON daily_logs(user_id, log_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_logs_category ON daily_logs(category);
CREATE INDEX IF NOT EXISTS idx_daily_logs_session ON daily_logs(session_id);

-- Full-text search index
CREATE INDEX IF NOT EXISTS idx_daily_logs_content_fts ON daily_logs
    USING gin(to_tsvector('english', content));

-- ============================================================
-- 7. LONG-TERM MEMORY (Facts & Preferences)
-- ============================================================
-- Stores extracted facts about the user
-- Replaces local MEMORY.md file (ephemeral on Railway)

CREATE TABLE IF NOT EXISTS long_term_memory (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    fact TEXT NOT NULL,
    category TEXT DEFAULT 'general',  -- 'preference', 'personal', 'decision', 'contact', etc.
    source TEXT,  -- 'conversation', 'compaction_flush', 'manual'
    importance FLOAT DEFAULT 0.5,  -- 0.0 to 1.0
    last_accessed TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0,
    embedding vector(1536),  -- For semantic search
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for long_term_memory
CREATE INDEX IF NOT EXISTS idx_long_term_memory_user ON long_term_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_long_term_memory_category ON long_term_memory(category);
CREATE INDEX IF NOT EXISTS idx_long_term_memory_importance ON long_term_memory(importance DESC);

-- Full-text search index
CREATE INDEX IF NOT EXISTS idx_long_term_memory_fact_fts ON long_term_memory
    USING gin(to_tsvector('english', fact));

-- Vector similarity index
CREATE INDEX IF NOT EXISTS idx_long_term_memory_embedding ON long_term_memory
    USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 8. SESSION CHUNKS (Indexed Session Content)
-- ============================================================
-- Stores chunked conversation content with embeddings
-- Enables semantic search across past conversations

CREATE TABLE IF NOT EXISTS session_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL,
    text TEXT NOT NULL,
    chunk_index INT DEFAULT 0,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for session_chunks
CREATE INDEX IF NOT EXISTS idx_session_chunks_user ON session_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_session_chunks_session ON session_chunks(session_id);

-- Vector similarity index
CREATE INDEX IF NOT EXISTS idx_session_chunks_embedding ON session_chunks
    USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 9. USAGE LOGS (API Usage Tracking)
-- ============================================================

CREATE TABLE IF NOT EXISTS usage_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT DEFAULT 'default',
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost FLOAT DEFAULT 0.0,
    endpoint TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for usage_logs
CREATE INDEX IF NOT EXISTS idx_usage_logs_user ON usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created ON usage_logs(created_at DESC);

-- ============================================================
-- RPC FUNCTIONS (For Search Operations)
-- ============================================================

-- ============================================================
-- Function: Search Daily Logs (Full-Text Search)
-- ============================================================
CREATE OR REPLACE FUNCTION search_daily_logs(
    search_query TEXT,
    search_user_id TEXT DEFAULT 'default',
    max_results INT DEFAULT 10
)
RETURNS TABLE (
    id UUID,
    log_date DATE,
    entry_time TIMESTAMPTZ,
    category TEXT,
    content TEXT,
    rank FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        dl.id,
        dl.log_date,
        dl.entry_time,
        dl.category,
        dl.content,
        ts_rank(to_tsvector('english', dl.content), plainto_tsquery('english', search_query))::FLOAT as rank
    FROM daily_logs dl
    WHERE dl.user_id = search_user_id
      AND to_tsvector('english', dl.content) @@ plainto_tsquery('english', search_query)
    ORDER BY rank DESC, dl.entry_time DESC
    LIMIT max_results;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Function: Match Long-Term Memory (Vector Similarity)
-- ============================================================
CREATE OR REPLACE FUNCTION match_long_term_memory(
    query_embedding vector(1536),
    match_user_id TEXT DEFAULT 'default',
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    fact TEXT,
    category TEXT,
    importance FLOAT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ltm.id,
        ltm.fact,
        ltm.category,
        ltm.importance,
        (1 - (ltm.embedding <=> query_embedding))::FLOAT as similarity
    FROM long_term_memory ltm
    WHERE ltm.user_id = match_user_id
      AND ltm.embedding IS NOT NULL
      AND (1 - (ltm.embedding <=> query_embedding)) > match_threshold
    ORDER BY ltm.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Function: Match Session Chunks (Vector Similarity)
-- ============================================================
CREATE OR REPLACE FUNCTION match_session_chunks(
    query_embedding vector(1536),
    match_user_id TEXT DEFAULT 'default',
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    session_id TEXT,
    text TEXT,
    similarity FLOAT,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        sc.id,
        sc.session_id,
        sc.text,
        (1 - (sc.embedding <=> query_embedding))::FLOAT as similarity,
        sc.created_at
    FROM session_chunks sc
    WHERE sc.user_id = match_user_id
      AND sc.embedding IS NOT NULL
    ORDER BY sc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Function: Search Notes by Text (Full-Text Search)
-- ============================================================
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
-- Function: Match Notes (Vector Similarity)
-- ============================================================
CREATE OR REPLACE FUNCTION match_notes(
    query_embedding vector(1536),
    match_user_id TEXT DEFAULT 'default',
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    note_id UUID,
    title VARCHAR(255),
    type VARCHAR(50),
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ne.id,
        ne.note_id,
        n.title,
        n.type,
        (1 - (ne.embedding <=> query_embedding))::FLOAT as similarity
    FROM note_embeddings ne
    JOIN notes n ON n.id = ne.note_id
    WHERE ne.user_id = match_user_id
      AND ne.embedding IS NOT NULL
      AND n.deleted = FALSE
      AND (1 - (ne.embedding <=> query_embedding)) > match_threshold
    ORDER BY ne.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- TRIGGERS (Auto-update timestamps)
-- ============================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to tables with updated_at
DROP TRIGGER IF EXISTS update_notes_updated_at ON notes;
CREATE TRIGGER update_notes_updated_at
    BEFORE UPDATE ON notes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_action_items_updated_at ON action_items;
CREATE TRIGGER update_action_items_updated_at
    BEFORE UPDATE ON action_items
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_chat_sessions_updated_at ON chat_sessions;
CREATE TRIGGER update_chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_long_term_memory_updated_at ON long_term_memory;
CREATE TRIGGER update_long_term_memory_updated_at
    BEFORE UPDATE ON long_term_memory
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- SUMMARY
-- ============================================================
--
-- Tables Created:
--   1. notes              - Note containers (title, type)
--   2. note_items         - Content within notes
--   3. note_embeddings    - Vector embeddings for notes
--   4. action_items       - Tasks/reminders/to-dos (ALL in one table)
--   5. chat_sessions      - Conversation sessions
--   6. daily_logs         - All conversation history
--   7. long_term_memory   - Extracted facts about user
--   8. session_chunks     - Indexed session content for search
--   9. usage_logs         - API usage tracking
--
-- RPC Functions:
--   - search_daily_logs()      - Full-text search on conversations
--   - match_long_term_memory() - Vector search on memories
--   - match_session_chunks()   - Vector search on sessions
--   - match_notes()            - Vector search on notes
--
-- Key Points:
--   - NO separate "reminders" table - use action_items
--   - Notes have items (parent-child relationship)
--   - All data persisted in Supabase (Railway is ephemeral)
--   - Vector search enabled via pgvector extension
--
-- ============================================================
