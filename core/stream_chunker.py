"""
Stream Block Chunker - Intelligent Text Chunking for Streaming

Adapted from Clawdbot's EmbeddedBlockChunker pattern.
Instead of sending every token, buffers and chunks at natural break points
(paragraphs, sentences, newlines) for smoother UI rendering.

Also handles:
- Thinking block filtering (<think>...</think>) - strips content
- Final tag handling (<final>...</final>) - strips tags, keeps content
- Code fence awareness (never breaks inside code blocks)
- Inline code span protection (backticks)
- Min/max character limits
- Partial tag handling (tags split across chunks)

Based on Clawdbot's:
- pi-embedded-block-chunker.ts
- pi-embedded-subscribe.ts (stripBlockTags)
- markdown/code-spans.ts
- markdown/fences.ts
"""
import re
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("brainmap.stream_chunker")

# Regex patterns exactly matching Clawdbot's pi-embedded-subscribe.ts line 20-21
THINKING_TAG_SCAN_RE = re.compile(
    r'<\s*(/?)\s*(?:think(?:ing)?|thought|antthinking)\s*>',
    re.IGNORECASE
)

FINAL_TAG_SCAN_RE = re.compile(
    r'<\s*(/?)\s*final\s*>',
    re.IGNORECASE
)

# For backwards compatibility
THINKING_TAG_RE = THINKING_TAG_SCAN_RE
FINAL_TAG_RE = FINAL_TAG_SCAN_RE


@dataclass
class ChunkingConfig:
    """Configuration for block chunking."""
    min_chars: int = 50
    max_chars: int = 500
    break_preference: str = "paragraph"
    enforce_final_tag: bool = False  # If True, only text inside <final> is shown


@dataclass
class FenceSpan:
    """Represents a code fence region."""
    start: int
    end: int  # -1 if unclosed
    marker: str  # ``` or ~~~
    indent: str
    language: str


@dataclass
class InlineCodeSpan:
    """Represents an inline code span (backticks)."""
    start: int
    end: int


@dataclass
class ChunkerState:
    """Tracks chunker state across streaming."""
    buffer: str = ""
    in_thinking: bool = False
    in_final: bool = False
    ever_in_final: bool = False  # Track if we've ever seen <final>
    tag_buffer: str = ""  # Buffer for incomplete tags
    pending_fence_continuation: str = ""  # Fence opener to prepend to next chunk


def build_code_span_index(text: str) -> List[InlineCodeSpan]:
    """
    Build index of inline code spans (Clawdbot markdown/code-spans.ts pattern).
    
    Handles:
    - Single backticks `code`
    - Multiple backticks ``code`` or ```code```
    - Escaped backticks
    """
    spans = []
    i = 0
    n = len(text)
    
    while i < n:
        if text[i] == '`':
            # Count consecutive backticks
            start = i
            tick_count = 0
            while i < n and text[i] == '`':
                tick_count += 1
                i += 1
            
            # Skip if at end with no matching close
            if i >= n:
                break
            
            # Find matching closing backticks
            close_start = text.find('`' * tick_count, i)
            if close_start != -1:
                close_end = close_start + tick_count
                spans.append(InlineCodeSpan(start=start, end=close_end))
                i = close_end
            else:
                # No matching close, continue
                continue
        else:
            i += 1
    
    return spans


def is_inside_code_span(pos: int, spans: List[InlineCodeSpan]) -> bool:
    """Check if position is inside any inline code span."""
    for span in spans:
        if span.start <= pos < span.end:
            return True
    return False


