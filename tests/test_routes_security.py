"""Tests for routes/security.py — sovereignty, network check, purge status.

Tests route handler functions directly (avoids TestClient startup issues).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/sovereignty/status
# ---------------------------------------------------------------------------


class TestSovereigntyStatusEndpoint:
    @patch("core.sovereignty.SovereigntyMonitor")
    def test_returns_status(self, MockMonitor):
        from routes.security import api_sovereignty_status

        mock_instance = MagicMock()
        mock_instance.snapshot.return_value = {
            "verdict": "clean",
            "violations": [],
            "allowed": [{"reason": "Tailscale peer (100.x.x.x)"}],
            "system": [],
            "total_connections": 5,
        }
        MockMonitor.return_value = mock_instance

        result = api_sovereignty_status()
        assert result["verdict"] == "clean"
        assert result["violations"] == 0
        assert result["tailscale"] is True

    @patch("core.sovereignty.SovereigntyMonitor")
    def test_violations_detected(self, MockMonitor):
        from routes.security import api_sovereignty_status

        mock_instance = MagicMock()
        mock_instance.snapshot.return_value = {
            "verdict": "violations",
            "violations": [{"remote_ip": "1.2.3.4", "remote_port": 443, "process": "python"}],
            "allowed": [],
            "system": [],
            "total_connections": 3,
        }
        MockMonitor.return_value = mock_instance

        result = api_sovereignty_status()
        assert result["verdict"] == "violations"


# ---------------------------------------------------------------------------
# GET /api/network-check
# ---------------------------------------------------------------------------


class TestNetworkCheckEndpoint:
    @patch("routes.security.subprocess.run")
    def test_clean_network(self, mock_run):
        from routes.security import api_network_check

        mock_result = MagicMock()
        mock_result.stdout = "COMMAND    PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\npython3  1234 user   5u  IPv4  12345      0t0  TCP 127.0.0.1:5000->127.0.0.1:6333 (ESTABLISHED)\n"
        mock_run.return_value = mock_result

        result = api_network_check()
        assert result["status"] == "clean"

    @patch("routes.security.subprocess.run")
    def test_violation_detected(self, mock_run):
        from routes.security import api_network_check

        mock_result = MagicMock()
        mock_result.stdout = "COMMAND    PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\npython3  1234 user   5u  IPv4  12345      0t0  TCP 10.0.0.1:5000->1.2.3.4:443 (ESTABLISHED)\n"
        mock_run.return_value = mock_result

        result = api_network_check()
        assert result["status"] == "violation"

    @patch("routes.security.subprocess.run", side_effect=FileNotFoundError)
    def test_lsof_not_available(self, mock_run):
        from routes.security import api_network_check

        result = api_network_check()
        assert result["status"] == "clean"
        assert result["total_system"] == 0


# ---------------------------------------------------------------------------
# GET /api/purge-status
# ---------------------------------------------------------------------------


class TestPurgeStatusEndpoint:
    @patch("routes.security.QdrantClient")
    @patch("routes.security.toml")
    def test_purged(self, mock_toml, MockQdrant):
        from routes.security import api_purge_status

        mock_toml.load.return_value = {"memory": {"collection_name": "adhd_memory"}}
        mock_client = MagicMock()
        mock_client.get_collections.return_value.collections = []
        MockQdrant.return_value = mock_client

        result = api_purge_status()
        assert result["purged"] is True

    @patch("routes.security.QdrantClient")
    @patch("routes.security.toml")
    def test_not_purged(self, mock_toml, MockQdrant):
        from routes.security import api_purge_status

        mock_toml.load.return_value = {"memory": {"collection_name": "adhd_memory"}}
        mock_collection = MagicMock()
        mock_collection.name = "adhd_memory"
        mock_client = MagicMock()
        mock_client.get_collections.return_value.collections = [mock_collection]
        MockQdrant.return_value = mock_client

        result = api_purge_status()
        assert result["purged"] is False
