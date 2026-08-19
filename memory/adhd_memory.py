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
        api_token: str = OBSIDIAN_CFG.get("api_token", ""),
        vault_path: str = OBSIDIAN_CFG.get("vault_path", "vault"),
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
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

        return self.write_note(filename, content)


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

    def capture_brain_dump(self, raw_text: str, metadata: Optional[dict] = None) -> dict:
        """Ingest raw brain-dump text. Extracts and stores semantic memories.

        Also writes a formatted note to the Obsidian vault (if configured).

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
                # Build a result-like dict for the note formatter
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
