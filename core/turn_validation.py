"""
Turn Validation - Provider-specific turn ordering.

Adapted from Clawdbot's pi-embedded-helpers/turns.ts

Handles:
1. Anthropic turn validation (merge consecutive user messages)
2. Gemini turn validation (merge consecutive assistant messages)
3. History turn limiting for long conversations

This runs BEFORE messages are sent to the LLM to ensure:
- Proper alternating user→assistant pattern
- No duplicate consecutive roles
- History doesn't exceed reasonable limits
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("brainmap.turn_validation")


def merge_content_blocks(
    existing: Any,
    new: Any
) -> List[Dict[str, Any]]:
    """
    Merge two content values into a single content list.
    
    Handles:
    - String content → convert to text block
    - List content → concatenate
    """
    def to_blocks(content: Any) -> List[Dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            return content
        return []
    
    return to_blocks(existing) + to_blocks(new)


def validate_anthropic_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and fix turn sequences for Anthropic API.
    
    From Clawdbot's pi-embedded-helpers/turns.ts validateAnthropicTurns.
    
    Anthropic requires strict alternating user→assistant pattern.
    Merges consecutive user messages together.
    
    Args:
        messages: List of conversation messages
    
    Returns:
        Validated messages with consecutive user messages merged
    """
    if not messages:
        return messages
    
    result: List[Dict[str, Any]] = []
    last_role: Optional[str] = None
    
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        
        role = msg.get("role")
        if not role:
            result.append(msg)
            continue
        
        # Merge consecutive user messages
        if role == "user" and last_role == "user" and result:
            last_msg = result[-1]
            merged_content = merge_content_blocks(
                last_msg.get("content", []),
                msg.get("content", [])
            )
            # Update last message with merged content
            result[-1] = {**last_msg, "content": merged_content}
            logger.debug("Merged consecutive user messages")
            continue
        
        result.append(msg)
        last_role = role
    
    return result


def validate_gemini_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and fix turn sequences for Gemini API.
    
    From Clawdbot's pi-embedded-helpers/turns.ts validateGeminiTurns.
    
    Gemini requires strict alternating user→assistant→tool→user pattern.
    Merges consecutive assistant messages together.
    
    Args:
        messages: List of conversation messages
    
    Returns:
        Validated messages with consecutive assistant messages merged
    """
    if not messages:
        return messages
    
    result: List[Dict[str, Any]] = []
    last_role: Optional[str] = None
    
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        
        role = msg.get("role")
        if not role:
            result.append(msg)
            continue
        
        # Merge consecutive assistant messages
        if role == "assistant" and last_role == "assistant" and result:
            last_msg = result[-1]
            merged_content = merge_content_blocks(
                last_msg.get("content", []),
                msg.get("content", [])
            )
            # Preserve usage and stop_reason from newer message
            merged_msg = {**last_msg, "content": merged_content}
            if "usage" in msg:
                merged_msg["usage"] = msg["usage"]
            if "stop_reason" in msg:
                merged_msg["stop_reason"] = msg["stop_reason"]
            result[-1] = merged_msg
            logger.debug("Merged consecutive assistant messages")
            continue
        
        result.append(msg)
        last_role = role
    
    return result


def limit_history_turns(
    messages: List[Dict[str, Any]],
    max_turns: int = 50,
    preserve_system: bool = True
) -> List[Dict[str, Any]]:
    """
    Limit history to a maximum number of turns.
    
    From Clawdbot's history.ts limitHistoryTurns.
    
    A "turn" is a user message + assistant response pair.
    This prevents context bloat in long conversations.
    
    Args:
        messages: List of conversation messages
        max_turns: Maximum number of user→assistant pairs to keep
        preserve_system: Keep system messages at the start
    
    Returns:
        Trimmed message list with at most max_turns pairs
    """
    if not messages or max_turns <= 0:
        return messages
    
    # Separate system messages (if preserving)
    system_messages: List[Dict[str, Any]] = []
    conversation_messages: List[Dict[str, Any]] = []
    
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else None
        if preserve_system and role == "system":
            system_messages.append(msg)
        else:
            conversation_messages.append(msg)
    
    # Count turns (user messages)
    turn_count = sum(
        1 for msg in conversation_messages 
        if isinstance(msg, dict) and msg.get("role") == "user"
    )
    
    if turn_count <= max_turns:
        return messages  # No limiting needed
    
    # Remove oldest turns
    turns_to_remove = turn_count - max_turns
    removed = 0
    start_idx = 0
    
    for i, msg in enumerate(conversation_messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            removed += 1
            if removed >= turns_to_remove:
                # Find the end of this turn (next user or end)
                end_idx = i + 1
                for j in range(i + 1, len(conversation_messages)):
                    if isinstance(conversation_messages[j], dict):
                        if conversation_messages[j].get("role") == "user":
                            end_idx = j
                            break
                else:
                    end_idx = len(conversation_messages)
                start_idx = end_idx
                break
            else:
                start_idx = i + 1
    
    # Keep messages from start_idx onwards
    trimmed = conversation_messages[start_idx:]
    
    logger.info(f"Limited history from {turn_count} to {max_turns} turns")
    
    return system_messages + trimmed


def ensure_valid_turn_start(
    messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Ensure messages start with a user message (required by most providers).
    
    If first non-system message is assistant, prepend a synthetic user message.
    
    Args:
        messages: List of conversation messages
    
    Returns:
        Messages with valid turn start
    """
    if not messages:
        return messages
    
    # Find first non-system message
    first_non_system_idx = None
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") != "system":
            first_non_system_idx = i
            break
    
    if first_non_system_idx is None:
        return messages  # All system messages
    
    first_msg = messages[first_non_system_idx]
    if isinstance(first_msg, dict) and first_msg.get("role") == "assistant":
        # Insert synthetic user message
        synthetic_user = {
            "role": "user",
            "content": "[Conversation continues]"
        }
        result = messages[:first_non_system_idx] + [synthetic_user] + messages[first_non_system_idx:]
        logger.warning("Inserted synthetic user message to fix turn ordering")
        return result
    
    return messages
