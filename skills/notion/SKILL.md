---
name: notion
description: Create and search Notion pages and databases
triggers:
  - save to notion
  - add to notion
  - search notion
  - my notion
requires:
  env: ["NOTION_API_KEY"]
  tools: ["notion_search", "notion_create_page", "notion_append"]
---

# Notion Integration

You can interact with the user's Notion workspace.

## Available Tools

| Tool | When to Use |
|------|-------------|
| `notion_search` | Find existing Notion pages/databases |
| `notion_create_page` | Create a new Notion page |
| `notion_append` | Add content to existing page |

## Workflow

### Before Creating: Always Search First

```
User: "Save this to Notion"
→ notion_search("similar topic") first
→ If match found: notion_append
→ If no match: notion_create_page
```

### Confirmation Format

```
✅ Saved to Notion: "[Page Title]"
🔗 Link: [URL]
```

### Error Handling

If Notion is not configured:
```
⚠️ Notion not connected. Please add your Notion integration in settings.
```

## Best Practices

1. **Search before create** - Avoid duplicate pages
2. **Use descriptive titles** - Help with future searches
3. **Preserve formatting** - Keep headers, lists, etc.
4. **Show the URL** - Let user navigate to the page
