"""
Vector Database for Semantic Search (using Supabase pgvector)

Provides embedding generation and similarity search for notes.
Uses the same Supabase pgvector setup as the original backend.

Hybrid search combines:
- Vector search (semantic similarity via pgvector)
- Keyword search (BM25/text matching)
Based on Clawdbot's hybrid.ts patterns.
"""
import os
import re
import math
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger("brainmap.vector_db")

# Hybrid search weights from Clawdbot (hybrid.ts)
from core.config import settings
HYBRID_VECTOR_WEIGHT = settings.HYBRID_VECTOR_WEIGHT
HYBRID_TEXT_WEIGHT = settings.HYBRID_TEXT_WEIGHT

# =============================================================================
# Embedding Cache (avoid duplicate API calls)
# =============================================================================
_embedding_cache: Dict[str, List[float]] = {}


def get_embedding(text: str) -> Optional[List[float]]:
    """
    Generate embedding for text using OpenAI text-embedding-3-small (1536 dimensions).
    
    Falls back to Gemini if OpenAI is not available.
    """
    # Check cache
    cache_key = text[:500]  # Use first 500 chars as key
    if cache_key in _embedding_cache:
        return _embedding_cache[cache_key]
    
    # Try OpenAI first (you have the API key)
    openai_key = os.environ.get('OPENAI_API_KEY')
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text[:8000]  # Limit to avoid token limits
            )
            embedding = response.data[0].embedding
            _embedding_cache[cache_key] = embedding
            return embedding
        except Exception as e:
            logger.warning(f"OpenAI embedding failed: {e}")
    
    # Fallback to Gemini
    google_key = os.environ.get('GOOGLE_API_KEY')
    if google_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=google_key)
            # Use the embedding model
            result = genai.embed_content(
                model="models/embedding-001",
                content=text[:8000],
                task_type="retrieval_document"
            )
            embedding = result['embedding']
            # Pad or truncate to 1536 dimensions if needed
            if len(embedding) < 1536:
                embedding = embedding + [0.0] * (1536 - len(embedding))
            elif len(embedding) > 1536:
                embedding = embedding[:1536]
            _embedding_cache[cache_key] = embedding
            return embedding
        except Exception as e:
            logger.warning(f"Gemini embedding failed: {e}")
    
    logger.error("No embedding provider available (need OPENAI_API_KEY or GOOGLE_API_KEY)")
    return None


def search_notes_by_vector(
    query: str,
    user_id: str = None,
    limit: int = 5,
    min_score: float = 0.4
) -> List[Dict[str, Any]]:
    """
    Semantic search for notes matching a query using pgvector.
    
    Args:
        query: Search query text
        user_id: Filter by user (optional for now)
        limit: Max results to return
        min_score: Minimum similarity score (0-1), rejects weak matches
        
    Returns:
        List of matching notes with similarity scores
    """
    embedding = get_embedding(query)
    if not embedding:
        return []
    
    try:
        from database import get_supabase
        client = get_supabase()
        
        # Call the match_notes RPC function
        result = client.rpc(
            'match_notes',
            {
                'query_embedding': embedding,
                'match_user_id': user_id or 'current_user',
                'match_count': limit * 2  # Get more to filter by score
            }
        ).execute()
        
        notes = []
        for row in result.data or []:
            score = row.get("similarity", 0)
            
            # Filter by minimum score
            if score >= min_score:
                notes.append({
                    "id": row.get("note_id"),
                    "user_id": row.get("user_id"),
                    "title": row.get("title"),
                    "type": row.get("type"),
                    "score": score
                })
        
        # Sort by score and limit
        notes.sort(key=lambda x: x["score"], reverse=True)
        return notes[:limit]
        
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


