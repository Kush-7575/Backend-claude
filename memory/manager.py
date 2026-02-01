"""
Memory Manager - Supabase-Based Memory with Vector Search

Implements Clawdbot's memory patterns with Supabase persistence:
1. Daily logs table (daily_logs) - Replaces daily/*.md files
2. Long-term memory table (long_term_memory) - Replaces MEMORY.md
3. Session chunks table (session_chunks) - For semantic search
4. Pre-compaction flush (save important info before context loss)
5. Vector search across all memory sources
6. Memory importance decay over time

IMPORTANT: Uses Supabase for persistence since Railway containers are ephemeral.
Local files would be lost on redeploy!
"""
import os
import logging
import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
import json
import asyncio
import uuid

from core.config import settings

logger = logging.getLogger("brainmap.memory")

# Memory decay constants (from Clawdbot patterns)
MEMORY_DECAY_HALF_LIFE_DAYS = 30  # Memories lose half relevance every 30 days
MEMORY_MIN_RELEVANCE = 0.1  # Floor - memories never go below this


class MemoryManager:
    """
    Manages persistent memory storage.
    
    This is what makes BrainMap a "second brain" - memories persist
    across sessions and can be recalled via semantic search.
    
    Storage layers:
    1. Daily logs: Append-only files for each day
    2. MEMORY.md: Curated long-term facts
    3. Vector DB: Embeddings for semantic search
    4. Supabase: Database records (notes, reminders)
    """
    
    def __init__(self, memory_dir: Optional[str] = None, supabase_client=None):
        """
        Initialize memory manager.

        Args:
            memory_dir: Directory for file-based memory (fallback only)
            supabase_client: Supabase client for database ops (PRIMARY storage)
        """
        self._memory_dir = Path(memory_dir or settings.MEMORY_DIR)
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._supabase = supabase_client

        # Flag to track if we're using database or files
        self._use_database = supabase_client is not None

        if self._use_database:
            logger.info("Memory manager initialized with Supabase (persistent)")
        else:
            logger.warning("Memory manager using local files (EPHEMERAL on Railway!)")

    # =========================================================================
    # Memory Importance Decay (from Clawdbot patterns)
    # =========================================================================

    def _calculate_decay(
        self,
        created_at: Optional[datetime] = None,
        days_ago: Optional[int] = None
    ) -> float:
        """
        Calculate time-based relevance decay for memories.

        Uses exponential decay with configurable half-life.
        Older memories naturally become less relevant over time.

        Args:
            created_at: When the memory was created
            days_ago: Alternative: number of days ago

        Returns:
            Decay multiplier (0.1 to 1.0)
        """
        if days_ago is None:
            if created_at is None:
                return 1.0  # No decay if no date
            days_ago = (datetime.now(timezone.utc) - created_at).days

        if days_ago <= 0:
            return 1.0  # No decay for today

        # Exponential decay: e^(-t * ln(2) / half_life)
        decay = math.exp(-days_ago * math.log(2) / MEMORY_DECAY_HALF_LIFE_DAYS)

        # Apply floor
        return max(MEMORY_MIN_RELEVANCE, decay)

    def _apply_decay_to_relevance(
        self,
        base_relevance: float,
        created_at: Optional[datetime] = None,
        days_ago: Optional[int] = None
    ) -> float:
        """
        Apply time decay to a relevance score.

        Args:
            base_relevance: Original relevance score (0-1)
            created_at: When the memory was created
            days_ago: Alternative: number of days ago

        Returns:
            Decayed relevance score
        """
        decay = self._calculate_decay(created_at, days_ago)
        return base_relevance * decay

    # =========================================================================
    # Daily Logs (Supabase daily_logs table)
    # =========================================================================

    def _get_daily_log_path(self, date: Optional[datetime] = None) -> Path:
        """Get path to daily log file (fallback only)."""
        date = date or datetime.now(timezone.utc)
        filename = date.strftime("%Y-%m-%d.md")
        return self._memory_dir / "daily" / filename

    async def append_to_daily_log(
        self,
        content: str,
        timestamp: Optional[datetime] = None,
        category: str = "conversation",
        session_id: Optional[str] = None,
        user_id: str = "default"
    ) -> None:
        """
        Append content to daily log.

        This is the PRIMARY storage for ALL conversations (like Clawdbot's session JSONL files).
        Every user message + assistant response gets logged here for semantic search.

        Uses Supabase daily_logs table for persistence on Railway.
        Falls back to local files if Supabase unavailable.
        """
        ts = timestamp or datetime.now(timezone.utc)
        log_date = ts.date()

        if self._use_database:
            try:
                data = {
                    "user_id": user_id,
                    "log_date": log_date.isoformat(),
                    "entry_time": ts.isoformat(),
                    "category": category,
                    "session_id": session_id,
                    "content": content,
                    "metadata": json.dumps({"source": "auto_capture"})
                }
                self._supabase.table("daily_logs").insert(data).execute()
                logger.debug(f"Appended to daily_logs table: {log_date}")
                return
            except Exception as e:
                logger.warning(f"Supabase daily_logs insert failed, falling back to file: {e}")

        # Fallback to local file
        log_path = self._get_daily_log_path(timestamp)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        time_str = ts.strftime("%H:%M")
        header = f"## {time_str} [{category}]"
        if session_id:
            header += f" (session: {session_id[:8]})"

        entry = f"\n{header}\n{content}\n"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.debug(f"Appended to daily log file: {log_path.name}")

    async def read_daily_log(
        self,
        date: Optional[datetime] = None,
        user_id: str = "default"
    ) -> str:
        """Read daily log entries for a specific date."""
        target_date = (date or datetime.now(timezone.utc)).date()

        if self._use_database:
            try:
                response = self._supabase.table("daily_logs").select("*").eq(
                    "user_id", user_id
                ).eq(
                    "log_date", target_date.isoformat()
                ).order("entry_time").execute()

                if response.data:
                    lines = []
                    for entry in response.data:
                        ts = entry.get("entry_time", "")[:16]  # HH:MM
                        cat = entry.get("category", "")
                        content = entry.get("content", "")
                        lines.append(f"## {ts} [{cat}]\n{content}")
                    return "\n\n".join(lines)
                return ""
            except Exception as e:
                logger.warning(f"Supabase daily_logs read failed, falling back to file: {e}")

        # Fallback to local file
        log_path = self._get_daily_log_path(date)
        if log_path.exists():
            return log_path.read_text(encoding="utf-8")
        return ""
    
    # =========================================================================
    # Long-Term Memory (Supabase long_term_memory table)
    # =========================================================================

    def _get_memory_file_path(self) -> Path:
        """Get path to MEMORY.md file (fallback only)."""
        return self._memory_dir / "MEMORY.md"

    async def save_to_long_term(
        self,
        fact: str,
        category: str = "general",
        source: Optional[str] = None,
        user_id: str = "default",
        importance: float = 0.5
    ) -> Optional[str]:
        """
        Save a fact to long-term memory.

        Uses Supabase long_term_memory table with optional vector embedding.
        Falls back to local MEMORY.md if Supabase unavailable.

        Returns:
            The ID of the created memory record, or None on failure
        """
        if self._use_database:
            try:
                # Try to generate embedding for semantic search
                embedding = None
                try:
                    from database.vector_db import get_embedding
                    embedding = get_embedding(fact)
                except Exception as e:
                    logger.debug(f"Embedding generation skipped: {e}")

                data = {
                    "user_id": user_id,
                    "fact": fact,
                    "category": category,
                    "source": source,
                    "importance": importance,
                    "last_accessed": datetime.now(timezone.utc).isoformat(),
                    "access_count": 0
                }

                if embedding:
                    data["embedding"] = embedding

                response = self._supabase.table("long_term_memory").insert(data).execute()
                memory_id = response.data[0]["id"] if response.data else None
                logger.info(f"Saved to long_term_memory table: {fact[:50]}...")
                return memory_id
            except Exception as e:
                logger.warning(f"Supabase long_term_memory insert failed, falling back to file: {e}")

        # Fallback to local file
        memory_path = self._get_memory_file_path()
        entry = f"\n- [{category}] {fact}"
        if source:
            entry += f" (source: {source})"
        entry += "\n"

        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(f"Saved to MEMORY.md file: {fact[:50]}...")
        return None

    async def read_long_term_memory(self, user_id: str = "default") -> str:
        """Read all long-term memory facts."""
        if self._use_database:
            try:
                response = self._supabase.table("long_term_memory").select(
                    "fact, category, source, importance"
                ).eq(
                    "user_id", user_id
                ).order("importance", desc=True).execute()

                if response.data:
                    lines = []
                    for mem in response.data:
                        fact = mem.get("fact", "")
                        cat = mem.get("category", "general")
                        source = mem.get("source", "")
                        line = f"- [{cat}] {fact}"
                        if source:
                            line += f" (source: {source})"
                        lines.append(line)
                    return "\n".join(lines)
                return ""
            except Exception as e:
                logger.warning(f"Supabase long_term_memory read failed, falling back to file: {e}")

        # Fallback to local file
        memory_path = self._get_memory_file_path()
        if memory_path.exists():
            return memory_path.read_text(encoding="utf-8")
        return ""

    async def update_memory_access(self, memory_id: str) -> None:
        """Update last_accessed and access_count for a memory (for decay calculation)."""
        if not self._use_database:
            return

        try:
            # Get current access_count
            response = self._supabase.table("long_term_memory").select(
                "access_count"
            ).eq("id", memory_id).execute()

            if response.data:
                current_count = response.data[0].get("access_count", 0)
                self._supabase.table("long_term_memory").update({
                    "last_accessed": datetime.now(timezone.utc).isoformat(),
                    "access_count": current_count + 1
                }).eq("id", memory_id).execute()
        except Exception as e:
            logger.debug(f"Memory access update failed: {e}")
    
    # =========================================================================
    # Pre-Compaction Flush (Critical!)
    # =========================================================================
    
    async def flush_messages(
        self,
        messages: List[Dict[str, Any]],
        extract_facts: bool = True
    ) -> Dict[str, Any]:
        """
        Flush messages to memory before compaction.

        This is called by SessionManager before messages are summarized
        and dropped from context. It ensures important information is
        preserved in durable storage.

        Args:
            messages: Messages about to be compacted
            extract_facts: If True, extract key facts for long-term memory

        Returns:
            Dict with flush results including success status
        """
        logger.info(f"Flushing {len(messages)} messages to memory")

        results = {
            "success": False,  # Track overall success
            "messages_processed": len(messages),
            "daily_log_entries": 0,
            "facts_extracted": 0,
            "errors": []
        }

        # 1. Append full conversation to daily log (CRITICAL - must succeed)
        conversation_text = self._format_messages_for_log(messages)
        if conversation_text:
            try:
                await self.append_to_daily_log(
                    conversation_text,
                    category="compaction_flush"
                )
                results["daily_log_entries"] = 1
            except Exception as e:
                error_msg = f"Failed to append to daily log: {e}"
                logger.error(error_msg)
                results["errors"].append(error_msg)
                # Don't continue if we can't even write the daily log
                return results

        # 2. Extract key facts if enabled (non-critical, can fail)
        if extract_facts:
            try:
                facts = await self._extract_facts(messages)
                for fact in facts:
                    try:
                        await self.save_to_long_term(
                            fact["text"],
                            category=fact.get("category", "conversation"),
                            source="compaction_flush"
                        )
                        results["facts_extracted"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to save fact: {e}")
            except Exception as e:
                logger.warning(f"Fact extraction failed: {e}")
                results["errors"].append(f"Fact extraction failed: {e}")

        # Mark as successful if we at least saved the conversation
        results["success"] = results["daily_log_entries"] > 0

        logger.info(
            f"Memory flush {'complete' if results['success'] else 'FAILED'}: "
            f"{results['facts_extracted']} facts extracted, "
            f"{len(results['errors'])} errors"
        )
        return results
    
    def _format_messages_for_log(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages for daily log entry."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                lines.append(f"**{role}**: {content}")
        
        return "\n\n".join(lines)
    
    async def _extract_facts(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """
        Extract key facts from messages for long-term memory.
        
        This should use Claude to intelligently extract:
        - User preferences
        - Important decisions
        - Key information mentioned
        - Action items resolved
        
        For now, uses simple heuristics.
        """
        facts = []
        
        for msg in messages:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # Simple heuristics for now
            # TODO: Use Claude for intelligent extraction
            
            # Look for "remember" patterns
            if role == "user" and isinstance(content, str):
                lower = content.lower()
                if "remember" in lower or "don't forget" in lower:
                    facts.append({
                        "text": content,
                        "category": "user_request"
                    })
                elif "my " in lower and len(content) < 200:
                    # Potential preference/fact
                    facts.append({
                        "text": content,
                        "category": "preference"
                    })
        
        return facts[:5]  # Limit to avoid spam
    
    # =========================================================================
    # Search (with Hybrid Vector Search)
    # =========================================================================

    async def search(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        limit: int = 10,
        use_hybrid: bool = True,
        vector_weight: Optional[float] = None,
        text_weight: Optional[float] = None,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search across all memory sources.

        Args:
            query: Search query
            sources: Which sources to search (daily, memory, notes, sessions)
            limit: Max results
            use_hybrid: If True, use hybrid vector+keyword search for notes
            user_id: Optional user ID to scope session search

        Returns:
            List of search results with source, content, relevance
        """
        sources = sources or ["daily", "memory", "notes", "sessions"]
        results = []

        # Parallelize all searches for speed (Clawdbot pattern)
        search_tasks = []
        task_names = []

        # Search daily logs
        if "daily" in sources:
            search_tasks.append(self._search_daily_logs(query, limit))
            task_names.append("daily")

        # Search MEMORY.md
        if "memory" in sources:
            search_tasks.append(self._search_memory_file(query, limit))
            task_names.append("memory")

        # Search notes - use hybrid vector search if available
        if "notes" in sources:
            if use_hybrid:
                search_tasks.append(
                    self._search_notes_hybrid(
                        query, limit,
                        vector_weight=vector_weight,
                        text_weight=text_weight
                    )
                )
                task_names.append("notes")
            elif self._supabase:
                search_tasks.append(self._search_notes(query, limit))
                task_names.append("notes")

        # Search past sessions (from Clawdbot session indexing)
        if "sessions" in sources:
            search_tasks.append(self.search_sessions(query, user_id, limit))
            task_names.append("sessions")

        # Execute all searches in parallel
        if search_tasks:
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            for i, result in enumerate(search_results):
                if isinstance(result, Exception):
                    logger.warning(f"Memory search {task_names[i]} failed: {result}")
                    continue
                if isinstance(result, list):
                    results.extend(result)

        # Sort by relevance and limit
        results.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        return results[:limit]

    async def _search_notes_hybrid(
        self,
        query: str,
        limit: int,
        vector_weight: Optional[float] = None,
        text_weight: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Search notes using hybrid vector + keyword search (from Clawdbot)."""
        results = []

        try:
            from database.vector_db import search_notes_hybrid

            # Hybrid search returns notes with combined scores
            notes = search_notes_hybrid(
                query=query,
                user_id=None,  # Search all for now
                limit=limit,
                min_score=0.3,
                vector_weight=vector_weight,
                text_weight=text_weight
            )

            for note in notes:
                # Fix: Use 'or' to handle None values properly
                content = note.get("content") or ""
                results.append({
                    "source": "notes_hybrid",
                    "id": note.get("id"),
                    "title": note.get("title") or "",
                    "content": content[:300],
                    "relevance": note.get("score") or 0.5,  # Use hybrid score
                    "type": note.get("type") or "note"
                })

            logger.info(f"Hybrid note search found {len(results)} results")
        except ImportError:
            logger.warning("vector_db not available, falling back to text search")
            if self._supabase:
                return await self._search_notes(query, limit)
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            if self._supabase:
                return await self._search_notes(query, limit)

        return results
    
    async def _search_daily_logs(
        self,
        query: str,
        limit: int,
        user_id: str = "default"
    ) -> List[Dict[str, Any]]:
        """Search daily logs with time-based decay."""
        results = []
        query_lower = query.lower()
        now = datetime.now(timezone.utc)

        if self._use_database:
            try:
                # Use Supabase RPC function for full-text search
                response = self._supabase.rpc(
                    "search_daily_logs",
                    {
                        "search_query": query,
                        "search_user_id": user_id,
                        "max_results": limit * 2  # Get more to filter after decay
                    }
                ).execute()

                for row in response.data or []:
                    log_date = row.get("log_date")
                    content = row.get("content", "")
                    base_relevance = row.get("rank", 0.5)

                    # Calculate days ago for decay
                    days_ago = 0
                    if log_date:
                        try:
                            if isinstance(log_date, str):
                                log_dt = datetime.fromisoformat(log_date).replace(tzinfo=timezone.utc)
                            else:
                                log_dt = datetime.combine(log_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                            days_ago = (now - log_dt).days
                        except:
                            pass

                    # Apply time-based decay
                    decayed_relevance = self._apply_decay_to_relevance(
                        base_relevance, days_ago=days_ago
                    )

                    results.append({
                        "source": "daily_log",
                        "date": str(log_date) if log_date else "",
                        "content": content[:500],
                        "relevance": decayed_relevance,
                        "days_ago": days_ago
                    })

                # Sort by decayed relevance
                results.sort(key=lambda x: x["relevance"], reverse=True)
                return results[:limit]

            except Exception as e:
                logger.warning(f"Supabase daily_logs search failed, falling back to file: {e}")

        # Fallback to local file search
        daily_dir = self._memory_dir / "daily"
        if not daily_dir.exists():
            return results

        for log_file in sorted(daily_dir.glob("*.md"), reverse=True)[:30]:
            content = log_file.read_text(encoding="utf-8")
            if query_lower in content.lower():
                try:
                    file_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    days_ago = (now - file_date).days
                except:
                    days_ago = 0

                sections = content.split("\n## ")
                for section in sections:
                    if query_lower in section.lower():
                        word_count = len(section.split()) or 1
                        base_relevance = min(1.0, section.lower().count(query_lower) / word_count * 10)
                        decayed_relevance = self._apply_decay_to_relevance(
                            base_relevance, days_ago=days_ago
                        )

                        results.append({
                            "source": "daily_log",
                            "date": log_file.stem,
                            "content": section[:500],
                            "relevance": decayed_relevance,
                            "days_ago": days_ago
                        })

        return results[:limit]
    
    async def _search_memory_file(
        self,
        query: str,
        limit: int,
        user_id: str = "default"
    ) -> List[Dict[str, Any]]:
        """Search long-term memory with vector similarity if available."""
        results = []

        if self._use_database:
            try:
                # Try vector search first (semantic similarity)
                try:
                    from database.vector_db import get_embedding

                    query_embedding = get_embedding(query)
                    if query_embedding:
                        response = self._supabase.rpc(
                            "match_long_term_memory",
                            {
                                "query_embedding": query_embedding,
                                "match_user_id": user_id,
                                "match_threshold": 0.5,
                                "match_count": limit
                            }
                        ).execute()

                        for row in response.data or []:
                            # Update access tracking for decay
                            mem_id = row.get("id")
                            if mem_id:
                                await self.update_memory_access(mem_id)

                            results.append({
                                "source": "long_term_memory",
                                "id": mem_id,
                                "content": f"[{row.get('category', '')}] {row.get('fact', '')}",
                                "relevance": row.get("similarity", 0.8),
                                "importance": row.get("importance", 0.5)
                            })

                        if results:
                            return results[:limit]
                except Exception as e:
                    logger.debug(f"Vector search for memory skipped: {e}")

                # Fallback to keyword search in database
                response = self._supabase.table("long_term_memory").select(
                    "id, fact, category, importance"
                ).eq(
                    "user_id", user_id
                ).ilike(
                    "fact", f"%{query}%"
                ).order("importance", desc=True).limit(limit).execute()

                for row in response.data or []:
                    results.append({
                        "source": "long_term_memory",
                        "id": row.get("id"),
                        "content": f"[{row.get('category', '')}] {row.get('fact', '')}",
                        "relevance": row.get("importance", 0.8)
                    })

                return results[:limit]

            except Exception as e:
                logger.warning(f"Supabase long_term_memory search failed, falling back to file: {e}")

        # Fallback to local file search
        memory_content = await self.read_long_term_memory()
        if not memory_content:
            return results

        query_lower = query.lower()
        for line in memory_content.split("\n"):
            if line.startswith("- [") and query_lower in line.lower():
                results.append({
                    "source": "long_term_memory",
                    "content": line,
                    "relevance": 0.8
                })

        return results[:limit]
    
    async def _search_notes(
        self,
        query: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Search notes in Supabase."""
        results = []
        
        if not self._supabase:
            return results
        
        try:
            # Text search (Supabase full-text)
            response = self._supabase.table("notes")\
                .select("id, title, content")\
                .textSearch("content", query)\
                .limit(limit)\
                .execute()
            
            for note in response.data:
                results.append({
                    "source": "notes",
                    "id": note["id"],
                    "title": note.get("title", ""),
                    "content": note.get("content", "")[:300],
                    "relevance": 0.7
                })
        except Exception as e:
            logger.error(f"Notes search failed: {e}")
        
        return results
    
    # =========================================================================
    # Context Building (with Hybrid Vector Search)
    # =========================================================================

    async def get_relevant_context(
        self,
        query: str,
        max_tokens: int = 2000,
        use_hybrid: bool = True
    ) -> str:
        """
        Get relevant memory context for a query using hybrid vector search.

        This is used to inject relevant memories into the system prompt
        or conversation context. Uses semantic + keyword search for best results.

        Args:
            query: The user's query to find relevant memories for
            max_tokens: Maximum context size (approximate)
            use_hybrid: Use hybrid vector+keyword search (default True)
        """
        results = await self.search(query, limit=8, use_hybrid=use_hybrid)

        if not results:
            return ""

        context_parts = ["## Relevant Memories"]
        char_count = 0
        max_chars = max_tokens * 4  # Rough token to char ratio

        for r in results:
            source = r.get("source", "unknown")
            content = r.get("content", "")
            relevance = r.get("relevance", 0)

            # Build entry based on source
            if source == "daily_log":
                date = r.get("date", "")
                entry = f"**{date}**: {content[:200]}"
            elif source == "long_term_memory":
                entry = f"• {content}"
            elif source in ("notes", "notes_hybrid"):
                title = r.get("title", "Note")
                score_str = f" (relevance: {relevance:.2f})" if relevance else ""
                entry = f"**{title}**{score_str}: {content[:250]}"
            else:
                entry = f"• {content[:200]}"

            # Check length limit
            if char_count + len(entry) > max_chars:
                break

            context_parts.append(entry)
            char_count += len(entry)

        return "\n".join(context_parts)


    # =========================================================================
    # Auto Memory Capture (from Clawdbot lifecycle hooks)
    # =========================================================================

    async def auto_capture_from_exchange(
        self,
        user_message: str,
        assistant_response: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Automatically capture ALL conversation exchanges to memory.

        This implements Clawdbot's pattern where EVERY message is logged
        to session files (daily logs in our case) for later semantic search.

        Unlike before (keyword-filtered), this now logs ALL exchanges.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response
            session_id: Optional session ID for tracking

        Returns:
            Dict with capture results
        """
        results = {
            "captured": False,
            "facts_saved": 0,
            "daily_logged": False
        }

        # Skip only truly trivial exchanges (single word acknowledgments)
        if len(user_message.strip()) < 3:
            return results

        # 1. ALWAYS log the full exchange to daily log (like Clawdbot session files)
        # This makes ALL conversations searchable via memory_search
        try:
            # Format like Clawdbot: "User: [full message]\nAssistant: [full response]"
            # No truncation - log the complete exchange for semantic search
            exchange_content = f"User: {user_message}\nAssistant: {assistant_response}"
            await self.append_to_daily_log(
                exchange_content,
                category="conversation",
                session_id=session_id
            )
            results["daily_logged"] = True
            results["captured"] = True
        except Exception as e:
            logger.warning(f"Failed to log exchange to daily log: {e}")

        # 2. Additionally extract facts for MEMORY.md if content is memorable
        if self._should_capture(user_message, assistant_response):
            facts = self._extract_facts_from_exchange(user_message, assistant_response)

            for fact in facts:
                try:
                    await self.save_to_long_term(
                        fact["text"],
                        category=fact.get("category", "auto_capture"),
                        source=session_id or "conversation"
                    )
                    results["facts_saved"] += 1
                except Exception as e:
                    logger.warning(f"Failed to save auto-captured fact: {e}")

        return results

    def _should_capture(self, user_message: str, assistant_response: str) -> bool:
        """
        Determine if an exchange should trigger memory capture.

        Capture when:
        - User explicitly asks to remember something
        - User shares personal preferences
        - Important decisions are made
        - Action items are discussed
        """
        user_lower = user_message.lower()
        response_lower = assistant_response.lower()

        # Explicit remember requests
        remember_keywords = ["remember", "don't forget", "keep in mind", "note that", "important:"]
        if any(kw in user_lower for kw in remember_keywords):
            return True

        # Personal information shared
        personal_keywords = ["my name is", "i prefer", "i like", "i hate", "i always", "i never",
                            "my favorite", "my birthday", "my email", "my phone", "my address"]
        if any(kw in user_lower for kw in personal_keywords):
            return True

        # Decision or commitment language
        decision_keywords = ["i decided", "i'll go with", "let's do", "i want to", "i'm going to"]
        if any(kw in user_lower for kw in decision_keywords):
            return True

        # Action items in response
        action_keywords = ["i've created", "i've added", "reminder set", "note saved", "scheduled"]
        if any(kw in response_lower for kw in action_keywords):
            return True

        return False

    def _extract_facts_from_exchange(
        self,
        user_message: str,
        assistant_response: str
    ) -> List[Dict[str, str]]:
        """
        Extract memorable facts from a conversation exchange.

        For now uses heuristics. Can be upgraded to Claude extraction later.
        """
        facts = []
        user_lower = user_message.lower()

        # Extract "remember X" patterns
        if "remember" in user_lower:
            # The fact is usually what follows "remember"
            parts = user_message.lower().split("remember")
            if len(parts) > 1:
                fact_text = parts[1].strip()
                if fact_text and len(fact_text) > 10:
                    facts.append({
                        "text": f"User asked to remember: {user_message[:200]}",
                        "category": "user_request"
                    })

        # Extract "my X is Y" patterns
        import re
        my_patterns = [
            r"my (\w+) is (.+?)(?:\.|,|$)",
            r"my (\w+) are (.+?)(?:\.|,|$)",
            r"i prefer (.+?)(?:\.|,|$)",
            r"i like (.+?)(?:\.|,|$)"
        ]
        for pattern in my_patterns:
            matches = re.findall(pattern, user_lower)
            for match in matches:
                if isinstance(match, tuple):
                    fact_text = " ".join(match)
                else:
                    fact_text = match
                if len(fact_text) > 5:
                    facts.append({
                        "text": f"User preference: {fact_text}",
                        "category": "preference"
                    })
                    break  # Only one per pattern

        return facts[:3]  # Limit to avoid spam

    # =========================================================================
    # Session Content Indexing (from Clawdbot sync-session-files.ts)
    # =========================================================================

    async def index_session_for_search(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Index session content for semantic search.

        This implements Clawdbot's session file indexing pattern,
        making past conversations searchable via vector embeddings.

        Args:
            session_id: Session identifier
            messages: List of messages to index
            user_id: User identifier for scoping search

        Returns:
            Dict with indexing results
        """
        results = {
            "indexed": False,
            "chunks_created": 0,
            "errors": []
        }

        if not messages or len(messages) < 2:
            return results

        try:
            from database.vector_db import get_embedding

            # Chunk the session content
            chunks = self._chunk_session_messages(messages, session_id)

            if not chunks:
                return results

            # Store embeddings for each chunk
            for chunk in chunks:
                try:
                    embedding = get_embedding(chunk["text"])
                    if embedding:
                        # Store in session_embeddings table (if it exists)
                        await self._store_session_embedding(
                            session_id=session_id,
                            chunk_id=chunk["id"],
                            text=chunk["text"],
                            embedding=embedding,
                            user_id=user_id
                        )
                        results["chunks_created"] += 1
                except Exception as e:
                    results["errors"].append(str(e))

            results["indexed"] = results["chunks_created"] > 0
            logger.info(f"Indexed session {session_id}: {results['chunks_created']} chunks")

        except ImportError:
            logger.warning("vector_db not available for session indexing")
        except Exception as e:
            logger.error(f"Session indexing failed: {e}")
            results["errors"].append(str(e))

        return results

    def _chunk_session_messages(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        max_chunk_size: int = 1000
    ) -> List[Dict[str, str]]:
        """
        Chunk session messages for embedding.

        Creates overlapping chunks of conversation for better
        semantic retrieval (from Clawdbot chunking patterns).
        """
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_index = 0

        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue

            # Format: "User: message" or "Assistant: message"
            formatted = f"{role.capitalize()}: {content}"
            msg_size = len(formatted)

            # If adding this message exceeds chunk size, save current chunk
            if current_size + msg_size > max_chunk_size and current_chunk:
                chunk_text = "\n\n".join(current_chunk)
                chunks.append({
                    "id": f"{session_id}-chunk-{chunk_index}",
                    "text": chunk_text,
                    "start_index": i - len(current_chunk),
                    "end_index": i - 1
                })
                chunk_index += 1
                # Keep last message for overlap
                current_chunk = [current_chunk[-1]] if current_chunk else []
                current_size = len(current_chunk[0]) if current_chunk else 0

            current_chunk.append(formatted)
            current_size += msg_size

        # Save final chunk
        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append({
                "id": f"{session_id}-chunk-{chunk_index}",
                "text": chunk_text,
                "start_index": len(messages) - len(current_chunk),
                "end_index": len(messages) - 1
            })

        return chunks

    async def _store_session_embedding(
        self,
        session_id: str,
        chunk_id: str,
        text: str,
        embedding: List[float],
        user_id: Optional[str] = None,
        chunk_index: int = 0
    ) -> bool:
        """
        Store a session chunk embedding in Supabase session_chunks table.

        Uses the session_chunks table defined in memory_schema.sql.
        """
        if not self._use_database:
            return await self._store_session_embedding_local(
                session_id, chunk_id, text, embedding
            )

        try:
            data = {
                "session_id": session_id,
                "user_id": user_id or "default",
                "text": text[:2000],  # Limit text size
                "chunk_index": chunk_index,
                "embedding": embedding
            }

            self._supabase.table("session_chunks").insert(data).execute()
            return True
        except Exception as e:
            logger.warning(f"Supabase session_chunks insert failed: {e}")
            return await self._store_session_embedding_local(
                session_id, chunk_id, text, embedding
            )

    async def _store_session_embedding_local(
        self,
        session_id: str,
        chunk_id: str,
        text: str,
        embedding: List[float]
    ) -> bool:
        """Fallback: store session embedding in local JSON file."""
        try:
            embeddings_dir = self._memory_dir / "embeddings"
            embeddings_dir.mkdir(parents=True, exist_ok=True)

            embeddings_file = embeddings_dir / f"{session_id}.json"

            # Load existing
            existing = {}
            if embeddings_file.exists():
                existing = json.loads(embeddings_file.read_text())

            # Add new chunk
            existing[chunk_id] = {
                "text": text[:2000],
                "embedding": embedding[:100],  # Store truncated for local (save space)
                "created_at": datetime.now(timezone.utc).isoformat()  # For decay calculation
            }

            # Save
            embeddings_file.write_text(json.dumps(existing))
            return True
        except Exception as e:
            logger.error(f"Local session embedding failed: {e}")
            return False

    async def search_sessions(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search across past session content using vector similarity.

        This enables "what did we discuss about X?" type queries.
        Uses session_chunks table with pgvector for semantic search.
        """
        results = []

        try:
            from database.vector_db import get_embedding

            query_embedding = get_embedding(query)
            if not query_embedding:
                return results

            if self._use_database:
                # Use Supabase RPC for vector search (match_session_chunks from memory_schema.sql)
                response = self._supabase.rpc(
                    "match_session_chunks",
                    {
                        "query_embedding": query_embedding,
                        "match_user_id": user_id or "default",
                        "match_count": limit
                    }
                ).execute()

                for row in response.data or []:
                    # Apply time-based decay to relevance
                    base_relevance = row.get("similarity", 0.5)
                    created_at = row.get("created_at")

                    if created_at:
                        try:
                            created_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                            decayed_relevance = self._apply_decay_to_relevance(
                                base_relevance, created_at=created_dt
                            )
                        except:
                            decayed_relevance = base_relevance
                    else:
                        decayed_relevance = base_relevance

                    results.append({
                        "source": "session",
                        "session_id": row.get("session_id"),
                        "content": row.get("text", "")[:300],
                        "relevance": decayed_relevance
                    })
            else:
                # Fallback: search local embeddings
                results = await self._search_local_session_embeddings(
                    query_embedding, limit
                )

        except ImportError:
            logger.warning("vector_db not available for session search")
        except Exception as e:
            logger.error(f"Session search failed: {e}")

        return results

    async def _search_local_session_embeddings(
        self,
        query_embedding: List[float],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Search local session embeddings (fallback when no Supabase)."""
        results = []
        embeddings_dir = self._memory_dir / "embeddings"

        if not embeddings_dir.exists():
            return results

        try:
            import numpy as np

            query_vec = np.array(query_embedding[:100])  # Match truncated size

            for emb_file in embeddings_dir.glob("*.json"):
                session_id = emb_file.stem
                data = json.loads(emb_file.read_text())

                for chunk_id, chunk_data in data.items():
                    chunk_vec = np.array(chunk_data.get("embedding", []))
                    if len(chunk_vec) == 0:
                        continue

                    # Cosine similarity
                    similarity = np.dot(query_vec, chunk_vec) / (
                        np.linalg.norm(query_vec) * np.linalg.norm(chunk_vec) + 1e-8
                    )

                    # Apply time-based decay
                    base_relevance = float(similarity)
                    created_at_str = chunk_data.get("created_at")
                    if created_at_str:
                        try:
                            created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            decayed_relevance = self._apply_decay_to_relevance(
                                base_relevance, created_at=created_dt
                            )
                        except:
                            decayed_relevance = base_relevance
                    else:
                        decayed_relevance = base_relevance

                    results.append({
                        "source": "session",
                        "session_id": session_id,
                        "chunk_id": chunk_id,
                        "content": chunk_data.get("text", "")[:300],
                        "relevance": decayed_relevance
                    })

            # Sort by relevance and limit
            results.sort(key=lambda x: x["relevance"], reverse=True)
            return results[:limit]

        except ImportError:
            logger.warning("numpy not available for local embedding search")
            return []
        except Exception as e:
            logger.error(f"Local session search failed: {e}")
            return []


# Factory
def get_memory_manager(
    memory_dir: Optional[str] = None,
    supabase_client=None
) -> MemoryManager:
    """Create a MemoryManager instance."""
    return MemoryManager(memory_dir, supabase_client)
