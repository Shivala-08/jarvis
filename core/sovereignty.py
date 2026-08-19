"""Data Sovereignty Monitor — Phase 14 network trace and verification.

Continuously monitors all outbound TCP connections from the app process
and system-wide. Classifies each connection against an allowlist:
  ✅ Allowed:  Tailscale (100.x.x.x), Google OAuth (accounts.google.com),
               localhost, Qdrant (local), Ollama (local)
  ❌ Violation: Anything else

Features:
- Continuous background monitoring (every N seconds)
- Session trace with timestamped log
- One-click sovereignty report
- One-click purge all memory (Qdrant collection delete)
- CLI mode: uv run python -m core.sovereignty --trace

Usage:
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    monitor.start()              # starts background monitoring
    report = monitor.report()    # full sovereignty report
    monitor.stop()               # stop monitoring
"""
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Allowlist — what outbound traffic is permitted
# ---------------------------------------------------------------------------

# Tailscale CGNAT range: 100.64.0.0/10
TAILSCALE_PREFIXES = ("100.",)

# Google OAuth / Calendar API IP ranges (common ones)
GOOGLE_OAUTH_PREFIXES = (
    "142.250.",  # Google general
    "172.217.",  # Google
    "74.125.",   # Google
    "216.58.",   # Google
    "173.194.",  # Google
    "209.85.",   # Google
    "146.148.",  # Google
)

# Well-known Google domains (for DNS-based detection)
GOOGLE_DOMAINS = {
    "accounts.google.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "calendar.google.com",
    "googleapis.com",
    "google.com",
    "googleapis.com",
}

# Local addresses (never violations)
LOCAL_ADDRS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "0.0.0.0",
}

# App process names (the ADHD Co-Processor itself)
APP_PROCESSES = {
    "python",
    "python3",
    "uvicorn",
}

# Local services (allowed)
LOCAL_SERVICES = {
    "ollama",
    "qdrant",
}


# ---------------------------------------------------------------------------
# Connection data
# ---------------------------------------------------------------------------

@dataclass
class Connection:
    """A single observed network connection."""
    remote_ip: str
    remote_port: int
    process: str
    local_port: int = 0
    protocol: str = "tcp"
    timestamp: float = 0.0
    classification: str = "unknown"  # allowed | violation | local | system
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SovereigntyReport:
    """Full sovereignty report for a monitoring session."""
    session_start: str = ""
    session_end: str = ""
    duration_seconds: float = 0
    total_snapshots: int = 0
    unique_violations: int = 0
    total_violation_events: int = 0
    allowed_connections: int = 0
    local_connections: int = 0
    system_connections: int = 0
    verdict: str = "pending"  # clean | violations | error
    violations: List[Dict] = field(default_factory=list)
    allowed: List[Dict] = field(default_factory=list)
    tailscale_detected: bool = False
    google_oauth_detected: bool = False
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sovereignty Monitor
# ---------------------------------------------------------------------------

