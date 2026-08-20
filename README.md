# ADHD Co-Processor

### Local-first personal AI — everyday assistant + coding assistant + second brain + web-task agent

**$0 cost. Zero cloud calls. All inference and storage runs on your machine.**

---

## What It Does

| Capability | How |
|------------|-----|
| **Brain dump** | Speak or type stream-of-consciousness → agent extracts tasks, ideas, reminders → stored in semantic memory |
| **Conversation memory** | Multi-turn context — the agent remembers what you discussed earlier in a session |
| **Adaptive schedule** | Time-blocked schedule with α-scaled durations (learns from your actual vs estimated times), 15-min transition buffers, silent zero-guilt rebalance |
| **Task completion tracking** | Record actual time spent vs estimates, auto-update alpha, show completion stats |
| **Study planner** | Any topic → sub-15-minute micro-units with dependencies, prerequisites, and active-recall questions |
| **Body doubling** | Monitors window focus, gentle spoken nudge on drift — never red badges, never overdue counters |
| **Coding assistant** | Natural language → code changes (fix bugs, add features, explain, refactor) — all local via Ollama |
| **Web tasks** | Search, scrape, or complete web tasks in plain English — drives a real browser session |
| **Voice** | Speak in (Faster-Whisper STT), hear back (Kokoro-82M TTS) — calm, non-urgent tone |
| **Obsidian mirror** | Every brain dump auto-writes a formatted note + auto-generated Dashboard.md with stats |
| **Notifications** | Browser/PWA notifications for task reminders, focus nudges, schedule updates |
| **Cron scheduler** | Persistent recurring tasks (daily digest, memory consolidation, hourly check-ins) |
| **Google Calendar sync** | Two-way sync: reads busy blocks, pushes schedule around them, identifies copilot events |
| **Android wake word** | "Hey Jarvis" via Home Assistant Companion → Wyoming protocol bridge |
| **Data sovereignty** | Continuous network trace, allowlist enforcement, one-click purge all memory |
| **Native desktop app** | Tauri v2 native macOS app (.app + .dmg installer) |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CLIENTS                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │  Phone   │  │   Mac    │  │  Native Desktop   │  │
│  │   PWA    │  │ Browser  │  │  (Tauri / pywebview)│ │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       │              │                 │              │
│       └──────────────┼─────────────────┘              │
│                      │ WebSocket / REST               │
└──────────────────────┼───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│               FastAPI Backend (Python)                │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Agents     │  │    Memory    │  │   Speech   │  │
│  │  ─ braindump│  │  ─ Mem0      │  │  ─ Whisper │  │
│  │  ─ schedule │  │  ─ Qdrant    │  │  ─ Kokoro  │  │
│  │  ─ study    │  │  ─ Obsidian  │  │            │  │
│  │  ─ body dbl │  │  ─ Calendar  │  │            │  │
│  │  ─ coding   │  │              │  │            │  │
│  │  ─ web task │  │              │  │            │  │
│  └─────────────┘  └──────────────┘  └────────────┘  │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │  Event Bus   │  │  Scheduler   │  │  Skills   │  │
│  │  ─ publish   │  │  ─ cron      │  │  ─ invoke │  │
│  │  ─ subscribe │  │  ─ recurring │  │  ─ stats  │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐                  │
│  │  Sovereignty │  │   Metrics    │                  │
│  │  ─ trace     │  │  ─ latency   │                  │
│  │  ─ allowlist │  │  ─ energy    │                  │
│  │  ─ purge     │  │  ─ dashboard │                  │
│  └──────────────┘  └──────────────┘                  │
└──────────────────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌─────────┐   ┌─────────┐
   │ Ollama  │   │ Qdrant  │   │ Tailscale│
   │ :11434  │   │ :6333   │   │ (VPN)   │
   └─────────┘   └─────────┘   └─────────┘
```

---

## Quick Start

### Prerequisites

```bash
# Install uv (Python package manager)
curl -fsSL https://astral.sh/uv/install.sh | bash

