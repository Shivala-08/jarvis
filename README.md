# ADHD Cognitive Co-Processor — Build Blueprint for Google Antigravity
### Zero-cost, fully open-source, local-first implementation

---

## 0. What this document is

The research report describes *why* and *what* — the neuroscience and the target architecture (behavioral ingestion, agentic orchestration, persistent memory, voice pipeline). This document is the *how*: a phased build plan specifically shaped for **Google Antigravity** as your dev environment, using only **₹0 / $0, free, open-source** tools for both the build process and the runtime.

**Two different roles for Antigravity, don't conflate them:**
1. **Antigravity-as-builder** — you sit in the Antigravity IDE/Agent Manager and dispatch agents (Gemini 3 / Claude / GPT-OSS, all free in public preview) to write, test, and verify the code for you, producing Artifacts (plans, diffs, browser recordings) you review.
2. **The thing being built** — a separate, local-first ADHD assistant that runs on your machine using Ollama, Mem0, Faster-Whisper, and Kokoro. Antigravity does **not** need to run at runtime; it's your construction site, not part of the finished house.

---

## 1. Cost & tool substitution table

Everything in the original report is already open-source except the Google Calendar piece, which stays free (personal Google account, free API quota — no billing tier needed for this scale of use).

| Layer | Report's choice | Cost | Kept as-is? |
|---|---|---|---|
| Build environment | — | — | **Google Antigravity** (free public preview) |
| Agent orchestration | OpenJarvis | Free/OSS | ✅ Keep |
| Local inference | Ollama (qwen2.5-coder, llama3.1) | Free/OSS | ✅ Keep |
| Memory | Mem0 + Qdrant | Free/OSS (self-hosted) | ✅ Keep |
| STT | Faster-Whisper | Free/OSS | ✅ Keep |
| TTS | Kokoro-82M | Free/OSS (Apache 2.0) | ✅ Keep |
| Calendar | Google Calendar OAuth | Free tier, no card | ✅ Keep |
| UI shell | — | Free | **Tauri** (Rust+web, free, lighter than Electron) or plain local web app |
| Vector DB hosting | Qdrant Cloud | — | Swap to **Qdrant local Docker** (self-hosted, $0) |
| Visible memory log | *(not in original report)* | Free/OSS | **Obsidian** (local Markdown vault + Dataview + Local REST API plugin) — added as Phase 3.5, agent-written only, never manually filed |
| Human-readable memory view | — | Free/OSS | **Obsidian** + Local REST API + Dataview plugins |

No paid API keys anywhere in this plan. If a step ever asks for an OpenAI/Anthropic cloud key, skip it — Ollama replaces it.

---

## 2. Repo structure (what you'll ask Antigravity's agent to scaffold first)

```
adhd-copilot/
├── agents/              # OpenJarvis agent definitions
│   ├── scheduler_agent.py
│   ├── study_agent.py
│   ├── braindump_agent.py
│   └── body_double_agent.py
├── memory/
│   └── adhd_memory.py   # Mem0 wrapper
├── speech/
│   └── speech_pipeline.py
├── ui/                  # Tauri or local web dashboard
├── config/
│   └── config.toml
├── vault/               # Obsidian vault — agent-written, human-browsable
│   ├── braindumps/
│   ├── study-plans/
│   └── daily-digests/
├── docker-compose.yml   # Qdrant, local only
└── README.md
```

---

## 3. Phase-by-phase plan

### Phase 0 — Environment setup (Day 1)
**Goal:** Antigravity installed, hardware verified, base repo created.

- Install Google Antigravity (free, Mac/Windows/Linux) from `antigravity.google/download`.
- Open a new empty folder as an Antigravity workspace.
- In the Agent Manager, dispatch a task:
  > "Scaffold a Python 3.11 project named `adhd-copilot` with the folder structure below, a `pyproject.toml` using `uv`, and a `.gitignore` for Python/Node. Verify by running `uv sync` and reporting the output as an Artifact."
