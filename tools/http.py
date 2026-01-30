"""
HTTP Request Tool

Allows the agent to make HTTP requests to external APIs.
This enables skill-based integrations like Notion, where the agent
reads API documentation and makes curl-like requests directly.

Security: Only allows requests to whitelisted domains.
Response Limiting: Large responses are truncated to prevent rate limits.
"""
import json
import logging
import os
from typing import Optional, Dict, Any, Union, List
import httpx

from tools.registry import tool

logger = logging.getLogger("brainmap.tools.http")

# Maximum response size in characters (roughly ~8K tokens)
MAX_RESPONSE_CHARS = 30000
# Maximum number of items in array responses (for Notion search, etc.)
MAX_ARRAY_ITEMS = 10


def _extract_notion_title(item: Dict) -> str:
    """Extract title from a Notion page or database object."""
    if item.get("object") == "page":
        props = item.get("properties", {})
        for title_key in ["title", "Title", "Name", "name", "Task", "Task name"]:
            if title_key in props:
                title_prop = props[title_key]
                if isinstance(title_prop, dict) and "title" in title_prop:
                    title_array = title_prop["title"]
                    if title_array and isinstance(title_array, list):
                        return title_array[0].get("plain_text", "(untitled)")
        return "(untitled page)"
    elif item.get("object") == "database":
        title_array = item.get("title", [])
        if title_array and isinstance(title_array, list):
            return title_array[0].get("plain_text", "(untitled)")
        return "(untitled database)"
    return "(unknown)"


def _format_notion_search_results(body: Dict) -> str:
    """Format Notion search results as human-readable text.

    Instead of returning raw JSON, return a clean summary that's
    easy for Claude to understand and uses minimal tokens.
    """
    results = body.get("results", [])
    total = len(results)
    has_more = body.get("has_more", False)

    if not results:
        return "No results found."

    lines = [f"Found {total} result(s){' (more available)' if has_more else ''}:\n"]

    for i, item in enumerate(results[:MAX_ARRAY_ITEMS], 1):
        if not isinstance(item, dict):
            continue

        obj_type = item.get("object", "unknown")
        title = _extract_notion_title(item)
        page_id = item.get("id", "")
        url = item.get("url", "")

        # Format: "1. [page] Meeting Notes (id: abc123)"
        lines.append(f"{i}. [{obj_type}] {title}")
        lines.append(f"   id: {page_id}")
        if url:
            lines.append(f"   url: {url}")

    if total > MAX_ARRAY_ITEMS:
        lines.append(f"\n... and {total - MAX_ARRAY_ITEMS} more results")
        if body.get("next_cursor"):
            lines.append(f"Use next_cursor: {body['next_cursor']} to fetch more")

    return "\n".join(lines)


def _format_notion_page(body: Dict) -> Dict:
    """Format a single Notion page response - keep as JSON but simplified."""
    if body.get("object") != "page":
        return body

    return {
        "id": body.get("id"),
        "url": body.get("url"),
        "title": _extract_notion_title(body),
        "created_time": body.get("created_time"),
        "last_edited_time": body.get("last_edited_time"),
        "properties": body.get("properties", {})  # Keep properties for updates
    }


