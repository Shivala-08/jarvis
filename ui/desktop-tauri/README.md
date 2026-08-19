# ADHD Co-Processor — Tauri Desktop Shell

Native desktop app wrapping the FastAPI web UI. Gives you a Tauri-native window
with system tray, auto-update, and native menus — while the Python backend
does all the heavy lifting.

## Prerequisites

1. **Rust** (for Tauri):
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   source ~/.cargo/env
   ```

2. **Tauri CLI** (installed via npm):
   ```bash
   cd ui/desktop-tauri
   npm install
   ```

3. **Python backend** running on port 8080:
   ```bash
   uv run python main.py --ui
   ```

## Development

```bash
# Terminal 1: Start the Python backend
uv run python main.py --ui

# Terminal 2: Start Tauri dev mode (hot-reload)
cd ui/desktop-tauri
npm run dev
```

## Build for distribution

```bash
cd ui/desktop-tauri
npm run build
# Output: src-tauri/target/release/bundle/
```

## How it works

```
┌─────────────────────────────────┐
│  Tauri Native Window             │
│  ┌───────────────────────────┐  │
│  │  Webview                   │  │
│  │  Loads: localhost:8080/app │  │
│  │  (FastAPI serves the UI)   │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
           │ WebSocket / REST
           ▼
┌─────────────────────────────────┐
│  FastAPI Backend (Python)        │
│  - Braindump Agent               │
│  - Scheduler Agent               │
│  - Study Agent                   │
│  - Code Assistant                │
│  - Web Task Agent                │
│  - Memory (Mem0 + Qdrant)        │
│  - Speech (Whisper + Kokoro)     │
│  - Calendar Sync                 │
└─────────────────────────────────┘
```

## Files

- `src-tauri/` — Rust source + Tauri config
- `src/` — Frontend (just a loader that redirects to FastAPI)
- `package.json` — npm dependencies (Tauri CLI)