# Install Ollama (local inference)
curl -fsSL https://ollama.com/install.sh | bash

# Pull models
ollama pull qwen3.5:9b
ollama pull nomic-embed-text

# Start Qdrant (vector database)
docker compose up -d
```

### Run

```bash
# Install dependencies
uv sync

# CLI mode (interactive)
uv run python main.py

# Web UI server
uv run python main.py --ui

# Voice interface
uv run python main.py --voice

# Native desktop app
uv run python main.py --desktop

# Wyoming bridge (Android wake word)
uv run python main.py --wake-word

# Data sovereignty check
uv run python main.py --sovereignty

# Run tests
uv run python main.py --test
```

---

## Agents

| Agent | File | What It Does |
|-------|------|-------------|
| **Braindump** | `agents/braindump_agent.py` | Parses stream-of-consciousness text into structured JSON tasks with type, priority, estimated time, and tags |
| **Scheduler** | `agents/scheduler_agent.py` | Builds time-blocked schedules with α-scaling, transition buffers, and silent rebalance on missed blocks |
| **Study** | `agents/study_agent.py` | Decomposes topics into sub-15-minute micro-units with dependencies and active-recall questions |
| **Body Double** | `agents/body_double_agent.py` | Monitors window focus via psutil/OS APIs, triggers gentle spoken nudges on sustained drift |
| **Coding** | `agents/coding_agent.py` | Fix bugs, add features, explain code, refactor — Ollama-powered with Aider fallback |
| **Web Task** | `agents/web_task_agent.py` | Search, scrape, or complete web tasks using Scrapling (anti-bot) + LLM planning |
| **Calendar Sync** | `agents/calendar_sync.py` | Two-way Google Calendar sync: reads busy blocks, pushes schedule, merges around conflicts |
| **Monitor** | `agents/monitor_operative.py` | Persistent stateful monitoring with change detection across sessions |

---

## API Endpoints (55+ total)

### Core
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | System health + component status |
| GET | `/api/dashboard` | Performance metrics (latency, energy) |
| GET | `/api/recommendations` | Performance improvement suggestions |

### Agents
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/braindump` | Process a brain dump (supports `conversation_id`) |
| GET | `/api/schedule` | Build schedule from tasks |
| POST | `/api/rebalance` | Rebalance after missed block |
| POST | `/api/study` | Decompose study topic |
| POST | `/api/sprint` | Generate micro-sprint suggestion |
| POST | `/api/code` | Coding assistant (fix/add/explain/refactor) |
| POST | `/api/code/apply` | Apply code changes (dry run or real) |
| POST | `/api/web-task` | Execute web task |
| GET | `/api/web-task/search` | Quick web search |
| GET | `/api/web-task/scrape` | Quick web scrape |

### Conversation Memory
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/conversation` | Multi-turn conversation with context |
| GET | `/api/conversations` | List all conversations |
| GET | `/api/conversations/{id}` | Get conversation history |
| DELETE | `/api/conversations/{id}` | Delete a conversation |

### Task Completion Tracking
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tasks/start` | Record task started |
| POST | `/api/tasks/complete` | Record task completed (updates alpha) |
| GET | `/api/tasks/completions` | Get completion history |
| GET | `/api/tasks/alpha` | Get current time-scaling alpha |

### Memory
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memories` | List all memories |
| POST | `/api/memories/search` | Semantic memory search |
| POST | `/api/purge` | Delete all memories |

### Calendar
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/calendar/status` | Check OAuth status |
| GET | `/api/calendar/today` | Today's events + busy blocks |
| POST | `/api/calendar/sync` | Sync schedule to Google Calendar |
| POST | `/api/calendar/clear` | Remove copilot events |

