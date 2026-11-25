"""Transcripción local con faster-whisper (sin coste por minuto, corre en el servidor)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("clipradar.transcribe")
_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            s = get_settings()
            log.info("Cargando modelo Whisper '%s' (%s)…", s.whisper_model, s.whisper_compute_type)
            _model = WhisperModel(
                s.whisper_model, device="auto", compute_type=s.whisper_compute_type,
                download_root=str(s.models_dir),
            )
        return _model


def transcribe(wav: Path, prompt: str | None = None) -> list[dict]:
    """Devuelve frases: [{start, end, text, words: [{w, s, e}]}] relativas al inicio del wav."""
    s = get_settings()
    model = _get_model()
    segments, _info = model.transcribe(
        str(wav),
        language=s.whisper_language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        initial_prompt=prompt or None,
        condition_on_previous_text=False,
        beam_size=3,
    )
    out: list[dict] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        words = [
            {"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
            for w in (seg.words or []) if w.word.strip()
        ]
        out.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": text, "words": words})
    return out
