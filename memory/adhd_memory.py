"""ADHD Memory Engine — persistent semantic memory using Mem0 + Qdrant + Ollama embeddings.

Features:
- capture_brain_dump(): ingest raw brain-dump text, extract + store memories
- retrieve_context_for_task(): semantic search for relevant past memories
- get_all_memories(): list all stored memories
- purge_all(): one-click wipe (Data Sovereignty)
- History tracking for scheduler's alpha computation
- All inference + storage local — zero cloud calls
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import toml
    CONFIG = toml.load("config/config.toml")
except (FileNotFoundError, Exception):
    CONFIG = {}

MEMORY_CFG = CONFIG.get("memory", {})
OLLAMA_CFG = CONFIG.get("engine", {}).get("ollama", {})
OBSIDIAN_CFG = CONFIG.get("obsidian", {})


# ---------------------------------------------------------------------------
# Mem0 setup (pointed at local Qdrant + Ollama embeddings)
# ---------------------------------------------------------------------------

def _get_memory_client():
    """Initialize Mem0 with local Qdrant + Ollama embeddings."""
    from mem0 import Memory

    config = {
        "llm": {
            "provider": "ollama",
            "config": {
                "model": OLLAMA_CFG.get("default_model", "qwen2.5-coder:14b"),
                "ollama_base_url": OLLAMA_CFG.get("base_url", "http://localhost:11434"),
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": OLLAMA_CFG.get("embedding_model", "nomic-embed-text"),
                "ollama_base_url": OLLAMA_CFG.get("base_url", "http://localhost:11434"),
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "host": "localhost",
                "port": 6333,
                "collection_name": MEMORY_CFG.get("collection_name", "adhd_memory"),
                "embedding_model_dims": MEMORY_CFG.get("embedding_dim", 768),
            },
        },
        "version": "v1.1",
    }
    return Memory.from_config(config)


# ---------------------------------------------------------------------------
# History file for scheduler alpha
# ---------------------------------------------------------------------------

HISTORY_PATH = Path("data/task_history.jsonl")
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _append_history(entry: dict):
    """Append a task history entry."""
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_history() -> list[dict]:
    """Read all task history entries."""
    if not HISTORY_PATH.exists():
        return []
    entries = []
    with open(HISTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Obsidian Local REST API client
# ---------------------------------------------------------------------------

import httpx


class ObsidianClient:
    """Write notes to an Obsidian vault via its Local REST API plugin.

    One-directional: agents write here, they never read from Obsidian.
    Fails silently if the Obsidian server isn't running.
    """

    def __init__(
        self,
        base_url: str = OBSIDIAN_CFG.get("base_url", "http://localhost:27124"),
        api_token: str = None,
        vault_path: str = OBSIDIAN_CFG.get("vault_path", "vault"),
    ):
        import os
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token or os.environ.get("OBSIDIAN_API_TOKEN") or OBSIDIAN_CFG.get("api_token", "")
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self._server_available: Optional[bool] = None

    @property
    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    def is_available(self) -> bool:
        """Check if the Obsidian REST API server is reachable."""
        if self._server_available is not None:
            return self._server_available
        try:
            r = httpx.get(f"{self.base_url}/", headers=self.headers, timeout=3.0)
            self._server_available = r.status_code < 500
        except Exception:
            self._server_available = False
        return self._server_available

    def write_note(self, filename: str, content: str) -> bool:
        """Write a markdown note to the vault.

        Tries the REST API first (Obsidian picks it up immediately).
        Falls back to direct file write if the server is down.
        Returns True on success.
        """
        # Try REST API
        if self.is_available():
            try:
                # The vault path relative to Obsidian's vault root
                note_path = f"{self.vault_path}/{filename}"
                payload = {"content": content}
                r = httpx.put(
                    f"{self.base_url}/vault/{note_path}",
                    headers=self.headers,
                    json=payload,
                    timeout=5.0,
                )
                if r.status_code < 300:
                    return True
            except Exception:
                pass

        # Fallback: write directly to disk
        try:
            note_file = self.vault_path / filename
            note_file.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            print(f"  ⚠️  Obsidian write failed: {e}")
            return False

    def write_brain_dump_note(self, raw_text: str, result: dict) -> bool:
        """Format a brain dump as a note and write it to the vault.

        Note format:
          - Filename: YYYY-MM-DD-HHmm.md
          - Frontmatter: tags, source_agent, status, timestamp
          - Body: original text + extracted thoughts
        """
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y-%m-%d-%H%M") + ".md"
        timestamp = now.isoformat()

        thoughts = result.get("thoughts", [])
        mood = result.get("mood_hint", "unknown")
        first_step = result.get("suggested_first_step", "")

        # Collect all tags from thoughts
        all_tags = []
        for t in thoughts:
            all_tags.extend(t.get("tags", []))
        all_tags = list(dict.fromkeys(all_tags))  # dedupe, preserve order
        if not all_tags:
            all_tags = ["braindump"]

        tag_str = "\n".join(f"  - {tag}" for tag in all_tags)

        # Build thought list
        thought_lines = []
        for t in thoughts:
            ttype = t.get("type", "task")
            priority = t.get("priority", "soon")
            mins = t.get("estimated_minutes", "?")
            thought_lines.append(
                f"- **[{ttype}]** {t['text']} "
                f"*(priority: {priority}, ~{mins} min)*"
            )
        thoughts_md = "\n".join(thought_lines) if thought_lines else "- *(no structured thoughts extracted)*"

        content = f"""---
