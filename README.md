# BrainMap Backend (Claude SDK)

A production-grade AI assistant backend built with Claude Agent SDK, implementing battle-tested patterns from Clawdbot.

## 🏗️ Architecture

```
Backend-claude/
├── main.py              # FastAPI app with lifecycle
├── requirements.txt     # Dependencies
├── .env.template        # Configuration template
│
├── core/                # Core systems
│   ├── config.py        # Pydantic Settings
│   ├── context.py       # Token counting, compaction
│   ├── session.py       # Session state management
│   ├── agent.py         # Claude SDK integration
│   └── prompt.py        # Modular prompt builder
│
├── tools/               # Tool system
│   ├── registry.py      # Tool registration/execution
│   └── core.py          # Notes, reminders, utilities
│
├── memory/              # Memory persistence
│   └── manager.py       # Daily logs, MEMORY.md, search
│
├── database/            # Database layer
│   └── client.py        # Supabase client
│
├── routers/             # FastAPI routers
│   ├── health.py        # Health checks
│   ├── chat.py          # SSE streaming chat
│   ├── notes.py         # Notes CRUD
│   └── reminders.py     # Reminders CRUD
│
├── scheduler/           # Proactive features
│   └── heartbeat.py     # Periodic checks
│
└── skills/              # Extensible skills
    ├── loader.py        # SKILL.md parser
    ├── notes/SKILL.md   # Notes skill
    └── reminders/SKILL.md
```

## ✨ Key Features

### Context Management
- **Token Counting**: Uses tiktoken for accurate token measurement
- **Auto-Compaction**: Summarizes old messages before context overflow
- **Memory Flush**: Persists important info before compaction

### Session Management
- **State Persistence**: Sessions saved to Supabase
- **Compaction Tracking**: Counts how many times session compacted
- **Memory Integration**: Coordinates with MemoryManager for pre-flush

### Smart Tools
- **smart_save**: Automatic deduplication - searches before creating
- **Natural Time Parsing**: "tomorrow 9am", "in 2 hours", "Friday 3pm"
- **Structured Results**: Consistent response format

### Proactive Assistant
- **Heartbeat Runner**: Checks reminders/calendar every 30 min
- **Notification Hooks**: Callback system for alerts

### Skill System
- **SKILL.md Format**: YAML frontmatter + markdown instructions
- **Dynamic Loading**: Skills loaded at startup
- **Per-User Activation**: Enable/disable skills per user

## 🚀 Quick Start

```bash
# 1. Copy environment template
cp .env.template .env

# 2. Add your API keys to .env
#    - ANTHROPIC_API_KEY
#    - SUPABASE_URL
#    - SUPABASE_KEY

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the server
python main.py
```

Server runs on `http://localhost:8001`

## 📡 API Endpoints

### Chat
```http
POST /v1/chat/send
Content-Type: application/json

{"message": "Save a note about project ideas"}
```

Returns Server-Sent Events (SSE):
- `data: <token>` - Response tokens
- `think: <status>` - Tool execution
- `action: <json>` - Structured data
- `data: [DONE]` - Complete

### Notes
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/notes` | List notes |
| POST | `/v1/notes` | Create note |
| GET | `/v1/notes/{id}` | Get note |
| PATCH | `/v1/notes/{id}` | Update note |
| DELETE | `/v1/notes/{id}` | Delete note |

### Reminders
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/reminders` | List reminders |
| POST | `/v1/reminders` | Create reminder |
| POST | `/v1/reminders/{id}/complete` | Mark complete |

### Health
| Endpoint | Description |
|----------|-------------|
| `/health` | Full status |
| `/health/ready` | Readiness probe |
| `/health/live` | Liveness probe |

## ⚙️ Configuration

Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | Claude API key (required) |
| `CLAUDE_MODEL` | claude-sonnet-4-20250514 | Model to use |
| `MAX_CONTEXT_TOKENS` | 150000 | Context limit |
| `MEMORY_DIR` | ./memory_store | Memory files location |
| `HEARTBEAT_ENABLED` | true | Enable proactive checks |
| `HEARTBEAT_INTERVAL_MINUTES` | 30 | Check interval |

## 🔧 Extending

### Add a Tool

```python
# In tools/core.py or new file

from tools.registry import tool

@tool(description="Do something useful")
async def my_tool(param: str) -> dict:
    """Tool implementation."""
    return {"status": "success", "result": param}
```

### Add a Skill

Create `skills/my-skill/SKILL.md`:

```markdown
---
name: my-skill
description: What this skill does
triggers:
  - trigger phrase
---

# Instructions

Detailed instructions for the AI...
```

## 📊 Comparison with Old Backend

| Feature | Old Backend | New Backend |
|---------|-------------|-------------|
| AI Model | Gemini | Claude |
| Context Tracking | ❌ | ✅ tiktoken |
| Session Compaction | ❌ | ✅ Auto |
| Memory Persistence | ❌ | ✅ Files + DB |
| Proactive Checks | ❌ | ✅ Heartbeat |
| Skill System | ❌ | ✅ SKILL.md |
| Retry Logic | ❌ | ✅ Tenacity |

## 📝 License

MIT
