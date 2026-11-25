# ClipRadar AI

Plataforma en la nube que vigila directos de YouTube (de creadores que te han autorizado), detecta con IA los
mejores momentos, los convierte en clips verticales 9:16 con subtítulos y los publica en TikTok con la API
oficial. Se maneja desde el móvil (PWA instalable) y trabaja 24/7 en un servidor: no necesitas tener el
ordenador ni el móvil encendidos.

---

## 1. Arquitectura

```
                ┌──────────────── Tu servidor (VPS) ─ docker compose ────────────────┐
 iPhone/Android │                                                                     │
  PWA ◄──HTTPS──┤ Caddy ──► api (FastAPI + panel)  ◄── SSE (tiempo real) ── Redis      │
                │              │  REST                                    ▲   ▲      │
                │              ▼                                          │   │      │
                │          PostgreSQL ◄────────────┬──────────────────────┘   │      │
                │                                  │                          │      │
 YouTube Live ──┼──► monitor (24/7, 1 instancia)   │       worker (cola RQ)  ─┘      │
 o HLS/RTMP     │    FFmpeg → segmentos 10 s       │       · revisión visual IA      │
                │    Whisper → texto + tiempos     │       · encuadre + 9:16 + subs  │
                │    señales volumen/escena        │       · publicación TikTok      │
                │    Claude → momentos ────────────┴──►  cola "render"/"publish"     │
                │                                                                     │
                │    Volumen /data: buffer de directos, cortes, clips (o S3/R2)        │
                └─────────────────────────────────────────────────────────────────────┘
                                         │                         │
                               API de Claude (Anthropic)   TikTok Content Posting API
```

### Tecnologías elegidas

