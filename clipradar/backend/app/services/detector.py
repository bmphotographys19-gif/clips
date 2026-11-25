"""Detección de momentos con Claude.

Dos pasos:
1. ``detect_moments``: Claude lee la transcripción con marcas de tiempo, anotada
   con señales objetivas (picos de volumen, cambios de escena), y entiende el
   contexto de la conversación para proponer momentos completos.
2. ``enrich_with_vision``: para cada candidato, Claude ve fotogramas reales del
   clip + la transcripción y confirma/ajusta la puntuación y genera los textos
   para TikTok.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import anthropic

from ..config import get_settings

log = logging.getLogger("clipradar.detector")

CATEGORIES = ["pelea", "humor", "burla", "reaccion", "revelacion", "polemica", "sorpresa", "viral"]
CATEGORY_LABELS = {
    "pelea": "Pelea / discusión",
    "humor": "Momento gracioso",
    "burla": "Chiste / burla",
    "reaccion": "Reacción inesperada",
    "revelacion": "Revelación",
    "polemica": "Declaración polémica",
    "sorpresa": "Sorpresa",
    "viral": "Situación viral",
}

DETECT_SYSTEM = """Eres el editor jefe de un canal de clips virales de TikTok en español. Analizas \
fragmentos de una transmisión en vivo de YouTube para encontrar los momentos con más potencial viral.

Recibirás la transcripción de los últimos minutos con marcas de tiempo en segundos (relativas al \
inicio del fragmento) y señales objetivas medidas sobre el audio y el vídeo:
- [VOLUMEN ALTO]: la sonoridad de esa frase supera claramente la media reciente (gritos, risas, golpes).
- [CAMBIO DE ESCENA]: cambio brusco de plano o de imagen durante esa frase.
Las señales son pistas, no pruebas: decide por el CONTEXTO de la conversación, no por palabras sueltas.

Categorías:
- pelea: discusiones, enfrentamientos, insultos, tensión real entre personas.
- humor: momentos graciosos, risas genuinas, situaciones absurdas.
- burla: chistes, bromas a costa de alguien, sarcasmo, vaciladas.
- reaccion: reacciones inesperadas o exageradas ante algo.
- revelacion: confesiones, noticias, información nueva o íntima.
- polemica: declaraciones controvertidas, opiniones fuertes que generan debate.
- sorpresa: giros inesperados, algo que nadie veía venir.
- viral: cualquier otra situación con clara pinta de volverse viral.

