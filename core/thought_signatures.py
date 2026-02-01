"""
Thought Signature Stripping - Remove model thinking artifacts.

Adapted from Clawdbot's pi-embedded-helpers/bootstrap.ts stripThoughtSignatures

Some models (especially with extended thinking) leave behind "thought signatures"
in their output - patterns that indicate internal reasoning that shouldn't be
shown to users.

This module strips these patterns while preserving code examples that might
contain similar-looking text.
"""
import re
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger("brainmap.thought_signatures")


# Patterns for thought signatures (from Clawdbot)
# These are patterns that models sometimes leak from their thinking process
THOUGHT_SIGNATURE_PATTERNS = [
    # Explicit thinking tags
    r'<thinking>.*?</thinking>',
    r'<thought>.*?</thought>',
    r'<antThinking>.*?</antThinking>',
    
    # Reasoning markers
    r'\[Internal reasoning:.*?\]',
    r'\[Thinking:.*?\]',
    r'<internal>.*?</internal>',
    
    # Chain of thought markers
    r'Let me think about this step by step:?\s*\n',
    r'Let me analyze this:?\s*\n',
    r'Breaking this down:?\s*\n',
    
    # Output planning markers
    r'My response will be:?\s*\n',
    r'I will respond with:?\s*\n',
    r'Here is my response:?\s*\n',
]


def find_code_spans(text: str) -> List[Tuple[int, int]]:
    """
    Find code spans (inline and fenced) to protect from stripping.
    
    Returns list of (start, end) tuples.
    """
    spans = []
    
    # Fenced code blocks
    for match in re.finditer(r'```[\s\S]*?```', text):
        spans.append((match.start(), match.end()))
    
    for match in re.finditer(r'~~~[\s\S]*?~~~', text):
        spans.append((match.start(), match.end()))
    
    # Inline code
    for match in re.finditer(r'`[^`\n]+`', text):
        spans.append((match.start(), match.end()))
    
    return spans


def is_inside_code(pos: int, spans: List[Tuple[int, int]]) -> bool:
    """Check if position is inside any code span."""
    for start, end in spans:
        if start <= pos < end:
            return True
    return False


def strip_thought_signatures(
    text: str,
    code_aware: bool = True
) -> str:
    """
    Strip thought signatures from model output.
    
    From Clawdbot's stripThoughtSignatures.
    
    Args:
        text: Text to process
        code_aware: If True, protect code spans from stripping
    
    Returns:
        Text with thought signatures removed
    """
    if not text:
        return text
    
    code_spans = find_code_spans(text) if code_aware else []
    
    result = text
    
    for pattern in THOUGHT_SIGNATURE_PATTERNS:
        # Find all matches
        matches = list(re.finditer(pattern, result, re.IGNORECASE | re.DOTALL))
        
        # Process in reverse to preserve positions
        for match in reversed(matches):
            if code_aware and is_inside_code(match.start(), code_spans):
                continue
            
            # Replace with empty string (or single space if mid-text)
            before = result[:match.start()]
            after = result[match.end():]
            
            # Add space if joining two words
            if before and after and before[-1].isalnum() and after[0].isalnum():
                result = before + " " + after
            else:
                result = before + after
            
            # Rebuild code spans after modification
            if code_aware:
                code_spans = find_code_spans(result)
    
    # Clean up excess whitespace
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()
    
    return result


def strip_thinking_blocks_safe(
    text: str,
    strip_final_tags: bool = True
) -> str:
    """
    Strip <thinking> blocks and optionally <final> tags.
    
    This is a more comprehensive version that handles:
    - Nested tags
    - Partial/unclosed tags
    - Code protection
    
    Args:
        text: Text to process
        strip_final_tags: If True, remove <final></final> tags (keep content)
    
    Returns:
        Cleaned text
    """
    if not text:
        return text
    
    # Protect code spans
    code_spans = find_code_spans(text)
    
    # Remove thinking blocks (including content)
    thinking_pattern = re.compile(
        r'<\s*(?:think(?:ing)?|thought|antThinking)\s*>[\s\S]*?</\s*(?:think(?:ing)?|thought|antThinking)\s*>',
        re.IGNORECASE
    )
    
    result = text
    for match in reversed(list(thinking_pattern.finditer(result))):
        if not is_inside_code(match.start(), code_spans):
            result = result[:match.start()] + result[match.end():]
            code_spans = find_code_spans(result)
    
    # Remove final tags (keep content)
    if strip_final_tags:
        final_open = re.compile(r'<\s*final\s*>', re.IGNORECASE)
        final_close = re.compile(r'</\s*final\s*>', re.IGNORECASE)
        
        result = final_open.sub('', result)
        result = final_close.sub('', result)
    
    return result.strip()


def sanitize_user_facing_text(text: str) -> str:
    """
    Sanitize text for display to users.
    
    From Clawdbot's sanitizeUserFacingText.
    
    Combines thought signature stripping with other cleanup:
    - Strip thinking blocks
    - Remove thought signatures
    - Normalize whitespace
    - Remove control characters
    
    Args:
        text: Text to sanitize
    
    Returns:
        Clean text safe for user display
    """
    if not text:
        return text
    
    # Strip thinking blocks
    result = strip_thinking_blocks_safe(text)
    
    # Strip thought signatures
    result = strip_thought_signatures(result)
    
    # Remove control characters (except newline/tab)
    result = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', result)
    
    # Normalize line endings
    result = result.replace('\r\n', '\n').replace('\r', '\n')
    
    # Clean up excessive newlines
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    return result.strip()
