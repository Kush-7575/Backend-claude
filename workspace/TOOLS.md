# TOOLS.md - Local Notes

Skills define *how* tools work. This file is for *your* specifics — environment-specific info that Fable needs.

## What Goes Here

- Device names and locations
- User preferences for tool behavior
- API keys or service aliases (if needed)
- Any setup-specific details

## Quick Reference

| Action | Tool |
|--------|------|
| Find anything | `memory_search` |
| Save content | `smart_save` |
| Create reminder | `create_reminder` |
| List notes | `get_notes` |
| Search web | `web_search` |

## Key Principle

**Search first, then act.** Before answering questions about past info:
1. Call `memory_search` with relevant keywords
2. Answer based on what you found

## Confirmation Style

- Notes: `✅ Saved: [title]`
- Reminders: `⏰ Set: [task] at [time]`
- Searches: `🔍 Found [N] notes`
- Errors: `⚠️ [Brief message]`

---

Add your environment specifics below as you learn them.
