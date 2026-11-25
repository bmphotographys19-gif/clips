"""Eventos en tiempo real: Redis pub/sub → Server-Sent Events en el panel."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import redis

from .config import get_settings
from .db import ActivityLog, session_scope

log = logging.getLogger("clipradar.events")
CHANNEL = "clipradar:events"
_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def publish(kind: str, data: dict[str, Any] | None = None) -> None:
    payload = json.dumps({"type": kind, "data": data or {}, "ts": time.time()}, default=str)
    try:
        get_redis().publish(CHANNEL, payload)
    except redis.RedisError as exc:  # el panel lo mostrará en el estado del sistema
        log.warning("No se pudo publicar evento %s: %s", kind, exc)


def activity(message: str, stream_id: int | None = None, level: str = "info") -> None:
    """Guarda una línea en el registro de actividad y la emite en vivo."""
    with session_scope() as s:
        entry = ActivityLog(stream_id=stream_id, level=level, message=message[:2000])
        s.add(entry)
        s.flush()
        data = {"id": entry.id, "stream_id": stream_id, "level": level, "message": entry.message,
                "created_at": entry.created_at.isoformat()}
    getattr(log, "warning" if level in ("warn", "error") else "info")("[stream %s] %s", stream_id, message)
    publish("activity", data)
