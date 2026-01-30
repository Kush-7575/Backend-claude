---
name: notion
description: Notion integration for pages, databases, and content management.
homepage: https://developers.notion.com
triggers:
  - save to notion
  - add to notion
  - search notion
  - my notion
  - notion database
  - query notion
  - notion page
  - create table
requires:
  env: ["NOTION_API_KEY"]
  tools: ["notion_search", "notion_create_page", "notion_create_database"]
---

# Notion Integration

You have dedicated Notion tools - use them instead of http_request!

## Available Tools

### Reading
- `notion_search(query, filter_type)` - Find pages/databases
- `notion_get_page(page_id)` - Read page content
- `notion_query_database(database_id, ...)` - List/filter database rows

### Creating
- `notion_create_page(title, content, parent_id)` - Create a page
- `notion_create_database(title, parent_page_id, columns)` - Create a table
- `notion_add_row(database_id, properties)` - Add row to database

### Updating
- `notion_append(page_id, content)` - Add content to page
- `notion_update_row(row_id, properties)` - Edit database row

### Deleting
- `notion_delete_block(block_id)` - Delete page/database/block

## Examples

**Find a page:**
```
notion_search("AI-buddy")
→ {"results": [{"type": "page", "id": "abc123", "title": "AI-buddy"}]}
```

**Create a database (table):**
```
notion_create_database(
  title="Top VCs",
  parent_page_id="abc123",
  columns=[
    {"name": "Name", "type": "title"},
    {"name": "Website", "type": "url"},
    {"name": "Focus", "type": "select"}
  ]
)
```

**Add rows:**
```
notion_add_row(
  database_id="xyz789",
  properties={"Name": "Sequoia", "Website": "https://sequoia.com", "Focus": "Consumer"}
)
```

**Query database:**
```
notion_query_database(database_id="xyz789", limit=20)
→ {"rows": [{"Name": "Sequoia", "Website": "..."}, ...]}
```

**Delete something:**
```
notion_delete_block(block_id="abc123")
```

## Column Types for Databases

When creating databases, use these types:
- `title` - Required, one per database (the main name column)
- `text` - Plain text
- `number` - Numeric values
- `url` - Website links
- `email` - Email addresses
- `select` - Single choice
- `multi_select` - Multiple choices
- `date` - Date/time
- `checkbox` - True/false

## Important Notes

- Always search before creating to avoid duplicates
- Use `notion_get_page` to read content before modifying
- The tools handle all API formatting automatically
- No need to construct JSON or headers manually
