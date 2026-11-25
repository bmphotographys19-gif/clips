"""Recorte inteligente: límites de escena, contexto, cortes limpios y duplicados.

Funciones puras (sin E/S) para poder probarlas de forma aislada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GAP_PAD = 0.25  # margen dentro de un silencio para no cortar sílabas


@dataclass
class Bounds:
    clip_start: float
    clip_end: float
    peak_start: float
    peak_end: float

    @property
    def duration(self) -> float:
        return self.clip_end - self.clip_start


def _line_at(lines: list[dict], t: float) -> dict | None:
    for ln in lines:
        if ln["t_start"] <= t <= ln["t_end"]:
            return ln
    return None


def refine(
    start: float,
    end: float,
    lines: list[dict],
    avail_start: float,
    avail_end: float,
    before: float,
    after: float,
    min_len: float,
    max_len: float,
) -> Bounds:
    """Ajusta un momento propuesto a límites de frase y añade contexto sin cortes bruscos.

    lines: frases ordenadas [{t_start, t_end, text}] en el mismo eje temporal.
    """
    lines = sorted(lines, key=lambda x: x["t_start"])
    # 1) El pico empieza al inicio de su frase y termina al final de su frase.
    ln = _line_at(lines, start)
    peak_start = ln["t_start"] if ln else start
    ln = _line_at(lines, end)
    peak_end = ln["t_end"] if ln else end

    # 2) Contexto previo: retrocedemos `before` segundos y, si caemos dentro de
    #    una frase, incluimos la frase entera (empieza en su inicio).
    cs = peak_start - before
    ln = _line_at(lines, cs)
    if ln and ln["t_start"] < peak_start:
        cs = ln["t_start"] - GAP_PAD
    # 3) Contexto posterior: si caemos dentro de una frase, la terminamos.
    ce = peak_end + after
    ln = _line_at(lines, ce)
    if ln and ln["t_end"] > peak_end:
        ce = ln["t_end"] + GAP_PAD

    # 4) Duración mínima: añadimos frases completas alrededor.
    prev_lines = [x for x in lines if x["t_end"] <= cs]
    next_lines = [x for x in lines if x["t_start"] >= ce]
    take_before = True  # alternamos: una frase antes, una después
    while ce - cs < min_len and (prev_lines or next_lines):
        if (take_before and prev_lines) or not next_lines:
            cs = prev_lines.pop()["t_start"] - GAP_PAD
        else:
            ce = next_lines.pop(0)["t_end"] + GAP_PAD
        take_before = not take_before
    if ce - cs < min_len:
        ce = cs + min_len

    # 5) Duración máxima: recortamos al final de la última frase que quepa.
    if ce - cs > max_len:
        limit = cs + max_len
        fitting = [x for x in lines if cs <= x["t_start"] and x["t_end"] + GAP_PAD <= limit]
        ce = (fitting[-1]["t_end"] + GAP_PAD) if fitting else limit
        if ce - cs > max_len:
            ce = limit

    cs = max(cs, avail_start)
    ce = min(ce, avail_end)
    peak_start = max(peak_start, cs)
    peak_end = min(peak_end, ce)
    return Bounds(round(cs, 3), round(ce, 3), round(peak_start, 3), round(peak_end, 3))


_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def text_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    shortest = max(1e-6, min(a1 - a0, b1 - b0))
    return inter / shortest


def is_duplicate(candidate: dict, existing: list[dict], text_threshold: float = 0.6) -> dict | None:
    """candidate/existing: {start, end, transcript}. Devuelve el clip existente que lo duplica."""
    for ex in existing:
        if overlap_ratio(candidate["start"], candidate["end"], ex["start"], ex["end"]) >= 0.5:
            return ex
        near = abs(candidate["start"] - ex["start"]) < 15 * 60
        if near and text_similarity(candidate.get("transcript", ""), ex.get("transcript", "")) >= text_threshold:
            return ex
    return None