### Scheduler
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scheduler/tasks` | List scheduled tasks |
| POST | `/api/scheduler/tasks` | Create scheduled task |
| POST | `/api/scheduler/tasks/{id}/pause` | Pause a task |
| POST | `/api/scheduler/tasks/{id}/resume` | Resume a task |

### Notifications
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/notifications` | Get notifications (filterable) |
| POST | `/api/notifications/send` | Send a notification |
| POST | `/api/notifications/{id}/read` | Mark notification as read |
| POST | `/api/notifications/read-all` | Mark all as read |
| DELETE | `/api/notifications` | Clear all notifications |
| GET | `/api/notifications/preferences` | Get notification preferences |
| POST | `/api/notifications/preferences` | Update notification preferences |

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/skills` | List available skills |
| POST | `/api/skills/{name}/invoke` | Invoke a skill |
| GET | `/api/monitor/stats` | Focus + task monitoring stats |
| GET | `/api/network-check` | Outbound connection audit |
| GET | `/api/obsidian` | Obsidian vault status |
| GET | `/api/obsidian/notes` | List vault notes |
| GET | `/api/sovereignty/status` | Quick sovereignty check |
| GET | `/api/sovereignty/snapshot` | Full network snapshot |
| GET | `/api/sovereignty/report` | 30s trace + report |
| POST | `/api/sovereignty/purge` | Purge all memory + logs |

### WebSocket
| Endpoint | Description |
|----------|-------------|
| `/ws/voice` | Real-time voice streaming (STT + TTS) |
| `/ws/pwa` | PWA push-to-talk + commands |

---

## Configuration

All settings in `config/config.toml`:

```toml
[engine.ollama]
base_url = "http://localhost:11434"
default_model = "llama3.1:latest"
embedding_model = "nomic-embed-text"

[speech]
whisper_model = "small.en"
whisper_compute_type = "int8"
tts_voice = "af_heart"
tts_speed = 1.07

[memory]
qdrant_url = "http://localhost:6333"
collection_name = "adhd_memory"
embedding_dim = 768

[scheduler]
transition_buffer_minutes = 15
time_scaling_alpha_min = 1.4
time_scaling_alpha_max = 1.8

[body_double]
drift_threshold_minutes = 10
nudge_cooldown_minutes = 15

[obsidian]
enabled = true
vault_path = "vault"

