"""Fuentes de vídeo.

Tipos admitidos:
- ``youtube``: enlace de un directo de YouTube. La URL de reproducción (HLS) se
  obtiene con yt-dlp. Úsalo solo con canales que te hayan autorizado a hacer
  clips (el panel exige registrar esa autorización).
- ``hls``: cualquier URL HLS/RTMP/SRT/HTTP que FFmpeg pueda leer (por ejemplo,
  un re-stream que el creador envía a tu servidor RTMP).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import get_settings

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class SourceError(RuntimeError):
    pass


@dataclass
class SourceMeta:
    title: str = ""
    channel: str = ""
    is_live: bool | None = None
    live_status: str = ""  # live | upcoming | none | was_live


def youtube_id(url: str) -> str | None:
    url = url.strip()
    if _YT_ID.match(url):
        return url
    try:
        u = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
        return cand if _YT_ID.match(cand) else None
    if host.endswith("youtube.com"):
        if u.path == "/watch":
            cand = parse_qs(u.query).get("v", [""])[0]
            return cand if _YT_ID.match(cand) else None
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("live", "shorts", "embed", "v"):
            return parts[1] if _YT_ID.match(parts[1]) else None
    return None


def classify(url: str) -> tuple[str, str | None]:
    vid = youtube_id(url)
    if vid:
        return "youtube", vid
    scheme = urlparse(url).scheme.lower()
    if scheme in ("http", "https", "rtmp", "rtmps", "srt", "rtsp"):
        return "hls", None
    raise SourceError("URL no reconocida: usa un enlace de YouTube o una URL HLS/RTMP/SRT")


def _ytdlp_base() -> list[str]:
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist"]
    cookies = get_settings().ytdlp_cookies_file
    if cookies:
        cmd += ["--cookies", cookies]
    return cmd


def youtube_metadata(video_id: str) -> SourceMeta:
    """Metadatos del directo: YouTube Data API v3 si hay clave; si no, yt-dlp."""
    key = get_settings().youtube_api_key
    if key:
        r = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,liveStreamingDetails", "id": video_id, "key": key},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            raise SourceError("YouTube no encontró ese vídeo")
        sn = items[0]["snippet"]
        lbc = sn.get("liveBroadcastContent", "none")
        return SourceMeta(sn.get("title", ""), sn.get("channelTitle", ""), lbc == "live", lbc)
    proc = subprocess.run(
        [*_ytdlp_base(), "-J", "--skip-download", f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=90,
    )
    if proc.returncode != 0:
        raise SourceError("yt-dlp no pudo leer el vídeo: " + proc.stderr.strip()[-400:])
    data = json.loads(proc.stdout)
    status = data.get("live_status") or ("is_live" if data.get("is_live") else "none")
    return SourceMeta(
        data.get("title", ""), data.get("channel") or data.get("uploader") or "",
        status == "is_live", {"is_live": "live", "is_upcoming": "upcoming"}.get(status, status),
    )


def resolve_playback_url(source_type: str, source_url: str, video_id: str | None) -> str:
    """Devuelve la URL que FFmpeg leerá. Para YouTube caduca: se resuelve en cada reconexión."""
    if source_type != "youtube":
        return source_url
    max_h = get_settings().max_capture_height
    proc = subprocess.run(
        [*_ytdlp_base(), "-g", "-f", f"best[height<={max_h}][protocol^=m3u8]/best[height<={max_h}]/best",
         f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=90,
    )
    if proc.returncode != 0:
        msg = proc.stderr.strip()[-500:]
        if "not currently live" in msg or "will begin" in msg or "Premieres" in msg:
            raise SourceError("El directo aún no ha empezado o ya terminó")
        raise SourceError("No se pudo obtener la señal de YouTube: " + msg)
    url = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if not url:
        raise SourceError("yt-dlp no devolvió ninguna URL")
    return url
