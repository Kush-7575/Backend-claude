"""
Notion Integration Tools

Tools for creating and searching Notion pages.
Requires NOTION_API_KEY in environment.
"""
import logging
from typing import Optional, List, Dict, Any
import httpx

from core.config import settings
from tools.registry import tool

logger = logging.getLogger("brainmap.tools.notion")


# Notion API client
_notion_client: Optional[httpx.AsyncClient] = None


def _get_notion_client() -> httpx.AsyncClient:
    """Get or create Notion API client."""
    global _notion_client
    if _notion_client is None:
        if not settings.NOTION_API_KEY:
            raise RuntimeError("NOTION_API_KEY not configured")
        
        _notion_client = httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {settings.NOTION_API_KEY}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            },
            timeout=30.0
        )
    return _notion_client


def is_notion_available() -> bool:
    """Check if Notion integration is configured."""
    return bool(settings.NOTION_API_KEY)


@tool(
    name="notion_search",
    description="""Search Notion workspace for pages and databases.
    Use when user asks about Notion content or before creating new pages."""
)
async def notion_search(
    query: str,
    filter_type: str = "page"
) -> Dict[str, Any]:
    """
    Search Notion workspace.
    
    Args:
        query: Search query
        filter_type: "page", "database", or None for all
    
    Returns:
        Search results with page/database info
    """
    if not is_notion_available():
        return {"error": "Notion not configured", "results": []}
    
    client = _get_notion_client()
    
    try:
        payload = {"query": query}
        if filter_type in ("page", "database"):
            payload["filter"] = {"property": "object", "value": filter_type}
        
        response = await client.post("/search", json=payload)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("results", [])[:10]:
            obj_type = item.get("object")
            
            if obj_type == "page":
                # Extract title from properties
                title = ""
                props = item.get("properties", {})
                if "title" in props:
                    title_arr = props["title"].get("title", [])
                    title = "".join(t.get("plain_text", "") for t in title_arr)
                elif "Name" in props:
                    title_arr = props["Name"].get("title", [])
                    title = "".join(t.get("plain_text", "") for t in title_arr)
                
                results.append({
                    "type": "page",
                    "id": item["id"],
                    "title": title or "Untitled",
                    "url": item.get("url", "")
                })
            
            elif obj_type == "database":
                title_arr = item.get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_arr)
                
                results.append({
                    "type": "database",
                    "id": item["id"],
                    "title": title or "Untitled Database",
                    "url": item.get("url", "")
                })
        
        return {"results": results, "count": len(results)}
        
    except httpx.HTTPError as e:
        logger.error(f"Notion search failed: {e}")
        return {"error": str(e), "results": []}


@tool(
    name="notion_create_page",
    description="""Create a new Notion page.
    Can create standalone page or add to a database.
    Use when user wants to save something to Notion."""
)
async def notion_create_page(
    title: str,
    content: str,
    parent_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a Notion page.
    
    Args:
        title: Page title
        content: Page content (markdown-like)
        parent_id: Parent page/database ID (optional)
    
    Returns:
        Created page info with URL
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}
    
    client = _get_notion_client()
    
    try:
        # Build page data
        page_data = {
            "properties": {
                "title": {
                    "title": [{"text": {"content": title}}]
                }
            },
            "children": _content_to_blocks(content)
        }
        
        # Set parent
        if parent_id:
            # Determine if parent is page or database
            page_data["parent"] = {"page_id": parent_id}
        else:
            # Create in workspace root (requires page parent for API)
            return {"error": "parent_id required for page creation"}
        
        response = await client.post("/pages", json=page_data)
        response.raise_for_status()
        data = response.json()
        
        return {
            "status": "created",
            "id": data["id"],
            "url": data.get("url", ""),
            "title": title
        }
        
    except httpx.HTTPError as e:
        logger.error(f"Notion create failed: {e}")
        return {"error": str(e)}


@tool(
    name="notion_append",
    description="""Append content to an existing Notion page.
    Use when user wants to add to an existing Notion page."""
)
async def notion_append(
    page_id: str,
    content: str
) -> Dict[str, Any]:
    """
    Append content to a Notion page.
    
    Args:
        page_id: ID of the page to append to
        content: Content to append
    
    Returns:
        Status of the operation
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}
    
    client = _get_notion_client()
    
    try:
        blocks = _content_to_blocks(content)
        
        response = await client.patch(
            f"/blocks/{page_id}/children",
            json={"children": blocks}
        )
        response.raise_for_status()
        
        return {"status": "appended", "page_id": page_id}
        
    except httpx.HTTPError as e:
        logger.error(f"Notion append failed: {e}")
        return {"error": str(e)}


def _content_to_blocks(content: str) -> List[Dict[str, Any]]:
    """Convert content string to Notion blocks."""
    blocks = []
    
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        
        # Heading detection
        if line.startswith("# "):
            blocks.append({
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                }
            })
        elif line.startswith("## "):
            blocks.append({
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                }
            })
        elif line.startswith("### "):
            blocks.append({
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": line[4:]}}]
                }
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                }
            })
        elif line.startswith("1. ") or line.startswith("2. "):
            blocks.append({
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                }
            })
        else:
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line}}]
                }
            })
    
    return blocks or [{"type": "paragraph", "paragraph": {"rich_text": []}}]