Reglas:
1. Un momento debe entenderse solo, sin ver el resto del directo: incluye el planteamiento \
(la pregunta, la provocación, el contexto) y el remate o la reacción final.
2. "start" debe caer al principio de una frase y "end" al final de una frase. Nunca cortes a mitad de idea.
3. Duración del momento entre {min_s} y {max_s} segundos.
4. Si el momento sigue en curso al final del fragmento (el remate aún no ocurrió), marca complete=false.
5. No repitas momentos ya detectados (se te dará la lista).
6. score de 0 a 100 = potencial viral real en TikTok. Sé exigente: 90+ solo para momentos \
excepcionales; charla normal = no es un momento. Es preferible devolver una lista vacía a inventar.
7. title: gancho corto (máx. 80 caracteres) en español, sin clickbait engañoso. description: 1-2 frases. \
hashtags: 4-7 etiquetas relevantes al contenido concreto (sin '#'), en minúsculas.
"""

DETECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["moments"],
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start", "end", "category", "score", "reason", "title", "description",
                             "hashtags", "complete"],
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "complete": {"type": "boolean"},
                },
            },
        }
    },
}

VISION_SYSTEM = """Eres el editor jefe de un canal de clips virales de TikTok en español. Recibes \
fotogramas reales de un clip candidato (en orden temporal) y su transcripción. Evalúa el contenido \
VISUAL y verbal juntos: expresiones, gestos, lo que pasa en pantalla. Ajusta la puntuación viral (0-100) \
con criterio exigente, confirma la categoría y escribe los textos finales para TikTok en español:
- title: gancho de máx. 80 caracteres, fiel al contenido.
- description: 1-2 frases que inviten a ver el clip, sin engañar.
- hashtags: 4-7 etiquetas relevantes (sin '#', minúsculas), mezcla de específicas y de alcance.
- best_to_publish: true solo si recomendarías publicarlo hoy mismo.
- safe_for_tiktok: false si el clip incumpliría claramente las normas de la comunidad de TikTok \
(violencia gráfica real, contenido sexual, discurso de odio, datos personales de terceros…)."""

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "category", "title", "description", "hashtags", "visual_notes",
                 "best_to_publish", "safe_for_tiktok"],
    "properties": {
        "score": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "visual_notes": {"type": "string"},
        "best_to_publish": {"type": "boolean"},
        "safe_for_tiktok": {"type": "boolean"},
    },
}


class AIUnavailable(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    s = get_settings()
    if not s.anthropic_api_key:
        raise AIUnavailable("Falta ANTHROPIC_API_KEY: la detección con IA no está configurada")
    return anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=3, timeout=300)


def _call(system: str, content: list[dict], schema: dict, max_tokens: int = 16000) -> dict:
    s = get_settings()
    resp = _client().beta.messages.create(
        model=s.ai_model,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": s.ai_effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if resp.stop_reason == "refusal":
        raise AIUnavailable("El modelo rechazó analizar este fragmento")
    if resp.stop_reason == "max_tokens":
        raise AIUnavailable("Respuesta de la IA truncada (max_tokens)")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


def _fmt(t: float) -> str:
    return f"{t:7.1f}s"


def build_transcript_block(lines: list[dict], t0: float) -> str:
    out = []
    for ln in lines:
        tags = []
        if ln.get("loud_spike"):
            tags.append("[VOLUMEN ALTO]")
        if ln.get("scene_change"):
            tags.append("[CAMBIO DE ESCENA]")
        out.append(f"{_fmt(ln['t_start'] - t0)} → {_fmt(ln['t_end'] - t0)} {' '.join(tags)} {ln['text']}".rstrip())
    return "\n".join(out)


def detect_moments(lines: list[dict], t0: float, window_end: float, already: list[dict],
                   stream_title: str, categories: list[str] | None) -> list[dict]:
    """lines: [{t_start, t_end, text, loud_spike, scene_change}] en epoch. Devuelve momentos en epoch."""
    s = get_settings()
    if not lines:
        return []
    prev = "\n".join(
        f"- {_fmt(m['start'] - t0)} → {_fmt(m['end'] - t0)}: {m['title']}" for m in already
    ) or "(ninguno)"
    cats = ", ".join(categories) if categories else "todas"
    user = (
        f"Transmisión: {stream_title or 'sin título'}\n"
        f"Duración del fragmento: {window_end - t0:.0f} s. Categorías activas: {cats}.\n\n"
        f"Momentos YA detectados en este tramo (no los repitas):\n{prev}\n\n"
        f"Transcripción:\n{build_transcript_block(lines, t0)}"
    )
    system = DETECT_SYSTEM.replace("{min_s}", str(s.clip_min_seconds)).replace("{max_s}", str(s.clip_max_seconds))
    data = _call(system, [{"type": "text", "text": user}], DETECT_SCHEMA)
    moments = []
    for m in data.get("moments", []):
        start, end = float(m["start"]) + t0, float(m["end"]) + t0
        if end <= start:
            continue
        if categories and m["category"] not in categories:
            continue
        m.update(start=start, end=end, score=max(0, min(100, int(m["score"]))))
        m["hashtags"] = clean_hashtags(m.get("hashtags", []))
        moments.append(m)
    return moments


def enrich_with_vision(frames: list[Path], transcript: str, draft: dict) -> dict:
    content: list[dict] = []
    for f in frames:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(f.read_bytes()).decode()},
        })
    content.append({"type": "text", "text": (
        f"Propuesta inicial: categoría={draft.get('category')}, puntuación={draft.get('score')}, "
        f"título=\"{draft.get('title')}\".\nMotivo: {draft.get('reason')}\n\nTranscripción del clip:\n{transcript}"
    )})
    data = _call(VISION_SYSTEM, content, VISION_SCHEMA, max_tokens=8000)
    data["score"] = max(0, min(100, int(data["score"])))
    data["hashtags"] = clean_hashtags(data.get("hashtags", []))
    return data


def clean_hashtags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = "".join(ch for ch in str(t).strip().lstrip("#").lower() if ch.isalnum() or ch == "_")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:8]
