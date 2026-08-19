# Jarvis Upgrade Report

This report documents the security fixes, architectural enhancements, configuration upgrades, repository hygiene, and frontend UI redesign implemented for the Jarvis ADHD Cognitive Co-Processor.

---

## 1. Security & Correctness Enhancements

### Safe Path Resolution
- Implemented `_safe_resolve(file_path: str) -> Path` inside [coding_agent.py](file:///Users/pallav/Downloads/jarvis-ai-assistant/agents/coding_agent.py) to prevent absolute path escapes, symlink exploits, and parent directory traversal (`../../`) vulnerabilities.
- Safe resolution validates that all read/write paths resolve strictly within `PROJECT_ROOT` and rejects traversal strings.
- Integrated `_safe_resolve` into the core code analyzer (`_read_file`) and code application engine (`apply_changes`).

### API Token Authentication
- Added an API authentication layer in [core/auth.py](file:///Users/pallav/Downloads/jarvis-ai-assistant/core/auth.py) validating the `ADHD_COPILOT_TOKEN` environment variable.
- Protected all mutating or action-invoking routes by applying a FastAPI `Depends(require_token)` security dependency. Protected endpoints include:
  - `/api/purge` (Data purge)
  - `/api/sovereignty/*` (Sovereignty status, reports, and purges)
  - `/api/code/apply` & `/api/code` (Coding model execution)
  - `/api/rebalance` (Schedule rebalancing)
  - `/api/scheduler/tasks` (POST, Pause, Resume)
  - `/api/tasks/start` & `/api/tasks/complete`
  - `/api/skills/{skill_name}/invoke`
  - `/api/calendar/sync` & `/api/calendar/clear`
  - `/api/web-task`
- Secured WebSocket real-time voice streaming (`/ws/voice` and PWA `/ws/pwa`) by validating the security token from the query parameter `?token=...`.

### Configurable CORS Origins
- Replaced the permissive `allow_origins=["*"]` wildcard middle-ware with a dynamic allowed origins parser reading the `JARVIS_ALLOWED_ORIGINS` environment variable.
- Safely defaults to `localhost` and local development ports if no environment variables are configured.

### Secure Tailscale Bind Configuration
- Decoupled host binding in [main.py](file:///Users/pallav/Downloads/jarvis-ai-assistant/main.py). 
- Allows binding to `0.0.0.0` for remote/Tailscale access only if `ADHD_COPILOT_TOKEN` is set, automatically falling back to secure `localhost` to prevent accidental public exposures.

---

## 2. Local AI Configuration & Diagnostics

### Model Upgrade to Qwen 3.5 9B
- Configured default and fallback models in [config/config.toml](file:///Users/pallav/Downloads/jarvis-ai-assistant/config/config.toml) to `qwen3.5:9b`.
- Updated coding, reasoning, and voice agent modules to fetch configured models dynamically from a central config manager [core/config.py](file:///Users/pallav/Downloads/jarvis-ai-assistant/core/config.py) rather than hardcoding legacy model names.

### Model Startup Diagnostics & Graceful Fallbacks
- Created a startup diagnostics suite in [core/diagnostics.py](file:///Users/pallav/Downloads/jarvis-ai-assistant/core/diagnostics.py) to check local Ollama availability, reachability of configured models, and list installed models.
- Implemented fallback strategy: if `qwen3.5:9b` is not pulled on the user's machine, it gracefully detects installed local models (like `llama3.1`) and continues the session using the fallback model rather than crashing.

---

## 3. Repository Hygiene & Secrets Migration

### Tauri Build Target Clean-up
- Updated [.gitignore](file:///Users/pallav/Downloads/jarvis-ai-assistant/.gitignore) to recursively ignore nested Tauri target outputs under `**/src-tauri/target/`.
- Successfully untracked legacy build targets from git cache.

### Secrets Migration
- Moved sensitive configuration keys (`OBSIDIAN_API_TOKEN` and `ADHD_COPILOT_TOKEN`) out of configuration files into environment variables.
- Created [.env.example](file:///Users/pallav/Downloads/jarvis-ai-assistant/.env.example) detailing token setups.

### Licensing
- Added a standard MIT [LICENSE](file:///Users/pallav/Downloads/jarvis-ai-assistant/LICENSE) file to the repository.

---

## 4. Frontend UI/UX Redesign

Redesigned the primary web dashboard in [index.html](file:///Users/pallav/Downloads/jarvis-ai-assistant/ui/index.html) following a warm-monochrome, editorial-style **Premium Utilitarian Minimalism** aesthetic:
- **Typography**: Incorporated premium system typefaces (`SF Pro Display`, `Switzer`) and elegant editorial serif (`Instrument Serif`) for titles, with code formatted in `Geist Mono`.
- **Palette**: Warm bone-white canvas (`#FBFBFA`) and clean dividers (`#EAEAEA`) with charcoal text and desaturated pastel status tags (`#FDEBEC`, `#E1F3FE`, `#EDF3EC`).
- **Whitespace & Bento Grid**: Rebuilt the interface into an asymmetrical Bento Box layout with crisp borders (`1px solid #EAEAEA`) and rounded corners (`8px`).
- **Active Sprint Banner**: Highlighted "Active Sprint / What should I do now?" at the very top of the dashboard.
- **Diagnostics Status Panel**: Added a dedicated system status widget displaying local AI server reachability, active local model, database connection status, and any diagnostic warnings inline.
- **Emoji Clean-up**: Replaced all emojis in headings, labels, and buttons with clean, lightweight SVG paths.

---

## 5. Security & Verification Suite

Added dedicated integration test cases in `run_tests()` verifying:
- Path traversal blocks (relative path resolution, absolute path blocks, and directory traversal blocks).
- API Token verification (requests with missing tokens, invalid tokens, and valid tokens).
