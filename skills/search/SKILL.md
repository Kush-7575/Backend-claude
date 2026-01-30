---
name: search
description: Search across notes, memories, and knowledge base
triggers:
  - search for
  - find
  - look up
  - what do I have about
  - my notes about
---

# Search Skill

You can search across the user's entire knowledge base.

## Available Tools

| Tool | When to Use |
|------|-------------|
| `search_notes` | Search saved notes by topic/content |
| `get_notes` | List recent notes (no search) |

## Search Workflow

### 1. Always Search First
When user asks about existing information:
```
User: "What do I have about the project?"
→ search_notes("project")
→ Return matching notes with summaries
```

### 2. Present Results Clearly
```
📚 Found 3 notes about "project":

1. **Project Timeline** (Jan 25)
   Key milestones and deadlines...

2. **Project Ideas** (Jan 20)
   Initial brainstorm concepts...

3. **Meeting Notes - Project Kickoff** (Jan 15)
   Attendees and action items...
```

### 3. Handle No Results
```
User: "Find my notes about dragons"
→ search_notes("dragons")
→ No results

Response:
"I couldn't find any notes about dragons. Would you like me to save something about this topic?"
```

## Search Tips

1. **Use keywords** - Search with key terms, not full sentences
2. **Try variations** - If no results, try synonyms
3. **Check recent** - Use `get_notes()` if user wants recent items
4. **Be specific** - Quote exact titles if known

## Error Handling

If search fails:
```
⚠️ Search temporarily unavailable. Please try again.
```