def strip_block_tags(text: str, state: ChunkerState, enforce_final: bool = False) -> str:
    """
    Strip thinking blocks and handle final tags (Clawdbot pattern).
    
    CRITICAL: Skips tags inside code spans to protect code examples.
    
    - <think>...</think>: Remove entire block including content (unless in code)
    - <final>...</final>: 
        - If enforce_final=True: ONLY return content inside <final>
        - If enforce_final=False: Keep content but remove the tags
    - Stateful: tracks open tags across chunks
    - Handles partial tags split across chunks
    
    Based on Clawdbot's pi-embedded-subscribe.ts stripBlockTags function.
    """
    if not text:
        return text
    
    # Combine any buffered partial tag with new text
    combined = state.tag_buffer + text
    state.tag_buffer = ""
    
    # Build inline code span index to protect code examples
    code_spans = build_code_span_index(combined)
    
    # Pass 1: Handle <think> blocks (stateful, strip content inside)
    processed = []
    i = 0
    
    while i < len(combined):
        # Check for potential tag at '<'
        if combined[i] == '<':
            # Skip if inside code span
            if is_inside_code_span(i, code_spans):
                if not state.in_thinking:
                    processed.append(combined[i])
                i += 1
                continue
            
            remaining = combined[i:]
            
            # Try to match thinking tag
            think_match = THINKING_TAG_SCAN_RE.match(remaining)
            if think_match:
                is_close = think_match.group(1) == "/"
                if is_close:
                    state.in_thinking = False
                else:
                    state.in_thinking = True
                i += think_match.end()
                continue
            
            # Check for incomplete tag near end
            if i >= len(combined) - 20:
                # Could be partial tag - buffer for next chunk
                state.tag_buffer = combined[i:]
                break
            
            # Not a thinking tag, pass through
            if not state.in_thinking:
                processed.append(combined[i])
            i += 1
        else:
            if not state.in_thinking:
                processed.append(combined[i])
            i += 1
    
    after_thinking = ''.join(processed)
    
    # Pass 2: Handle <final> blocks
    if not enforce_final:
        # Just strip the tags, keep all content
        code_spans_2 = build_code_span_index(after_thinking)
        result = []
        i = 0
        while i < len(after_thinking):
            if after_thinking[i] == '<' and not is_inside_code_span(i, code_spans_2):
                remaining = after_thinking[i:]
                final_match = FINAL_TAG_SCAN_RE.match(remaining)
                if final_match:
                    i += final_match.end()
                    continue
            result.append(after_thinking[i])
            i += 1
        return ''.join(result)
    
    # enforce_final=True: ONLY return text inside <final> blocks
    code_spans_2 = build_code_span_index(after_thinking)
    result = []
    in_final = state.in_final
    ever_in_final = state.ever_in_final
    last_final_idx = 0
    i = 0
    
    while i < len(after_thinking):
        if after_thinking[i] == '<' and not is_inside_code_span(i, code_spans_2):
            remaining = after_thinking[i:]
            final_match = FINAL_TAG_SCAN_RE.match(remaining)
            if final_match:
                is_close = final_match.group(1) == "/"
                if not in_final and not is_close:
                    # <final> start
                    in_final = True
                    ever_in_final = True
                    last_final_idx = i + final_match.end()
                elif in_final and is_close:
                    # </final> end - capture content
                    result.append(after_thinking[last_final_idx:i])
                    in_final = False
                    last_final_idx = i + final_match.end()
                i += final_match.end()
                continue
        i += 1
    
    # Handle unclosed <final>
    if in_final:
        result.append(after_thinking[last_final_idx:])
    
    state.in_final = in_final
    state.ever_in_final = ever_in_final
    
    # If we've never seen a <final> tag, return nothing (strict mode)
    if not ever_in_final:
        return ""
    
    return ''.join(result)