[guardrails]
no_red_badges = true
no_overdue_counters = true
supportive_phrasing = true
```

---

## File Structure

```
adhd-copilot/
├── agents/
│   ├── braindump_agent.py        # Stream-of-consciousness → structured JSON
│   ├── scheduler_agent.py        # Adaptive calendar with α-scaling
│   ├── study_agent.py            # Topic → sub-15min micro-units
│   ├── body_double_agent.py      # Window focus monitoring + nudges
│   ├── coding_agent.py           # Ollama + Aider coding assistant
│   ├── web_task_agent.py         # Scrapling web scraping + LLM planning
│   ├── calendar_sync.py          # Google Calendar two-way sync
│   └── monitor_operative.py      # Persistent stateful monitoring
├── memory/
│   └── adhd_memory.py            # Mem0 + Qdrant + Obsidian + Conversation + Task Tracker
├── speech/
│   └── speech_pipeline.py        # Faster-Whisper STT + Kokoro TTS
├── core/
│   ├── event_bus.py              # Publish/subscribe event system
│   ├── skill_manager.py          # Composable skill registry
│   ├── eval_metrics.py           # Latency + energy tracking
│   ├── cron_scheduler.py         # Persistent recurring tasks
│   ├── memory_service.py         # Background fact extraction
│   ├── sovereignty.py            # Network trace + allowlist + purge
│   └── notifications.py          # Browser + PWA notification manager
├── remote/
│   ├── pwa_server.py             # WebSocket + REST for PWA
│   ├── wyoming_bridge.py         # Android wake-word bridge
│   └── desktop_shell.py          # pywebview native window
├── ui/
│   ├── pwa/                      # Mobile PWA (manifest, app.js, icons)
│   ├── desktop/                  # Desktop shell UI (9 views)
│   └── desktop-tauri/            # Tauri native app (builds to .app/.dmg)
├── vault/                        # Obsidian vault (auto-written)
├── config/
│   ├── config.toml               # All settings
│   └── google_client_secret.json # OAuth credentials (gitignored)
├── scripts/
│   └── calendar_setup.py         # OAuth setup wizard
├── data/                         # Runtime data (task_history, logs)
├── docker-compose.yml            # Qdrant
├── pyproject.toml                # Python dependencies (uv)
└── main.py                       # Entry point (CLI, API, voice, desktop)
```

---

## Interfaces

### CLI Mode
```bash
uv run python main.py
```
Interactive commands: `dump`, `schedule`, `study`, `memory`, `search`, `sprint`, `code`, `web`, `complete`, `conversations`, `skills`, `dashboard`, `desktop`, `purge`, `quit`

### Web UI
```bash
uv run python main.py --ui
# Open http://localhost:8080
```
Full dashboard with brain dump input, schedule view, voice recording, and sovereignty status.

### PWA (Phone)
```bash
uv run python main.py --ui
# Open http://YOUR-TAILSCALE-IP:8080 on phone
# Add to home screen → install as app
```
Push-to-talk voice, text commands, real-time responses via WebSocket.

### Desktop App
```bash
uv run python main.py --desktop
# Or launch the native .app directly:
open ui/desktop-tauri/src-tauri/target/release/bundle/macos/ADHD\ Co-Processor.app
```
pywebview (immediate) or Tauri native (pre-built).

### Voice Interface
```bash
uv run python main.py --voice
```
Full-duplex voice: speak in → Whisper transcribes → agent processes → Kokoro speaks reply.

### Android Wake Word
```bash
uv run python main.py --wake-word
# Runs Wyoming protocol server on port 10700
# Connect via Home Assistant Companion app
```

---

## Data Sovereignty

The app makes **zero outbound calls** except:
- **Tailscale** (100.x.x.x) — private VPN tunnel
- **Google OAuth** (142.250.x.x:443) — Calendar sync only
- **Localhost** — Ollama, Qdrant

### Verify yourself:
```bash
# Quick check
uv run python main.py --sovereignty

# Full 30-second trace
uv run python -m core.sovereignty --report

# One-click purge all memory
uv run python -m core.sovereignty --purge
```

### API check:
```
GET /api/sovereignty/status → {"verdict": "clean", "violations": 0, ...}
```

---

## Cost

| Component | Cost |
|-----------|------|
| Ollama (local inference) | $0 |
| Qdrant (vector DB) | $0 (Docker, local) |
| Mem0 (memory) | $0 |
| Faster-Whisper (STT) | $0 |
| Kokoro-82M (TTS) | $0 |
| Google Calendar API | $0 (free tier) |
| Obsidian (vault viewer) | $0 |
| Tauri (desktop app) | $0 |
| Tailscale (VPN) | $0 (personal tier, 100 devices) |
| **Total** | **$0** |

Optional: Raspberry Pi 5 (~$80) for always-on host.

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Environment setup | ✅ |
| 1 | Local inference (Ollama) | ✅ |
| 2 | Agent orchestration | ✅ |
| 3 | Persistent memory (Mem0 + Qdrant) | ✅ |
| 3.5 | Obsidian visible mirror | ✅ |
| 4 | Voice pipeline (Whisper + Kokoro) | ✅ |
| 5 | Adaptive scheduler | ✅ |
| 6 | Study decomposition | ✅ |
| 7 | Body doubling + ambient focus | ✅ |
| 8 | Coding assistant | ✅ |
| 9 | Web-task agent | ✅ |
| 10 | Always-on host migration | ⏳ Needs Pi/mini |
| 11 | Remote access + PWA | ✅ |
| 12 | Phone-side wake word | ✅ Android / ⚠️ iOS (manual) |
| 13 | UI shell & integration | ✅ Tauri + pywebview |
| 14 | Data sovereignty pass | ✅ |

---

## License

Free and open-source. Built with ❤️ for the ADHD community.
