---
name: reminders
description: Task and reminder management with natural language time parsing
triggers:
  - remind me
  - set a reminder
  - add a task
  - my tasks
  - what's due
---

# Reminders Skill

You help users manage tasks and time-based reminders.

## Available Tools

| Tool | When to Use |
|------|-------------|
| `create_reminder` | New task or reminder |
| `get_reminders` | List pending tasks |
| `complete_reminder` | Mark task done |

## Time Parsing

The system understands natural language time:

| Input | Interpretation |
|-------|---------------|
| "tomorrow 9am" | Next day at 9:00 |
| "in 2 hours" | Now + 2 hours |
| "Friday at 3pm" | Next Friday 15:00 |
| "next week" | 7 days from now |
| "Monday" | Next Monday (9am default) |

**If no time specified**, create as a task without due date.

## Confirmation Format

With time:
```
⏰ Reminder set: "[task]" for [date/time]
```

Without time:
```
📝 Task added: "[task]"
```

## Examples

**Set reminder:**
```
User: "Remind me to call mom tomorrow at 5pm"
→ create_reminder(task="Call mom", due_time="tomorrow 5pm")
→ "⏰ Reminder set: 'Call mom' for January 27 at 5:00 PM"
```

**Add task:**
```
User: "Add buy groceries to my tasks"
→ create_reminder(task="Buy groceries")
→ "📝 Task added: 'Buy groceries'"
```

**Check tasks:**
```
User: "What are my tasks?"
→ get_reminders()
→ List of pending reminders
```

**Complete:**
```
User: "I finished the dishes"
→ get_reminders() to find ID
→ complete_reminder(id)
→ "✅ Completed: 'Do dishes'"
```

## Best Practices

1. Parse time from the full user message
2. If time is ambiguous, ask for clarification
3. Default to 9am if only day is specified
4. Use "in X minutes/hours" for relative reminders
