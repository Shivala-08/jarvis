# Audit Report & Fix Manual — Shivala-08/jarvis

I cloned and read the actual code (not just the README) to do this — 8 agents, the core layer, memory, speech, remote, and the config. Overall: this is a genuinely impressive, near-complete implementation of the blueprint — every phase really is built, not just claimed. But I found **one serious security bug** and **one bug that breaks the phone-access feature entirely**, plus several smaller things worth fixing. Ordered by severity.

---

## 🔴 Critical — fix before you use this on anything but localhost

### 1. Path traversal / arbitrary file write in the coding agent

**File:** `agents/coding_agent.py`, `apply_changes()`, line ~329

```python
full_path = PROJECT_ROOT / file_path
```

**The problem:** if `file_path` is an absolute path (e.g. `/Users/you/.ssh/authorized_keys`) or contains `../../`, Python's `pathlib` `/` operator doesn't safely join it — an absolute right-hand side **replaces the entire path**. So `PROJECT_ROOT / "/etc/passwd"` resolves to `/etc/passwd`, not `PROJECT_ROOT/etc/passwd`. Combined with `full_path.write_text(...)` a few lines later, this is a write-anywhere-on-disk primitive.

**Why it matters for you specifically:** the LLM is the thing deciding what `file_path` is, based on natural-language instructions you give it. A local model occasionally hallucinating a bad path is a realistic failure mode, not a hypothetical attacker — and this endpoint is reachable over the network via `/api/code/apply` with **no authentication** (see #2 and #3 below).

**Fix:**
```python
def _safe_resolve(file_path: str) -> Path:
    full_path = (PROJECT_ROOT / file_path).resolve()
    if not full_path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(f"Refusing to write outside project root: {file_path}")
    return full_path
```
Use `_safe_resolve()` everywhere `PROJECT_ROOT / file_path` currently appears in `coding_agent.py` (both `_read_file` and `apply_changes`).

---

### 2. Zero authentication on every API endpoint

**File:** `main.py` — 55+ endpoints, none behind auth.

Endpoints like `/api/purge`, `/api/sovereignty/purge`, `/api/code/apply`, and `/api/scheduler/tasks` (create/pause/resume) are all callable by anyone who can reach the port — no token, no password. Right now this is low-risk because of bug #3 (server only listens on localhost), but the README explicitly documents opening this to your phone over Tailscale — the moment that's fixed, every device on your tailnet (or anyone who compromises one) can silently wipe your memory or write files through the coding endpoint.

**Fix:** add a shared-secret header check. Simple and enough for a personal project:
```python
# core/auth.py
import os
from fastapi import Header, HTTPException

API_TOKEN = os.environ.get("ADHD_COPILOT_TOKEN", "")

def require_token(x_api_token: str = Header(default="")):
    if not API_TOKEN or x_api_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
```
Then add `dependencies=[Depends(require_token)]` to the `@app.post`/`@app.delete` routes in `main.py` — at minimum on `/api/purge`, `/api/sovereignty/purge`, `/api/code/apply`, `/api/sovereignty/*`. Set `ADHD_COPILOT_TOKEN` as an env var on the host, and have your PWA send it as a header on every request.

Also tighten CORS — `allow_origins=["*"]` (main.py line ~118) should be your specific PWA origin, not a wildcard.

---

### 3. Server only binds to `localhost` — phone access as documented doesn't actually work

**File:** `main.py`, both `run_ui_server()` and `run_voice()`:
```python
uvicorn.run(app, host="localhost", port=PORT, ...)
```

`localhost` = `127.0.0.1` = **only reachable from the same machine.** The README tells you to open `http://YOUR-TAILSCALE-IP:8080` from your phone — that will simply time out, because the server never listens on the Tailscale interface at all. This isn't a minor bug, it's the entire Phase 11 feature silently not working.

**Fix:** bind to `0.0.0.0` (all interfaces) — Tailscale's own network isolation is what keeps this from being publicly exposed, not the bind address:
```python
uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
```
Do this in both places in `main.py`. **Do this together with fix #2** — binding wider without adding auth first is the wrong order of operations.

---

## 🟠 Should fix soon

### 4. Repo is 751MB because Rust build artifacts got committed

```
git ls-files | grep -E "node_modules|target/" | wc -l
→ 2615 files
```

Your `.gitignore` has:
```
src-tauri/target/
```
but the actual path is `ui/desktop-tauri/src-tauri/target/`. Gitignore patterns containing a `/` (other than a trailing one) are anchored to the directory the `.gitignore` file lives in — since yours is at repo root, `src-tauri/target/` only matches `./src-tauri/target/`, which doesn't exist. The real nested path was never ignored, so the entire Rust build cache (`.fingerprint`, compiled deps, the binary itself) got committed.

**Fix:**
```bash
# in .gitignore, replace the line with:
**/src-tauri/target/

# then actually remove it from git history/tracking:
git rm -r --cached ui/desktop-tauri/src-tauri/target
git commit -m "Stop tracking Tauri build artifacts"
```
(Full history cleanup with `git filter-repo` is optional — worth doing before you ever make this repo public, since 751MB of someone else's Rust toolchain output is a bad first impression and a slow clone for anyone else.)

### 5. Still pinned to Llama 3.1, not updated for your actual hardware

**File:** `config/config.toml`
```toml
default_model = "llama3.1:latest"
fallback_model = "llama3.1:8b"
[engine.ollama.models]
reasoning = "llama3.1:latest"
coding = "llama3.1:latest"
```
We covered this in conversation — on your 16GB MacBook Pro M4 Pro, Qwen3.5 9B is a better fit: comparable size, meaningfully better tool-calling reliability, which matters a lot given how much of this system (scheduler, coding agent, web-task agent) depends on the model reliably emitting structured function calls.

**Fix:**
```bash
ollama pull qwen3.5:9b
```
```toml
default_model = "qwen3.5:9b"
fallback_model = "qwen3.5:9b"
[engine.ollama.models]
reasoning = "qwen3.5:9b"
coding = "qwen3.5:9b"
```

### 6. No LICENSE file, despite the README claiming "free and open-source"

A README claim isn't a license grant. Without a `LICENSE` file, the legal default is **all rights reserved** — nobody (including future-you on a different machine, technically) has clear permission to use, modify, or redistribute the code, whatever the README says.

**Fix:** add an actual license file. MIT is the simplest fit for a personal tool like this:
```bash
curl -o LICENSE https://raw.githubusercontent.com/github/choosealicense.com/gh-pages/_licenses/mit.txt
```
Then fill in your name/year at the top.

---

## 🟡 Worth doing, lower urgency

### 7. Obsidian REST API token is stored in plaintext config

`config/config.toml`:
```toml
[obsidian]
api_token = ""    # Set if you enabled auth in the Local REST API plugin
```
Fine for now since it's gitignored-adjacent, but if you ever do enable that token, storing it in a plaintext TOML file that other local processes/agents can read is weaker than it needs to be. Low priority given this is a single-user local machine, but worth moving to an env var (`OBSIDIAN_API_TOKEN`) alongside the fix in #2, so all your secrets live in one consistent place.

### 8. Web-task agent uses Scrapling instead of browser-use

Not a bug — just flagging the divergence from the blueprint we discussed, since the code comment explicitly notes `# Phase 9: Web-task agent (replaces browser-use)`. Scrapling is a legitimate, actively maintained, free/OSS anti-bot scraping library, so this is a reasonable substitution — just know that Scrapling is scraping-focused (extract data from pages) rather than browser-use's broader "click buttons, fill forms, navigate multi-step flows" scope. If Phase 9 ever needs to do something interactive (book something, submit a form) rather than just read/search, you'll want to add browser-use alongside it rather than as a full replacement.

### 9. Phase 10 (always-on host) genuinely isn't done yet

The README is honest about this one (⏳ status) — everything currently assumes it's running on your MacBook. Once you do move it to a Pi/mini per the blueprint, re-run the audit checks above (especially #2/#3 together) since that's exactly the point where "only reachable from my own laptop" stops being true and the auth gap becomes a real exposure rather than a theoretical one.

---

## Priority order to actually work through this

1. **Fix #1 (path traversal)** — do this regardless of anything else, it's a correctness/safety bug even on localhost-only.
2. **Fix #2 + #3 together** — never widen the bind address without the auth check landing in the same change.
3. Fix #5 (model swap) — quick win, meaningfully improves reliability.
4. Fix #4 (repo bloat) — do before you ever push again or share the repo.
5. Fix #6 (LICENSE) — five minutes, closes the gap between what the README claims and what's legally true.
6. #7–#9 — whenever you get to them, no urgency.

Nice work on this, genuinely — the architecture matches the blueprint closely and the guardrails (dry-run defaults, sovereignty tracing, the alpha-scaling scheduler) are all real, working code, not scaffolding. The two critical items are the kind of thing that's easy to miss precisely *because* everything else is this thorough.