| Pieza | Tecnología | Por qué |
|---|---|---|
| Backend / API | Python 3.12 + FastAPI | El mejor ecosistema para vídeo e IA (FFmpeg, Whisper, OpenCV) |
| Panel móvil | PWA sin compilación (HTML/CSS/JS módulos) servida por la API | Instalable en iPhone/Android, un solo servidor, sin tiendas de apps |
| Tiempo real | Redis pub/sub → Server-Sent Events | Funciona en Safari iOS, reconecta solo |
| Cola de trabajos | Redis + RQ (colas `render` y `publish`) | Simple, fiable, escalable con `--scale worker=N` |
| Monitoreo 24/7 | Servicio propio con supervisor + un hilo por transmisión | Reconexión automática, candado en Redis para no duplicarse |
| Captura | FFmpeg (segmentos de 10 s, buffer circular) + yt-dlp para la URL de YouTube | Estándar de la industria; corte exacto y sin recodificar en captura |
| Transcripción | faster-whisper local (palabras con marca de tiempo) | Sin coste por minuto, español, base de los subtítulos |
| Señales objetivas | FFmpeg `ebur128` (sonoridad) + detección de cambios de escena | Pistas de gritos, risas, golpes, cambios de plano |
| IA de momentos | Claude (`claude-opus-5-5`) con salida JSON estructurada | Entiende contexto, ironía y tensión, no solo palabras clave |
| Revisión visual | Claude con fotogramas reales del clip | Confirma la puntuación, escribe título/descripción/hashtags, filtra lo que incumpliría normas |
| Encuadre | OpenCV (caras + movimiento de boca) → trayectoria suave de cámara | Mantiene visible a quien habla; si no hay caras, vídeo completo sobre fondo desenfocado |
| Render | FFmpeg: H.264 1080×1920, AAC 48 kHz, −14 LUFS, `faststart` | Formato recomendado por TikTok |
| Subtítulos | ASS generado desde Whisper, estilos "palabra resaltada", "simple", "caja" | Sincronizados palabra a palabra, editables en el panel |
| Base de datos | PostgreSQL (SQLite en desarrollo) | Fiable y estándar |
| Almacenamiento | Volumen del servidor o S3 compatible (Cloudflare R2, Backblaze B2, AWS) | Escala barata para muchos clips |
| HTTPS | Caddy (Let's Encrypt automático) | Obligatorio para PWA y OAuth de TikTok |

### Flujo de un clip

1. **Captura**: FFmpeg lee la señal y guarda segmentos de 10 s (se conservan 40 min por defecto).
2. **Cada minuto**: se extrae el audio nuevo → Whisper (texto con tiempos por palabra) + medición de
   sonoridad y cambios de escena. Las frases con pico de volumen o cambio de plano se marcan.
3. **IA de contexto**: Claude lee los últimos 4 min de conversación anotada y propone momentos completos
   (planteamiento + remate) con categoría, puntuación 0-100, título, descripción y hashtags. Si un momento
   aún no ha terminado, espera a la siguiente pasada.
4. **Recorte inteligente**: el inicio y el final se ajustan a principio/fin de frase, se añade contexto
   antes y después sin cortar frases, se respetan duración mínima y máxima, y se descartan duplicados
   (por solapamiento temporal o por similitud de texto).
5. **Revisión visual**: Claude ve 3 fotogramas del clip y la transcripción, recalcula la puntuación,
   escribe los textos finales, marca los mejores para publicar y descarta lo que no cumpliría las normas
   de TikTok.
6. **Render 9:16** con encuadre inteligente y subtítulos.
7. **Publicación**: manual desde el editor, o automática si tú la activas (ver §5).

### Categorías detectadas
Pelea/discusión · Momento gracioso · Chiste/burla · Reacción inesperada · Revelación · Declaración
polémica · Sorpresa · Situación viral.

---

## 2. Estado por etapas

| Etapa | Estado | Qué incluye |
|---|---|---|
| 1. Arquitectura y estructura | ✅ | Este documento, docker-compose, servicios separados |
| 2. Interfaz móvil | ✅ | PWA: Dashboard, Monitor en vivo, Clips, Editor, Publicaciones, Ajustes, login |
| 3. Backend y almacenamiento | ✅ | API REST, PostgreSQL, Redis, colas, local o S3, tiempo real |
| 4. Fuente de YouTube Live | ✅ | Enlace de YouTube (yt-dlp) o señal HLS/RTMP/SRT; reconexión; detecta fin del directo |
| 5. Detección inteligente | ✅ | Whisper + señales + Claude con contexto + revisión visual |
| 6. Clips verticales | ✅ | 1080×1920 MP4, encuadre que sigue a quien habla, subtítulos editables, re-render |
| 7. TikTok oficial | ✅ | OAuth (Login Kit), creator_info, publicación directa o a bandeja, estado, auto-publicación con límites |

Lo que está **probado en este repositorio**: 11 pruebas automáticas (límites de escena, duplicados,
subtítulos, URLs, cifrado, plan de subida a TikTok, render 1080×1920) y una prueba de extremo a extremo
con una señal HLS en vivo: captura → segmentos → análisis → clip detectado → corte con contexto → render
1080×1920 H.264/AAC listo en el panel. En esa prueba Whisper y Claude se simularon porque el entorno de
desarrollo no tiene acceso a sus servicios; la llamada a Claude se verificó contra un servidor de prueba.
**Lo que debes validar tú en el servidor real**: la descarga del modelo Whisper, tu clave de Claude, la
conexión a YouTube desde la IP del servidor y la app de TikTok (ver §6).

---

## 3. Instalación en un servidor (24/7)

### Requisitos
- Un VPS Linux con Docker. Recomendado para 1-2 directos simultáneos: **4 vCPU, 8 GB RAM, 80 GB de disco**
  (p. ej. Hetzner CPX31, DigitalOcean 4 vCPU). Whisper y el render usan CPU.
- Un dominio (o subdominio) apuntando a la IP del servidor (registro DNS tipo A).
- Clave de API de Claude: <https://console.anthropic.com> → API Keys.
- App de TikTok para desarrolladores (ver §4).

### Pasos

```bash
# 1. En el servidor
git clone https://github.com/bmphotographys19-gif/clips.git
cd clips/clipradar
cp .env.example .env
nano .env            # rellena DOMAIN, PUBLIC_URL, ADMIN_PASSWORD, SECRET_KEY, POSTGRES_PASSWORD,
                     # DATABASE_URL (misma contraseña), ANTHROPIC_API_KEY, TIKTOK_*

# Genera secretos:  openssl rand -hex 32

# 2. Arrancar todo
docker compose up -d --build

# 3. Ver que todo funciona
docker compose ps
docker compose logs -f monitor worker
```

Abre `https://TU-DOMINIO` en el móvil, entra con `ADMIN_PASSWORD` y revisa **Ajustes → Estado del sistema**:
todo debe estar en verde.

### Instalar en el móvil
- **iPhone**: Safari → Compartir → *Añadir a pantalla de inicio*.
- **Android**: Chrome → ⋮ → *Instalar aplicación*.

### Actualizar
```bash
git pull && docker compose up -d --build
```

### Copias de seguridad
`docker compose exec postgres pg_dump -U clipradar clipradar > backup.sql` y el volumen `data`
(o usa S3/R2 para los clips).

---

## 4. Configurar TikTok

1. Entra en <https://developers.tiktok.com>, crea (o usa) tu app y añade los productos **Login Kit** y
   **Content Posting API** (activa *Direct Post* si quieres publicación directa).
2. Permisos (scopes): `user.info.basic`, `video.upload`, `video.publish`.
3. URL de redirección (elige una):
   - `https://TU-DOMINIO/api/tiktok/callback` (deja `TIKTOK_REDIRECT_URI` vacío), **o**
   - la página `callback.html` de este repositorio en GitHub Pages, que ya está verificada: pon su URL en
     `TIKTOK_REDIRECT_URI`. La página reenvía la autorización a tu servidor automáticamente.
4. Copia *Client key* y *Client secret* en `.env` y reinicia (`docker compose up -d`).
5. En el panel: **Publicar → Conectar con TikTok**.

**Importante (reglas de TikTok):**
- Mientras TikTok no audite tu app, la publicación directa solo puede ser **privada ("Solo yo")** y con
  pocos usuarios. El modo **Bandeja** envía el vídeo como borrador a tu app de TikTok y tú lo publicas
  allí: funciona sin auditoría y es el modo por defecto.
- Para publicar en público debes solicitar la auditoría de la app en el portal de TikTok.
- El panel muestra la cuenta de destino, obliga a elegir la privacidad (sin valor por defecto), respeta
  los ajustes de comentarios/dúo/stitch de la cuenta, incluye la divulgación de contenido comercial y
  pide tu confirmación explícita antes de cada publicación manual.

## 5. Publicación automática

Está **desactivada por defecto**. Solo publica si se cumplen todas estas condiciones:
1. Tienes una cuenta de TikTok conectada.
2. La activas en **Publicar → Publicación automática** y marcas la casilla de consentimiento.
3. La transmisión tiene activada la auto-publicación en sus ajustes.
4. El clip está recomendado por la IA, supera la puntuación mínima y pasó el filtro de normas.
5. No se supera el máximo diario ni el intervalo mínimo entre publicaciones.

Cada publicación incluye el crédito al creador según la plantilla configurada.

---

## 6. YouTube: fuentes autorizadas

- Usa ClipRadar solo con directos de creadores que te hayan dado permiso; el panel obliga a registrar
  esa autorización y el crédito.
- **Enlace de YouTube**: el servidor obtiene la señal con yt-dlp. YouTube a veces bloquea IPs de centros
  de datos; si ocurre (verás "Fuente no disponible" en el monitor), exporta las cookies de una cuenta en
  formato Netscape, súbelas al volumen y apunta `YTDLP_COOKIES_FILE` a ellas.
- **Alternativa más fiable y 100 % autorizada**: el creador envía su señal (re-stream desde OBS o
  Restream) a un servidor RTMP/HLS, y tú pegas esa URL en ClipRadar. No depende de YouTube.
- `YOUTUBE_API_KEY` (opcional) usa la YouTube Data API oficial para título, canal y estado del directo.

---

## 7. Costes orientativos

- **Servidor**: ~15-40 €/mes según tamaño.
- **Claude** (`claude-opus-5-5`, 4 $/M tokens de entrada y 20 $/M de salida): una pasada por minuto con
  ~4 min de transcripción. El gasto depende mucho de cuánto se hable; vigílalo en
  console.anthropic.com durante los primeros días. Para reducirlo puedes:
  subir `ANALYSIS_INTERVAL_SECONDS`, bajar `AI_EFFORT` a `low`, o cambiar `AI_MODEL` por
  `claude-sonnet-5-5` (2 $/10 $) o `claude-haiku-5-5` (0,10 $/0,50 $), con menor calidad de detección.
- **Whisper**: gratis (corre en tu servidor).
- **Almacenamiento**: un clip de 60 s ocupa ~15-25 MB.

---

## 8. Desarrollo local

```bash
cd clipradar/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
redis-server --daemonize yes
export ADMIN_PASSWORD=dev SECRET_KEY=dev ANTHROPIC_API_KEY=...
uvicorn app.main:app --reload            # API + panel en http://localhost:8000
python -m app.workers.monitor            # en otra terminal
rq worker render publish                 # en otra terminal
python -m pytest -q tests                # pruebas
```

### Estructura
```
clipradar/
├── docker-compose.yml, Caddyfile, .env.example
├── backend/
│   ├── app/
│   │   ├── main.py            API, auth, estadísticas, SSE, archivos, PWA
│   │   ├── config.py db.py security.py storage.py events.py appsettings.py
│   │   ├── routers/           streams.py clips.py tiktok.py
│   │   ├── services/          sources (YouTube/HLS) · media (FFmpeg) · transcribe (Whisper)
│   │   │                      detector (Claude) · boundaries (recorte) · reframe (encuadre)
│   │   │                      subtitles (ASS) · render (9:16) · tiktok (OAuth + publicación)
│   │   └── workers/           monitor.py (24/7) · tasks.py (cola render/publicación)
│   └── tests/
└── frontend/                  PWA: index.html, sw.js, manifest, css/, js/views/
```

### API
Documentación interactiva en `https://TU-DOMINIO/api/docs` (requiere sesión).
