#!/data/data/com.termux/files/usr/bin/bash
# ClipRadar AI — instalador para Android (Termux).
# Uso:  bash ~/clips/clipradar/android/instalar.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
WHISPER_DIR="$HOME/whisper.cpp"
verde() { printf "\n\033[1;32m==> %s\033[0m\n" "$1"; }
aviso() { printf "\033[1;33m%s\033[0m\n" "$1"; }

if [ -z "${PREFIX:-}" ] || [ ! -d "/data/data/com.termux" ]; then
  echo "Este instalador es para la app Termux en Android."; exit 1
fi

verde "1/6 Actualizando Termux e instalando programas (puede tardar 5-10 min)"
pkg update -y && pkg upgrade -y
pkg install -y python git ffmpeg redis rust clang cmake make binutils fontconfig
pkg install -y python-cryptography || NEED_CRYPTO=1
pkg install -y python-numpy || true
pkg install -y ttf-dejavu || aviso "Fuente DejaVu no disponible: se usará la del sistema."
pkg install -y opencv-python || aviso "OpenCV no disponible: los clips usarán encuadre completo con fondo desenfocado."

verde "2/6 Instalando ClipRadar (compila algunas piezas: 10-30 min la primera vez)"
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
export CARGO_BUILD_JOBS=2
pip install -r "$BACKEND/requirements-android.txt"
[ "${NEED_CRYPTO:-0}" = "1" ] && pip install cryptography
ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass " || aviso "Tu FFmpeg no tiene subtítulos (libass): los clips se generarán sin subtítulos."

verde "3/6 Compilando el transcriptor (whisper.cpp)"
if [ ! -d "$WHISPER_DIR" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
fi
cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF >/dev/null
cmake --build "$WHISPER_DIR/build" -j4 --target whisper-cli
WHISPER_BIN="$WHISPER_DIR/build/bin/whisper-cli"
[ -x "$WHISPER_BIN" ] || { echo "No se pudo compilar whisper.cpp"; exit 1; }

verde "4/6 Modelo de transcripción"
echo "  1) base  — 150 MB, rápido (recomendado para la mayoría de móviles)"
echo "  2) small — 490 MB, más preciso (móviles potentes, 8 GB de RAM o más)"
read -rp "Elige 1 o 2 [1]: " OPC
MODELO="base"; [ "${OPC:-1}" = "2" ] && MODELO="small"
MODEL_FILE="$WHISPER_DIR/models/ggml-$MODELO.bin"
if [ ! -f "$MODEL_FILE" ]; then
  curl -L --fail -o "$MODEL_FILE" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODELO.bin"
fi

verde "5/6 Configuración"
ENV_FILE="$BACKEND/.env"
if [ -f "$ENV_FILE" ]; then
  aviso "Ya existe una configuración ($ENV_FILE). Se conserva. Bórrala si quieres empezar de cero."
else
  while true; do
    read -rsp "Crea una contraseña para el panel: " PASS; echo
    [ ${#PASS} -ge 6 ] && [[ "$PASS" != *"'"* ]] && break
    aviso "Mínimo 6 caracteres y sin comillas simples."
  done
  read -rsp "Pega tu clave de API de Claude (console.anthropic.com; vacío = sin IA por ahora): " AKEY; echo
  echo "Modelo de IA para detectar momentos (coste aprox. por hora de directo analizada):"
  echo "  1) Claude Haiku 5.5  — el más barato (~0,05 \$/h)"
  echo "  2) Claude Sonnet 5.5 — intermedio (~1 \$/h)"
  echo "  3) Claude Opus 5.5   — el mejor detectando (~2 \$/h)"
  while true; do
    read -rp "Elige 1, 2 o 3: " M
    case "$M" in 1) AIMODEL=claude-haiku-5-5; break;; 2) AIMODEL=claude-sonnet-5-5; break;;
                 3) AIMODEL=claude-opus-5-5; break;; *) aviso "Escribe 1, 2 o 3.";; esac
  done
  read -rp "TikTok Client Key (vacío = configurar más tarde): " TKEY
  TSECRET=""
  if [ -n "$TKEY" ]; then read -rsp "TikTok Client Secret: " TSECRET; echo; fi
  SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
  cat > "$ENV_FILE" <<CONF
PUBLIC_URL=http://localhost:8000
ADMIN_PASSWORD='$PASS'
SECRET_KEY=$SECRET
DATABASE_URL=sqlite:///./data/clipradar.db
REDIS_URL=redis://127.0.0.1:6379/0
DATA_DIR=./data
STORAGE_BACKEND=local

ANTHROPIC_API_KEY=$AKEY
AI_MODEL=$AIMODEL
AI_EFFORT=low

TRANSCRIBE_BACKEND=whispercpp
WHISPERCPP_BIN=$WHISPER_BIN
WHISPERCPP_MODEL=$MODEL_FILE
WHISPER_LANGUAGE=es

TIKTOK_CLIENT_KEY=$TKEY
TIKTOK_CLIENT_SECRET=$TSECRET
# Página ya registrada en TikTok; reenvía la autorización a este móvil.
TIKTOK_REDIRECT_URI=https://bmphotographys19-gif.github.io/clips/callback.html

# Ajustes ligeros para el móvil
MAX_CAPTURE_HEIGHT=720
RENDER_PRESET=superfast
AI_VISION_FRAMES=2
BUFFER_RETENTION_MINUTES=30
CONF
  chmod 600 "$ENV_FILE"
fi

verde "6/6 Comprobación"
cd "$BACKEND"
python -c "import app.main, app.workers.monitor, app.workers.tasks; print('ClipRadar instalado correctamente')"
python -c "import cv2" 2>/dev/null && echo "Encuadre inteligente: activado" || aviso "Encuadre inteligente: no disponible (OpenCV)."

verde "Listo"
echo "Para arrancar ClipRadar:   bash $ROOT/android/iniciar.sh"
echo "Para detenerlo:            bash $ROOT/android/detener.sh"
