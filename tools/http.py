"""
HTTP Request Tool

Allows the agent to make HTTP requests to external APIs.
This enables skill-based integrations like Notion, where the agent
reads API documentation and makes curl-like requests directly.

Security: Only allows requests to whitelisted domains.
"""
import json
import logging
import os
from typing import Optional, Dict, Any, Union
import httpx

from tools.registry import tool

logger = logging.getLogger("brainmap.tools.http")


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
    """Substitute environment variables in header values."""
    result = {}
    for key, value in headers.items():
        if isinstance(value, str) and value.startswith("$"):
            env_val = _get_env_value(value)
            if env_val:
                result[key] = env_val
            else:
                logger.warning(f"Environment variable {value} not found")
                result[key] = value
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
            except Exception:
                response_body = response.text[:5000]  # Limit text response

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
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
