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


def _truncate_notion_response(body: Any) -> Any:
    """Truncate Notion API responses to prevent token explosion.

    Notion search can return hundreds of pages with full metadata.
    We only need the essential info: id, title, url.
    """
    if not isinstance(body, dict):
        return body

    # Handle Notion search/query results
    if "results" in body and isinstance(body["results"], list):
        results = body["results"]
        truncated_results = []

        for item in results[:MAX_ARRAY_ITEMS]:  # Limit to 10 items
            if isinstance(item, dict):
                # Extract only essential fields
                simplified = {
                    "id": item.get("id"),
                    "object": item.get("object"),  # "page" or "database"
                    "url": item.get("url"),
                    "created_time": item.get("created_time"),
                }

                # Extract title based on object type
                if item.get("object") == "page":
                    props = item.get("properties", {})
                    # Try common title property names
                    for title_key in ["title", "Title", "Name", "name"]:
                        if title_key in props:
                            title_prop = props[title_key]
                            if isinstance(title_prop, dict) and "title" in title_prop:
                                title_array = title_prop["title"]
                                if title_array and isinstance(title_array, list):
                                    simplified["title"] = title_array[0].get("plain_text", "")
                                    break
                elif item.get("object") == "database":
                    title_array = item.get("title", [])
                    if title_array and isinstance(title_array, list):
                        simplified["title"] = title_array[0].get("plain_text", "")

                truncated_results.append(simplified)
            else:
                truncated_results.append(item)

        return {
            "results": truncated_results,
            "total_results": len(results),
            "showing": len(truncated_results),
            "has_more": body.get("has_more", False),
            "next_cursor": body.get("next_cursor"),
            "_truncated": True if len(results) > MAX_ARRAY_ITEMS else False
        }

    return body


def _truncate_response(body: Any, url: str) -> Any:
    """Truncate response body to prevent rate limits.

    Different APIs need different truncation strategies.
    """
    # Notion-specific handling
    if "api.notion.com" in url:
        return _truncate_notion_response(body)

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
