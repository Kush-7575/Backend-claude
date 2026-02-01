"""
Tool Call ID Handling - Cross-provider ID compatibility.

Adapted from Clawdbot's tool-call-id.ts

Handles:
1. Tool call ID sanitization for different providers
2. Collision detection and resolution
3. Unique ID generation with hash fallback

Different providers have different requirements:
- Anthropic: alphanumeric + _ - (max 64 chars)
- Gemini/Google: alphanumeric only
- Mistral: alphanumeric only, length 9
"""
import hashlib
import logging
import re
from typing import List, Dict, Any, Set, Optional

logger = logging.getLogger("brainmap.tool_call_id")


# Modes for tool call ID sanitization
STRICT_MODE = "strict"  # alphanumeric + underscore + hyphen (Anthropic)
STRICT9_MODE = "strict9"  # alphanumeric only, 9 chars (Mistral)


def sanitize_tool_call_id(
    tool_id: str,
    mode: str = STRICT_MODE
) -> str:
    """
    Sanitize a tool call ID for provider compatibility.
    
    From Clawdbot's tool-call-id.ts sanitizeToolCallId.
    
    Args:
        tool_id: Original tool call ID
        mode: "strict" (Anthropic) or "strict9" (Mistral)
    
    Returns:
        Sanitized ID
    """
    if not tool_id or not isinstance(tool_id, str):
        return "defaultid" if mode == STRICT9_MODE else "defaulttoolid"
    
    if mode == STRICT9_MODE:
        # Mistral: alphanumeric only, 9 chars
        alphanumeric = re.sub(r'[^a-zA-Z0-9]', '', tool_id)
        if len(alphanumeric) >= 9:
            return alphanumeric[:9]
        if alphanumeric:
            return short_hash(alphanumeric, 9)
        return short_hash("sanitized", 9)
    
    # Anthropic: allow alphanumeric, underscore, hyphen
    # But some providers (Gemini) need strictly alphanumeric
    alphanumeric = re.sub(r'[^a-zA-Z0-9]', '', tool_id)
    return alphanumeric if alphanumeric else "sanitizedtoolid"


def short_hash(text: str, length: int = 8) -> str:
    """Generate a short hash from text."""
    return hashlib.sha1(text.encode()).hexdigest()[:length]


def is_valid_tool_call_id(tool_id: str, mode: str = STRICT_MODE) -> bool:
    """
    Check if tool call ID is valid for the given mode.
    
    Args:
        tool_id: Tool call ID to check
        mode: "strict" or "strict9"
    
    Returns:
        True if valid
    """
    if not tool_id or not isinstance(tool_id, str):
        return False
    
    if mode == STRICT9_MODE:
        return bool(re.match(r'^[a-zA-Z0-9]{9}$', tool_id))
    
    return bool(re.match(r'^[a-zA-Z0-9]+$', tool_id))


def make_unique_tool_id(
    tool_id: str,
    used_ids: Set[str],
    mode: str = STRICT_MODE
) -> str:
    """
    Generate a unique tool call ID avoiding collisions.
    
    From Clawdbot's tool-call-id.ts makeUniqueToolId.
    
    Args:
        tool_id: Original tool call ID
        used_ids: Set of already used IDs
        mode: Sanitization mode
    
    Returns:
        Unique sanitized ID
    """
    max_len = 9 if mode == STRICT9_MODE else 40
    
    # Try base sanitized ID first
    base = sanitize_tool_call_id(tool_id, mode)
    if mode == STRICT9_MODE:
        candidate = base[:9] if len(base) >= 9 else ""
    else:
        candidate = base[:max_len]
    
    if candidate and candidate not in used_ids:
        return candidate
    
    # Add hash suffix
    hash_val = short_hash(tool_id, 8)
    
    if mode == STRICT9_MODE:
        # Try with hash only (9 chars)
        for i in range(1000):
            hashed = short_hash(f"{tool_id}:{i}", 9)
            if hashed not in used_ids:
                return hashed
        return short_hash(f"{tool_id}:{hash(tool_id)}", 9)
    
    # Non-strict mode: base + hash
    clipped_base = base[:max_len - len(hash_val)]
    candidate = f"{clipped_base}{hash_val}"
    
    if candidate not in used_ids:
        return candidate
    
    # Try with counter
    for i in range(2, 1000):
        suffix = f"x{i}"
        next_id = f"{candidate[:max_len - len(suffix)]}{suffix}"
        if next_id not in used_ids:
            return next_id
    
    # Last resort: timestamp
    import time
    ts_suffix = f"t{int(time.time())}"
    return f"{candidate[:max_len - len(ts_suffix)]}{ts_suffix}"


def sanitize_tool_call_ids_in_messages(
    messages: List[Dict[str, Any]],
    mode: str = STRICT_MODE
) -> List[Dict[str, Any]]:
    """
    Sanitize all tool call IDs in messages for provider compatibility.
    
    From Clawdbot's tool-call-id.ts sanitizeSessionToolCallIds.
    
    Also builds a mapping and updates tool_result references.
    
    Args:
        messages: Message list to process
        mode: Sanitization mode
    
    Returns:
        Messages with sanitized IDs
    """
    id_map: Dict[str, str] = {}
    used_ids: Set[str] = set()
    
    # First pass: collect and sanitize assistant tool_use IDs
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        if msg.get("role") != "assistant":
            continue
        
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                original_id = block.get("id")
                if original_id and original_id not in id_map:
                    new_id = make_unique_tool_id(original_id, used_ids, mode)
                    id_map[original_id] = new_id
                    used_ids.add(new_id)
    
    if not id_map:
        return messages  # No changes needed
    
    # Second pass: apply mapping
    result: List[Dict[str, Any]] = []
    
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "assistant" and isinstance(content, list):
            new_content = []
            changed = False
            
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    original_id = block.get("id")
                    if original_id in id_map and id_map[original_id] != original_id:
                        new_block = dict(block)
                        new_block["id"] = id_map[original_id]
                        new_content.append(new_block)
                        changed = True
                        continue
                new_content.append(block)
            
            if changed:
                new_msg = dict(msg)
                new_msg["content"] = new_content
                result.append(new_msg)
            else:
                result.append(msg)
                
        elif role == "user" and isinstance(content, list):
            new_content = []
            changed = False
            
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    original_id = block.get("tool_use_id")
                    if original_id in id_map and id_map[original_id] != original_id:
                        new_block = dict(block)
                        new_block["tool_use_id"] = id_map[original_id]
                        new_content.append(new_block)
                        changed = True
                        continue
                new_content.append(block)
            
            if changed:
                new_msg = dict(msg)
                new_msg["content"] = new_content
                result.append(new_msg)
            else:
                result.append(msg)
        else:
            result.append(msg)
    
    logger.debug(f"Sanitized {len(id_map)} tool call IDs")
    return result
