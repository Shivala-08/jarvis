#!/bin/bash
# Launch the ADHD Co-Processor desktop app
# Starts the backend server, then opens the Tauri native app.

set -e

echo "🧠 ADHD Co-Processor — Desktop Launcher"
echo ""

# Kill any existing instances
pkill -f "adhd-copilot-desktop" 2>/dev/null || true
pkill -f "uvicorn.*main" 2>/dev/null || true
sleep 1

# Start the backend server
echo "Starting backend server..."
uv run python main.py --ui &
SERVER_PID=$!

# Wait for server to be ready
for i in $(seq 1 20); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        echo "✅ Backend ready (PID: $SERVER_PID)"
        break
    fi
    sleep 1
done

# Launch the Tauri app
echo "Launching desktop app..."
open "ui/desktop-tauri/src-tauri/target/release/bundle/macos/ADHD Co-Processor.app"

echo ""
echo "Both running. Close this window or press Ctrl+C to stop the server."
echo ""

# Keep the server alive
wait $SERVER_PID
