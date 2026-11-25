"""Base de datos (PostgreSQL en producción, SQLite para desarrollo/tests)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Stream(Base):
    """Una transmisión monitorizada (YouTube Live o fuente HLS/RTMP autorizada)."""

    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    channel_name: Mapped[str] = mapped_column(String(200), default="")
    source_type: Mapped[str] = mapped_column(String(20))  # youtube | hls
    source_url: Mapped[str] = mapped_column(Text)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    permission_note: Mapped[str] = mapped_column(Text, default="")  # cómo se obtuvo la autorización
    credit: Mapped[str] = mapped_column(String(200), default="")  # crédito al creador en cada post

    desired_state: Mapped[str] = mapped_column(String(16), default="stopped")  # running | stopped
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|starting|live|reconnecting|ended|error|stopped
    status_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_segment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analyzed_until: Mapped[float] = mapped_column(Float, default=0.0)  # epoch hasta donde se transcribió
    seconds_captured: Mapped[float] = mapped_column(Float, default=0.0)
    seconds_analyzed: Mapped[float] = mapped_column(Float, default=0.0)

    # Ajustes por transmisión
    min_score: Mapped[int] = mapped_column(Integer, default=60)
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list)  # vacío = todas
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    clips: Mapped[list["Clip"]] = relationship(back_populates="stream", cascade="all, delete-orphan")


class Segment(Base):
    """Trozo de vídeo capturado (buffer circular en disco)."""

    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    t_start: Mapped[float] = mapped_column(Float, index=True)  # epoch (s)
    t_end: Mapped[float] = mapped_column(Float)
    transcribed: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class TranscriptLine(Base):
    """Frase transcrita con marcas de tiempo por palabra y señales de audio/vídeo."""

    __tablename__ = "transcript_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), index=True)
    t_start: Mapped[float] = mapped_column(Float, index=True)
    t_end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    words: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{w, s, e}] epoch
    loudness: Mapped[float | None] = mapped_column(Float, nullable=True)  # LUFS momentáneo máx
    loud_spike: Mapped[bool] = mapped_column(Boolean, default=False)
    scene_change: Mapped[bool] = mapped_column(Boolean, default=False)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), index=True)
    # detected → rendering → ready | discarded | failed ; publicado se ve en Publication
    status: Mapped[str] = mapped_column(String(16), default="detected", index=True)
    category: Mapped[str] = mapped_column(String(40), default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    t_start: Mapped[float] = mapped_column(Float)  # epoch, incluye contexto
    t_end: Mapped[float] = mapped_column(Float)
    peak_start: Mapped[float] = mapped_column(Float)  # el momento en sí
    peak_end: Mapped[float] = mapped_column(Float)
    transcript: Mapped[str] = mapped_column(Text, default="")
    subtitles: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{start,end,text,words:[{w,s,e}]}] relativo
    subtitles_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subtitle_style: Mapped[str] = mapped_column(String(20), default="highlight")
    layout: Mapped[str] = mapped_column(String(16), default="auto")  # auto | track | blur
    trim_start: Mapped[float] = mapped_column(Float, default=0.0)  # recorte manual (s, relativo)
    trim_end: Mapped[float] = mapped_column(Float, default=0.0)
    source_path: Mapped[str] = mapped_column(Text, default="")  # corte original horizontal
    video_key: Mapped[str] = mapped_column(Text, default="")
    thumb_key: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    vision_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)  # "mejor para publicar"
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stream: Mapped[Stream] = relationship(back_populates="clips")
    publications: Mapped[list["Publication"]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class TikTokAccount(Base):
    __tablename__ = "tiktok_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    open_id: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(Text, default="")
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("tiktok_accounts.id", ondelete="SET NULL"), nullable=True)
    mode: Mapped[str] = mapped_column(String(10))  # direct | inbox
    trigger: Mapped[str] = mapped_column(String(10), default="manual")  # manual | auto
    caption: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # queued → uploading → processing → published | sent_to_inbox | failed
    status: Mapped[str] = mapped_column(String(20), default="queued")
    publish_id: Mapped[str] = mapped_column(String(128), default="")
    post_id: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    clip: Mapped[Clip] = relationship(back_populates="publications")


class AppSetting(Base):
    """Ajustes globales editables desde el panel (clave → JSON)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ActivityLog(Base):
    """Registro de eventos visibles en el panel (monitor en vivo)."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int | None] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------------------
_settings = get_settings()
_connect_args = {"check_same_thread": False, "timeout": 30} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, pool_pre_ping=True, connect_args=_connect_args)

if _settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
