"""Log buffer with SSE streaming and file persistence."""

import time
import asyncio
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_FILE = LOG_DIR / "app.log"

def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

# In-memory ring buffer - last 500 entries
_buffer = deque(maxlen=500)
# List of SSE subscriber queues
_subscribers: list[asyncio.Queue] = []


def log(level, module, message):
    """Write a log entry and broadcast to SSE subscribers."""
    now = datetime.now(timezone.utc)
    entry = {
        "time": now.strftime("%H:%M:%S"),
        "level": level,
        "module": module,
        "message": message,
    }
    _buffer.append(entry)
    
    # Write to file
    try:
        _ensure_log_dir()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{level.upper():5}] [{module}] {message}\n")
    except Exception:
        pass
    
    for q in _subscribers:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass


def info(module, message):    log("info", module, message)
def warn(module, message):    log("warn", module, message)
def error(module, message):   log("error", module, message)
def debug(module, message):   log("debug", module, message)


def get_recent(limit=200):
    """Return the most recent log entries."""
    return list(_buffer)[-limit:]


def subscribe():
    """Create a new SSE subscriber queue. Returns (queue, unsubscribe_fn)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)

    def unsubscribe():
        if q in _subscribers:
            _subscribers.remove(q)

    return q, unsubscribe