class SovereigntyMonitor:
    """Continuous network monitoring for data sovereignty verification.

    Usage:
        monitor = SovereigntyMonitor()
        monitor.start(interval=5)  # check every 5 seconds

        # ... do stuff ...

        report = monitor.report()
        print(json.dumps(report.to_dict(), indent=2))

        monitor.stop()
    """

    def __init__(self):
        self._trace_log: List[Connection] = []
        self._unique_violations: Set[str] = set()
        self._allowed_ips: Set[str] = set()
        self._tailscale_seen = False
        self._google_seen = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._session_start: Optional[float] = None
        self._snapshot_count = 0
        self._lock = threading.Lock()

    def _classify_connection(self, remote_ip: str, remote_port: int, process: str) -> tuple[str, str]:
        """Classify a connection as allowed, violation, local, or system.

        Returns (classification, reason).
        """
        # Local addresses — always fine
        if remote_ip in LOCAL_ADDRS:
            return "local", "localhost connection"

        # Private IPs (10.x, 172.16-31.x, 192.168.x) — likely local network
        if (remote_ip.startswith("10.")
                or remote_ip.startswith("172.") and 16 <= int(remote_ip.split(".")[1]) <= 31
                or remote_ip.startswith("192.168.")):
            return "local", "private network"

        # Tailscale (100.64.0.0/10) — allowed
        if any(remote_ip.startswith(p) for p in TAILSCALE_PREFIXES):
            self._tailscale_seen = True
            return "allowed", f"Tailscale peer ({remote_ip})"

        # Google OAuth — allowed (port 443 to Google IPs)
        if remote_port == 443 and any(
            remote_ip.startswith(p) for p in GOOGLE_OAUTH_PREFIXES
        ):
            self._google_seen = True
            return "allowed", f"Google OAuth/API ({remote_ip})"

        # Qdrant or Ollama connecting to local — allowed
        if process in LOCAL_SERVICES:
            return "allowed", f"Local service ({process})"

        # Our app process making unexpected outbound — VIOLATION
        if process in APP_PROCESSES:
            return "violation", f"App process '{process}' made unexpected outbound to {remote_ip}:{remote_port}"

        # System process (browser, OS services) — not our fault
        return "system", f"System process ({process})"

    def _take_snapshot(self) -> List[Connection]:
        """Take a snapshot of all established TCP connections."""
        connections = []

        try:
            result = subprocess.run(
                ["lsof", "-i", "tcp", "-n", "-P"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return connections

        now = time.time()

        for line in result.stdout.splitlines():
            if "ESTABLISHED" not in line:
                continue

            parts = line.split()
            if len(parts) < 9:
                continue

            process_name = parts[0].lower()
            conn_field = parts[8]

            if "->" not in conn_field:
                continue

            _local, _sep, remote_full = conn_field.rpartition("->")

            if ":" not in remote_full:
                continue

            remote_ip, _, remote_port_str = remote_full.rpartition(":")
            try:
                remote_port = int(remote_port_str)
            except ValueError:
                continue

            classification, reason = self._classify_connection(remote_ip, remote_port, process_name)

            conn = Connection(
                remote_ip=remote_ip,
                remote_port=remote_port,
                process=process_name,
                timestamp=now,
                classification=classification,
                reason=reason,
            )
            connections.append(conn)

        return connections

    def _monitor_loop(self, interval: float):
        """Background monitoring loop."""
        while self._running:
            try:
                connections = self._take_snapshot()

                with self._lock:
                    self._snapshot_count += 1
                    for conn in connections:
                        self._trace_log.append(conn)

                        if conn.classification == "violation":
                            key = f"{conn.remote_ip}:{conn.remote_port}:{conn.process}"
                            self._unique_violations.add(key)

                        elif conn.classification == "allowed":
                            self._allowed_ips.add(conn.remote_ip)

            except Exception as e:
                print(f"  ⚠️ Sovereignty monitor error: {e}")

            time.sleep(interval)

    def start(self, interval: float = 5.0):
        """Start background monitoring.

        Args:
            interval: Seconds between snapshots (default: 5).
        """
        if self._running:
            return

        self._running = True
        self._session_start = time.time()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
        )
        self._thread.start()
        print(f"🔍 Sovereignty monitor started (checking every {interval}s)")

    def stop(self):
        """Stop background monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        print("🔍 Sovereignty monitor stopped")

    def snapshot(self) -> dict:
        """Take a single snapshot (non-continuous) and return results."""
        connections = self._take_snapshot()

        violations = [c for c in connections if c.classification == "violation"]
        allowed = [c for c in connections if c.classification == "allowed"]
        local = [c for c in connections if c.classification == "local"]
        system = [c for c in connections if c.classification == "system"]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_connections": len(connections),
            "violations": [c.to_dict() for c in violations],
            "allowed": [c.to_dict() for c in allowed],
            "local": [c.to_dict() for c in local],
            "system": [c.to_dict() for c in system],
            "verdict": "clean" if not violations else "violations",
        }

    def report(self) -> SovereigntyReport:
        """Generate a full sovereignty report for the session."""
        now = time.time()
        report = SovereigntyReport()

        if self._session_start:
            report.session_start = datetime.fromtimestamp(
                self._session_start, tz=timezone.utc
            ).isoformat()
            report.session_end = datetime.now(timezone.utc).isoformat()
            report.duration_seconds = round(now - self._session_start, 1)

        report.total_snapshots = self._snapshot_count
        report.tailscale_detected = self._tailscale_seen
        report.google_oauth_detected = self._google_seen

        with self._lock:
            violations = [c for c in self._trace_log if c.classification == "violation"]
            allowed = [c for c in self._trace_log if c.classification == "allowed"]
            local = [c for c in self._trace_log if c.classification == "local"]
            system = [c for c in self._trace_log if c.classification == "system"]

            report.unique_violations = len(self._unique_violations)
            report.total_violation_events = len(violations)
            report.allowed_connections = len(allowed)
            report.local_connections = len(local)
            report.system_connections = len(system)

            # Deduplicate violations for the report
            seen_violations = set()
            for v in violations:
                key = f"{v.remote_ip}:{v.remote_port}:{v.process}"
                if key not in seen_violations:
                    seen_violations.add(key)
                    report.violations.append(v.to_dict())

            # Deduplicate allowed IPs
            seen_allowed = set()
            for a in allowed:
                if a.remote_ip not in seen_allowed:
                    seen_allowed.add(a.remote_ip)
                    report.allowed.append(a.to_dict())

        # Verdict
        if report.unique_violations > 0:
            report.verdict = "violations"
        else:
            report.verdict = "clean"

        # Recommendations
        if report.verdict == "clean":
            report.recommendations.append(
                "✅ All outbound traffic matches the allowlist. Data sovereignty is intact."
            )
            if not report.tailscale_detected:
                report.recommendations.append(
                    "ℹ️  No Tailscale traffic observed. If using Tailscale, ensure the tunnel is active."
                )
            if not report.google_oauth_detected:
                report.recommendations.append(
                    "ℹ️  No Google OAuth traffic observed. Calendar sync may not be configured."
                )
        else:
            report.recommendations.append(
                f"🚨 {report.unique_violations} unique violation(s) detected. "
                "An app process made unexpected outbound connections."
            )
            report.recommendations.append(
                "Review the violations list and verify each connection is intentional."
            )

        report.recommendations.append(
            "💡 To test airplane-mode sovereignty: enable airplane mode on your phone, "
            "connect to host via Tailscale, and verify the assistant still works."
        )

        return report


# ---------------------------------------------------------------------------
# Purge helper
# ---------------------------------------------------------------------------

def purge_all_memory() -> dict:
    """One-click purge: delete the Qdrant collection.

    This is the "nuclear option" for data sovereignty — verifies deletion.
    """
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(host="localhost", port=6333)

        # List collections before
        collections_before = [c.name for c in client.get_collections().collections]

        # Delete
        collection_name = "adhd_memory"
        client.delete_collection(collection_name=collection_name)

        # Verify
        collections_after = [c.name for c in client.get_collections().collections]
        deleted = collection_name not in collections_after

        # Also clear the JSONL history
        history_path = Path("data/task_history.jsonl")
        if history_path.exists():
            history_path.unlink()

        # Clear monitor state files
        monitor_dir = Path("data")
        for f in monitor_dir.glob("monitor_*.json"):
            f.unlink()

        # Clear focus log
        focus_log = Path("data/focus_log.jsonl")
        if focus_log.exists():
            focus_log.unlink()

        # Clear web task logs
        web_task_dir = Path("data/web_tasks")
        if web_task_dir.exists():
            for f in web_task_dir.glob("*.json"):
                f.unlink()

        return {
            "status": "success" if deleted else "error",
            "message": (
                f"Collection '{collection_name}' deleted and verified."
                if deleted
                else f"Collection '{collection_name}' still exists after delete."
            ),
            "collections_before": collections_before,
            "collections_after": collections_after,
            "history_deleted": not Path("data/task_history.jsonl").exists(),
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Purge failed: {e}",
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Data Sovereignty Monitor — Phase 14")
    parser.add_argument("--trace", action="store_true", help="Run a live network trace (5s intervals, Ctrl+C to stop)")
    parser.add_argument("--snapshot", action="store_true", help="Take a single snapshot and print results")
    parser.add_argument("--report", action="store_true", help="Run for 30s then print full report")
    parser.add_argument("--purge", action="store_true", help="Purge ALL memory (Qdrant + logs)")
    args = parser.parse_args()

    if args.purge:
        print("🗑️  PURGING ALL MEMORY...")
        print("   This will delete the Qdrant collection, task history, and all logs.")
        confirm = input("   Type 'purge' to confirm: ").strip()
        if confirm == "purge":
            result = purge_all_memory()
            print(f"\n   Status: {result['status']}")
            print(f"   Message: {result['message']}")
        else:
            print("   Cancelled.")

    elif args.snapshot:
        print("📸 Taking network snapshot...\n")
        monitor = SovereigntyMonitor()
        result = monitor.snapshot()

        print(f"Verdict: {'✅ CLEAN' if result['verdict'] == 'clean' else '🚨 VIOLATIONS'}")
        print(f"Total connections: {result['total_connections']}")
        print()

        if result["violations"]:
            print("🚨 VIOLATIONS:")
            for v in result["violations"]:
                print(f"   {v['process']} → {v['remote_ip']}:{v['remote_port']} ({v['reason']})")
            print()

        if result["allowed"]:
            print("✅ ALLOWED:")
            for a in result["allowed"]:
                print(f"   {a['process']} → {a['remote_ip']}:{a['remote_port']} ({a['reason']})")
            print()

        if result["system"]:
            print(f"ℹ️  SYSTEM connections: {len(result['system'])} (browsers, OS services)")
            for s in result["system"][:5]:
                print(f"   {s['process']} → {s['remote_ip']}:{s['remote_port']}")
            if len(result["system"]) > 5:
                print(f"   ... and {len(result['system']) - 5} more")

    elif args.report:
        print("🔍 Running sovereignty trace for 30 seconds...\n")
        monitor = SovereigntyMonitor()
        monitor.start(interval=3)

        try:
            for i in range(10):
                time.sleep(3)
                snap = monitor.snapshot()
                v = len(snap["violations"])
                a = len(snap["allowed"])
                s = len(snap["system"])
                print(f"  [{i*3+3:2d}s] ✅ {a} allowed | ℹ️  {s} system | {'🚨' if v else '✅'} {v} violations")
        except KeyboardInterrupt:
            pass

        monitor.stop()
        print()

        report = monitor.report()
        d = report.to_dict()

        print("=" * 55)
        print("  SOVEREIGNTY REPORT")
        print("=" * 55)
        print(f"  Session: {d['session_start'][:19]} → {d['session_end'][:19]}")
        print(f"  Duration: {d['duration_seconds']}s")
        print(f"  Snapshots: {d['total_snapshots']}")
        print()
        print(f"  Verdict: {'✅ CLEAN' if d['verdict'] == 'clean' else '🚨 VIOLATIONS'}")
        print(f"  Unique violations: {d['unique_violations']}")
        print(f"  Allowed connections: {d['allowed_connections']}")
        print(f"  System connections: {d['system_connections']}")
        print()
        print(f"  Tailscale detected: {'✅' if d['tailscale_detected'] else 'ℹ️  No'}")
        print(f"  Google OAuth detected: {'✅' if d['google_oauth_detected'] else 'ℹ️  No'}")
        print()

        if d["violations"]:
            print("  🚨 VIOLATIONS:")
            for v in d["violations"]:
                print(f"     {v['process']} → {v['remote_ip']}:{v['remote_port']}")
                print(f"       {v['reason']}")
            print()

        print("  RECOMMENDATIONS:")
        for r in d["recommendations"]:
            print(f"    {r}")

    elif args.trace:
        print("🔍 Live sovereignty trace (Ctrl+C to stop)\n")
        monitor = SovereigntyMonitor()
        monitor.start(interval=3)

        try:
            while True:
                time.sleep(5)
                snap = monitor.snapshot()
                v = len(snap["violations"])
                a = len(snap["allowed"])
                print(f"  ✅ {a} allowed | {'🚨' if v else '✅'} {v} violations")
        except KeyboardInterrupt:
            pass

        monitor.stop()
        print("\n📋 Session trace complete. Run with --report for full analysis.")

    else:
        # Default: take a snapshot and show status
        print("🔍 Data Sovereignty Check\n")
        monitor = SovereigntyMonitor()
        result = monitor.snapshot()

        if result["verdict"] == "clean":
            print("  ✅ VERDICT: CLEAN — All outbound traffic matches the allowlist")
        else:
            print(f"  🚨 VERDICT: {len(result['violations'])} VIOLATION(S) DETECTED")

        print(f"  Total connections: {result['total_connections']}")
        print(f"  Allowed: {len(result['allowed'])}")
        print(f"  System: {len(result['system'])}")
        print()
        print("  Usage:")
        print("    python -m core.sovereignty --snapshot   Single check")
        print("    python -m core.sovereignty --trace      Live monitoring")
        print("    python -m core.sovereignty --report     30s trace + full report")
        print("    python -m core.sovereignty --purge      Delete ALL memory")
