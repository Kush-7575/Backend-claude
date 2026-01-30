---
name: notion
description: Notion API for creating and managing pages, databases, and blocks.
homepage: https://developers.notion.com
triggers:
  - save to notion
  - add to notion
  - search notion
  - my notion
  - notion database
  - query notion
requires:
  env: ["NOTION_API_KEY"]
---

# Notion Integration

Use the Notion API to create/read/update pages, data sources (databases), and blocks.

## Setup

The user must:
1. Create an integration at https://notion.so/my-integrations
2. Add `NOTION_API_KEY` to environment variables
3. Share target pages/databases with the integration (click "..." → "Connect to" → integration name)

## API Basics

All requests need:
```bash
curl -X GET "https://api.notion.com/v1/..." \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json"
```

> **Note:** The `Notion-Version` header is required. This skill uses `2025-09-03` (latest). In this version, databases are called "data sources" in the API.

## Common Operations

**Search for pages and data sources:**
```bash
curl -X POST "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"query": "page title"}'
```

**Get page:**
```bash
curl "https://api.notion.com/v1/pages/{page_id}" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03"
```

**Get page content (blocks):**
```bash
curl "https://api.notion.com/v1/blocks/{page_id}/children" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03"
```

**Create page in a data source:**
```bash
curl -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"database_id": "xxx"},
    "properties": {
      "Name": {"title": [{"text": {"content": "New Item"}}]},
      "Status": {"select": {"name": "Todo"}}
    }
  }'
```

**Create page under another page:**
```bash
curl -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"page_id": "xxx"},
    "properties": {
      "title": {"title": [{"text": {"content": "Page Title"}}]}
    },
    "children": [
      {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Content here"}}]}}
    ]
  }'
```

**Query a data source (database):**
```bash
curl -X POST "https://api.notion.com/v1/data_sources/{data_source_id}/query" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {"property": "Status", "select": {"equals": "Active"}},
    "sorts": [{"property": "Date", "direction": "descending"}]
  }'
```

**Create a data source (database):**
```bash
curl -X POST "https://api.notion.com/v1/data_sources" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"page_id": "xxx"},
    "title": [{"text": {"content": "My Database"}}],
    "properties": {
      "Name": {"title": {}},
      "Status": {"select": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
      "Date": {"date": {}}
    }
  }'
```

**Update page properties:**
```bash
curl -X PATCH "https://api.notion.com/v1/pages/{page_id}" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"properties": {"Status": {"select": {"name": "Done"}}}}'
```

**Add blocks to page:**
```bash
curl -X PATCH "https://api.notion.com/v1/blocks/{page_id}/children" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "children": [
      {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Hello"}}]}}
    ]
  }'
```

**Delete a block:**
```bash
curl -X DELETE "https://api.notion.com/v1/blocks/{block_id}" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03"
```

## Property Types

Common property formats for database items:
- **Title:** `{"title": [{"text": {"content": "..."}}]}`
- **Rich text:** `{"rich_text": [{"text": {"content": "..."}}]}`
- **Select:** `{"select": {"name": "Option"}}`
- **Multi-select:** `{"multi_select": [{"name": "A"}, {"name": "B"}]}`
- **Date:** `{"date": {"start": "2024-01-15", "end": "2024-01-16"}}`
- **Checkbox:** `{"checkbox": true}`
- **Number:** `{"number": 42}`
- **URL:** `{"url": "https://..."}`
- **Email:** `{"email": "a@b.com"}`
- **Relation:** `{"relation": [{"id": "page_id"}]}`

## Block Types

Common block formats:
- **Paragraph:** `{"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Heading 1:** `{"type": "heading_1", "heading_1": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Heading 2:** `{"type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Heading 3:** `{"type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Bulleted list:** `{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Numbered list:** `{"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": "..."}}]}}`
- **To-do:** `{"type": "to_do", "to_do": {"rich_text": [{"text": {"content": "..."}}], "checked": false}}`
- **Toggle:** `{"type": "toggle", "toggle": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Code:** `{"type": "code", "code": {"rich_text": [{"text": {"content": "..."}}], "language": "python"}}`
- **Quote:** `{"type": "quote", "quote": {"rich_text": [{"text": {"content": "..."}}]}}`
- **Divider:** `{"type": "divider", "divider": {}}`
- **Callout:** `{"type": "callout", "callout": {"rich_text": [{"text": {"content": "..."}}], "icon": {"emoji": "💡"}}}`

## Key Differences in 2025-09-03

- **Databases → Data Sources:** Use `/data_sources/` endpoints for queries and retrieval
- **Two IDs:** Each database now has both a `database_id` and a `data_source_id`
  - Use `database_id` when creating pages (`parent: {"database_id": "..."}`)
  - Use `data_source_id` when querying (`POST /v1/data_sources/{id}/query`)
- **Search results:** Databases return as `"object": "data_source"` with their `data_source_id`
- **Parent in responses:** Pages show `parent.data_source_id` alongside `parent.database_id`

## Filter Examples

**Filter by select property:**
```json
{"filter": {"property": "Status", "select": {"equals": "Done"}}}
```

**Filter by date:**
```json
{"filter": {"property": "Due", "date": {"on_or_before": "2024-12-31"}}}
```

**Filter by checkbox:**
```json
{"filter": {"property": "Completed", "checkbox": {"equals": true}}}
```

**Compound filter (AND):**
```json
{
  "filter": {
    "and": [
      {"property": "Status", "select": {"equals": "Active"}},
      {"property": "Priority", "select": {"equals": "High"}}
    ]
  }
}
```

**Compound filter (OR):**
```json
{
  "filter": {
    "or": [
      {"property": "Status", "select": {"equals": "Todo"}},
      {"property": "Status", "select": {"equals": "In Progress"}}
    ]
  }
}
```

## Workflow

### Before Creating: Always Search First

```
User: "Save this to Notion"
→ Search for similar pages first
→ If match found: append to existing page
→ If no match: create new page
```

### Confirmation Format

```
✅ Saved to Notion: "[Page Title]"
🔗 Link: [URL]
```

## Notes

- Page/database IDs are UUIDs (with or without dashes)
- The API cannot set database view filters — that's UI-only
- Rate limit: ~3 requests/second average
- Use `is_inline: true` when creating data sources to embed them in pages
- Always share pages/databases with the integration before accessing them
