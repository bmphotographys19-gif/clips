"""Transcripción local (sin coste por minuto, corre en el propio equipo).

Dos motores:
- ``faster_whisper``: el habitual en servidores Linux x86/ARM.
- ``whispercpp``: whisper.cpp compilado en el equipo; es el que funciona en
  Android (Termux), donde faster-whisper no se puede instalar.

Ambos devuelven frases con marcas de tiempo por palabra.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("clipradar.transcribe")
_model = None
_lock = threading.Lock()


class TranscriptionError(RuntimeError):
    pass


def transcribe(wav: Path, prompt: str | None = None) -> list[dict]:
    """Devuelve frases: [{start, end, text, words: [{w, s, e}]}] relativas al inicio del wav."""
    if get_settings().transcribe_backend == "whispercpp":
        return _transcribe_whispercpp(wav, prompt)
    return _transcribe_faster_whisper(wav, prompt)


# ------------------------------------------------------------------ faster-whisper
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


def _transcribe_faster_whisper(wav: Path, prompt: str | None) -> list[dict]:
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


# ------------------------------------------------------------------ whisper.cpp
_NOISE = re.compile(r"^[\[\(\*♪].*[\]\)\*♪]$")  # [Música], (risas), ♪…♪


def _transcribe_whispercpp(wav: Path, prompt: str | None) -> list[dict]:
    s = get_settings()
    if not s.whispercpp_model or not Path(s.whispercpp_model).is_file():
        raise TranscriptionError(f"No se encuentra el modelo de whisper.cpp: '{s.whispercpp_model}'")
    out_base = wav.with_suffix("")
    threads = s.whisper_threads or max(1, min(8, os.cpu_count() or 4))
    cmd = [
        s.whispercpp_bin, "-m", s.whispercpp_model, "-f", str(wav), "-l", s.whisper_language or "auto",
        "-t", str(threads), "-ml", "1", "-sow", "-oj", "-of", str(out_base), "-np",
    ]
    if prompt:
        cmd += ["--prompt", prompt[-200:]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    json_path = out_base.with_suffix(".json")
    if proc.returncode != 0 or not json_path.exists():
        raise TranscriptionError("whisper.cpp falló: " + (proc.stderr or proc.stdout)[-500:])
    try:
        data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    finally:
        json_path.unlink(missing_ok=True)
    return words_to_lines(_whispercpp_words(data))


def _whispercpp_words(data: dict) -> list[dict]:
    """Con ``-ml 1 -sow`` cada segmento de whisper.cpp es una palabra (offsets en ms)."""
    words: list[dict] = []
    for seg in data.get("transcription", []):
        text = (seg.get("text") or "").strip()
        off = seg.get("offsets") or {}
        if not text or _NOISE.match(text) or "from" not in off:
            continue
        start, end = off["from"] / 1000, off["to"] / 1000
        if end <= start:
            end = start + 0.1
        # Los signos sueltos se pegan a la palabra anterior.
        if words and re.fullmatch(r"[.,;:!?¡¿…»\"')]+", text):
            words[-1]["w"] += text
            words[-1]["e"] = max(words[-1]["e"], end)
            continue
        words.append({"w": text, "s": round(start, 3), "e": round(end, 3)})
    return words


def words_to_lines(words: list[dict], max_gap: float = 0.7, max_words: int = 18, max_secs: float = 9.0) -> list[dict]:
    """Agrupa palabras en frases: corta en fin de oración, pausas largas o frases demasiado largas."""
    lines: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            lines.append({"start": cur[0]["s"], "end": cur[-1]["e"],
                          "text": " ".join(w["w"] for w in cur), "words": list(cur)})
            cur.clear()

    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > max_gap or len(cur) >= max_words or w["e"] - cur[0]["s"] > max_secs):
            flush()
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"]):
            flush()
    flush()
    return lines
