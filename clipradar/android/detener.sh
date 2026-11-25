#!/data/data/com.termux/files/usr/bin/bash
# Detiene ClipRadar en el móvil.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/backend/data/logs"
if [ -f "$LOGS/pids" ]; then
  while read -r pid; do kill "$pid" 2>/dev/null; done < "$LOGS/pids"
  rm -f "$LOGS/pids"
fi
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "app.workers.monitor" 2>/dev/null
pkill -f "rq worker render publish" 2>/dev/null
pkill -f "whisper-cli" 2>/dev/null
redis-cli -p 6379 shutdown >/dev/null 2>&1
termux-wake-unlock 2>/dev/null || true
echo "ClipRadar detenido."
