"""Background Memory Service — automatic fact extraction and learning.

Inspired by OpenJarvis's MemoryService that runs fact extraction on a
background thread, allowing automatic learning from conversations
without blocking the main application.

Features:
- Background fact extraction from conversations
- Event-driven: automatically processes completed braindumps
- Thread-safe queue for non-blocking operation
- Error isolation: extraction failures don't affect main application
- Statistics tracking for monitoring
"""
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

from core.config import get_default_model
from core.event_bus import EventType, Event, publish, subscribe

import logging

logger = logging.getLogger(__name__)


class FactExtractor:
    """Extracts facts from conversations using the local LLM."""
    
    def __init__(self, model: str = None):
        self.model = model or get_default_model()
        self._extraction_prompt = """Extract factual information from this conversation.
Return a JSON array of facts, where each fact has:
- "text": the fact (concise, clear statement)
- "category": one of ["task", "preference", "habit", "schedule", "learning", "health", "observation"]
- "importance": "high", "medium", or "low"
- "tags": list of relevant tags

Conversation:
User: {user_text}
Assistant: {assistant_text}

Return ONLY the JSON array, no markdown fences."""
    
    def extract(self, user_text: str, assistant_text: str = "") -> List[Dict[str, Any]]:
        """Extract facts from a conversation exchange.
        
        Args:
            user_text: What the user said
            assistant_text: What the assistant responded (optional)
            
        Returns:
            List of extracted facts with metadata
        """
        if not user_text or not user_text.strip():
            return []
        
        if not HAS_OLLAMA:
            logger.warning("Ollama not available, cannot extract facts")
            return []
        
        try:
            prompt = self._extraction_prompt.format(
                user_text=user_text,
                assistant_text=assistant_text or "(no response)"
            )
            
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a fact extractor. Extract factual information from conversations."},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.1},  # Low temperature for consistency
            )
            
            content = response["message"]["content"].strip()
            
            # Strip markdown fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0].strip()
            
            # Parse JSON
            facts = json.loads(content)
            
            # Validate and normalize
            validated_facts = []
            for fact in facts:
                if isinstance(fact, dict) and "text" in fact:
                    validated_facts.append({
                        "text": fact["text"],
                        "category": fact.get("category", "observation"),
                        "importance": fact.get("importance", "medium"),
                        "tags": fact.get("tags", []),
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                        "source_text": user_text[:200],  # Keep source for reference
                    })
            
            return validated_facts
            
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse extracted facts as JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")
            return []


