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
  tools: ["http_request"]
---

# Notion Integration

Use the Notion API to create/read/update pages, data sources (databases), and blocks.

**Use the `http_request` tool** to make API calls. The tool automatically handles authentication via `$NOTION_API_KEY`.

## Setup

The user must:
1. Create an integration at https://notion.so/my-integrations
2. Add `NOTION_API_KEY` to environment variables
3. Share target pages/databases with the integration (click "..." → "Connect to" → integration name)

## How to Use http_request

All Notion API calls use the `http_request` tool with these standard headers:
```json
{
  "Authorization": "Bearer $NOTION_API_KEY",
  "Notion-Version": "2022-06-28",
  "Content-Type": "application/json"
}
```

The `$NOTION_API_KEY` is automatically substituted from environment variables.

## Common Operations

Use `http_request` tool for all operations. Standard headers for all requests:
```json
{"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"}
```

**Search for pages and databases:**
```
http_request(
  method="POST",
  url="https://api.notion.com/v1/search",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={"query": "page title"}
)
```

**Get page:**
```
http_request(
  method="GET",
  url="https://api.notion.com/v1/pages/{page_id}",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"}
)
```

**Get page content (blocks):**
```
http_request(
  method="GET",
  url="https://api.notion.com/v1/blocks/{page_id}/children",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"}
)
```

**Create page in a database:**
```
http_request(
  method="POST",
  url="https://api.notion.com/v1/pages",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={
    "parent": {"database_id": "xxx"},
    "properties": {
      "Name": {"title": [{"text": {"content": "New Item"}}]},
      "Status": {"select": {"name": "Todo"}}
    }
  }
)
```

**Create page under another page:**
```
http_request(
  method="POST",
  url="https://api.notion.com/v1/pages",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={
    "parent": {"page_id": "xxx"},
    "properties": {
      "title": {"title": [{"text": {"content": "Page Title"}}]}
    },
    "children": [
      {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Content here"}}]}}
    ]
  }
)
```

**Query a database:**
```
http_request(
  method="POST",
  url="https://api.notion.com/v1/databases/{database_id}/query",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={
    "filter": {"property": "Status", "select": {"equals": "Active"}},
    "sorts": [{"property": "Date", "direction": "descending"}]
  }
)
```

**Create a database:**
```
http_request(
  method="POST",
  url="https://api.notion.com/v1/databases",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={
    "parent": {"page_id": "xxx"},
    "title": [{"text": {"content": "My Database"}}],
    "properties": {
      "Name": {"title": {}},
      "Status": {"select": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
      "Date": {"date": {}}
    }
  }
)
```

**Update page properties:**
```
http_request(
  method="PATCH",
  url="https://api.notion.com/v1/pages/{page_id}",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={"properties": {"Status": {"select": {"name": "Done"}}}}
)
```

**Add blocks to page:**
```
http_request(
  method="PATCH",
  url="https://api.notion.com/v1/blocks/{page_id}/children",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"},
  body={
    "children": [
      {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Hello"}}]}}
    ]
  }
)
```

**Delete a block:**
```
http_request(
  method="DELETE",
  url="https://api.notion.com/v1/blocks/{block_id}",
  headers={"Authorization": "Bearer $NOTION_API_KEY", "Notion-Version": "2022-06-28"}
)
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

## Important Notes

- Use API version `2022-06-28` (stable) for all requests
- Always include both `Authorization` and `Notion-Version` headers
- Database endpoints use `/databases/` (not `/data_sources/`)
- Page IDs and database IDs are UUIDs (with or without dashes)

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
