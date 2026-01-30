"""
Notion Integration Tools

Tools for creating and searching Notion pages.
Requires NOTION_API_KEY in environment.
"""
import json
import logging
from typing import Optional, List, Dict, Any, Union
import httpx

from core.config import settings
from tools.registry import tool

logger = logging.getLogger("brainmap.tools.notion")


def _parse_json_if_string(value: Any) -> Any:
    """Parse JSON string to dict/list if needed.

    The LLM sometimes passes JSON as a string instead of structured data.
    This ensures we always work with proper Python objects.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


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
    name="notion_get_page",
    description="""Get the content of a Notion page.
    Use to read what's on a page before modifying or to show the user."""
)
async def notion_get_page(
    page_id: str
) -> Dict[str, Any]:
    """
    Get page content (blocks).

    Args:
        page_id: ID of the page

    Returns:
        Page content as readable text
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    client = _get_notion_client()

    try:
        # Get page metadata
        page_resp = await client.get(f"/pages/{page_id}")
        page_resp.raise_for_status()
        page_data = page_resp.json()

        # Get page blocks (content)
        blocks_resp = await client.get(f"/blocks/{page_id}/children")
        blocks_resp.raise_for_status()
        blocks_data = blocks_resp.json()

        # Extract title
        title = ""
        props = page_data.get("properties", {})
        for key in ["title", "Title", "Name", "name"]:
            if key in props and "title" in props[key]:
                title_arr = props[key]["title"]
                title = "".join(t.get("plain_text", "") for t in title_arr)
                break

        # Convert blocks to readable text
        content_lines = []
        for block in blocks_data.get("results", []):
            block_type = block.get("type", "")
            block_content = block.get(block_type, {})
            rich_text = block_content.get("rich_text", [])
            text = "".join(rt.get("plain_text", "") for rt in rich_text)

            if block_type == "heading_1":
                content_lines.append(f"# {text}")
            elif block_type == "heading_2":
                content_lines.append(f"## {text}")
            elif block_type == "heading_3":
                content_lines.append(f"### {text}")
            elif block_type == "bulleted_list_item":
                content_lines.append(f"• {text}")
            elif block_type == "numbered_list_item":
                content_lines.append(f"- {text}")
            elif block_type == "to_do":
                checked = "✓" if block_content.get("checked") else "○"
                content_lines.append(f"{checked} {text}")
            elif block_type == "code":
                lang = block_content.get("language", "")
                content_lines.append(f"```{lang}\n{text}\n```")
            elif block_type == "child_database":
                db_title = block.get("child_database", {}).get("title", "Database")
                content_lines.append(f"[Database: {db_title}] (id: {block['id']})")
            elif text:
                content_lines.append(text)

        return {
            "id": page_id,
            "title": title or "Untitled",
            "url": page_data.get("url", ""),
            "content": "\n".join(content_lines) if content_lines else "(empty page)"
        }

    except httpx.HTTPError as e:
        logger.error(f"Notion get page failed: {e}")
        return {"error": str(e)}


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


@tool(
    name="notion_create_database",
    description="""Create a new database (table) in Notion.
    Use when user wants to create a table, list, or database in Notion.
    Columns are defined by their names and types."""
)
async def notion_create_database(
    title: str,
    parent_page_id: str,
    columns: Union[List[Dict[str, str]], str]
) -> Dict[str, Any]:
    """
    Create a Notion database (table).

    Args:
        title: Database title
        parent_page_id: ID of the parent page
        columns: List of column definitions, e.g.:
                 [{"name": "Name", "type": "title"},
                  {"name": "Status", "type": "select"},
                  {"name": "Website", "type": "url"}]

                 Supported types: title, text, number, select,
                 multi_select, date, checkbox, url, email

    Returns:
        Created database info with ID and URL
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    # Parse columns if passed as JSON string
    columns = _parse_json_if_string(columns)
    if not isinstance(columns, list):
        return {"error": f"columns must be a list, got {type(columns).__name__}"}

    client = _get_notion_client()

    try:
        # Build properties schema
        properties = {}
        for col in columns:
            name = col.get("name", "Column")
            col_type = col.get("type", "text")

            if col_type == "title":
                properties[name] = {"title": {}}
            elif col_type == "text":
                properties[name] = {"rich_text": {}}
            elif col_type == "number":
                properties[name] = {"number": {}}
            elif col_type == "select":
                properties[name] = {"select": {}}
            elif col_type == "multi_select":
                properties[name] = {"multi_select": {}}
            elif col_type == "date":
                properties[name] = {"date": {}}
            elif col_type == "checkbox":
                properties[name] = {"checkbox": {}}
            elif col_type == "url":
                properties[name] = {"url": {}}
            elif col_type == "email":
                properties[name] = {"email": {}}
            else:
                properties[name] = {"rich_text": {}}

        # Ensure at least one title column
        if not any(p.get("title") for p in properties.values()):
            properties["Name"] = {"title": {}}

        db_data = {
            "parent": {"page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
            "is_inline": True  # Embed in page
        }

        response = await client.post("/databases", json=db_data)
        response.raise_for_status()
        data = response.json()

        return {
            "status": "created",
            "id": data["id"],
            "url": data.get("url", ""),
            "title": title
        }

    except httpx.HTTPError as e:
        logger.error(f"Notion create database failed: {e}")
        error_detail = ""
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json().get("message", "")
            except:
                error_detail = e.response.text[:200]
        return {"error": str(e), "detail": error_detail}


@tool(
    name="notion_add_row",
    description="""Add a row to a Notion database.
    Use when user wants to add an item/entry to an existing table."""
)
async def notion_add_row(
    database_id: str,
    properties: Union[Dict[str, Any], str]
) -> Dict[str, Any]:
    """
    Add a row to a Notion database.

    Args:
        database_id: ID of the database
        properties: Row data as key-value pairs, e.g.:
                   {"Name": "Acme Corp", "Website": "https://acme.com"}

                   Values are auto-formatted based on property type.

    Returns:
        Created row info
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    # Parse properties if passed as JSON string
    properties = _parse_json_if_string(properties)
    if not isinstance(properties, dict):
        return {"error": f"properties must be a dict, got {type(properties).__name__}"}

    client = _get_notion_client()

    try:
        # First get database schema to know property types
        db_response = await client.get(f"/databases/{database_id}")
        db_response.raise_for_status()
        db_schema = db_response.json().get("properties", {})

        # Format properties according to schema
        formatted_props = {}
        for key, value in properties.items():
            if key not in db_schema:
                continue

            prop_type = db_schema[key].get("type")

            if prop_type == "title":
                formatted_props[key] = {"title": [{"text": {"content": str(value)}}]}
            elif prop_type == "rich_text":
                formatted_props[key] = {"rich_text": [{"text": {"content": str(value)}}]}
            elif prop_type == "number":
                formatted_props[key] = {"number": float(value) if value else None}
            elif prop_type == "select":
                formatted_props[key] = {"select": {"name": str(value)}}
            elif prop_type == "multi_select":
                if isinstance(value, list):
                    formatted_props[key] = {"multi_select": [{"name": v} for v in value]}
                else:
                    formatted_props[key] = {"multi_select": [{"name": str(value)}]}
            elif prop_type == "date":
                formatted_props[key] = {"date": {"start": str(value)}}
            elif prop_type == "checkbox":
                formatted_props[key] = {"checkbox": bool(value)}
            elif prop_type == "url":
                formatted_props[key] = {"url": str(value) if value else None}
            elif prop_type == "email":
                formatted_props[key] = {"email": str(value) if value else None}

        page_data = {
            "parent": {"database_id": database_id},
            "properties": formatted_props
        }

        response = await client.post("/pages", json=page_data)
        response.raise_for_status()
        data = response.json()

        return {
            "status": "added",
            "id": data["id"],
            "url": data.get("url", "")
        }

    except httpx.HTTPError as e:
        logger.error(f"Notion add row failed: {e}")
        return {"error": str(e)}


@tool(
    name="notion_delete_block",
    description="""Delete a block, page, or database from Notion.
    Use when user wants to remove something from Notion."""
)
async def notion_delete_block(
    block_id: str
) -> Dict[str, Any]:
    """
    Delete a Notion block (page, database, or block).

    Args:
        block_id: ID of the block to delete

    Returns:
        Status of the deletion
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    client = _get_notion_client()

    try:
        response = await client.delete(f"/blocks/{block_id}")
        response.raise_for_status()

        return {"status": "deleted", "block_id": block_id}

    except httpx.HTTPError as e:
        logger.error(f"Notion delete failed: {e}")
        return {"error": str(e)}


@tool(
    name="notion_query_database",
    description="""Query a Notion database with optional filters and sorting.
    Use to list, filter, or search rows in a database/table."""
)
async def notion_query_database(
    database_id: str,
    filter_property: Optional[str] = None,
    filter_value: Optional[str] = None,
    sort_property: Optional[str] = None,
    sort_direction: str = "descending",
    limit: int = 20
) -> Dict[str, Any]:
    """
    Query rows from a Notion database.

    Args:
        database_id: ID of the database
        filter_property: Property name to filter by (optional)
        filter_value: Value to filter for (optional)
        sort_property: Property to sort by (optional)
        sort_direction: "ascending" or "descending"
        limit: Max rows to return (default 20)

    Returns:
        List of rows with their properties
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    client = _get_notion_client()

    try:
        # Build query
        query: Dict[str, Any] = {"page_size": min(limit, 100)}

        # Add filter if specified
        if filter_property and filter_value:
            query["filter"] = {
                "property": filter_property,
                "rich_text": {"contains": filter_value}
            }

        # Add sort if specified
        if sort_property:
            query["sorts"] = [{
                "property": sort_property,
                "direction": sort_direction
            }]

        response = await client.post(f"/databases/{database_id}/query", json=query)
        response.raise_for_status()
        data = response.json()

        # Format results
        rows = []
        for page in data.get("results", []):
            row = {"id": page["id"]}
            props = page.get("properties", {})

            for prop_name, prop_data in props.items():
                prop_type = prop_data.get("type")

                if prop_type == "title":
                    title_arr = prop_data.get("title", [])
                    row[prop_name] = "".join(t.get("plain_text", "") for t in title_arr)
                elif prop_type == "rich_text":
                    text_arr = prop_data.get("rich_text", [])
                    row[prop_name] = "".join(t.get("plain_text", "") for t in text_arr)
                elif prop_type == "number":
                    row[prop_name] = prop_data.get("number")
                elif prop_type == "select":
                    select = prop_data.get("select")
                    row[prop_name] = select.get("name") if select else None
                elif prop_type == "multi_select":
                    row[prop_name] = [s.get("name") for s in prop_data.get("multi_select", [])]
                elif prop_type == "date":
                    date = prop_data.get("date")
                    row[prop_name] = date.get("start") if date else None
                elif prop_type == "checkbox":
                    row[prop_name] = prop_data.get("checkbox")
                elif prop_type == "url":
                    row[prop_name] = prop_data.get("url")
                elif prop_type == "email":
                    row[prop_name] = prop_data.get("email")

            rows.append(row)

        return {
            "rows": rows,
            "count": len(rows),
            "has_more": data.get("has_more", False)
        }

    except httpx.HTTPError as e:
        logger.error(f"Notion query failed: {e}")
        return {"error": str(e)}


@tool(
    name="notion_update_row",
    description="""Update a row in a Notion database.
    Use to edit existing entries in a table."""
)
async def notion_update_row(
    row_id: str,
    properties: Union[Dict[str, Any], str]
) -> Dict[str, Any]:
    """
    Update a database row.

    Args:
        row_id: ID of the row (page) to update
        properties: Properties to update, e.g.:
                   {"Status": "Done", "Priority": "High"}

    Returns:
        Status of the update
    """
    if not is_notion_available():
        return {"error": "Notion not configured"}

    # Parse properties if passed as JSON string
    properties = _parse_json_if_string(properties)
    if not isinstance(properties, dict):
        return {"error": f"properties must be a dict, got {type(properties).__name__}"}

    client = _get_notion_client()

    try:
        # Get current page to determine property types
        page_resp = await client.get(f"/pages/{row_id}")
        page_resp.raise_for_status()
        current_props = page_resp.json().get("properties", {})

        # Format properties
        formatted_props = {}
        for key, value in properties.items():
            if key not in current_props:
                continue

            prop_type = current_props[key].get("type")

            if prop_type == "title":
                formatted_props[key] = {"title": [{"text": {"content": str(value)}}]}
            elif prop_type == "rich_text":
                formatted_props[key] = {"rich_text": [{"text": {"content": str(value)}}]}
            elif prop_type == "number":
                formatted_props[key] = {"number": float(value) if value else None}
            elif prop_type == "select":
                formatted_props[key] = {"select": {"name": str(value)}}
            elif prop_type == "multi_select":
                if isinstance(value, list):
                    formatted_props[key] = {"multi_select": [{"name": v} for v in value]}
                else:
                    formatted_props[key] = {"multi_select": [{"name": str(value)}]}
            elif prop_type == "date":
                formatted_props[key] = {"date": {"start": str(value)}}
            elif prop_type == "checkbox":
                formatted_props[key] = {"checkbox": bool(value)}
            elif prop_type == "url":
                formatted_props[key] = {"url": str(value) if value else None}
            elif prop_type == "email":
                formatted_props[key] = {"email": str(value) if value else None}

        response = await client.patch(f"/pages/{row_id}", json={"properties": formatted_props})
        response.raise_for_status()

        return {"status": "updated", "row_id": row_id}

    except httpx.HTTPError as e:
        logger.error(f"Notion update row failed: {e}")
        return {"error": str(e)}
