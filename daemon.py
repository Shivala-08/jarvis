#!/usr/bin/env python3
"""Start ADHD Co-Processor as a daemon."""
import os, sys, time

# Double-fork daemon
if os.fork() > 0:
    # Parent: print PID and exit
    sys.exit(0)

os.setsid()
if os.fork() > 0:
    sys.exit(0)

# Redirect stdio
sys.stdin = open(os.devnull)
sys.stdout = open('/tmp/adhd-server.log', 'w')
sys.stderr = sys.stdout

os.chdir('/Users/pallav/Downloads/jarvis-ai-assistant')
sys.path.insert(0, '.')

import uvicorn
from main import app
uvicorn.run(app, host='localhost', port=8080, log_level='warning')
