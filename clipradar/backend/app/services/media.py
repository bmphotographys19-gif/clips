"""Utilidades FFmpeg/ffprobe compartidas."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise MediaError(f"{Path(cmd[0]).name} falló ({proc.returncode}): {tail}")
    return proc


def probe(path: Path | str) -> dict:
    out = run([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)
    ], timeout=60).stdout
    return json.loads(out)


def video_info(path: Path | str) -> dict:
    info = probe(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d or 1) if float(d or 1) else 30.0
    return {
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
        "duration": float(info.get("format", {}).get("duration") or 0.0),
    }


def concat_segments(paths: list[Path], dest: Path, start: float, duration: float) -> Path:
    """Une segmentos .ts y recorta [start, start+duration] (re-codificando para cortes exactos)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
        "-movflags", "+faststart", str(dest),
    ], timeout=900)
    lst.unlink(missing_ok=True)
    return dest


def extract_audio_wav(paths: list[Path], dest: Path) -> Path:
    """Audio mono 16 kHz para transcripción, a partir de uno o varios segmentos."""
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest),
    ], timeout=600)
    lst.unlink(missing_ok=True)
    return dest


_PTS = re.compile(r"pts_time:([\d.]+)")
_M = re.compile(r"lavfi\.r128\.M=(-?[\d.]+|-inf)")


def audio_video_signals(paths: list[Path], workdir: Path) -> dict:
    """Señales objetivas por segundo: sonoridad (LUFS momentáneo) y cambios de escena.

    Devuelve {"loudness": [(t, lufs)], "scenes": [t, ...]} con t relativo al inicio
    del primer segmento.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    lst = workdir / "sig.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    loud_file = workdir / "loud.txt"
    scene_file = workdir / "scene.txt"
    for f in (loud_file, scene_file):
        f.unlink(missing_ok=True)
    has_audio = video_info(paths[0])["has_audio"]
    fc = f"[0:v]fps=4,scale=192:-2,select='gt(scene,0.32)',metadata=print:file={scene_file}[v]"
    maps = ["-map", "[v]"]
    if has_audio:
        fc += f";[0:a]ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file={loud_file}[a]"
        maps += ["-map", "[a]"]
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-filter_complex", fc, *maps, "-f", "null", "-",
    ], timeout=600)
    loud: list[tuple[float, float]] = []
    if loud_file.exists():
        t = None
        for line in loud_file.read_text().splitlines():
            m = _PTS.search(line)
            if m:
                t = float(m.group(1))
                continue
            m = _M.search(line)
            if m and t is not None:
                val = -70.0 if m.group(1) == "-inf" else float(m.group(1))
                loud.append((t, val))
    scenes: list[float] = []
    if scene_file.exists():
        scenes = [float(m.group(1)) for m in _PTS.finditer(scene_file.read_text())]
    return {"loudness": loud, "scenes": scenes}


def extract_frames(video: Path, times: list[float], dest_dir: Path, width: int = 640) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, t in enumerate(times):
        f = dest_dir / f"frame_{i}.jpg"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, t):.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(f),
        ], timeout=60)
        if f.exists():
            out.append(f)
    return out