tags:
{tag_str}
  - adhd-copilot
source_agent: braindump
status: captured
timestamp: "{timestamp}"
---

# Brain Dump — {now.strftime("%Y-%m-%d %H:%M")}

## Mood
{mood}

## Raw Input
> {raw_text}

## Extracted Thoughts
{thoughts_md}

## Suggested First Step
{first_step}
"""

        # Write the note
        success = self.write_note(filename, content)
        
        # Update Dashboard.md after each brain dump
        self._update_dashboard()
        
        return success
    
    def _update_dashboard(self) -> bool:
        """Generate/update the Obsidian Dashboard.md with stats and today's captures.
        
        This provides a Dataview-compatible overview of the vault:
        - Total notes count
        - Today's brain dumps
        - Recent tags
        - Task summary
        """
        try:
            vault = self.vault_path
            if not vault.exists():
                return False
            
            # Collect all notes
            all_notes = sorted(vault.glob("*.md"), reverse=True)
            notes = [n for n in all_notes if n.name != "Dashboard.md"]
            
            # Today's notes
            today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_notes = [n for n in notes if n.name.startswith(today_prefix)]
            
            # Collect recent tags from note filenames
            recent_tags = set()
            for note in notes[:20]:
                # Try to read frontmatter for tags
                try:
                    content = note.read_text(encoding="utf-8")
                    if "tags:" in content:
                        for line in content.split("\n"):
                            if line.strip().startswith("-") and "tags:" not in line:
                                tag = line.strip().lstrip("- ").strip()
                                if tag and tag != "adhd-copilot":
                                    recent_tags.add(tag)
                except Exception:
                    pass
            
            # Build dashboard content
            dashboard = f"""---
tags:
  - dashboard
  - adhd-copilot
timestamp: "{datetime.now(timezone.utc).isoformat()}"
---

# 🧠 ADHD Co-Processor Dashboard

> Auto-generated by the Obsidian memory mirror. Do not edit manually.

## 📊 Stats

| Metric | Value |
|--------|-------|
| Total notes | {len(notes)} |
| Today's captures | {len(today_notes)} |
| Unique tags | {len(recent_tags)} |
| Last updated | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} |

## 📝 Today's Captures ({today_prefix})

