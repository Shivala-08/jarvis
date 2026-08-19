#!/bin/bash
# ADHD Co-Processor — Launch Server
cd "$(dirname "$0")"

echo "🧠 Starting ADHD Co-Processor..."
echo "   UI:       http://localhost:8080"
echo "   PWA:      http://localhost:8080/app"
echo "   API docs: http://localhost:8080/docs"
echo ""

# Kill any existing instance on port 8080
lsof -ti :8080 | xargs kill -9 2>/dev/null
sleep 1

# Start the server
exec uv run uvicorn main:app --host localhost --port 8080 --log-level info