class MemoryService:
    """Background service for automatic fact extraction and memory learning.
    
    Runs fact extraction on a dedicated background thread so it never
    blocks the main application. Uses an event-driven architecture to
    automatically process completed braindumps and conversations.
    """
    
    def __init__(
        self,
        memory_engine: Any,
        model: str = None,
        max_queue: int = 256,
    ):
        if model is None:
            model = get_default_model()
        self.memory_engine = memory_engine
        self.extractor = FactExtractor(model)
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, max_queue))
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stats = {
            "jobs_processed": 0,
            "facts_extracted": 0,
            "facts_stored": 0,
            "errors": 0,
            "started_at": None,
        }
        self._lock = threading.Lock()
        
        # Subscribe to events
        self._subscribe_events()
    
    def _subscribe_events(self) -> None:
        """Subscribe to relevant events for automatic processing."""
        subscribe(EventType.BRAINDUMP_COMPLETED, self._on_braindump_completed)
        subscribe(EventType.TASK_COMPLETED, self._on_task_completed)
    
    def _unsubscribe_events(self) -> None:
        """Unsubscribe from events."""
        from core.event_bus import unsubscribe
        unsubscribe(EventType.BRAINDUMP_COMPLETED, self._on_braindump_completed)
        unsubscribe(EventType.TASK_COMPLETED, self._on_task_completed)
    
    def _on_braindump_completed(self, event: Event) -> None:
        """Handle completed braindump events."""
        data = event.data
        user_text = data.get("text", "")
        result = data.get("result", {})
        
        # Create a combined text for fact extraction
        thoughts = result.get("thoughts", [])
        thought_texts = [f"- {t.get('text', '')} ({t.get('type', 'task')})" for t in thoughts]
        assistant_text = "Extracted thoughts:\n" + "\n".join(thought_texts)
        
        self.submit(user_text, assistant_text)
    
    def _on_task_completed(self, event: Event) -> None:
        """Handle completed task events."""
        data = event.data
        task_text = data.get("task_text", "")
        actual_minutes = data.get("actual_minutes", 0)
        estimated_minutes = data.get("estimated_minutes", 0)
        
        # Record task completion for alpha computation
        if self.memory_engine:
            self.memory_engine.record_task_completion(
                task_text, estimated_minutes, actual_minutes
            )
        
        # Extract facts about the completed task
        user_text = f"Completed task: {task_text}"
        assistant_text = f"Took {actual_minutes} minutes (estimated {estimated_minutes})"
        self.submit(user_text, assistant_text)
    
    def start(self) -> None:
        """Start the background worker thread."""
        if self._running.is_set():
            return
        
        self._running.set()
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        
        self._thread = threading.Thread(
            target=self._loop,
            name="memory-service",
            daemon=True,
        )
        self._thread.start()
        logger.debug("Memory service started")
    
    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background worker thread."""
        if not self._running.is_set():
            return
        
        self._running.clear()
        
        # Send stop sentinel
        try:
            self._queue.put_nowait(None)  # Sentinel
        except queue.Full:
            pass
        
        # Wait for thread to finish
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        
        self._unsubscribe_events()
        logger.debug("Memory service stopped")
    
    @property
    def is_running(self) -> bool:
        return self._running.is_set()
    
    def submit(self, user_text: str, assistant_text: str = "") -> bool:
        """Queue a conversation for fact extraction.
        
        Non-blocking; never raises. Returns True if the job was enqueued,
        False if the service is not running or the queue is full.
        """
        if not self._running.is_set():
            return False
        
        if not user_text or not user_text.strip():
            return False
        
        try:
            self._queue.put_nowait((user_text, assistant_text))
            return True
        except queue.Full:
            logger.debug("Memory service queue full; dropping exchange")
            return False
    
    def _loop(self) -> None:
        """Background worker loop."""
        while True:
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self._running.is_set():
                    break
                continue
            
            if job is None:  # Stop sentinel
                self._queue.task_done()
                break
            
            try:
                self._process(job)
            except Exception as e:
                logger.debug(f"Memory extraction job failed: {e}")
                with self._lock:
                    self._stats["errors"] += 1
            finally:
                self._queue.task_done()
            
            if not self._running.is_set() and self._queue.empty():
                break
    
    def _process(self, job: tuple) -> None:
        """Process a single extraction job."""
        user_text, assistant_text = job
        
        # Extract facts
        facts = self.extractor.extract(user_text, assistant_text)
        
        with self._lock:
            self._stats["jobs_processed"] += 1
            self._stats["facts_extracted"] += len(facts)
        
        if not facts:
            return
        
        # Store facts in memory
        if self.memory_engine:
            stored_count = 0
            for fact in facts:
                try:
                    metadata = {
                        "source": "auto_extract",
                        "category": fact.get("category", "observation"),
                        "importance": fact.get("importance", "medium"),
                        "tags": fact.get("tags", []),
                        "extracted_at": fact.get("extracted_at", ""),
                    }
                    
                    self.memory_engine.memory.add(
                        fact["text"],
                        user_id=self.memory_engine.user_id,
                        metadata=metadata,
                    )
                    stored_count += 1
                except Exception as e:
                    logger.debug(f"Failed to store fact: {e}")
            
            with self._lock:
                self._stats["facts_stored"] += stored_count
            
            # Publish event
            publish(
                EventType.MEMORY_STORED,
                {
                    "facts_count": stored_count,
                    "source": "auto_extract",
                    "user_text": user_text[:100],
                },
                source="memory_service",
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        with self._lock:
            return {**self._stats, "queue_size": self._queue.qsize()}
    
    def clear_stats(self) -> None:
        """Reset statistics."""
        with self._lock:
            self._stats = {
                "jobs_processed": 0,
                "facts_extracted": 0,
                "facts_stored": 0,
                "errors": 0,
                "started_at": self._stats.get("started_at"),
            }


# Global memory service instance
_memory_service: Optional[MemoryService] = None
_service_lock = threading.Lock()


def build_memory_service(
    memory_engine: Any,
    model: str = None,
) -> Optional[MemoryService]:
    """Build and start the memory service.
    
    Returns None if memory engine is not available.
    """
    if model is None:
        model = get_default_model()
    global _memory_service
    
    if memory_engine is None:
        return None
    
    with _service_lock:
        if _memory_service is None:
            _memory_service = MemoryService(memory_engine, model)
            _memory_service.start()
    
    return _memory_service


def get_memory_service() -> Optional[MemoryService]:
    """Get the global memory service instance."""
    return _memory_service


def stop_memory_service() -> None:
    """Stop the global memory service."""
    global _memory_service
    with _service_lock:
        if _memory_service is not None:
            _memory_service.stop()
            _memory_service = None
