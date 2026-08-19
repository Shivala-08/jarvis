"""Desktop Shell — native macOS window wrapping the ADHD Co-Processor.

Uses pywebview to create a native desktop window that loads the FastAPI
backend's web UI. This gives you a Tauri-like experience without Rust.

Features:
- Native window with custom title bar
- WebSocket connection to FastAPI backend
- Push-to-talk voice via browser MediaRecorder
- All views (braindump, schedule, study, code, web, skills, memories, dashboard)

Usage:
    uv run python -m remote.desktop_shell
    uv run python main.py --desktop
"""
import argparse
import multiprocessing
import os
import signal
import sys
import time
import webbrowser
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def start_fastapi_server(port: int = 8080):
    """Start the FastAPI server in a subprocess."""
    import uvicorn
    from main import app

    # Ignore SIGINT in the child — parent handles shutdown
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    uvicorn.run(
        app,
        host="localhost",
        port=port,
        log_level="warning",  # Keep it quiet
        access_log=False,
    )


def launch_desktop_window(url: str, width: int = 1100, height: int = 720):
    """Launch the pywebview desktop window."""
    try:
        import webview
    except ImportError:
        print("❌ pywebview not installed. Run: uv add pywebview")
        sys.exit(1)

    # Create the window
    window = webview.create_window(
        title="🧠 ADHD Co-Processor",
        url=url,
        width=width,
        height=height,
        min_size=(800, 500),
        resizable=True,
        text_select=True,
        # macOS specific
        title_bar_color="#161b22",
        background_color="#0d1117",
    )

    # Start pywebview (blocks until window is closed)
    webview.start(debug=("--debug" in sys.argv))


def main():
    parser = argparse.ArgumentParser(description="ADHD Co-Processor Desktop Shell")
    parser.add_argument("--port", type=int, default=8080, help="FastAPI port (default: 8080)")
    parser.add_argument("--width", type=int, default=1100, help="Window width (default: 1100)")
    parser.add_argument("--height", type=int, default=720, help="Window height (default: 720)")
    parser.add_argument("--debug", action="store_true", help="Enable pywebview debug mode")
    parser.add_argument("--no-server", action="store_true", help="Don't start FastAPI (assume it's running)")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}/"

    server_process = None

    if not args.no_server:
        # Check if server is already running
        import urllib.request
        try:
            urllib.request.urlopen(f"http://localhost:{args.port}/api/health", timeout=2)
            print(f"✅ FastAPI already running on port {args.port}")
        except Exception:
            print(f"🚀 Starting FastAPI server on port {args.port}...")
            server_process = multiprocessing.Process(
                target=start_fastapi_server,
                args=(args.port,),
                daemon=True,
            )
            server_process.start()

            # Wait for server to be ready
            for i in range(30):
                try:
                    urllib.request.urlopen(f"http://localhost:{args.port}/api/health", timeout=1)
                    print(f"✅ Server ready")
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                print("⚠️  Server may not be fully ready, opening window anyway")

    print(f"🖥️  Opening desktop window → {url}")
    print(f"   Close the window to quit.")

    try:
        launch_desktop_window(url, args.width, args.height)
    except KeyboardInterrupt:
        pass
    finally:
        if server_process:
            print("\n🛑 Shutting down server...")
            server_process.terminate()
            server_process.join(timeout=5)
            if server_process.is_alive():
                server_process.kill()


if __name__ == "__main__":
    main()
