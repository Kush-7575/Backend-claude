"""
Message Transformation - Cross-provider compatibility layer.

Adapted from Clawdbot's pi-ai/src/providers/transform-messages.ts

Handles:
1. Tool call ID normalization across providers
2. Orphaned tool call handling (insert synthetic results)
3. Errored/aborted assistant message filtering
4. Thinking block signature preservation

This runs BEFORE messages are sent to the LLM to ensure:
- Tool calls always have matching tool_result
- IDs are compatible with target provider
- Incomplete turns don't cause API errors
"""
import re
import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger("brainmap.message_transform")


@dataclass
class TransformConfig:
    """Configuration for message transformation."""
    # Anthropic requires IDs matching ^[a-zA-Z0-9_-]+$ (max 64 chars)
    max_tool_id_length: int = 64
    synthetic_result_text: str = "No result provided"
    drop_errored_assistant: bool = True
    normalize_tool_ids: bool = True


def normalize_tool_call_id(tool_id: str, max_length: int = 64) -> str:
    """
    Normalize tool call ID for cross-provider compatibility.
    
    OpenAI Responses API generates IDs that are 450+ chars with special characters.
    Anthropic APIs require IDs matching ^[a-zA-Z0-9_-]+$ (max 64 chars).
    
    From Clawdbot's anthropic.ts line 504-506.
    """
    if not tool_id:
        return tool_id
    
    # Replace invalid characters with underscore
    normalized = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_id)
    
    # Truncate to max length
    return normalized[:max_length]


def is_errored_or_aborted(message: Dict[str, Any]) -> bool:
    """Check if assistant message is errored or aborted (from Clawdbot)."""
    if message.get("role") != "assistant":
        return False
    
    stop_reason = message.get("stop_reason") or message.get("stopReason")
    return stop_reason in ("error", "aborted")


