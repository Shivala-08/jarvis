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
# Core memory operations
# ---------------------------------------------------------------------------

class ADHDMemoryEngine:
    """Wrapper around Mem0 providing ADHD-specific memory operations."""

    def __init__(self, user_id: str = "default_user"):
        self.user_id = user_id
        self._memory = None

    @property
    def memory(self):
        if self._memory is None:
            self._memory = _get_memory_client()
        return self._memory

    def capture_brain_dump(self, raw_text: str, metadata: Optional[dict] = None) -> dict:
        """Ingest raw brain-dump text. Extracts and stores semantic memories.

        Returns: {"memories_stored": int, "memories": [...]}
        """
        meta = {
            "source": "brain_dump",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        result = self.memory.add(raw_text, user_id=self.user_id, metadata=meta)
        return {
            "memories_stored": len(result.get("results", [])),
            "memories": result.get("results", []),
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