class StreamBlockChunker:
    """
    Buffers streaming tokens and emits at natural breakpoints.
    """
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()
        self.state = ChunkerState()
        
    def reset(self):
        """Reset state for new stream."""
        self.state = ChunkerState()
    
    def process(self, text: str) -> List[str]:
        """
        Process incoming text, potentially emit chunks.
        
        Returns list of chunks to emit (may be empty).
        """
        if not text:
            return []
        
        # Filter thinking blocks and strip/handle final tags
        filtered = strip_block_tags(
            text, 
            self.state,
            enforce_final=self.config.enforce_final_tag
        )
        if not filtered:
            return []
        
        # Add to buffer
        self.state.buffer += filtered
        
        # Check if we should emit
        return self._try_emit(force=False)
    
    def flush(self) -> List[str]:
        """Force emit any remaining buffered content."""
        # Process any remaining tag buffer
        if self.state.tag_buffer:
            # Treat remaining as literal text if we're at end
            if not self.state.in_thinking:
                self.state.buffer += self.state.tag_buffer
            self.state.tag_buffer = ""
        
        return self._try_emit(force=True)
    
    def _try_emit(self, force: bool) -> List[str]:
        """
        Try to emit chunks from buffer.

        Handles fence splits by closing fence at break and prepending
        fence opener to next chunk (Clawdbot pattern).
        """
        chunks = []
        min_chars = self.config.min_chars
        max_chars = self.config.max_chars

        # Prepend any pending fence continuation from previous split
        if self.state.pending_fence_continuation:
            self.state.buffer = self.state.pending_fence_continuation + self.state.buffer
            self.state.pending_fence_continuation = ""

        if len(self.state.buffer) < min_chars and not force:
            return chunks

        if force and self.state.buffer:
            # DON'T strip - preserve whitespace for proper word separation
            chunk = self.state.buffer
            if chunk:
                chunks.append(chunk)
            self.state.buffer = ""
            return chunks

        while len(self.state.buffer) >= min_chars:
            break_idx, fence_at_break = self._find_break_point()

            if break_idx <= 0:
                if len(self.state.buffer) >= max_chars:
                    break_idx = max_chars
                    # Re-check fence at forced break
                    fences = self._find_code_fences(self.state.buffer[:break_idx])
                    fence_at_break = self._find_fence_at(break_idx, fences)
                else:
                    break

            # Handle fence split if breaking inside a fence
            if fence_at_break:
                before, continuation = self._handle_fence_split(
                    self.state.buffer, break_idx, fence_at_break
                )
                # DON'T strip - preserve whitespace
                if before:
                    chunks.append(before)
                self.state.buffer = continuation
            else:
                # DON'T strip - preserve trailing spaces for word separation
                chunk = self.state.buffer[:break_idx]
                if chunk:
                    chunks.append(chunk)
                # DON'T lstrip - preserve leading whitespace/newlines
                self.state.buffer = self.state.buffer[break_idx:]

        return chunks
    
    def _find_break_point(self) -> Tuple[int, Optional[FenceSpan]]:
        """
        Find best break point in buffer.

        Returns:
            Tuple of (break_index, fence_if_inside)
            If fence_if_inside is not None, the break is inside a fence
            and needs close/reopen handling.
        """
        buffer = self.state.buffer
        max_chars = min(self.config.max_chars, len(buffer))
        window = buffer[:max_chars]

        fence_positions = self._find_code_fences(window)
        pref = self.config.break_preference

        # "word" mode: prioritize word boundaries for low-latency streaming
        # This is ideal for voice assistants where frontend handles animation
        if pref == "word":
            # Find any word break (space) in the window
            for i in range(max_chars - 1, self.config.min_chars - 1, -1):
                if buffer[i].isspace() and self._is_safe_break(i, fence_positions):
                    return i + 1, None
            # If no word break found but we have min_chars, emit anyway
            if len(buffer) >= self.config.min_chars:
                return self.config.min_chars, None

        # Try paragraph break first
        if pref == "paragraph":
            idx = window.rfind('\n\n')
            if idx >= self.config.min_chars and self._is_safe_break(idx, fence_positions):
                return idx + 1, None

        # Try newline
        if pref in ("paragraph", "newline"):
            idx = window.rfind('\n')
            if idx >= self.config.min_chars and self._is_safe_break(idx, fence_positions):
                return idx + 1, None

        # Try sentence end
        if pref in ("paragraph", "newline", "sentence"):
            sentence_ends = list(re.finditer(r'[.!?](?=\s|$)', window))
            for match in reversed(sentence_ends):
                idx = match.end()
                if idx >= self.config.min_chars and self._is_safe_break(idx, fence_positions):
                    return idx, None

        # Try word break (fallback for all modes)
        for i in range(max_chars - 1, self.config.min_chars - 1, -1):
            if buffer[i].isspace() and self._is_safe_break(i, fence_positions):
                return i + 1, None

        # Force break at max_chars - check if inside fence
        force_idx = max_chars
        fence_at_break = self._find_fence_at(force_idx, fence_positions)

        return force_idx if force_idx > 0 else -1, fence_at_break
    
    def _find_code_fences(self, text: str) -> List[FenceSpan]:
        """
        Find code fence spans to avoid breaking inside them.

        Returns FenceSpan objects with full metadata for fence close/reopen
        if we're forced to break inside a fence.
        """
        fences = []
        # Match fence opener: optional indent, 3+ backticks or tildes, optional language
        fence_pattern = re.compile(r'^(\s*)(```|~~~)(\w*)\s*$', re.MULTILINE)

        open_fence: Optional[tuple] = None  # (start, marker, indent, lang)

        for match in fence_pattern.finditer(text):
            indent, marker, lang = match.group(1), match.group(2), match.group(3)

            if open_fence is None:
                # Opening fence
                open_fence = (match.start(), marker, indent, lang)
            elif marker.startswith(open_fence[1][0]):  # Same marker type closes (` or ~)
                # Closing fence
                fences.append(FenceSpan(
                    start=open_fence[0],
                    end=match.end(),
                    marker=open_fence[1],
                    indent=open_fence[2],
                    language=open_fence[3]
                ))
                open_fence = None

        # Handle unclosed fence
        if open_fence:
            fences.append(FenceSpan(
                start=open_fence[0],
                end=-1,  # Unclosed
                marker=open_fence[1],
                indent=open_fence[2],
                language=open_fence[3]
            ))

        return fences

    def _find_fence_at(self, idx: int, fences: List[FenceSpan]) -> Optional[FenceSpan]:
        """Find fence containing position."""
        for fence in fences:
            end = fence.end if fence.end != -1 else float('inf')
            if fence.start <= idx < end:
                return fence
        return None

    def _is_safe_break(self, idx: int, fence_positions: List[FenceSpan]) -> bool:
        """Check if break point is outside code fences."""
        return self._find_fence_at(idx, fence_positions) is None

    def _handle_fence_split(self, text: str, idx: int, fence: FenceSpan) -> tuple:
        """
        Handle splitting text inside a code fence (Clawdbot pattern).

        When forced to break inside a fence, we:
        1. Close the fence before the break
        2. Store fence opener to prepend to next chunk

        Returns:
            (text_with_close, fence_continuation_marker)
        """
        before = text[:idx]
        after = text[idx:]

        # Close fence at break point
        close_marker = f"\n{fence.indent}{fence.marker}\n"
        # Reopen fence for continuation
        open_marker = f"{fence.indent}{fence.marker}{fence.language}\n"

        return before + close_marker, open_marker + after


def create_stream_chunker(
    min_chars: int = 50,
    max_chars: int = 500,
    break_preference: str = "paragraph"
) -> StreamBlockChunker:
    """Factory function to create a chunker with config."""
    return StreamBlockChunker(ChunkingConfig(
        min_chars=min_chars,
        max_chars=max_chars,
        break_preference=break_preference
    ))
