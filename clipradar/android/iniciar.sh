#!/data/data/com.termux/files/usr/bin/bash
# Arranca ClipRadar en el móvil. Cada servicio se reinicia solo si se cae.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
LOGS="$BACKEND/data/logs"
mkdir -p "$LOGS"
cd "$BACKEND" || exit 1

if [ -f "$LOGS/pids" ] && kill -0 $(head -n1 "$LOGS/pids") 2>/dev/null; then
  echo "ClipRadar ya está en marcha: abre http://localhost:8000"; exit 0
fi
[ -f .env ] || { echo "Falta la configuración. Ejecuta primero: bash $ROOT/android/instalar.sh"; exit 1; }

termux-wake-lock 2>/dev/null || true   # evita que Android duerma los procesos
redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --dir "$BACKEND/data" --save 60 1 >/dev/null

siempre() {  # siempre <nombre> <comando…>: relanza el proceso si termina
  local nombre="$1"; shift
  ( while true; do
      "$@" >> "$LOGS/$nombre.log" 2>&1
      echo "[$(date)] $nombre terminó; reinicio en 5 s" >> "$LOGS/$nombre.log"
      sleep 5
    done ) &
  echo $! >> "$LOGS/pids"
}

: > "$LOGS/pids"
siempre api uvicorn app.main:app --host 127.0.0.1 --port 8000
siempre monitor python -m app.workers.monitor
siempre worker rq worker render publish --url redis://127.0.0.1:6379/0 --worker-class rq.worker.SimpleWorker

sleep 4
echo "ClipRadar está en marcha."
echo "Panel: http://localhost:8000  (ábrelo en Chrome y elige 'Instalar aplicación')"
echo "Registros: $LOGS"
termux-open-url http://localhost:8000 2>/dev/null || true