- Install system prerequisites locally (Antigravity's terminal agent can do this for you, or run manually):
  ```bash
  curl -fsSL https://astral.sh/uv/install.sh | bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
- Ask Antigravity to run `jarvis doctor`-equivalent diagnostics (VRAM, CUDA/Metal, disk) and produce a readiness Artifact before you proceed.

**Exit check:** `uv run python --version` works, Ollama daemon is running.

---

### Phase 1 — Local inference core (Days 2–3)
**Goal:** A model is answering locally, no cloud calls.

- Pull models:
  ```bash
  ollama pull qwen2.5-coder:14b
  ollama pull llama3.1:8b
  ollama pull nomic-embed-text
  ```
  (If your machine is modest, substitute `qwen2.5-coder:7b` / `llama3.1:8b-instruct-q4_0` — Antigravity's agent can benchmark and pick the right quantization for your VRAM.)
- Task an Antigravity agent:
  > "Write `config/config.toml` wiring `[engine.ollama]` to `http://localhost:11434` with `default_model = qwen2.5-coder:14b`. Write a smoke-test script that sends one prompt and prints the response. Run it and attach the output as an Artifact."

**Exit check:** Local round-trip response, zero network calls to any paid API (verify with a network monitor if paranoid).

---

### Phase 2 — Agent orchestration (Days 4–6)
**Goal:** OpenJarvis running with a working orchestrator agent.

```bash
git clone https://github.com/open-jarvis/OpenJarvis.git
cd OpenJarvis && uv sync --extra dev
jarvis init --preset morning-digest-minimal
```

- Have Antigravity's agent read the OpenJarvis docs (it can browse), then implement `agents/braindump_agent.py` as a first registered agent — the simplest possible one, just echoing structured JSON from raw text. This validates the plumbing before you add memory or voice.
- Ask for a **verification Artifact**: agent registry list, one successful `jarvis run` execution log.

**Exit check:** You can run one custom agent end-to-end from the CLI.

---

### Phase 3 — Persistent memory (Days 7–9)
**Goal:** Mem0 + local Qdrant, no cloud vector DB.

`docker-compose.yml`:
```yaml
services:
  qdrant:
    image: qdrant/qdrant
    ports: ["6333:6333"]
    volumes: ["./qdrant_data:/qdrant/storage"]
```

```bash
docker compose up -d
uv pip install mem0ai qdrant-client
```

- Task Antigravity to implement `memory/adhd_memory.py` (the `ADHDMemoryEngine` class from the report — `capture_brain_dump`, `retrieve_context_for_task`), pointed at local Qdrant + Ollama embeddings (`nomic-embed-text`).
- Ask for a test: store 5 fake memories, retrieve by semantic query, confirm relevant one surfaces. This should come back as a browser/terminal Artifact you can inspect without re-running it yourself.

**Exit check:** Memory persists across a process restart (kill script, rerun, data still there).

---

### Phase 3.5 — Obsidian as the visible memory mirror (Days 9–10, optional but recommended)
**Goal:** A human-browsable window into what Mem0 knows, with zero manual filing.

Obsidian is local-first (plain Markdown files on disk) and free, so it fits the sovereignty principle — unlike Notion, which is cloud-hosted and whose manual-database UX actively works against the zero-setup goal this whole build is designed around. Obsidian's role here is strictly **read/log surface, not a second source of truth**: agents write to it, you never file anything into it yourself.

- Install Obsidian (free) and create a vault, e.g. `adhd-copilot/vault/`.
- Install two community plugins inside Obsidian:
  - **Local REST API** — exposes `https://localhost:27124` so your agents can create/update notes programmatically.
  - **Dataview** — lets the vault behave like a queryable database over plain notes (filter by tag, date, status) without you ever defining a schema by hand.
- Task Antigravity's agent:
  > "Extend `memory/adhd_memory.py` so every `capture_brain_dump()` call also POSTs a formatted note to the Obsidian Local REST API — filename `YYYY-MM-DD-HHmm.md`, frontmatter with `tags`, `source_agent`, `status`, body = the captured text. Write a Dataview query file `vault/Dashboard.md` that lists today's captures grouped by tag. Verify by capturing 3 test items and confirming they render correctly in the vault."
- Keep this one-directional: Mem0/Qdrant stays the actual semantic memory the agents *query against*; Obsidian is only ever *written to*, so there's never a sync-conflict or "which one is the real record" problem.

**Exit check:** A voice brain-dump produces a note in the vault within a couple seconds, with no manual tagging, filing, or database setup on your end — and Dataview's dashboard note updates itself.

---

### Phase 4 — Voice pipeline (Days 10–12)
**Goal:** Speak in, hear back, no typing required.

```bash
uv pip install faster-whisper "kokoro>=0.9.4" soundfile sounddevice
```

- Task Antigravity to implement `speech/speech_pipeline.py` per the report (Faster-Whisper `small.en` model, Kokoro `af_heart` voice, 1.0–1.1× speed for a calm, non-urgent tone — this pacing detail matters for the "co-regulation, not command" feel).
- CPU-only machines: use `compute_type="int8"` instead of `float16` — still free, just slower. Antigravity's agent can auto-detect and pick this.

**Exit check:** Say a sentence → see accurate transcript → hear a spoken reply within a couple seconds.

---

### Phase 5 — Adaptive scheduler (Days 13–16)
**Goal:** Calendar-aware, zero-guilt rescheduling.

- Enable Google Calendar API (free tier) in Google Cloud Console — no billing account required for personal-use quota.
- Task Antigravity to build `agents/scheduler_agent.py`:
  - OAuth flow (local, token cached on disk — never leaves your machine)
  - The time-scaling multiplier (α ≈ 1.4–1.8) pulled from Mem0's stored history of actual-vs-estimated durations
  - 15-minute transition buffers auto-inserted between blocks
  - The "silent re-balance" behavior: on a missed block, recompute remaining budget and produce a spoken micro-sprint suggestion — **never** a red flag or overdue count.
- Verification Artifact: a scripted scenario (simulate a missed 90-min block) showing the before/after schedule and the generated calm prompt text.

**Exit check:** Missing a block updates the plan without any visual "failure" state anywhere in the UI.

---

### Phase 6 — Study/task decomposition agent (Days 17–20)
**Goal:** Any syllabus/topic → sub-15-minute steps automatically.

- Implement `agents/study_agent.py` as in the report — the JSON-schema-constrained prompt that outputs micro-units with dependencies and active-recall questions.
- This is a good phase to let Antigravity's **multi-agent parallel mode** shine: one agent writes the schema/prompt, a second agent writes a validator that rejects any unit >15 minutes, a third writes a test harness with 2–3 sample topics.

**Exit check:** Feed it "graph algorithms" or your actual syllabus; get back an ordered, small-step JSON plan, not a wall of text.

---

### Phase 7 — Body doubling + ambient focus (Days 21–24)
**Goal:** Passive, non-intrusive nudges — no alert badges anywhere.

- Local process/window-focus monitoring (Python: `psutil` + OS-specific window APIs — all free/OSS, no telemetry SDKs).
- Soft-nudge logic: on sustained tab drift, trigger a single spoken Kokoro prompt, not a popup.
- Explicitly design against the anti-patterns table in the report: no red badges, no overdue counters, no modal dialogs. Have Antigravity's agent literally lint the UI code for these patterns as a verification step.

**Exit check:** Deliberately drift for 10+ minutes — the system should nudge once, gently, and stay out of the way otherwise.

---

### Phase 8 — UI shell & integration (Days 25–28)
**Goal:** One place that ties braindump → memory → schedule → study → voice together.

- **Tauri** (free, OSS, far lighter than Electron) for a minimal desktop shell, or skip the shell entirely and drive everything by voice + a local web dashboard (`localhost:PORT`, plain HTML/JS, zero framework cost).
- Task Antigravity's browser-in-the-loop agent to click through the UI itself and confirm: capture → routing → calendar update → spoken confirmation, end to end, and attach a recording Artifact.

**Exit check:** One voice brain-dump produces a routed task, a calendar adjustment, and a spoken confirmation — with zero manual data entry.

---

### Phase 9 — Data sovereignty pass ✅ COMPLETED
**Goal:** Confirm the privacy promise is real, not aspirational.

- **Network trace**: `GET /api/network-check` uses `lsof` to detect outbound connections, distinguishing **app-level** from **system-level**. Verified: **app makes ZERO outbound calls** — only localhost connections to Ollama (`:11434`) and Qdrant (`:6333`).
- **One-click purge**: `POST /api/purge` deletes the Qdrant collection. `GET /api/purge-status` verifies it's actually cleared. Tested and confirmed.
- **Offline verification**: All features (braindump, memory, study, scheduling, voice) work without internet — inference is via local Ollama, storage is via local Qdrant.

**API endpoints:**
- `GET /api/network-check` — real-time outbound connection audit
- `POST /api/purge` — delete all memories
- `GET /api/purge-status` — verify purge worked

**Exit check passed:** Zero app-level outbound connections, purge verified, all features local-first.

---

## 4. Ongoing use of Antigravity after v1 ships

Antigravity isn't a one-time scaffolding tool — keep using its Agent Manager for:
- Regression checks whenever you tweak an agent prompt (dispatch a verification agent instead of manually retesting)
- Refactors ("split scheduler_agent.py's rebalancing logic into its own module, verify nothing breaks")
- The "learning" primitive OpenJarvis offers — Antigravity's own knowledge-base memory of your codebase compounds the same way, so later builds get faster.

---

## 5. Guardrails carried over from the report (don't skip these when prompting agents)

When you write task prompts for Antigravity's agents, explicitly bake these in — they're easy to lose in translation:
- "No red/overdue visual states, ever."
- "Reschedule silently; never surface a failure count."
- "Voice pacing 1.0–1.1×, supportive phrasing, not imperative commands."
- "All inference/storage must stay local — flag anything that would require a paid API key."

---

*Total new spend across all 9 phases: $0. Everything above is either already installed free software, a free-tier API with no card on file, or code Antigravity's agents write for you inside the free public preview.*

---

## 6. Implementation Status

The following gaps from the original design have been completed and verified:

### A. API Server & Dynamic UI Connection ✅
* **State**: FastAPI backend is fully integrated into [`main.py`](file:///Users/pallav/Downloads/jarvis-ai-assistant/main.py) at module level, serving both the API and the web dashboard.
* **Endpoints**: `/api/braindump`, `/api/schedule`, `/api/rebalance`, `/api/study`, `/api/memories`, `/api/memories/search`, `/api/purge`, `/api/purge-status`, `/api/sprint`, `/api/network-check`, `/api/health`, `/ws/voice` (WebSocket)
* **UI**: [`ui/index.html`](file:///Users/pallav/Downloads/jarvis-ai-assistant/ui/index.html) sends real `fetch()` requests to the FastAPI backend.
* **Start**: `uv run python main.py --ui` or `uvicorn main:app --host localhost --port 8080`

### B. Daemonized Focus Monitor (Body Double) ✅
* **State**: [`agents/body_double_agent.py`](file:///Users/pallav/Downloads/jarvis-ai-assistant/agents/body_double_agent.py) is spawned as a daemon thread in all modes (CLI, voice, and UI server).
* **Integration**: The `start_body_double_daemon()` function in `main.py` runs automatically in UI mode and can be called in other modes.

### C. Google Calendar Setup & Configuration Files
* **State**: Google Calendar OAuth is integrated in code but inactive by default.
* **Missing**: You must manually register a project in the Google Cloud Console, enable the Google Calendar API, download your Desktop credentials JSON to `config/google_client_secret.json`, and run it once to cache the local credentials.

#### Step-by-step Google Calendar OAuth Setup

This is the only step that requires a Google account. No billing, no credit card, no paid tier — the personal-use quota is generous enough for this project.

**1. Create a Google Cloud Project**

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown at the top → **New Project**.
3. Name it something like `adhd-copilot` and click **Create**.
4. Make sure the new project is selected in the top dropdown.

**2. Enable the Google Calendar API**

1. In the left sidebar, go to **APIs & Services → Library**.
2. Search for **Google Calendar API**.
3. Click on it → click **Enable**.

**3. Create OAuth 2.0 Credentials**

1. Go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials** → **OAuth client ID**.
3. If prompted, configure the **OAuth consent screen** first:
   - Choose **External** (or Internal if you have a Workspace account).
   - Fill in the app name (e.g., `ADHD Co-Processor`), your email, and a developer contact email.
   - Add the scope `https://www.googleapis.com/auth/calendar`.
   - Add your own Google email as a **test user** (required while the app is in "Testing" status).
   - You do **not** need to submit for verification — test mode works fine for personal use.
4. Back in **Credentials**, click **+ Create Credentials** → **OAuth client ID**.
5. Choose **Desktop app** as the application type.
6. Name it (e.g., `adhd-copilot-desktop`) and click **Create**.
7. Click **Download JSON** to get the credentials file.

**4. Place the Credentials File**

```bash
# Rename and move the downloaded file into the project config directory
mv ~/Downloads/client_secret_*.json config/google_client_secret.json
```

Your project structure should now include:
```
config/
├── config.toml
└── google_client_secret.json   # ← OAuth credentials (DO NOT commit this file)
```

**5. First Run — Cache the Token**

The first time you use any calendar feature, the scheduler agent will:
1. Open your default browser to Google's OAuth consent page.
2. Ask you to sign in and grant calendar access.
3. Redirect to a local callback URL.
4. Save the token to `config/google_token.json` (cached locally, never leaves your machine).

```bash
# Trigger the OAuth flow by running any calendar command
uv run python main.py --test
```

Or start the UI server and click **🔄 Rebalance** — it will trigger the OAuth flow if not yet authorized.

**6. Verify It Works**

```bash
# Quick check — list today's calendar events
uv run python -c "
from agents.scheduler_agent import get_events
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
start = now.replace(hour=0, minute=0, second=0, microsecond=0)
end = start + timedelta(days=1)
events = get_events(start, end)
for e in events:
    print(f\"  {e['summary']} — {e['start'].get('dateTime', e['start'].get('date'))}\")
"
```

**Security Notes:**
- `config/google_client_secret.json` — contains your OAuth client secret. **Never commit this to git.**
- `config/google_token.json` — contains your access/refresh tokens. **Never commit this to git.**
- Both files are already in `.gitignore`. Verify before pushing.
- The token stays on your machine only. No data is sent to any third-party server.
- You can revoke access at any time at [Google Account Security](https://myaccount.google.com/permissions).

**Troubleshooting:**
- *"OAuth client secret not found"* — You skipped step 4. Make sure the file is at `config/google_client_secret.json`.
- *"Token has been revoked or expired"* — Delete `config/google_token.json` and re-run the OAuth flow.
- *"Access Not Configured"* — The Calendar API isn't enabled. Go back to step 2.
- *Calendar events not showing* — Make sure you added your email as a test user in step 3.3.

### D. Obsidian Integration (Phase 3.5) ❌ (Pending)
* **State**: Not yet implemented.
* **Todo**: Extend `memory/adhd_memory.py` to POST captured brain dumps to Obsidian's Local REST API as formatted notes, and create the Dataview query dashboard.