"""
            
            if today_notes:
                for note in today_notes[:10]:
                    name = note.stem
                    dashboard += f"- [[{name}]]\n"
            else:
                dashboard += "_No captures today yet. Do a brain dump to add one._\n"
            
            dashboard += f"\n## 🏷️ Recent Tags\n\n"
            if recent_tags:
                dashboard += " ".join(f"`{tag}`" for tag in sorted(recent_tags))
            else:
                dashboard += "_No tags yet._"
            
            dashboard += f"\n\n## 📋 Recent Notes (last 10)\n\n"
            for note in notes[:10]:
                name = note.stem
                dashboard += f"- [[{name}]]\n"
            
            if len(notes) > 10:
                dashboard += f"\n_... and {len(notes) - 10} more notes._\n"
            
            dashboard += f"\n---\n_Generated by ADHD Co-Processor — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n"
            
            return self.write_note("Dashboard.md", dashboard)
            
        except Exception as e:
            print(f"  ⚠️  Dashboard update failed: {e}")
            return False


# ---------------------------------------------------------------------------
# Core memory operations
# ---------------------------------------------------------------------------

class ADHDMemoryEngine:
    """Wrapper around Mem0 providing ADHD-specific memory operations."""

    def __init__(self, user_id: str = "default_user", obsidian: Optional["ObsidianClient"] = None):
        self.user_id = user_id
        self._memory = None
        self._obsidian = obsidian

    @property
    def memory(self):
        if self._memory is None:
            self._memory = _get_memory_client()
        return self._memory

    def capture_brain_dump(self, raw_text: str, metadata: Optional[dict] = None, braindump_result: Optional[dict] = None) -> dict:
        """Ingest raw brain-dump text. Extracts and stores semantic memories.

        Also writes a formatted note to the Obsidian vault (if configured).

        Args:
            raw_text: The original brain-dump text.
            metadata: Optional metadata to attach to the memory.
            braindump_result: Optional structured output from the braindump agent
                (with thoughts, mood_hint, suggested_first_step).  When provided,
                the Obsidian note will include the full structured thoughts.

        Returns: {"memories_stored": int, "memories": [...], "obsidian_written": bool}
        """
        meta = {
            "source": "brain_dump",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        result = self.memory.add(raw_text, user_id=self.user_id, metadata=meta)
        memories_stored = len(result.get("results", []))

        # Write to Obsidian vault (best-effort, non-blocking)
        obsidian_written = False
        if self._obsidian is None and OBSIDIAN_CFG.get("enabled", False):
            try:
                self._obsidian = ObsidianClient()
            except Exception:
                pass
        if self._obsidian:
            try:
                # Use braindump agent's structured output if available,
                # otherwise build from Mem0 results
                if braindump_result and braindump_result.get("thoughts"):
                    note_result = braindump_result
                else:
                    note_result = {
                        "thoughts": [
                            {
                                "text": mem.get("memory", ""),
                                "type": "observation",
                                "priority": "soon",
                                "estimated_minutes": 15,
                                "tags": [],
                            }
                            for mem in result.get("results", [])
                        ],
                        "mood_hint": "captured",
                        "suggested_first_step": "Review in Obsidian vault",
                    }
                obsidian_written = self._obsidian.write_brain_dump_note(raw_text, note_result)
                if obsidian_written:
                    print("  📓 Note written to Obsidian vault")
            except Exception as e:
                print(f"  ⚠️  Obsidian write skipped: {e}")

        return {
            "memories_stored": memories_stored,
            "memories": result.get("results", []),
            "obsidian_written": obsidian_written,
        }

    def store_task(self, task_text: str, estimated_minutes: int, priority: str = "soon") -> dict:
        """Store a task with scheduling metadata."""
        meta = {
            "source": "scheduler",
            "type": "task",
            "priority": priority,
            "estimated_minutes": estimated_minutes,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = self.memory.add(task_text, user_id=self.user_id, metadata=meta)
        return result

    def record_task_completion(
        self,
        task_text: str,
        estimated_minutes: int,
        actual_minutes: int,
    ):
        """Record actual time spent (used for alpha computation)."""
        _append_history({
            "task": task_text,
            "estimated_minutes": estimated_minutes,
            "actual_minutes": actual_minutes,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    def retrieve_context_for_task(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search: find memories relevant to a query.

        Returns list of {"memory": str, "score": float, "metadata": dict}
        """
        results = self.memory.search(query, filters={"user_id": self.user_id}, limit=limit)
        memories = []
        for r in results.get("results", []):
            memories.append({
                "memory": r.get("memory", ""),
                "score": r.get("score", 0.0),
                "metadata": r.get("metadata", {}),
            })
        return memories

    def get_all_memories(self) -> list[dict]:
        """List all stored memories for this user."""
        results = self.memory.get_all(filters={"user_id": self.user_id})
        return results.get("results", [])

    def purge_all(self) -> dict:
        """One-click purge: delete all memories for this user.

        Verifies the collection is actually deleted (Phase 9 data sovereignty).
        """
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(
                host="localhost",
                port=6333,
            )
            collection = MEMORY_CFG.get("collection_name", "adhd_memory")
            client.delete_collection(collection_name=collection)
            # Verify deletion
            collections = [c.name for c in client.get_collections().collections]
            self._memory = None  # Force re-init
            if collection not in collections:
                return {"status": "success", "message": f"Collection '{collection}' purged and verified."}
            else:
                return {"status": "error", "message": f"Collection '{collection}' still exists after delete."}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Conversation Memory — multi-turn context
