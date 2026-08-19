#!/usr/bin/env python3
"""Start the ADHD Co-Processor server as a background daemon and open the app."""
import multiprocessing
import os
import signal
import sys
import time
import subprocess
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
PORT = 8080

def run_server():
    """Run the FastAPI server (runs in a child process)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.chdir(str(PROJECT_ROOT))
    sys.path.insert(0, str(PROJECT_ROOT))

    import uvicorn
    from main import app
    uvicorn.run(app, host="localhost", port=PORT, log_level="warning", access_log=False)

def wait_for_server(timeout=20):
    """Wait until the server responds to /api/health."""
    for _ in range(timeout):
        try:
            r = urllib.request.urlopen(f"http://localhost:{PORT}/api/health", timeout=2)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def main():
    # Kill any existing instance
    os.system(f"lsof -ti :{PORT} | xargs kill -9 2>/dev/null")
    time.sleep(1)

    print("🧠 Starting ADHD Co-Processor backend...")
    server = multiprocessing.Process(target=run_server, daemon=True)
    server.start()

    if wait_for_server():
        print(f"✅ Backend running at http://localhost:{PORT}")
    else:
        print("⚠️  Server may not be fully ready, opening app anyway")

    # Open the Tauri desktop app
    app_path = PROJECT_ROOT / "ui/desktop-tauri/src-tauri/target/release/bundle/macos/ADHD Co-Processor.app"
    if app_path.exists():
        subprocess.Popen(["open", str(app_path)])
        print("🖥️  Desktop app opened")
    else:
        # Fallback: open the web dashboard in the default browser
        subprocess.Popen(["open", f"http://localhost:{PORT}/"])
        print("🌐 Opened dashboard in browser")

    print(f"\n  Server PID: {server.pid}")
    print(f"  Dashboard:  http://localhost:{PORT}/")
    print(f"  PWA:        http://localhost:{PORT}/app")
    print(f"  API docs:   http://localhost:{PORT}/docs")
    print(f"\n  Press Ctrl+C to stop.")

    try:
        server.join()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        server.terminate()
        server.join(timeout=5)
        if server.is_alive():
            server.kill()
        print("👋 Done.")

if __name__ == "__main__":
    main()
