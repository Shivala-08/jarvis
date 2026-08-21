"""Tests for routes/sync.py — sync and proactive trigger endpoints.

Tests route handler functions directly (avoids TestClient startup issues).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/sync/status
# ---------------------------------------------------------------------------


class TestSyncStatusEndpoint:
    @patch("core.sync.get_sync_status")
    def test_returns_status(self, mock_status):
        from routes.sync import api_sync_status

        mock_status.return_value = {
            "export_dir": "sync/export",
            "pending_deltas": 0,
            "completed_exports": 5,
            "last_export_time": "2026-08-22T10:00:00+00:00",
        }

        result = api_sync_status()
        assert result["pending_deltas"] == 0
        assert result["completed_exports"] == 5


# ---------------------------------------------------------------------------
# POST /api/sync/export
# ---------------------------------------------------------------------------


class TestSyncExportEndpoint:
    @patch("core.sync.export_state_delta")
    @patch("core.dependencies.get_memory")
    def test_exports(self, mock_get_memory, mock_export):
        from routes.sync import api_sync_export

        mock_get_memory.return_value = MagicMock()
        mock_export.return_value = {"exported": 3, "file": "sync/export/delta_123.jsonl"}

        result = api_sync_export()
        assert result["exported"] == 3


# ---------------------------------------------------------------------------
# POST /api/sync/ingest
# ---------------------------------------------------------------------------


class TestSyncIngestEndpoint:
    @patch("core.sync.ingest_pending_deltas")
    @patch("core.dependencies.get_memory")
    def test_ingests(self, mock_get_memory, mock_ingest):
        from routes.sync import api_sync_ingest

        mock_get_memory.return_value = MagicMock()
        mock_ingest.return_value = {"ingested_files": 1, "records_upserted": 5}

        result = api_sync_ingest()
        assert result["records_upserted"] == 5


# ---------------------------------------------------------------------------
# GET /api/proactive/status
# ---------------------------------------------------------------------------


class TestProactiveStatusEndpoint:
    @patch("core.proactive.get_proactive_status")
    def test_returns_status(self, mock_status):
        from routes.sync import api_proactive_status

        mock_status.return_value = {
            "triggers": {"morning_briefing": "08:00 daily"},
            "registered_cron_tasks": [],
            "status": "active",
        }

        result = api_proactive_status()
        assert result["status"] == "active"


# ---------------------------------------------------------------------------
# POST /api/proactive/morning-briefing
# ---------------------------------------------------------------------------


class TestMorningBriefingEndpoint:
    @patch("core.proactive.morning_briefing")
    def test_triggers_briefing(self, mock_briefing):
        from routes.sync import api_proactive_morning

        mock_briefing.return_value = "Good morning. 3 things on the agenda today."

        result = api_proactive_morning()
        assert result["triggered"] is True
        assert "morning" in result["text"].lower()


# ---------------------------------------------------------------------------
# POST /api/proactive/idle-check
# ---------------------------------------------------------------------------


class TestIdleCheckEndpoint:
    @patch("core.proactive.idle_check")
    def test_triggers_idle_check(self, mock_idle):
        from routes.sync import api_proactive_idle

        mock_idle.return_value = "You've been quiet for 45 minutes."

        result = api_proactive_idle(minutes_idle=30)
        assert result["triggered"] is True
        assert result["minutes_idle"] == 30

    @patch("core.proactive.idle_check")
    def test_not_idle_enough(self, mock_idle):
        from routes.sync import api_proactive_idle

        mock_idle.return_value = None

        result = api_proactive_idle(minutes_idle=10)
        assert result["triggered"] is False
