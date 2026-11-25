"""Ajustes globales editables desde el panel."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .db import AppSetting

DEFAULTS: dict[str, Any] = {
    "publishing": {
        # Publicación automática: apagada por defecto. Solo actúa si hay cuenta
        # de TikTok conectada y el usuario la activa explícitamente.
        "auto_publish": False,
        "mode": "inbox",  # inbox (borrador en TikTok) | direct
        "min_score": 80,
        "max_per_day": 3,
        "min_minutes_between": 60,
        "privacy_level": "SELF_ONLY",
        "disable_comment": False,
        "disable_duet": False,
        "disable_stitch": False,
        "is_aigc": False,
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "credit_template": "🎥 Créditos: {credit}",
        "consent_confirmed": False,
    },
    "detection": {
        "min_score": 60,
        "categories": ["pelea", "humor", "burla", "reaccion", "revelacion", "polemica", "sorpresa", "viral"],
        "max_clips_per_hour": 12,
        "dedupe_similarity": 0.6,
    },
    "editor": {
        "subtitle_style": "highlight",
        "layout": "auto",
    },
}


def get_section(db: Session, key: str) -> dict[str, Any]:
    row = db.get(AppSetting, key)
    merged = dict(DEFAULTS.get(key, {}))
    if row and isinstance(row.value, dict):
        merged.update(row.value)
    return merged


def set_section(db: Session, key: str, values: dict[str, Any]) -> dict[str, Any]:
    current = get_section(db, key)
    allowed = set(DEFAULTS.get(key, {}).keys())
    for k, v in values.items():
        if k in allowed:
            current[k] = v
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=current))
    else:
        row.value = current
    db.commit()
    return current
