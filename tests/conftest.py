"""Shared test fixtures for route tests.

Mocks heavy dependencies (Qdrant, Ollama, etc.) so route tests
can run without external services.
"""

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock heavy modules BEFORE any test collection.
# pytest_configure runs before test collection, preventing
# memory.adhd_memory from trying to connect to Qdrant.
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """Called before test collection — mock heavy modules early."""
    # Mock memory module to prevent Qdrant connection attempts
    if "memory.adhd_memory" not in sys.modules:
        mock_memory = MagicMock()
        mock_memory.ADHDMemoryEngine = MagicMock()
        mock_memory.ConversationMemory = MagicMock()
        mock_memory.TaskCompletionTracker = MagicMock()
        mock_memory.get_history = MagicMock(return_value=[])
        mock_memory.ObsidianClient = MagicMock()
        sys.modules["memory.adhd_memory"] = mock_memory

    # Mock other heavy modules that try to connect to external services
    for mod in ["ollama", "qdrant_client", "mem0ai", "mem0", "faster_whisper", "kokoro", "soundfile", "sounddevice"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()


import pytest


@pytest.fixture(autouse=True)
def mock_heavy_dependencies(request):
    """Mock external service connections for all route tests."""
    if "test_proactive" in getattr(request.node, "fspath", {}).__str__():
        with patch("core.sync.ingest_pending_deltas", return_value={"ingested_files": 0, "records_upserted": 0}), \
             patch("core.notifications.setup_notification_handlers"):
            yield
    else:
        with patch("core.sync.ingest_pending_deltas", return_value={"ingested_files": 0, "records_upserted": 0}), \
             patch("core.notifications.setup_notification_handlers"), \
             patch("core.proactive.register_proactive_triggers"):
            yield