# ---------------------------------------------------------------------------

class ConversationMemory:
    """Manages multi-turn conversation context.
    
    Stores conversation history on disk as JSONL files, one per conversation.
    Provides context window management (keeps last N turns) and relevance
    scoring for injecting past context into new prompts.
    
    Usage:
        conv = ConversationMemory()
        conv.add_turn(conversation_id, "user", "What's my schedule?")
        conv.add_turn(conversation_id, "assistant", "You have 3 tasks...")
        context = conv.get_context(conversation_id)  # recent turns
    """
    
    def __init__(self, storage_dir: str = "data/conversations"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_turns = 20  # Keep last N turns for context
    
    def _conversation_path(self, conversation_id: str) -> Path:
        """Get the file path for a conversation."""
        # Sanitize ID for filesystem
        safe_id = "_".join(c if c.isalnum() or c in "-_" else "_" for c in conversation_id)
        return self.storage_dir / f"{safe_id}.jsonl"
    
    def add_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Add a turn to the conversation history.
        
        Args:
            conversation_id: Unique conversation identifier
            role: "user" or "assistant"
            content: The message content
            metadata: Optional metadata (e.g., agent used, latency)
        
        Returns: The turn dict that was stored
        """
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        
        path = self._conversation_path(conversation_id)
        with open(path, "a") as f:
            f.write(json.dumps(turn) + "\n")
        
        return turn
    
    def get_turns(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        """Get conversation turns, most recent first.
        
        Args:
            conversation_id: Conversation to retrieve
            limit: Max turns to return (None = all)
        """
        path = self._conversation_path(conversation_id)
        if not path.exists():
            return []
        
        turns = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
        
        # Return most recent first
        turns.reverse()
        if limit:
            turns = turns[:limit]
        return turns
    
    def get_context(self, conversation_id: str, max_turns: Optional[int] = None) -> str:
        """Get formatted context string for the LLM prompt.
        
        Returns the last N turns formatted as:
            User: ...
            Assistant: ...
            User: ...
        """
        turns = self.get_turns(conversation_id, limit=max_turns or self.max_turns)
        if not turns:
            return ""
        
        # Reverse to get chronological order
        turns.reverse()
        
        lines = ["Conversation context:"]
        for turn in turns:
            role_label = "User" if turn["role"] == "user" else "Assistant"
            lines.append(f"{role_label}: {turn['content'][:500]}")  # Truncate long messages
        
        return "\n".join(lines)
    
    def get_conversation_ids(self) -> list[str]:
        """List all conversation IDs."""
        ids = []
        for f in self.storage_dir.glob("*.jsonl"):
            ids.append(f.stem)
        return sorted(ids)
    
    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation."""
        path = self._conversation_path(conversation_id)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def get_stats(self, conversation_id: str) -> dict:
        """Get stats for a conversation."""
        turns = self.get_turns(conversation_id)
        if not turns:
            return {"turns": 0}
        
        user_turns = [t for t in turns if t["role"] == "user"]
        assistant_turns = [t for t in turns if t["role"] == "assistant"]
        
        return {
            "turns": len(turns),
            "user_messages": len(user_turns),
            "assistant_messages": len(assistant_turns),
            "first_turn": turns[-1]["timestamp"] if turns else None,
            "last_turn": turns[0]["timestamp"] if turns else None,
        }


# ---------------------------------------------------------------------------
# Task Completion Tracker — record actual vs estimated times
# ---------------------------------------------------------------------------

class TaskCompletionTracker:
    """Tracks task completions for alpha computation and stats.
    
    Records when tasks are started and completed, computing the
    actual vs estimated time ratio that feeds into the scheduler's
    alpha (time-scaling multiplier).
    
    Usage:
        tracker = TaskCompletionTracker()
        tracker.start_task("Finish report", estimated_minutes=60)
        # ... user works on it ...
        tracker.complete_task("Finish report", actual_minutes=45)
        alpha = tracker.get_alpha()
    """
    
    def __init__(self, storage_path: str = "data/task_completions.jsonl"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._active_tasks: dict[str, dict] = {}  # task_text -> start info
    
    def start_task(self, task_text: str, estimated_minutes: int) -> dict:
        """Record that a task has been started."""
        entry = {
            "task": task_text,
            "estimated_minutes": estimated_minutes,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._active_tasks[task_text] = entry
        return entry
    
    def complete_task(self, task_text: str, actual_minutes: float) -> dict:
        """Record that a task has been completed.
        
        Returns the completion record with ratio info.
        """
        start_info = self._active_tasks.pop(task_text, {})
        estimated = start_info.get("estimated_minutes", 25)
        
        ratio = actual_minutes / estimated if estimated > 0 else 1.0
        
        record = {
            "task": task_text,
            "estimated_minutes": estimated,
            "actual_minutes": actual_minutes,
            "ratio": round(ratio, 3),
            "started_at": start_info.get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Persist
        with open(self.storage_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        
        # Also append to the scheduler's history for alpha computation
        _append_history({
            "task": task_text,
            "estimated_minutes": estimated,
            "actual_minutes": actual_minutes,
            "completed_at": record["completed_at"],
        })
        
        return record
    
    def get_alpha(self) -> float:
        """Compute current alpha from completion history."""
        history = get_history()
        ratios = []
        for entry in history:
            est = entry.get("estimated_minutes", 0)
            act = entry.get("actual_minutes", 0)
            if est > 0 and act > 0:
                ratios.append(act / est)
        
        if not ratios:
            return 1.6  # Default midpoint
        
        ratios.sort()
        mid = len(ratios) // 2
        median = ratios[mid] if len(ratios) % 2 else (ratios[mid-1] + ratios[mid]) / 2
        
        alpha_min = MEMORY_CFG.get("scheduler", {}).get("time_scaling_alpha_min", 1.4)
        alpha_max = MEMORY_CFG.get("scheduler", {}).get("time_scaling_alpha_max", 1.8)
        return max(alpha_min, min(alpha_max, median))
    
    def get_stats(self) -> dict:
        """Get completion statistics."""
        completions = []
        if self.storage_path.exists():
            with open(self.storage_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        completions.append(json.loads(line))
        
        if not completions:
            return {
                "total_completions": 0,
                "avg_ratio": 1.0,
                "alpha": 1.6,
            }
        
        ratios = [c.get("ratio", 1.0) for c in completions]
        return {
            "total_completions": len(completions),
            "avg_ratio": sum(ratios) / len(ratios),
            "min_ratio": min(ratios),
            "max_ratio": max(ratios),
            "alpha": self.get_alpha(),
            "recent_completions": completions[-5:],
        }
    
    def get_completion_history(self, limit: int = 20) -> list[dict]:
        """Get recent task completions."""
        completions = []
        if self.storage_path.exists():
            with open(self.storage_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        completions.append(json.loads(line))
        return completions[-limit:]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = ADHDMemoryEngine()

    # Demo: store some memories
    test_memories = [
        "I work best in the morning, especially before 10am",
        "The graph algorithms assignment is due next Thursday",
        "I keep forgetting to take breaks — need the body double to remind me",
        "Calculus integration by parts always takes me longer than expected",
        "My most productive study spot is the library 3rd floor",
    ]
    print("=== Storing test memories ===")
    for mem in test_memories:
        result = engine.capture_brain_dump(mem)
        print(f"  ✅ Stored: {mem[:60]}...")

    print("\n=== Semantic search: 'study habits' ===")
    results = engine.retrieve_context_for_task("study habits")
    for r in results:
        print(f"  📌 {r['memory'][:80]}  (score: {r['score']:.3f})")

    print(f"\n=== Total memories: {len(engine.get_all_memories())} ===")
