"""Render final para TikTok: MP4 H.264 1080×1920, AAC 48 kHz, sonoridad −14 LUFS."""
from __future__ import annotations

from pathlib import Path

from ..config import get_settings
from . import reframe, subtitles
from .media import run, video_info

OUT_W, OUT_H = 1080, 1920
_HAS_ASS: bool | None = None


def ffmpeg_has_ass() -> bool:
    """Algunas compilaciones de FFmpeg (p. ej. en móviles) no incluyen libass."""
    global _HAS_ASS
    if _HAS_ASS is None:
        import subprocess

        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        _HAS_ASS = any(line.split()[1:2] == ["ass"] for line in out.splitlines() if line.strip())
    return _HAS_ASS


def _shift(plan: reframe.Plan, offset: float) -> reframe.Plan:
    if offset <= 0 or not plan.keyframes:
        return plan
    kf = [(t - offset, x) for t, x in plan.keyframes]
    before = [k for k in kf if k[0] <= 0]
    after = [k for k in kf if k[0] > 0]
    start = [(0.0, before[-1][1])] if before else []
    return reframe.Plan(plan.mode, plan.src_w, plan.src_h, plan.crop_w, start + after, plan.face_ratio)


def build_video_filter(plan: reframe.Plan, ass_path: Path | None, fps: float) -> str:
    out_fps = 60 if fps > 50 else 30
    if plan.mode == "track":
        chain = (f"[0:v]crop=w={plan.crop_w}:h={plan.src_h}:x='{plan.x_expr()}':y=0,"
                 f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1")
    elif plan.mode == "vertical":
        chain = (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    else:  # blur: vídeo completo centrado sobre fondo desenfocado
        chain = (f"[0:v]split=2[bgsrc][fgsrc];"
                 f"[bgsrc]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
                 f"boxblur=luma_radius=40:luma_power=2,eq=brightness=-0.08[bg];"
                 f"[fgsrc]scale={OUT_W}:-2:flags=lanczos[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    chain += f",fps={out_fps}"
    if ass_path is not None:
        chain += f",ass=filename={ass_path}"
    return chain + ",format=yuv420p[vout]"


def render_clip(source: Path, out_mp4: Path, out_thumb: Path, *, trim_start: float, trim_end: float,
                layout: str, cues: list[dict] | None, subtitle_style: str, thumb_at: float) -> dict:
    """Renderiza el clip. trim_* en segundos relativos al archivo fuente.

    Los cues de subtítulos están en tiempo relativo al archivo fuente y se
    desplazan aquí según el recorte.
    """
    s = get_settings()
    info = video_info(source)
    total = info["duration"]
    t0 = max(0.0, trim_start)
    t1 = min(total, total - max(0.0, trim_end)) if trim_end > 0 else total
    if t1 - t0 < 1:
        raise ValueError("El recorte deja el clip vacío")
    dur = t1 - t0

    plan = _shift(reframe.analyze(source, layout if layout in ("track", "blur") else "auto"), t0)

    ass_path = None
    if cues and ffmpeg_has_ass():
        shifted = []
        for c in cues:
            if c["end"] <= t0 or c["start"] >= t1:
                continue
            shifted.append({
                "start": max(0.0, c["start"] - t0), "end": min(dur, c["end"] - t0), "text": c["text"],
                "words": [{"w": w["w"], "s": max(0.0, w["s"] - t0), "e": min(dur, w["e"] - t0)}
                          for w in c.get("words", []) if w["e"] > t0 and w["s"] < t1],
            })
        if shifted:
            ass_path = out_mp4.with_suffix(".ass")
            ass_path.write_text(subtitles.to_ass(shifted, subtitle_style), encoding="utf-8")

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
           "-filter_complex", build_video_filter(plan, ass_path, info["fps"]), "-map", "[vout]"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    cmd += ["-c:v", "libx264", "-preset", s.render_preset, "-crf", str(s.render_crf),
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", str(out_mp4)]
    run(cmd, timeout=3600)

    thumb_t = min(max(0.0, thumb_at - t0), dur - 0.1)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{thumb_t:.2f}", "-i", str(out_mp4),
         "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", str(out_thumb)], timeout=120)
    if ass_path:
        ass_path.unlink(missing_ok=True)
    final = video_info(out_mp4)
    return {"duration": final["duration"], "width": final["width"], "height": final["height"],
            "layout": plan.mode, "face_ratio": round(plan.face_ratio, 2), "size": out_mp4.stat().st_size}
