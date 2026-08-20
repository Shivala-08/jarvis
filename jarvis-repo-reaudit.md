# Re-Audit — Shivala-08/jarvis (post-fix pass)

Pulled your latest commit (`237a59b — "secure, refactor, and redesign"`) and checked every item from the first audit against the actual code. Good news first: **everything critical is fixed, and fixed correctly** — not just patched to make the symptom disappear.

---

## ✅ Confirmed fixed

| # | Issue | Verified fix |
|---|---|---|
| 1 | Path traversal in coding agent | New `_safe_resolve()` in `agents/coding_agent.py` resolves the path and checks `is_relative_to(PROJECT_ROOT)` before any read/write — used in both `_read_file` and `apply_changes`. Correct fix, matches what I suggested. |
| 2 | No API authentication | `core/auth.py` added — `require_token` dependency checks `X-API-Token` against `ADHD_COPILOT_TOKEN`. Applied to all state-changing/destructive endpoints: `/api/purge`, `/api/sovereignty/purge`, `/api/code/apply`, `/api/scheduler/tasks*`, `/api/notifications*`, etc. |
| 3 | Server only bound to localhost | Fixed, and fixed *well* — new `_get_bind_host()` helper **refuses to bind to `0.0.0.0` unless `ADHD_COPILOT_TOKEN` is set**, falling back to localhost with a printed warning otherwise. This is better than what I asked for: it structurally prevents you from ever re-introducing bug #2 by accident when you flip on remote access. |
| 4 | 751MB of committed Rust build artifacts | `.gitignore` now correctly uses `**/src-tauri/target/`. Repo is 2.1MB tracked, down from 751MB. Confirmed zero `src-tauri/target` files remain in git. |
| 5 | Model still on Llama 3.1 | `config/config.toml` now sets `default_model`, `fallback_model`, and both `reasoning`/`coding` model slots to `qwen3.5:9b`. |
| 6 | No LICENSE file | `LICENSE` now present in repo root. |
| 7 | Obsidian token in plaintext config only | `memory/adhd_memory.py` now checks `OBSIDIAN_API_TOKEN` env var first, config value as fallback — same pattern as the API auth fix, consistent approach across the codebase. |

CORS was also tightened beyond what I flagged — `allowed_origins` now reads from `JARVIS_ALLOWED_ORIGINS` env var instead of a hardcoded wildcard, defaulting to a specific origin list rather than `["*"]`.

---

## 🟡 Still open (unchanged from before, still low priority)

### Scrapling instead of browser-use
Unchanged, and that's fine — this was flagged as a known scope tradeoff, not a bug. Scrapling handles read/extract-style web tasks well; if you later need interactive tasks (filling forms, multi-step navigation, clicking through a booking flow), that's the point to add browser-use alongside it rather than swap back.

### Phase 10 (always-on host) still not done
Still expected — this was never a code fix, it's a hardware/deployment step (Pi 5 or Mac mini) that hasn't happened yet. Worth flagging again now because of one specific interaction: once you *do* set `ADHD_COPILOT_TOKEN` and flip on remote binding for real phone access, that's the exact moment the new `_get_bind_host()` safeguard switches from "protecting you" to "actually exposing the API to your tailnet" — so treat setting that env var as the real go-live moment, not the Pi migration itself. Worth a final pass through the sovereignty trace (`--sovereignty`) right after you do it, just to confirm the token is actually being required and nothing's reachable unauthenticated.

---

## Instruction manual — what to actually do now

1. **Set the token once, permanently, before you ever bind remotely:**
   ```bash
   # add to your shell profile (~/.zshrc) so it's always set
   export ADHD_COPILOT_TOKEN="$(openssl rand -hex 32)"
   ```
   Save this value somewhere too (password manager) — your PWA needs to send it as `X-API-Token` on every request.

2. **When you're ready for real phone access (Phase 11), turn on remote mode explicitly:**
   ```bash
   export JARVIS_REMOTE=true
   uv run python main.py --ui
   ```
   You should see it bind to `0.0.0.0`, not the localhost fallback warning. If you see the warning, `ADHD_COPILOT_TOKEN` isn't set in that shell session.

3. **Set your CORS origin explicitly** once the PWA has a real address, rather than relying on defaults:
   ```bash
   export JARVIS_ALLOWED_ORIGINS="http://YOUR-TAILSCALE-IP:8080"
   ```

4. **Pull the new model** if you haven't yet on this machine:
   ```bash
   ollama pull qwen3.5:9b
   ```

5. **One verification pass after all of the above**, to close the loop:
   ```bash
   uv run python main.py --sovereignty
   ```
   Confirm it reports clean with no unexpected outbound connections, and manually try hitting `/api/purge` from another device on your tailnet *without* the token header — it should 401, not succeed.

That's genuinely the full list — nothing else from the first pass is outstanding. This is in good shape.
