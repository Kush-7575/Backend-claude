---
name: notes
description: Core notes management - save, search, organize information
triggers:
  - save this
  - remember
  - make a note
  - add to my notes
  - my notes about
---

# Notes Skill

You have access to the user's notes system for storing and retrieving information.

## Available Tools

| Tool | When to Use |
|------|-------------|
| `smart_save` | **Primary** - Saves with automatic deduplication |
| `search_notes` | Find existing notes by topic |
| `get_notes` | List recent notes |
| `append_to_note` | Add to an existing note |
| `delete_note` | Remove a note |

## Key Behaviors

### 1. Search Before Save
Before creating a new note, ALWAYS search for existing notes on the same topic:
```
User: "Add eggs to my grocery list"
→ search_notes("grocery") first
→ If found, append_to_note instead of creating duplicate
```

### 2. Smart Deduplication
Use `smart_save` as default - it handles deduplication automatically:
- Searches for similar topic
- Appends if match found
- Creates new only if no match

### 3. Confirmation Format
After saving:
```
✅ Saved to "[Note Title]"
```

After appending:
```
✅ Added to "[Existing Note Title]"
```

## Note Types

| Type | Use For |
|------|---------|
| `collection` | General notes, ideas, info |
| `list` | Checklists, todos, items |

## Examples

**Save new info:**
```
User: "Remember that the meeting is at 3pm tomorrow"
→ smart_save(topic="Meeting", content="Meeting at 3pm tomorrow")
→ "✅ Saved: Meeting"
```

**Add to existing:**
```
User: "Add milk to my grocery list"
→ search_notes("grocery") → finds "Grocery List"
→ append_to_note(note_id="...", content="milk")
→ "✅ Added to: Grocery List"
```

**Search:**
```
User: "What did I save about the project?"
→ search_notes("project")
→ Returns matching notes
```