def upsert_note_embedding(
    note_id: str,
    title: str,
    content: str,
    user_id: str = None
) -> bool:
    """
    Store/update a note embedding in Supabase pgvector.
    
    Should be called after creating or updating a note.
    """
    text = f"{title}\n\n{content}"
    embedding = get_embedding(text)
    
    if not embedding:
        return False
    
    try:
        from database import get_supabase
        client = get_supabase()
        
        data = {
            "user_id": user_id or "current_user",
            "note_id": note_id,
            "embedding": embedding
        }
        
        client.table('note_embeddings').upsert(
            data,
            on_conflict='note_id'
        ).execute()
        
        logger.info(f"Upserted embedding for note: {note_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error upserting embedding: {e}")
        return False


def delete_note_embedding(note_id: str) -> bool:
    """Delete a note embedding."""
    try:
        from database import get_supabase
        client = get_supabase()

        client.table('note_embeddings').delete().eq('note_id', note_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting embedding: {e}")
        return False


# =============================================================================
# Hybrid Search (from Clawdbot hybrid.ts)
# =============================================================================


def bm25_rank_to_score(rank: float) -> float:
    """
    Convert BM25 rank to 0-1 score (from Clawdbot).

    BM25 returns ranks where lower is better.
    This converts to a 0-1 score where higher is better.
    """
    if rank is None or math.isinf(rank):
        normalized = 999
    else:
        normalized = max(0, rank)
    return 1 / (1 + normalized)


def build_fts_query(raw: str) -> Optional[str]:
    """
    Build full-text search query from raw text (from Clawdbot).

    Extracts alphanumeric tokens and joins with AND.
    """
    tokens = re.findall(r'[A-Za-z0-9_]+', raw)
    tokens = [t.strip() for t in tokens if t.strip()]
    if not tokens:
        return None
    # Quote each token for exact match, join with AND
    quoted = [f'"{t.replace(chr(34), "")}"' for t in tokens]
    return " & ".join(quoted)  # PostgreSQL tsquery uses &


def search_notes_by_keyword(
    query: str,
    user_id: str = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Keyword search for notes using PostgreSQL full-text search.

    Args:
        query: Search query text
        user_id: Filter by user
        limit: Max results to return

    Returns:
        List of matching notes with BM25-like ranks
    """
    fts_query = build_fts_query(query)
    if not fts_query:
        return []

    try:
        from database import get_supabase
        client = get_supabase()

        # Use PostgreSQL text search with ts_rank
        # This searches both title and content
        result = client.rpc(
            'search_notes_text',
            {
                'search_query': fts_query,
                'match_user_id': user_id or 'current_user',
                'match_count': limit
            }
        ).execute()

        notes = []
        for row in result.data or []:
            notes.append({
                "id": row.get("note_id") or row.get("id"),
                "user_id": row.get("user_id"),
                "title": row.get("title"),
                "content": row.get("content"),
                "type": row.get("type"),
                "rank": row.get("rank", 1)  # BM25-style rank
            })

        return notes

    except Exception as e:
        logger.warning(f"Keyword search error (may not have RPC): {e}")
        # Fallback to ILIKE if RPC doesn't exist
        return _search_notes_ilike_fallback(query, user_id, limit)


def _search_notes_ilike_fallback(
    query: str,
    user_id: str = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Fallback keyword search using ILIKE."""
    try:
        from database import get_supabase
        client = get_supabase()

        # Simple ILIKE search
        search_pattern = f"%{query}%"

        builder = client.table('notes').select('*')
        if user_id:
            builder = builder.eq('user_id', user_id)

        result = builder.or_(
            f"title.ilike.{search_pattern},content.ilike.{search_pattern}"
        ).limit(limit).execute()

        notes = []
        for row in result.data or []:
            # Approximate rank based on where match appears
            title = row.get("title", "")
            content = row.get("content", "")
            query_lower = query.lower()

            rank = 10  # Default high rank (worse)
            if query_lower in title.lower():
                rank = 0.5  # Title match is good
            elif query_lower in content.lower()[:200]:
                rank = 1  # Early content match
            else:
                rank = 5  # Late content match

            notes.append({
                "id": row.get("id"),
                "user_id": row.get("user_id"),
                "title": title,
                "content": content,
                "type": row.get("type"),
                "rank": rank
            })

        return notes

    except Exception as e:
        logger.error(f"ILIKE fallback search error: {e}")
        return []


def merge_hybrid_results(
    vector_results: List[Dict[str, Any]],
    keyword_results: List[Dict[str, Any]],
    vector_weight: float = HYBRID_VECTOR_WEIGHT,
    text_weight: float = HYBRID_TEXT_WEIGHT,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Merge vector and keyword search results (from Clawdbot hybrid.ts).

    Combines results using weighted scoring:
    - vector_weight * (1 - cosine_distance) = vector similarity
    - text_weight * bm25_score = keyword relevance

    Args:
        vector_results: Results from vector search (must have 'score')
        keyword_results: Results from keyword search (must have 'rank')
        vector_weight: Weight for vector results (default 0.7)
        text_weight: Weight for keyword results (default 0.3)
        limit: Max results to return

    Returns:
        Merged and sorted results with combined scores
    """
    scores = {}  # id -> {vector_score, text_score, data}

    # Add vector results
    for r in vector_results:
        note_id = r.get('id')
        if not note_id:
            continue
        scores[note_id] = {
            'vector_score': r.get('score', 0),  # Already 0-1 similarity
            'text_score': 0,
            'data': r
        }

    # Add keyword results
    for r in keyword_results:
        note_id = r.get('id')
        if not note_id:
            continue
        text_score = bm25_rank_to_score(r.get('rank', 999))

        if note_id in scores:
            scores[note_id]['text_score'] = text_score
            # Merge data (keyword results may have more fields)
            scores[note_id]['data'].update({
                k: v for k, v in r.items()
                if k not in scores[note_id]['data'] or not scores[note_id]['data'][k]
            })
        else:
            scores[note_id] = {
                'vector_score': 0,
                'text_score': text_score,
                'data': r
            }

    # Calculate final scores and build results
    results = []
    for note_id, s in scores.items():
        final_score = (vector_weight * s['vector_score']) + (text_weight * s['text_score'])
        result = {**s['data'], 'score': final_score, 'id': note_id}
        results.append(result)

    # Sort by score descending
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]


def search_notes_hybrid(
    query: str,
    user_id: str = None,
    limit: int = 10,
    min_score: float = 0.3,
    vector_weight: Optional[float] = None,
    text_weight: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    Hybrid search combining vector and keyword search (from Clawdbot).

    This is the main search function that should be used for note search.
    It combines:
    - Semantic similarity (what the user means)
    - Keyword matching (what the user says)

    Args:
        query: Search query text
        user_id: Filter by user
        limit: Max results to return
        min_score: Minimum combined score threshold

    Returns:
        List of matching notes with combined scores
    """
    # Run both searches
    vector_results = search_notes_by_vector(
        query=query,
        user_id=user_id,
        limit=limit * 2,  # Get more to merge
        min_score=0.2  # Lower threshold for vector, will filter after merge
    )

    keyword_results = search_notes_by_keyword(
        query=query,
        user_id=user_id,
        limit=limit * 2
    )

    logger.info(
        f"Hybrid search: {len(vector_results)} vector, "
        f"{len(keyword_results)} keyword results"
    )

    # Merge results
    merged = merge_hybrid_results(
        vector_results=vector_results,
        keyword_results=keyword_results,
        vector_weight=HYBRID_VECTOR_WEIGHT if vector_weight is None else vector_weight,
        text_weight=HYBRID_TEXT_WEIGHT if text_weight is None else text_weight,
        limit=limit * 2  # Get more to filter by score
    )

    # Filter by minimum score
    filtered = [r for r in merged if r.get('score', 0) >= min_score]

    logger.info(f"Hybrid search returning {len(filtered[:limit])} results")
    return filtered[:limit]
