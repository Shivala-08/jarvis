# Jarvis v3 Instruction Manual

### Setup, configuration, and usage for the ADHD Co-Processor

---

## Table of Contents

1. [What It Does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Setup](#4-setup)
5. [Configuration](#5-configuration)
6. [Running the App](#6-running-the-app)
7. [Agents](#7-agents)
8. [Phase B — Cloud Escalation](#8-phase-b--cloud-escalation)
9. [Phase C — Cross-Device Sync](#9-phase-c--cross-device-sync)
10. [Phase D — Proactive Triggers](#10-phase-d--proactive-triggers)
11. [Phase E — Vision Agent](#11-phase-e--vision-agent)
12. [API Reference](#12-api-reference)
13. [Data Sovereignty](#13-data-sovereignty)
14. [Troubleshooting](#14-troubleshooting)
15. [Phase Status](#15-phase-status)

---

## 1. What It Does

A local-first personal AI that runs entirely on your machine. No cloud calls for core functionality. All inference and storage stays on your hardware.

| Capability | How |
|------------|-----|
| **Brain dump** | Stream-of-consciousness → structured JSON tasks with type, priority, estimated time, and tags |
| **Conversation memory** | Multi-turn context across sessions (Mem0 + Qdrant) |
| **Adaptive schedule** | Time-blocked with α-scaling, transition buffers, silent zero-guilt rebalance |
| **Task tracking** | Actual time vs estimates, auto-update alpha, completion stats |
| **Study planner** | Topic → sub-15-minute micro-units with dependencies and active-recall questions |
| **Body doubling** | Window focus monitoring with gentle spoken nudge on drift |
| **Coding assistant** | Natural language → code changes via Ollama + Aider fallback |
| **Web tasks** | Search, scrape, complete web tasks in plain English via Scrapling |
| **Voice** | Faster-Whisper STT + Kokoro-82M TTS — calm, non-urgent tone |
| **Obsidian mirror** | Auto-written notes + Dashboard.md with stats |
| **Notifications** | Browser/PWA notifications for reminders, nudges, schedule updates |
| **Cron scheduler** | Persistent recurring tasks (daily digest, memory consolidation, hourly check-ins) |
| **Google Calendar sync** | Two-way sync: reads busy blocks, pushes schedule around them |
| **Android wake word** | "Hey Jarvis" via Home Assistant Companion → Wyoming protocol bridge |
| **Data sovereignty** | Network trace, allowlist enforcement, one-click purge |
| **Native desktop app** | Tauri v2 native macOS app (.app + .dmg) |
| **Cloud escalation** | Free-tier cloud providers for hard tasks (Groq, Cerebras, OpenRouter, Google AI Studio) |
| **Cross-device sync** | Syncthing-based sync between MacBook and phone |
| **Proactive speech** | Morning briefings, missed-block nudges, idle check-ins |
| **Vision** | Screenshot analysis via local VLM (Qwen2.5-VL-7B) with cloud fallback |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CLIENTS                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │  Phone   │  │   Mac    │  │  Native Desktop   │  │
│  │   PWA    │  │ Browser  │  │  (Tauri / pywebview)│ │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       └──────────────┼─────────────────┘              │
│                      │ WebSocket / REST               │
└──────────────────────┼───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│               FastAPI Backend (Python)                │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Agents     │  │    Memory    │  │   Speech   │  │
│  │  braindump  │  │  Mem0+Qdrant │  │  Whisper   │  │
│  │  scheduler  │  │  Obsidian    │  │  Kokoro    │  │
│  │  study      │  │  Calendar    │  │            │  │
│  │  body_dbl   │  │              │  │            │  │
│  │  coding     │  │              │  │            │  │
│  │  web_task   │  │              │  │            │  │
│  │  vision     │  │              │  │            │  │
│  └─────────────┘  └──────────────┘  └────────────┘  │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │  Event Bus   │  │  Scheduler   │  │  Skills   │  │
│  │  publish/    │  │  cron +      │  │  invoke + │  │
│  │  subscribe   │  │  recurring   │  │  stats    │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Sovereignty  │  │ Escalation   │  │   Sync    │  │
│  │ trace +      │  │ cloud routing│  │ export/   │  │
│  │ purge        │  │ + llm_call   │  │ ingest    │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
└──────────────────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌─────────┐   ┌─────────┐
   │ Ollama  │   │ Qdrant  │   │Tailscale│
   │ :11434  │   │ :6333   │   │ (VPN)   │
   └─────────┘   └─────────┘   └─────────┘
```

---

## 3. Prerequisites

| Component | Install | Purpose |
|-----------|---------|---------|
| **uv** | `curl -fsSL https://astral.sh/uv/install.sh \| bash` | Python package manager |
| **Ollama** | `curl -fsSL https://ollama.com/install.sh \| bash` | Local inference |
| **Docker** | [docker.com](https://docker.com) | Qdrant vector database |
| **Syncthing** (optional) | `brew install syncthing` | Cross-device sync |

---

## 4. Setup

### 4.1 — Install and pull models

```bash
# Pull the required Ollama models
ollama pull qwen3.5:9b           # reasoning + braindump
ollama pull qwen2.5-coder:7b     # coding assistant
ollama pull nomic-embed-text      # embeddings

# Start Qdrant
docker compose up -d
```

### 4.2 — Install Python dependencies

```bash
uv sync
```

### 4.3 — Set up authentication (required before remote access)

```bash
# Generate a token and add to your shell profile (~/.zshrc)
export ADHD_COPILOT_TOKEN="$(openssl rand -hex 32)"
```

Save this value in a password manager — your PWA needs it as the `X-API-Token` header.

### 4.4 — Copy and configure environment variables

```bash
cp .env.example .env
# Edit .env to set ADHD_COPILOT_TOKEN, cloud keys, etc.
```

### 4.5 — Verify everything works

```bash
# Smoke test: sends one prompt to Ollama, prints response
uv run python smoke_test.py

# Data sovereignty check: verifies no unexpected outbound connections
uv run python main.py --sovereignty

# Run the test suite (170+ tests)
uv run pytest tests/ -v
```

---

## 5. Configuration

All settings live in `config/config.toml`:

```toml
[engine.ollama]
base_url = "http://localhost:11434"
default_model = "qwen3.5:9b"
embedding_model = "nomic-embed-text"

[engine.ollama.models]
reasoning = "qwen3.5:9b"
coding = "qwen2.5-coder:7b"
embedding = "nomic-embed-text"

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

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ADHD_COPILOT_TOKEN` | For remote access | API auth token. Server refuses `0.0.0.0` binding without it. |
| `JARVIS_REMOTE` | Optional | Set to `true` to bind to `0.0.0.0` instead of localhost. |
| `JARVIS_MODEL` | Optional | Override default Ollama model. |
| `JARVIS_CODING_MODEL` | Optional | Override coding model. |
| `JARVIS_REASONING_MODEL` | Optional | Override reasoning model. |
| `OBSIDIAN_API_TOKEN` | Optional | Auth for Obsidian Local REST API plugin. |
| `GROQ_API_KEY` | Optional | Cloud escalation: Groq (free tier, fast). |
| `CEREBRAS_API_KEY` | Optional | Cloud escalation: Cerebras (free tier). |
| `OPENROUTER_API_KEY` | Optional | Cloud escalation: OpenRouter (free models). |
| `GOOGLE_AI_STUDIO_KEY` | Optional | Cloud escalation: Google AI Studio (Gemini free tier). |

All cloud keys are **optional** — if unset, escalation is skipped and everything stays local.

---

## 6. Running the App

### CLI Mode (interactive)

```bash
uv run python main.py
```

Commands: `dump`, `schedule`, `study`, `memory`, `search`, `sprint`, `code`, `web`, `complete`, `conversations`, `skills`, `dashboard`, `desktop`, `purge`, `quit`

### Web UI (browser + PWA)

```bash
uv run python main.py --ui
# Open http://localhost:8080
```

Full dashboard with brain dump input, schedule view, voice recording, and sovereignty status.

### PWA (phone)

```bash
uv run python main.py --ui
# Open http://YOUR-TAILSCALE-IP:8080 on phone
# Add to home screen → install as app
```

Push-to-talk voice, text commands, real-time responses via WebSocket.

### Desktop App (Tauri)

```bash
uv run python main.py --ui
# In another terminal:
cd ui/desktop-tauri && npx tauri dev

# Or launch the pre-built .app:
open ui/desktop-tauri/src-tauri/target/release/bundle/macos/ADHD\ Co-Processor.app
```

The desktop app loads a connection screen that polls `localhost:8080/api/health`, then redirects to the dashboard once the backend is ready.

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

### Remote Access (phone from another network)

```bash
export ADHD_COPILOT_TOKEN="your-token"
export JARVIS_REMOTE=true
uv run python main.py --ui
# Binds to 0.0.0.0 — accessible from any device on your Tailscale network
```

---

## 7. Agents

| Agent | File | What It Does |
|-------|------|-------------|
| **Braindump** | `agents/braindump_agent.py` | Parses stream-of-consciousness text into structured JSON tasks |
| **Scheduler** | `agents/scheduler_agent.py` | Builds time-blocked schedules with α-scaling, transition buffers, silent rebalance |
| **Study** | `agents/study_agent.py` | Decomposes topics into sub-15-minute micro-units with active-recall questions |
| **Body Double** | `agents/body_double_agent.py` | Monitors window focus, triggers gentle spoken nudges on drift |
| **Coding** | `agents/coding_agent.py` | Fix bugs, add features, explain code, refactor — Ollama + Aider + cloud escalation |
| **Web Task** | `agents/web_task_agent.py` | Search, scrape, or complete web tasks via Scrapling + LLM planning |
| **Calendar Sync** | `agents/calendar_sync.py` | Two-way Google Calendar sync: reads busy blocks, pushes schedule |
| **Monitor** | `agents/monitor_operative.py` | Persistent stateful monitoring with change detection |
| **Vision** | `agents/vision_agent.py` | Screenshot analysis via local VLM (Qwen2.5-VL-7B) with cloud fallback |

All agents now use the unified `llm_call()` helper from `core/escalation.py`, which handles cloud escalation automatically.

---

## 8. Phase B — Cloud Escalation

When local models struggle (large context, repeated failures, complex reasoning), tasks escalate to free-tier cloud providers. All keys are optional — no credit card required.

### How It Works

The escalation decision is deterministic and runs **before** any cloud call:

1. **Sensitive data detected** → stays local (hard block, no exceptions)
2. **Context > 16K tokens** → Google AI Studio (1M token context, free)
3. **Local model failed 2+ times** → Groq (fast repair pass)
4. **Architecture/complex reasoning tasks** → OpenRouter free tier (120B+ reasoning)
5. **Everything else** → stays local

### Privacy Guardrail

`core/escalation.py` checks for sensitive patterns (passwords, API keys, SSH keys, SSNs, credit cards, private keys) before every cloud call. If detected, the task stays local **regardless of other conditions**.

### Multi-Provider Failover

`core/cloud_router.py` tries providers in order, falling through on rate limits or errors:

```
Groq → Cerebras → OpenRouter → Google AI Studio
```

If all providers fail, falls back to local Ollama.

### Setup

```bash
# Get free API keys (no credit card required):
# Groq:           https://console.groq.com
# Cerebras:       https://cloud.cerebras.ai
# OpenRouter:     https://openrouter.ai
# Google AI Studio: https://aistudio.google.com

# Add to .env or shell profile:
export GROQ_API_KEY="your-key"
export CEREBRAS_API_KEY="your-key"
export OPENROUTER_API_KEY="your-key"
export GOOGLE_AI_STUDIO_KEY="your-key"
```

### Files

| File | Purpose |
|------|---------|
| `core/escalation.py` | Escalation rules, sensitive data detection, unified `llm_call()` helper |
| `core/cloud_router.py` | Multi-provider failover (Groq, Cerebras, OpenRouter, Google AI Studio) |

---

## 9. Phase C — Cross-Device Sync

One consistent state across MacBook + phone, without either device being a fixed always-on server.

### How It Works

Syncthing handles eventual consistency (works even if a device was offline for days). Tailscale carries the live WebSocket path for real-time interaction.

```
┌──────────────┐     Syncthing      ┌──────────────┐
│   MacBook    │ ◄──────────────────► │    Phone     │
│              │                      │              │
│ vault/       │  ← direct sync →    │ vault/       │
│ sync/export/ │  ← delta sync →     │ sync/export/ │
└──────────────┘                      └──────────────┘

On startup:  ingest_pending_deltas() — reconcile incoming changes
On shutdown: export_state_delta()    — snapshot modified memories
```

### Two Synced Folders

1. **`vault/`** — Obsidian notes (human-readable, direct sync)
2. **`sync/export/`** — append-only JSONL state dumps (machine state)

### Setup

```bash
# Install Syncthing
brew install syncthing   # macOS
# Android: Syncthing from F-Droid or Play Store

# Configure in Syncthing:
# Folder 1: vault/        (Obsidian — direct sync)
# Folder 2: sync/export/  (machine state — delta sync)
```

### API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sync/status` | GET | Pending deltas, last export, folder state |
| `/api/sync/export` | POST | Manually trigger state export |
| `/api/sync/ingest` | POST | Manually trigger delta ingestion |

### Critical Rule

**Never sync live Qdrant/SQLite files directly.** Always: export → sync the export → ingest. The `sync/export/` folder is the only way machine state leaves the machine.

### Files

| File | Purpose |
|------|---------|
| `core/sync.py` | Export/ingest logic, JSONL delta files, conflict resolution (last-write-wins) |

---

## 10. Phase D — Proactive Triggers

Speech that initiates, not just responds. A scheduled/event-driven layer that generates proactive speech.

### Triggers

| Trigger | When | Example |
|---------|------|---------|
| **Morning briefing** | 08:00 daily (cron) | "Good morning. 3 things on the agenda today, starting with 'Finish report' at 09:00." |
| **Missed block** | Event-driven (schedule_updated) | "I've noticed 'Finish report' slipped. Still room to try the first 15 minutes..." |
| **Idle check-in** | Event-driven (30+ min idle) | "You've been quiet for about 45 minutes. No rush — just checking in." |
| **Session end** | Event-driven (3+ tasks completed) | "Strong session — 3 tasks done in about 45 minutes. You're building momentum." |
| **Rebalance** | Event-driven (schedule_updated) | "I've adjusted the schedule. Take a breath — we've got this." |

### Design Principles

- **Supportive phrasing only** — no imperative commands, no red badges, no overdue counters
- **Publishes to event bus** for delivery (WebSocket, TTS, notifications)
- **Registers with cron scheduler** for time-based triggers
- **Subscribes to scheduler events** for reactive triggers

### API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/proactive/status` | GET | Registered triggers, cron tasks, status |
| `/api/proactive/morning-briefing` | POST | Manually trigger morning briefing |
| `/api/proactive/idle-check?minutes_idle=N` | POST | Manually trigger idle check-in |

### Files

| File | Purpose |
|------|---------|
| `core/proactive.py` | Trigger functions, event bus wiring, cron registration |

---

## 11. Phase E — Vision Agent

Screenshot and image analysis using a local Vision Language Model with cloud fallback.

### How It Works

1. Captures screenshot via `mss`
2. Resizes to max 720px (VLM memory budget)
3. Analyzes with local VLM (Qwen2.5-VL-7B via mlx-vlm)
4. Falls back to cloud if local model unavailable

### Memory Discipline

The VLM uses ~4.8GB VRAM. It loads on demand and releases after each analysis to avoid conflicts with reasoning/coding models.

### API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/vision/status` | GET | Dependencies, model availability, fallback mode |
| `/api/vision/analyze-screen?prompt=...` | POST | Capture and analyze a screenshot |
| `/api/vision/analyze-image` | POST | Analyze an image from URL or path |
| `/api/vision/analyze-upload` | POST | Analyze an uploaded image file |

### Privacy

The cloud fallback sends **only image metadata** (dimensions, mode), not raw pixel data. Actual image analysis requires the local VLM.

### Setup

```bash
# Install vision dependencies
uv sync  # includes mss, pillow, mlx-vlm
```

### Files

| File | Purpose |
|------|---------|
| `agents/vision_agent.py` | Screenshot capture, VLM analysis, cloud fallback |

---

## 12. API Reference

All endpoints require `X-API-Token` header unless marked as public.

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | System health + component status |

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
| POST | `/api/notifications/{id}/read` | Mark as read |
| POST | `/api/notifications/read-all` | Mark all as read |
| DELETE | `/api/notifications` | Clear all |
| GET | `/api/notifications/preferences` | Get preferences |
| POST | `/api/notifications/preferences` | Update preferences |

### Sync (Phase C)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sync/status` | Sync layer status |
| POST | `/api/sync/export` | Manually trigger state export |
| POST | `/api/sync/ingest` | Manually trigger delta ingestion |

### Proactive (Phase D)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/proactive/status` | Trigger status, registered cron tasks |
| POST | `/api/proactive/morning-briefing` | Manually trigger morning briefing |
| POST | `/api/proactive/idle-check?minutes_idle=N` | Manually trigger idle check-in |

### Vision (Phase E)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/vision/status` | Model availability, dependencies |
| POST | `/api/vision/analyze-screen?prompt=...` | Capture and analyze screenshot |
| POST | `/api/vision/analyze-image` | Analyze image from URL or path |
| POST | `/api/vision/analyze-upload` | Analyze uploaded image file |

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

## 13. Data Sovereignty

The app makes **zero outbound calls** except:

- **Tailscale** (100.x.x.x) — private VPN tunnel
- **Google OAuth** (142.250.x.x:443) — Calendar sync only
- **Cloud providers** (only when escalation is triggered and API keys are set)
- **Localhost** — Ollama, Qdrant

### Verify yourself

```bash
# Quick check
uv run python main.py --sovereignty

# Full 30-second network trace
uv run python -m core.sovereignty --report

# One-click purge all memory
uv run python -m core.sovereignty --purge
```

### API

```
GET /api/sovereignty/status → {"verdict": "clean", "violations": 0, ...}
```

### What Gets Purged

- All Qdrant memories
- All conversation history
- All Obsidian vault notes
- All sync export files
- All task history and logs

---

## 14. Troubleshooting

### Ollama not responding

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Restart Ollama
ollama serve
```

### Qdrant not responding

```bash
# Check if Qdrant is running
curl http://localhost:6333/collections

# Restart Qdrant
docker compose up -d
```

### Model not found

```bash
# List available models
ollama list

# Pull missing model
ollama pull qwen3.5:9b
```

### Server won't bind to 0.0.0.0

`ADHD_COPILOT_TOKEN` must be set before the server will bind remotely:

```bash
export ADHD_COPILOT_TOKEN="your-token"
export JARVIS_REMOTE=true
uv run python main.py --ui
```

### Vision agent not working

```bash
# Check dependencies
uv run python -c "import mss, PIL, mlx_vlm; print('All vision deps available')"

# If mlx-vlm not installed (Apple Silicon only)
uv sync  # includes mlx-vlm
```

### Cloud escalation not triggering

```bash
# Check which providers have keys configured
uv run python -c "from core.cloud_router import get_available_providers; print(get_available_providers())"
```

### Tests failing

```bash
# Run full test suite
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_escalation.py -v
```

---

## 15. Phase Status

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
| 12 | Phone-side wake word | ✅ Android / ⚠️ iOS |
| 13 | UI shell & integration | ✅ Tauri + pywebview |
| A | Model layer verification | ✅ |
| B | Cloud escalation router | ✅ |
| C | Cross-device sync | ✅ |
| D | Proactive triggers | ✅ |
| E | Vision agent | ✅ |
| 14 | Data sovereignty pass | ✅ |

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
│   ├── monitor_operative.py      # Persistent stateful monitoring
│   └── vision_agent.py           # Screenshot analysis via local VLM
├── memory/
│   └── adhd_memory.py            # Mem0 + Qdrant + Obsidian + Conversations + Tasks
├── speech/
│   └── speech_pipeline.py        # Faster-Whisper STT + Kokoro TTS
├── core/
│   ├── event_bus.py              # Publish/subscribe event system
│   ├── skill_manager.py          # Composable skill registry
│   ├── eval_metrics.py           # Latency + energy tracking
│   ├── cron_scheduler.py         # Persistent recurring tasks
│   ├── memory_service.py         # Background fact extraction
│   ├── sovereignty.py            # Network trace + allowlist + purge
│   ├── notifications.py          # Browser + PWA notification manager
│   ├── escalation.py             # Cloud escalation routing + llm_call helper
│   ├── cloud_router.py           # Multi-provider failover
│   ├── sync.py                   # Cross-device sync (export/ingest deltas)
│   ├── proactive.py              # Proactive speech triggers
│   ├── auth.py                   # API token authentication
│   └── config.py                 # Config loading + env var overrides
├── remote/
│   ├── pwa_server.py             # WebSocket + REST for PWA
│   ├── wyoming_bridge.py         # Android wake-word bridge
│   └── desktop_shell.py          # pywebview native window
├── ui/
│   ├── pwa/                      # Mobile PWA (manifest, app.js, icons)
│   ├── desktop/                  # Desktop shell UI (9 views)
│   └── desktop-tauri/            # Tauri native app (builds to .app/.dmg)
├── vault/                        # Obsidian vault (auto-written)
├── sync/
│   └── export/                   # Delta files for cross-device sync
├── tests/                        # 170+ tests across all phases
│   ├── test_agent_escalation.py  # Agent integration with escalation
│   ├── test_cloud_router.py      # Multi-provider failover
│   ├── test_escalation.py        # Escalation rules + sensitive data
│   ├── test_proactive.py         # Proactive speech triggers
│   ├── test_sync.py              # Cross-device sync layer
│   └── test_vision.py            # Vision agent + VLM fallback
├── config/
│   ├── config.toml               # All settings
│   └── google_client_secret.json # OAuth credentials (gitignored)
├── scripts/
│   └── calendar_setup.py         # OAuth setup wizard
├── data/                         # Runtime data (task_history, logs)
├── docker-compose.yml            # Qdrant
├── pyproject.toml                # Python dependencies (uv)
├── main.py                       # Entry point (CLI, API, voice, desktop)
└── smoke_test.py                 # Quick Ollama round-trip verification
```

---

*Everything in this project stays within the original constraint: $0 spend, free/open-source or genuinely free-tier-no-card cloud, entirely reversible if a piece doesn't work out for you.*
