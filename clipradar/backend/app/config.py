"""Configuración central de ClipRadar AI.

Todos los valores se leen de variables de entorno (o de un archivo .env).
Nada se inventa: si falta una credencial, la función correspondiente se
muestra como "no configurada" en el panel y no se ejecuta.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General ---
    public_url: str = "http://localhost:8000"  # URL pública HTTPS del panel (sin barra final)
    secret_key: str = "change-me"  # firma de sesiones y cifrado de tokens
    admin_password: str = ""  # contraseña del panel (obligatoria)
    session_days: int = 30

    # --- Infraestructura ---
    database_url: str = "sqlite:///./data/clipradar.db"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("./data")

    # --- Almacenamiento de clips ---
    storage_backend: str = "local"  # local | s3
    s3_bucket: str = ""
    s3_endpoint_url: str = ""  # p.ej. Cloudflare R2 / Backblaze / MinIO
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # --- Captura ---
    segment_seconds: int = 10
    analysis_interval_seconds: int = 60  # cada cuánto se analiza audio nuevo
    analysis_window_seconds: int = 240  # contexto que ve la IA en cada pasada
    buffer_retention_minutes: int = 40  # segmentos de vídeo que se conservan
    max_capture_height: int = 1080
    youtube_api_key: str = ""  # opcional: metadatos con YouTube Data API v3
    ytdlp_cookies_file: str = ""  # opcional: cookies para yt-dlp

    # --- Transcripción ---
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3
    whisper_compute_type: str = "int8"
    whisper_language: str = "es"

    # --- IA (Claude) ---
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_vision_frames: int = 3

    # --- Clips ---
    clip_min_seconds: int = 15
    clip_max_seconds: int = 90
    context_before_seconds: float = 4.0
    context_after_seconds: float = 2.5
    default_min_score: int = 60
    render_preset: str = "veryfast"
    render_crf: int = 20

    # --- TikTok ---
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    # Si se deja vacío se usa {public_url}/api/tiktok/callback
    tiktok_redirect_uri: str = ""
    tiktok_scopes: str = "user.info.basic,video.publish,video.upload"

    # ----------------------------------------------------------------
    @property
    def segments_dir(self) -> Path:
        return self.data_dir / "segments"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def effective_tiktok_redirect(self) -> str:
        return self.tiktok_redirect_uri or f"{self.public_url.rstrip('/')}/api/tiktok/callback"

    @property
    def fernet_key(self) -> bytes:
        digest = hashlib.sha256(("fernet:" + self.secret_key).encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def ai_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.segments_dir, self.clips_dir, self.work_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
