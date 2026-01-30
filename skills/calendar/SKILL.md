---
name: calendar
description: View and create Google Calendar events
triggers:
  - my calendar
  - schedule
  - what's on my calendar
  - am i free
requires:
  env: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
  tools: ["calendar_list_events", "calendar_create_event", "calendar_check_availability"]
---

# Calendar Integration

You can interact with the user's Google Calendar.

## Available Tools

| Tool | When to Use |
|------|-------------|
| `calendar_list_events` | Show upcoming events |
| `calendar_create_event` | Create a new calendar event |
| `calendar_check_availability` | Check if time slot is free |

## Workflow

### Viewing Schedule

```
User: "What's on my calendar tomorrow?"
→ calendar_list_events(days_ahead=1)
→ Format as readable list
```

### Creating Events

```
User: "Schedule a meeting tomorrow at 2pm"
→ Parse time: tomorrow 2pm → ISO format
→ calendar_create_event(title="Meeting", start_time="...")
```

### Checking Availability

```
User: "Am I free Friday afternoon?"
→ calendar_check_availability(start, end)
→ Report conflicts or confirm free
```

## Time Handling

- Always use ISO format for API calls
- Display times in user's local timezone
- Default event duration: 1 hour
- Default time if not specified: 9am

## Confirmation Format

For created events:
```
📅 Event created: "[Title]"
   [Date] at [Time]
```

For schedule view:
```
📅 Your upcoming events:
• [Time] - [Title]
• [Time] - [Title]
```

## Error Handling

If Calendar is not connected:
```
⚠️ Calendar not connected. Please connect Google Calendar in settings.
```
