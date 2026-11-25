"""Subtítulos automáticos en español: agrupación en bloques cortos y exportación ASS."""
from __future__ import annotations

import re

MAX_WORDS = 4
MAX_CHARS = 22
MAX_CUE_SECONDS = 1.8
GAP_BREAK = 0.55

STYLES = {
    # Fuente, tamaño, color primario, contorno, sombra, caja
    "highlight": {"size": 86, "outline": 6, "shadow": 2, "box": False, "highlight": True},
    "simple": {"size": 80, "outline": 5, "shadow": 2, "box": False, "highlight": False},
    "box": {"size": 74, "outline": 14, "shadow": 0, "box": True, "highlight": False},
}
FONT = "DejaVu Sans"
YELLOW = "&H0000E5FF&"  # BGR → amarillo cálido


def words_to_cues(words: list[dict]) -> list[dict]:
    """words: [{w, s, e}] relativos al clip → cues [{start, end, text, words}]."""
    cues: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            cues.append({
                "start": round(cur[0]["s"], 3),
                "end": round(cur[-1]["e"], 3),
                "text": " ".join(w["w"] for w in cur),
                "words": [dict(w) for w in cur],
            })
            cur.clear()

    for w in words:
        if cur:
            text_len = len(" ".join(x["w"] for x in cur)) + 1 + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            ends_sentence = re.search(r"[.!?¡¿…,;:]$", cur[-1]["w"]) is not None
            if (len(cur) >= MAX_WORDS or text_len > MAX_CHARS or gap > GAP_BREAK
                    or w["e"] - cur[0]["s"] > MAX_CUE_SECONDS or ends_sentence):
                flush()
        cur.append(w)
    flush()
    # Evitar solapes y huecos de parpadeo.
    for a, b in zip(cues, cues[1:]):
        if b["start"] - a["end"] < 0.25:
            a["end"] = b["start"]
    return cues


def retime_cue(cue: dict) -> dict:
    """Tras editar el texto de un cue, reparte las palabras en su intervalo."""
    tokens = cue["text"].split()
    old = cue.get("words") or []
    if len(old) == len(tokens):
        words = [{"w": t, "s": o["s"], "e": o["e"]} for t, o in zip(tokens, old)]
    else:
        n = max(1, len(tokens))
        span = (cue["end"] - cue["start"]) / n
        words = [{"w": t, "s": round(cue["start"] + i * span, 3), "e": round(cue["start"] + (i + 1) * span, 3)}
                 for i, t in enumerate(tokens)]
    return {**cue, "words": words}


def validate_cues(cues: list[dict], duration: float) -> list[dict]:
    clean = []
    for c in cues:
        text = str(c.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(c.get("start", 0)))
        end = min(duration, float(c.get("end", start + 1)))
        if end <= start:
            continue
        clean.append(retime_cue({"start": round(start, 3), "end": round(end, 3), "text": text[:120],
                                 "words": c.get("words") or []}))
    return sorted(clean, key=lambda c: c["start"])


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass(cues: list[dict], style: str = "highlight", margin_v: int = 560) -> str:
    st = STYLES.get(style, STYLES["highlight"])
    border_style = 3 if st["box"] else 1
    back = "&H99000000&" if st["box"] else "&H80000000&"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},{st['size']},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,{back},-1,0,0,0,100,100,0,0,{border_style},{st['outline']},{st['shadow']},2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        words = c.get("words") or []
        if st["highlight"] and words:
            # Un evento por palabra: el bloque completo con la palabra activa resaltada.
            plain = [_esc(w["w"]) for w in words]
            if words[0]["s"] > c["start"] + 0.02:
                lines.append(f"Dialogue: 0,{_ts(c['start'])},{_ts(words[0]['s'])},Main,,0,0,0,,{' '.join(plain)}")
            for i, w in enumerate(words):
                end = words[i + 1]["s"] if i + 1 < len(words) else c["end"]
                if end <= w["s"]:
                    continue
                parts = [f"{{\\c{YELLOW}\\fscx108\\fscy108}}{p}{{\\r}}" if j == i else p for j, p in enumerate(plain)]
                lines.append(f"Dialogue: 0,{_ts(w['s'])},{_ts(end)},Main,,0,0,0,,{' '.join(parts)}")
        else:
            lines.append(f"Dialogue: 0,{_ts(c['start'])},{_ts(c['end'])},Main,,0,0,0,,{_esc(c['text'])}")
    return header + "\n".join(lines) + "\n"
