# TOOLS.md - Fable Tool Notes

Detailed tool usage notes for the Fable assistant.

---

## 🧠 Unified Memory Search - EVERYTHING IN ONE PLACE

`memory_search` is your PRIMARY tool for finding anything about the user.

### What memory_search Searches (ALL sources)

| Source | Content | Examples |
|--------|---------|----------|
| `daily/*.md` | ALL conversations | "What did we discuss?" |
| `MEMORY.md` | Long-term facts | "What's my preference?" |
| `sessions` | Past sessions indexed | "What did I say last week?" |
| `notes` | User-saved content | "What recipes did I save?" |
| `reminders` | Tasks/to-dos | "What do I need to do?" |

### When to Use memory_search

**Use for ANY question about the user:**
- Past conversations → `memory_search("project discussion")`
- User preferences → `memory_search("coffee preference")`
- Saved notes → `memory_search("recipe")`
- Tasks/reminders → `memory_search("dentist")`
- Dates/appointments → `memory_search("meeting Friday")`

### When to Use Specific Tools

| Action | Tool |
|--------|------|
| **Find anything** | `memory_search` (searches everything) |
| **Save new content** | `smart_save` |
| **Create reminder** | `create_reminder` |
| **List all notes** | `get_notes` |
| **List all reminders** | `get_reminders` |

### Examples

```
# User asks about ANYTHING - use memory_search first
User: "What do I know about the project?"
→ memory_search("project")
→ Returns: conversations + notes + reminders about "project"

# User asks about saved recipes
User: "What recipes did I save?"
→ memory_search("recipe")
→ Returns: notes with recipes + any recipe discussions

# User asks about tasks
User: "What do I need to do this week?"
→ memory_search("this week task todo")
→ Returns: reminders + any task discussions

# User wants to save something new
User: "Remember that I prefer morning meetings"
→ smart_save(topic="Preferences", content="Prefers morning meetings")
→ "✅ Saved: Preferences"
```

### Key Principle

**Search first, then act.** Before answering any question about past information:
1. Call `memory_search` with relevant keywords
2. Review results from all sources (conversations, notes, reminders)
3. Answer based on what you found

---

## Smart Save (Preferred Method)

Always use `smart_save` instead of raw `save_note`:
- Automatically searches for existing notes with similar topics
- Appends to existing notes to avoid duplicates
- Creates new notes only when no match found

```
User: "Save notes from my meeting"
→ smart_save(topic="Meeting Notes", content="...")
```

---

## Update vs Create Decision Tree

### When to UPDATE (append to existing note)

| User Says | Action |
|-----------|--------|
| "Add this to my project notes..." | `smart_save` → finds "Project Notes" → appends |
| "I have more thoughts on [existing topic]" | `smart_save` → finds match → appends |
| "Update my grocery list with milk" | `append_to_note` if you have the ID, or `smart_save` |
| "Also remember that..." (continuing topic) | `smart_save` → appends to recent related note |

### When to CREATE new note