def extract_tool_calls_from_assistant(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool_use blocks from assistant message content."""
    content = message.get("content", [])
    if isinstance(content, str):
        return []
    
    tool_calls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id"),
                "name": block.get("name"),
            })
    
    return tool_calls


def create_synthetic_tool_result(
    tool_call_id: str,
    tool_name: str,
    text: str = "No result provided",
    is_error: bool = True
) -> Dict[str, Any]:
    """
    Create a synthetic tool_result for orphaned tool calls.
    
    From Clawdbot's transform-messages.ts line 107-114.
    """
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": text,
                "is_error": is_error
            }
        ]
    }


def transform_messages(
    messages: List[Dict[str, Any]],
    config: Optional[TransformConfig] = None,
    normalize_id_fn: Optional[Callable[[str], str]] = None
) -> List[Dict[str, Any]]:
    """
    Transform messages for cross-provider compatibility.
    
    Based on Clawdbot's pi-ai/src/providers/transform-messages.ts
    
    This function:
    1. Normalizes tool call IDs (for OpenAI → Anthropic compatibility)
    2. Drops errored/aborted assistant messages
    3. Inserts synthetic tool_result for orphaned tool calls
    4. Handles thinking blocks across providers
    
    Args:
        messages: List of conversation messages
        config: Transformation configuration
        normalize_id_fn: Optional custom ID normalization function
    
    Returns:
        Transformed messages safe for API call
    """
    config = config or TransformConfig()
    normalize_id = normalize_id_fn or (
        lambda x: normalize_tool_call_id(x, config.max_tool_id_length)
    )
    
    # Build a map of original tool call IDs to normalized IDs
    tool_call_id_map: Dict[str, str] = {}
    
    # First pass: normalize tool IDs and filter errored messages
    transformed: List[Dict[str, Any]] = []
    
    for msg in messages:
        role = msg.get("role")
        
        # User messages pass through unchanged (but may need ID normalization in tool_result)
        if role == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                new_content = []
                changed = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        original_id = block.get("tool_use_id")
                        normalized_id = tool_call_id_map.get(original_id, original_id)
                        if config.normalize_tool_ids and normalized_id != original_id:
                            new_block = dict(block)
                            new_block["tool_use_id"] = normalized_id
                            new_content.append(new_block)
                            changed = True
                        else:
                            new_content.append(block)
                    else:
                        new_content.append(block)
                
                if changed:
                    new_msg = dict(msg)
                    new_msg["content"] = new_content
                    transformed.append(new_msg)
                else:
                    transformed.append(msg)
            else:
                transformed.append(msg)
            continue
        
        # Drop errored/aborted assistant messages
        if role == "assistant" and config.drop_errored_assistant:
            if is_errored_or_aborted(msg):
                logger.debug("Dropping errored/aborted assistant message")
                continue
        
        # Normalize tool call IDs in assistant messages
        if role == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                new_content = []
                changed = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        original_id = block.get("id")
                        if config.normalize_tool_ids and original_id:
                            normalized_id = normalize_id(original_id)
                            if normalized_id != original_id:
                                tool_call_id_map[original_id] = normalized_id
                                new_block = dict(block)
                                new_block["id"] = normalized_id
                                new_content.append(new_block)
                                changed = True
                                continue
                    new_content.append(block)
                
                if changed:
                    new_msg = dict(msg)
                    new_msg["content"] = new_content
                    transformed.append(new_msg)
                else:
                    transformed.append(msg)
            else:
                transformed.append(msg)
            continue
        
        # Other roles pass through
        transformed.append(msg)
    
    # Second pass: insert synthetic tool results for orphaned tool calls
    result: List[Dict[str, Any]] = []
    pending_tool_calls: List[Dict[str, Any]] = []
    existing_tool_result_ids: set = set()
    
    for i, msg in enumerate(transformed):
        role = msg.get("role")
        
        if role == "assistant":
            # If we have pending orphaned tool calls from a previous assistant, 
            # insert synthetic results now
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    tc_id = tc.get("id")
                    if tc_id and tc_id not in existing_tool_result_ids:
                        logger.warning(
                            f"Inserting synthetic tool_result for orphaned call: "
                            f"{tc.get('name')} ({tc_id})"
                        )
                        result.append(create_synthetic_tool_result(
                            tc_id,
                            tc.get("name", "unknown"),
                            config.synthetic_result_text,
                            is_error=True
                        ))
                pending_tool_calls = []
                existing_tool_result_ids = set()
            
            # Track tool calls from this assistant message
            tool_calls = extract_tool_calls_from_assistant(msg)
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()
            
            result.append(msg)
        
        elif role == "user":
            # Check for tool_result blocks
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id")
                        if tool_id:
                            existing_tool_result_ids.add(tool_id)
            
            # User message interrupts tool flow - insert synthetic results for orphaned calls
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    tc_id = tc.get("id")
                    if tc_id and tc_id not in existing_tool_result_ids:
                        logger.warning(
                            f"Inserting synthetic tool_result for interrupted call: "
                            f"{tc.get('name')} ({tc_id})"
                        )
                        result.append(create_synthetic_tool_result(
                            tc_id,
                            tc.get("name", "unknown"),
                            config.synthetic_result_text,
                            is_error=True
                        ))
                pending_tool_calls = []
                existing_tool_result_ids = set()
            
            result.append(msg)
        
        else:
            result.append(msg)
    
    # Handle any remaining orphaned calls at end
    if pending_tool_calls:
        for tc in pending_tool_calls:
            tc_id = tc.get("id")
            if tc_id and tc_id not in existing_tool_result_ids:
                logger.warning(
                    f"Inserting synthetic tool_result at end for: "
                    f"{tc.get('name')} ({tc_id})"
                )
                result.append(create_synthetic_tool_result(
                    tc_id,
                    tc.get("name", "unknown"),
                    config.synthetic_result_text,
                    is_error=True
                ))
    
    return result


def soft_trim_tool_result(
    content: str,
    max_chars: int = 4000,
    head_chars: int = 1500,
    tail_chars: int = 500
) -> str:
    """
    Soft-trim tool result with head + tail preservation.
    
    From Clawdbot's context-pruning/pruner.ts softTrimToolResultMessage.
    
    Args:
        content: Tool result content to trim
        max_chars: Don't trim if content is smaller than this
        head_chars: Number of chars to keep from beginning
        tail_chars: Number of chars to keep from end
    
    Returns:
        Trimmed content with truncation note
    """
    if not content or len(content) <= max_chars:
        return content
    
    if head_chars + tail_chars >= len(content):
        return content
    
    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""
    
    trimmed = f"{head}\n...\n{tail}"
    note = f"\n\n[Tool result trimmed: kept first {head_chars} chars and last {tail_chars} chars of {len(content)} chars.]"
    
    return trimmed + note


def prune_tool_results_in_messages(
    messages: List[Dict[str, Any]],
    max_chars: int = 4000,
    head_chars: int = 1500,
    tail_chars: int = 500,
    hard_clear_threshold: int = 8000,
    hard_clear_placeholder: str = "[Content cleared to save context space]"
) -> List[Dict[str, Any]]:
    """
    Prune oversized tool results in messages.
    
    From Clawdbot's context-pruning/pruner.ts
    
    Two-phase pruning:
    1. Soft-trim: Keep head + tail of large results
    2. Hard-clear: Replace very large results with placeholder
    
    Args:
        messages: List of messages to prune
        max_chars: Threshold for soft-trim
        head_chars: Chars to keep from head in soft-trim
        tail_chars: Chars to keep from tail in soft-trim
        hard_clear_threshold: Threshold for hard-clear
        hard_clear_placeholder: Placeholder text for hard-clear
    
    Returns:
        Messages with pruned tool results
    """
    result: List[Dict[str, Any]] = []
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", [])
        
        if role == "user" and isinstance(content, list):
            new_content = []
            changed = False
            
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_content = block.get("content", "")
                    
                    if isinstance(tool_content, str):
                        if len(tool_content) >= hard_clear_threshold:
                            # Hard-clear: replace with placeholder
                            new_block = dict(block)
                            new_block["content"] = hard_clear_placeholder
                            new_content.append(new_block)
                            changed = True
                            logger.debug(
                                f"Hard-cleared tool result: {len(tool_content)} -> {len(hard_clear_placeholder)} chars"
                            )
                        elif len(tool_content) >= max_chars:
                            # Soft-trim: keep head + tail
                            new_block = dict(block)
                            new_block["content"] = soft_trim_tool_result(
                                tool_content, max_chars, head_chars, tail_chars
                            )
                            new_content.append(new_block)
                            changed = True
                            logger.debug(
                                f"Soft-trimmed tool result: {len(tool_content)} -> {len(new_block['content'])} chars"
                            )
                        else:
                            new_content.append(block)
                    else:
                        new_content.append(block)
                else:
                    new_content.append(block)
            
            if changed:
                new_msg = dict(msg)
                new_msg["content"] = new_content
                result.append(new_msg)
            else:
                result.append(msg)
        else:
            result.append(msg)
    
    return result