def _format_notion_blocks(body: Dict) -> str:
    """Format Notion blocks (page content) as readable text."""
    results = body.get("results", [])
    if not results:
        return "Page has no content blocks."

    lines = []
    for block in results[:20]:  # Limit to 20 blocks
        block_type = block.get("type", "unknown")
        block_data = block.get(block_type, {})

        # Extract text content
        rich_text = block_data.get("rich_text", [])
        text = "".join(rt.get("plain_text", "") for rt in rich_text)

        if block_type == "paragraph":
            lines.append(text or "(empty paragraph)")
        elif block_type.startswith("heading_"):
            level = block_type[-1]
            lines.append(f"{'#' * int(level)} {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"• {text}")
        elif block_type == "numbered_list_item":
            lines.append(f"- {text}")
        elif block_type == "to_do":
            checked = "✓" if block_data.get("checked") else "○"
            lines.append(f"{checked} {text}")
        elif block_type == "code":
            lang = block_data.get("language", "")
            lines.append(f"```{lang}\n{text}\n```")
        elif block_type == "divider":
            lines.append("---")
        else:
            if text:
                lines.append(f"[{block_type}] {text}")

    if len(results) > 20:
        lines.append(f"\n... and {len(results) - 20} more blocks")

    return "\n".join(lines)


def _format_notion_response(body: Any, url: str) -> Any:
    """Format Notion API responses as human-readable text.

    This dramatically reduces token usage while preserving
    all the information Claude needs to take action.
    """
    if not isinstance(body, dict):
        return body

    # Search results → formatted text list
    if "results" in body and isinstance(body["results"], list):
        # Check if it's blocks (page content) vs search results
        if body.get("results") and body["results"][0].get("type"):
            # This is blocks (page content)
            return _format_notion_blocks(body)
        else:
            # This is search/query results
            return _format_notion_search_results(body)

    # Single page → simplified JSON
    if body.get("object") == "page":
        return _format_notion_page(body)

    # Database schema → keep as-is (usually small)
    if body.get("object") == "database":
        return body

    return body


def _truncate_response(body: Any, url: str) -> Any:
    """Truncate response body to prevent rate limits.

    Different APIs need different truncation strategies.
    """
    # Notion-specific handling - format as readable text
    if "api.notion.com" in url:
        return _format_notion_response(body, url)

    # Generic truncation for other APIs
    if isinstance(body, dict):
        body_str = json.dumps(body)
        if len(body_str) > MAX_RESPONSE_CHARS:
            # For large dicts, try to preserve structure but truncate values
            return {
                "_truncated": True,
                "_original_size": len(body_str),
                "preview": body_str[:MAX_RESPONSE_CHARS] + "...[TRUNCATED]"
            }
    elif isinstance(body, list) and len(body) > MAX_ARRAY_ITEMS:
        return {
            "_truncated": True,
            "_total_items": len(body),
            "items": body[:MAX_ARRAY_ITEMS]
        }
    elif isinstance(body, str) and len(body) > MAX_RESPONSE_CHARS:
        return body[:MAX_RESPONSE_CHARS] + "...[TRUNCATED]"

    return body


def _parse_json_if_string(value: Any) -> Any:
    """Parse JSON string to dict if needed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# Whitelisted domains for HTTP requests
ALLOWED_DOMAINS = [
    "api.notion.com",
    "api.openai.com",
    "api.anthropic.com",
    "api.perplexity.ai",
    "api.github.com",
    "api.linear.app",
    "api.todoist.com",
    "api.google.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
]


def _is_domain_allowed(url: str) -> bool:
    """Check if URL domain is in whitelist."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_DOMAINS)
    except Exception:
        return False


def _get_env_value(key: str) -> Optional[str]:
    """Get environment variable value, supporting $VAR syntax."""
    if key.startswith("$"):
        key = key[1:]
    return os.environ.get(key)


def _substitute_env_vars(headers: Dict[str, str]) -> Dict[str, str]:
    """Substitute environment variables in header values.

    Supports both:
    - "$NOTION_API_KEY" -> "ntn_xxxxx"
    - "Bearer $NOTION_API_KEY" -> "Bearer ntn_xxxxx"
    """
    import re
    result = {}
    for key, value in headers.items():
        if isinstance(value, str) and "$" in value:
            # Find all $VAR patterns and substitute them
            def replace_env(match):
                var_name = match.group(1)
                env_val = os.environ.get(var_name)
                if env_val:
                    return env_val
                else:
                    logger.warning(f"Environment variable ${var_name} not found")
                    return match.group(0)  # Return original if not found

            result[key] = re.sub(r'\$([A-Z_][A-Z0-9_]*)', replace_env, value)
        else:
            result[key] = value
    return result


@tool(
    name="http_request",
    description="""Make an HTTP request to an external API.

Use this tool to interact with external services like Notion, GitHub, etc.
The agent should use this based on skill documentation (e.g., Notion skill).

IMPORTANT:
- Only whitelisted domains are allowed (notion, github, google, etc.)
- Use $ENV_VAR syntax in headers for API keys (e.g., $NOTION_API_KEY)
- The tool automatically substitutes environment variables

Example for Notion search:
  method: "POST"
  url: "https://api.notion.com/v1/search"
  headers: {"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"}
  body: {"query": "my page"}
"""
)
async def http_request(
    method: str,
    url: str,
    headers: Optional[Union[Dict[str, str], str]] = None,
    body: Optional[Union[Dict[str, Any], str]] = None,
    timeout: int = 30
) -> Dict[str, Any]:
    """
    Make an HTTP request.

    Args:
        method: HTTP method (GET, POST, PATCH, PUT, DELETE)
        url: Full URL to request
        headers: Request headers (use $ENV_VAR for secrets) - can be dict or JSON string
        body: JSON body for POST/PATCH/PUT requests - can be dict or JSON string
        timeout: Request timeout in seconds (default 30)

    Returns:
        Response with status_code, headers, and body
    """
    # Parse JSON strings if agent passed strings instead of dicts
    headers = _parse_json_if_string(headers)
    body = _parse_json_if_string(body)

    # Validate domain
    if not _is_domain_allowed(url):
        return {
            "error": f"Domain not allowed. Allowed domains: {', '.join(ALLOWED_DOMAINS)}",
            "status_code": 403
        }

    # Validate method
    method = method.upper()
    if method not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        return {
            "error": f"Invalid HTTP method: {method}",
            "status_code": 400
        }

    # Ensure headers is a dict
    if headers is None:
        headers = {}
    elif not isinstance(headers, dict):
        return {
            "error": f"headers must be a dictionary, got {type(headers).__name__}",
            "status_code": 400
        }

    # Substitute environment variables in headers
    request_headers = _substitute_env_vars(headers)

    # Add default content-type for requests with body
    if body and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=request_headers,
                json=body if body else None
            )

            # Try to parse JSON response
            try:
                response_body = response.json()
                # Truncate large responses to prevent rate limits
                response_body = _truncate_response(response_body, url)
            except Exception:
                response_body = response.text[:5000]  # Limit text response

            # Don't return headers (they're rarely needed and add tokens)
            return {
                "status_code": response.status_code,
                "body": response_body,
                "ok": response.is_success
            }

    except httpx.TimeoutException:
        return {
            "error": f"Request timed out after {timeout} seconds",
            "status_code": 408
        }
    except httpx.RequestError as e:
        logger.error(f"HTTP request failed: {e}")
        return {
            "error": str(e),
            "status_code": 500
        }