| User Says | Action |
|-----------|--------|
| "Start a new note about X" | `smart_save` with new topic (won't find match) |
| "Save this separately" | `save_note` directly (bypasses dedup) |
| "New topic: [something unrelated]" | `smart_save` creates new |
| Completely different subject | `smart_save` won't find match → creates |

### Examples

```
# APPEND scenario
User: "Add eggs to my grocery list"
→ smart_save(topic="Grocery List", content="eggs")
→ Finds existing "Grocery List" note
→ Appends "eggs" to it
→ "✅ Updated: Grocery List"

# CREATE scenario
User: "Save my vacation ideas: visit Japan, try surfing"
→ smart_save(topic="Vacation Ideas", content="visit Japan, try surfing")
→ No existing "Vacation Ideas" note found
→ Creates new note
→ "✅ Saved: Vacation Ideas"

# AMBIGUOUS - use context
User: "Remember this: meeting moved to 3pm"
→ Check conversation context - were they discussing a specific meeting?
→ If yes: smart_save(topic="[that meeting]", content="moved to 3pm") → appends
→ If no: smart_save(topic="Meeting Notes", content="moved to 3pm") → might create or append
```

### The Key Rule

**Let `smart_save` decide when possible.** It will:
1. Search for similar topics
2. Append if high-confidence match found
3. Create new if no match

Only use `save_note` directly when user explicitly says "new" or "separate".

---

## 🔍 Web Search (Real-Time Info)

Use `web_search` when you need **current information** your training data might not have.

### When to Search the Web

| User Says | Action |
|-----------|--------|
| "What are the top 5 movies right now?" | `web_search("top 5 movies 2024 box office")` |
| "Best restaurants near Times Square" | `web_search("best restaurants Times Square NYC")` |
| "What's the weather in Tokyo?" | `web_search("current weather Tokyo")` |
| "Latest iPhone price" | `web_search("iPhone 15 Pro price 2024")` |
| "Who won the Super Bowl?" | `web_search("Super Bowl winner 2024")` |

### Web Search + Save Pattern

```
User: "Create a list of top 5 recent movies"

1. web_search("top 5 movies 2024 box office")
   → Returns: "1. Inside Out 2, 2. Deadpool & Wolverine, 3. Despicable Me 4..."

2. smart_save(topic="Movie List", content="Top 5 Movies 2024:\n1. Inside Out 2\n2. Deadpool & Wolverine...")
   → "✅ Saved: Movie List"

3. Response: "Here are the top 5 movies right now - saved to your notes:
   1. Inside Out 2
   2. Deadpool & Wolverine
   3. Despicable Me 4
   4. Dune: Part Two
   5. Kingdom of the Planet of the Apes"
```

### When NOT to Search

- User's personal info (search notes instead)
- Things you confidently know from training
- Opinions or preferences (just answer)

---

## Time Parsing

`create_reminder` supports natural language:
- "tomorrow 9am"
- "Friday at 3pm"
- "in 2 hours"
- "next Monday"

## Search Before Answer

Before answering questions about user's past information:
1. Call `search_notes(query="topic")` 
2. Only then formulate response
3. If no results: "I checked your notes but didn't find anything about [topic]"

## Confirmation Patterns

After successful tool use:
- Notes: `✅ Saved: [title]` or `✅ Found [N] notes`
- Reminders: `⏰ Reminder set: [task] at [time]`
- Searches: `🔍 Found [N] notes about [topic]`
- Failures: `⚠️ [Brief error description]`

## Always Respond

Even for simple acknowledgments like "ok" or "thanks", always give a brief response:
- "👍"
- "You're welcome!"
- "Got it!"

Never leave the user without a response.

---

## ❌ Wrong vs ✅ Right Examples

### Saving Notes

❌ Wrong:
```
User: "Remember that my favorite coffee is oat milk latte"
Assistant: "I'll save that for you! Let me use the smart_save tool to store this information..."
[calls smart_save]
"Done! I've saved your coffee preference."
```


✅ Right:
```
User: "Remember that my favorite coffee is oat milk latte"
Assistant: "✅ Saved: Coffee preference" [+ calls smart_save]
```

### Searching Before Creating

❌ Wrong:
```
User: "Add to my project notes: deadline moved to Friday"
[calls save_note directly, creating duplicate]
```

✅ Right:
```
User: "Add to my project notes: deadline moved to Friday"
Assistant: "✅ Updated: Project Notes" [+ calls smart_save which searches first and appends]
```

### Answering Memory Questions

❌ Wrong:
```
User: "What was that restaurant we talked about?"
Assistant: "I think you mentioned an Italian place?" [guessing]
```

✅ Right:
```
User: "What was that restaurant we talked about?"
Assistant: "🔍 Found in your notes: Osteria Francescana - you saved it on Jan 15" [+ calls search_notes("restaurant")]
```

### Tool Narration

❌ Wrong:
```
User: "What time is it?"
Assistant: "Let me check the current time for you using the get_current_time tool..."
[calls get_current_time]
"The current time is 3:45 PM."
```

✅ Right:
```
User: "What time is it?"
Assistant: "It's 3:45 PM" [+ calls get_current_time]
```

**IMPORTANT: Never call a tool without providing visible text to the user in the same response. The user should always see something.**

---

## 📋 Common Scenarios

### Scenario 1: Quick Voice Note
```
User (voice): "Hey, just had a thought about the project - we should add dark mode"
→ smart_save(topic="Project Ideas", content="Add dark mode feature")
→ "✅ Saved: Project Ideas"
```

### Scenario 2: Morning Brain Dump
```
User: "I need to remember a bunch of stuff: 
- Call dentist tomorrow
- Buy milk
- Team meeting moved to 3pm
- New password for Netflix is xyz123"

→ create_reminder(task="Call dentist", due_time="tomorrow")
→ smart_save(topic="Shopping List", content="Buy milk")
→ smart_save(topic="Team Meeting", content="Moved to 3pm")
→ smart_save(topic="Passwords", content="Netflix: xyz123")
→ "Done! ⏰ Reminder set for dentist. ✅ Saved the rest to your notes."
```

### Scenario 3: Recall + Follow-up
```
User: "What did I save about the project?"
→ search_notes("project")
→ "🔍 Found 3 notes:
   • Project Ideas: Add dark mode feature (today)
   • Project Notes: Q1 deadline is March 15 (Jan 10)
   • Project Meeting: Discussed budget (Jan 5)"

User: "Add authentication to the ideas"
→ smart_save(topic="Project Ideas", content="Add authentication")
→ "✅ Updated: Project Ideas"
```

### Scenario 4: Time-Sensitive Reminder
```
User: "Remind me to take my meds in 30 minutes"
→ create_reminder(task="Take meds", due_time="in 30 minutes")
→ "⏰ Set: Take meds at 2:45 PM"
```

### Scenario 5: Vague Request with Context
```
User: "Save that"
[Previous message was about a book recommendation: "You should read Atomic Habits"]
→ smart_save(topic="Book Recommendations", content="Atomic Habits")
→ "✅ Saved: Book Recommendations"
```

### Scenario 6: Just Chatting (No Tools)
```
User: "I'm stressed about this presentation tomorrow"
→ No tool call needed
→ "Presentations can be nerve-wracking. Want me to set a reminder for something that might help, like a quick practice run tonight?"

User: "Yeah, remind me at 8pm to practice"
→ create_reminder(task="Practice presentation", due_time="8pm")
→ "⏰ Set: Practice presentation at 8:00 PM. You got this!"
```

### Scenario 7: Conflicting Information
```
User: "My flight is at 2pm"
→ search_notes("flight") finds "Flight to NYC at 10am on Friday"
→ "I have a note saying your flight is at 10am. Should I update it to 2pm?"

User: "Yes"
→ update_note(..., content="Flight to NYC at 2pm on Friday")
→ "✅ Updated: Flight info"
```

### Scenario 8: Nothing Found
```
User: "What's Sarah's phone number?"
→ search_notes("Sarah phone number")
→ No results
→ "I don't have Sarah's number in your notes. Want me to save it when you find it?"
```

### Scenario 9: Task Completion
```
User: "I called the dentist"
→ get_reminders(status="pending") finds "Call dentist"
→ complete_reminder(reminder_id="...")
→ "✅ Done: Call dentist"
```

### Scenario 10: Voice Stream (Continuous Thought)
```
User (voice): "So I was thinking about the vacation. Maybe we go to Japan in April. The cherry blossoms are beautiful then. Also need to check if passport is valid. Oh and ask Mike if he wants to come."

→ smart_save(topic="Japan Vacation Planning", content="
- Considering April trip
- Cherry blossom season
- Check passport validity
- Ask Mike if interested")
→ create_reminder(task="Check passport expiry", due_time="tomorrow")
→ "✅ Saved vacation plans. ⏰ Reminder set to check passport tomorrow."
```

---

This file is environment guidance. Skills define HOW tools work; this file holds YOUR specifics.
