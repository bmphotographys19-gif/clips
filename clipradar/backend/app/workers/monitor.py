"""Servicio de monitoreo 24/7.

Un supervisor (una sola instancia, protegida con un candado en Redis) lanza un
``StreamRunner`` por cada transmisión marcada como "running" en la base de datos:

  FFmpeg captura la señal → segmentos de 10 s en disco (buffer circular)
  → cada minuto: audio → Whisper (palabras con tiempos) + señales de volumen/escena
  → Claude analiza la ventana de los últimos minutos con contexto
  → momentos → límites limpios + contexto → sin duplicados → corte fuente
  → cola "render" (revisión visual IA + 9:16 + subtítulos).

Si la señal se corta, reconecta con espera progresiva. Si el directo termina,
la transmisión pasa a "ended".

Ejecutar:  python -m app.workers.monitor
"""
from __future__ import annotations

import csv
import logging
import os
import shutil
import signal
import socket
import statistics
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, update

from .. import appsettings
from ..config import get_settings
from ..db import Clip, Segment, Stream, TranscriptLine, init_db, session_scope, utcnow
from ..events import activity, get_redis, publish
from ..services import boundaries, detector, media, sources, transcribe
from . import tasks

log = logging.getLogger("clipradar.monitor")
LOCK_KEY = "clipradar:monitor:lock"


def stream_dict(st: Stream) -> dict:
    return {
        "id": st.id, "title": st.title, "channel_name": st.channel_name, "source_type": st.source_type,
        "source_url": st.source_url, "status": st.status, "status_message": st.status_message,
        "desired_state": st.desired_state,
        "started_at": st.started_at.isoformat() if st.started_at else None,
        "last_heartbeat": st.last_heartbeat.isoformat() if st.last_heartbeat else None,
        "last_segment_at": st.last_segment_at.isoformat() if st.last_segment_at else None,
        "last_analysis_at": st.last_analysis_at.isoformat() if st.last_analysis_at else None,
        "seconds_captured": round(st.seconds_captured or 0), "seconds_analyzed": round(st.seconds_analyzed or 0),
        "min_score": st.min_score, "categories": st.categories or [], "auto_publish": st.auto_publish,
        "credit": st.credit, "permission_note": st.permission_note,
    }


def set_status(stream_id: int, status: str, message: str = "", **extra) -> None:
    with session_scope() as db:
        st = db.get(Stream, stream_id)
        if st is None:
            return
        st.status, st.status_message = status, message
        for k, v in extra.items():
            setattr(st, k, v)
        db.flush()
        publish("stream", stream_dict(st))


class StreamRunner(threading.Thread):
    def __init__(self, stream_id: int):
        super().__init__(name=f"stream-{stream_id}", daemon=True)
        self.stream_id = stream_id
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.s = get_settings()
        self.seg_dir = self.s.segments_dir / str(stream_id)
        self.loud_history: deque[float] = deque(maxlen=600)
        self.ai_warned = False

    # ------------------------------------------------------------ ciclo de vida
    def stop(self) -> None:
        self.stop_event.set()
        self._kill_ffmpeg()

    def _kill_ffmpeg(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def run(self) -> None:
        backoff = 5
        with session_scope() as db:
            st = db.get(Stream, self.stream_id)
            if st is None:
                return
            st.started_at = st.started_at or utcnow()
        activity("Monitoreo iniciado", self.stream_id)
        while not self.stop_event.is_set():
            try:
                ended = self._capture_session()
                if ended:
                    set_status(self.stream_id, "ended", "La transmisión terminó", desired_state="stopped")
                    activity("La transmisión terminó; monitoreo detenido", self.stream_id)
                    self._analyze(final=True)
                    return
                backoff = 5
            except sources.SourceError as exc:
                set_status(self.stream_id, "reconnecting", str(exc))
                activity(f"Fuente no disponible: {exc}. Reintento en {backoff}s", self.stream_id, "warn")
            except Exception as exc:
                log.exception("runner")
                set_status(self.stream_id, "error", str(exc)[:500])
                activity(f"Error en el monitoreo: {exc}", self.stream_id, "error")
            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 120)
        self._kill_ffmpeg()
        set_status(self.stream_id, "stopped", "Detenido por el usuario")
        activity("Monitoreo detenido", self.stream_id)

    # ------------------------------------------------------------ captura
    def _capture_session(self) -> bool:
        """Captura hasta que FFmpeg termine. Devuelve True si el directo acabó."""
        with session_scope() as db:
            st = db.get(Stream, self.stream_id)
            stype, surl, vid = st.source_type, st.source_url, st.youtube_video_id
        set_status(self.stream_id, "starting", "Conectando con la fuente…")
        play_url = sources.resolve_playback_url(stype, surl, vid)
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"{int(time.time())}"
        listing = self.seg_dir / f"{run_id}.csv"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if play_url.startswith("http"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
                    "-rw_timeout", "20000000"]
        cmd += ["-i", play_url, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(self.s.segment_seconds), "-reset_timestamps", "1",
                "-segment_list", str(listing), "-segment_list_type", "csv",
                str(self.seg_dir / f"{run_id}_%06d.ts")]
        err_path = self.seg_dir / f"{run_id}.err"
        with err_path.open("w") as err:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        seen = 0
        last_end: float | None = None
        last_analysis = time.time()
        last_heartbeat = 0.0
        last_cleanup = time.time()
        while not self.stop_event.is_set():
            alive = self.proc.poll() is None
            rows = []
            if listing.exists():
                with listing.open() as fh:
                    rows = [r for r in csv.reader(fh) if len(r) >= 3]
            new = rows[seen:]
            if new:
                seen = len(rows)
                with session_scope() as db:
                    for name, s0, s1 in new:
                        dur = max(0.0, float(s1) - float(s0))
                        t_end = time.time() if last_end is None else last_end + dur
                        t_start = t_end - dur
                        last_end = t_end
                        db.add(Segment(stream_id=self.stream_id, path=str(self.seg_dir / name),
                                       t_start=t_start, t_end=t_end))
                    st = db.get(Stream, self.stream_id)
                    st.seconds_captured = (st.seconds_captured or 0) + sum(float(r[2]) - float(r[1]) for r in new)
                    st.last_segment_at = utcnow()
                    if st.status != "live":
                        st.status, st.status_message = "live", "Capturando y analizando"
                    db.flush()
                    publish("stream", stream_dict(st))
            now = time.time()
            if now - last_heartbeat > 5:
                last_heartbeat = now
                with session_scope() as db:
                    db.execute(update(Stream).where(Stream.id == self.stream_id).values(last_heartbeat=utcnow()))
            if now - last_analysis >= self.s.analysis_interval_seconds:
                last_analysis = now
                self._analyze()
            if now - last_cleanup > 120:
                last_cleanup = now
                self._cleanup()
            if not alive:
                break
            self.stop_event.wait(2)
        if self.stop_event.is_set():
            return False
        code = self.proc.returncode
        err_text = err_path.read_text()[-600:] if err_path.exists() else ""
        log.info("ffmpeg terminó (%s): %s", code, err_text)
        # ¿Terminó el directo o fue un corte?
        if stype == "youtube":
            try:
                meta = sources.youtube_metadata(vid)
                if meta.live_status not in ("live", "upcoming"):
                    return True
            except Exception:
                pass
        raise sources.SourceError(f"Señal interrumpida{': ' + err_text.strip().splitlines()[-1] if err_text.strip() else ''}")

    def _cleanup(self) -> None:
        """Borra segmentos fuera del buffer (los clips ya tienen su propio corte)."""
        cutoff = time.time() - self.s.buffer_retention_minutes * 60
        with session_scope() as db:
            old = db.scalars(select(Segment).where(Segment.stream_id == self.stream_id, Segment.deleted.is_(False),
                                                   Segment.t_end < cutoff, Segment.transcribed.is_(True))).all()
            for seg in old:
                Path(seg.path).unlink(missing_ok=True)
                seg.deleted = True
            db.execute(delete(Segment).where(Segment.stream_id == self.stream_id, Segment.deleted.is_(True),
                                             Segment.t_end < cutoff - 3600))
            # La transcripción se guarda 24 h para consulta.
            db.execute(delete(TranscriptLine).where(TranscriptLine.stream_id == self.stream_id,
                                                    TranscriptLine.t_end < time.time() - 86400))

    # ------------------------------------------------------------ análisis
    def _analyze(self, final: bool = False) -> None:
        with session_scope() as db:
            pending = db.scalars(select(Segment).where(
                Segment.stream_id == self.stream_id, Segment.transcribed.is_(False), Segment.deleted.is_(False)
            ).order_by(Segment.t_start)).all()
            pending = [(p.id, Path(p.path), p.t_start, p.t_end) for p in pending]
            prompt_line = db.scalar(select(TranscriptLine.text).where(TranscriptLine.stream_id == self.stream_id)
                                    .order_by(TranscriptLine.t_start.desc()).limit(1))
        pending = [p for p in pending if p[1].exists()]
        if not pending:
            return
        # Analizamos tramos contiguos (una reconexión rompe la continuidad).
        groups: list[list] = [[pending[0]]]
        for p in pending[1:]:
            if p[2] - groups[-1][-1][3] > 1.0:
                groups.append([p])
            else:
                groups[-1].append(p)
        for group in groups:
            self._transcribe_group(group, prompt_line)
        self._detect(final)

    def _transcribe_group(self, group: list, prompt: str | None) -> None:
        work = self.s.work_dir / f"stream_{self.stream_id}"
        work.mkdir(parents=True, exist_ok=True)
        paths = [g[1] for g in group]
        base = group[0][2]
        has_audio = media.video_info(paths[0])["has_audio"]
        lines: list[dict] = []
        sig = {"loudness": [], "scenes": []}
        try:
            sig = media.audio_video_signals(paths, work)
        except media.MediaError as exc:
            log.warning("señales: %s", exc)
        if has_audio:
            wav = media.extract_audio_wav(paths, work / "audio.wav")
            lines = transcribe.transcribe(wav, prompt)
            wav.unlink(missing_ok=True)
        loud = sig["loudness"]
        hist = list(self.loud_history)
        baseline = statistics.median(hist) if len(hist) > 30 else None
        self.loud_history.extend(v for _, v in loud[::5] if v > -60)
        rows = []
        for ln in lines:
            vals = [v for t, v in loud if ln["start"] <= t <= ln["end"]]
            peak = max(vals) if vals else None
            spike = bool(peak is not None and baseline is not None and peak > baseline + 7)
            scene = any(ln["start"] - 0.3 <= t <= ln["end"] + 0.3 for t in sig["scenes"])
            rows.append(TranscriptLine(
                stream_id=self.stream_id, t_start=base + ln["start"], t_end=base + ln["end"], text=ln["text"],
                words=[{"w": w["w"], "s": round(base + w["s"], 3), "e": round(base + w["e"], 3)} for w in ln["words"]],
                loudness=peak, loud_spike=spike, scene_change=scene))
        with session_scope() as db:
            db.add_all(rows)
            ids = [g[0] for g in group]
            db.execute(update(Segment).where(Segment.id.in_(ids)).values(transcribed=True))
            st = db.get(Stream, self.stream_id)
            st.analyzed_until = group[-1][3]
            st.seconds_analyzed = (st.seconds_analyzed or 0) + (group[-1][3] - group[0][2])
            st.last_analysis_at = utcnow()
            db.flush()
            publish("stream", stream_dict(st))
        if rows:
            publish("transcript", {"stream_id": self.stream_id, "lines": [
                {"t": r.t_start, "text": r.text, "loud": r.loud_spike, "scene": r.scene_change} for r in rows]})

    def _detect(self, final: bool) -> None:
        s = self.s
        if not s.ai_configured:
            if not self.ai_warned:
                activity("Transcribiendo, pero la detección con IA está desactivada: falta ANTHROPIC_API_KEY",
                         self.stream_id, "warn")
                self.ai_warned = True
            return
        with session_scope() as db:
            st = db.get(Stream, self.stream_id)
            det_cfg = appsettings.get_section(db, "detection")
            end = st.analyzed_until
            t0 = end - s.analysis_window_seconds
            lines = db.scalars(select(TranscriptLine).where(
                TranscriptLine.stream_id == self.stream_id, TranscriptLine.t_end >= t0
            ).order_by(TranscriptLine.t_start)).all()
            line_dicts = [{"t_start": l.t_start, "t_end": l.t_end, "text": l.text, "words": l.words,
                           "loud_spike": l.loud_spike, "scene_change": l.scene_change} for l in lines]
            recent = db.scalars(select(Clip).where(Clip.stream_id == self.stream_id,
                                                   Clip.t_end >= t0 - 900)).all()
            existing = [{"start": c.peak_start, "end": c.peak_end, "title": c.title, "transcript": c.transcript,
                         "id": c.id} for c in recent]
            hour_count = len([c for c in recent if c.created_at and
                              (utcnow() - (c.created_at if c.created_at.tzinfo else
                                           c.created_at.replace(tzinfo=timezone.utc))).total_seconds() < 3600])
            avail = db.execute(select(Segment.t_start).where(
                Segment.stream_id == self.stream_id, Segment.deleted.is_(False)).order_by(Segment.t_start).limit(1)
            ).scalar()
            title = st.title
            categories = st.categories or det_cfg["categories"]
            min_score = st.min_score
        if len(line_dicts) < 3:
            return
        t0 = min(t0, line_dicts[0]["t_start"])
        try:
            moments = detector.detect_moments(line_dicts, t0, end,
                                              [e for e in existing if e["end"] >= t0], title, categories)
        except detector.AIUnavailable as exc:
            activity(f"IA no disponible: {exc}", self.stream_id, "warn")
            return
        except Exception as exc:
            log.exception("detect")
            activity(f"Error de la IA al analizar: {exc}", self.stream_id, "error")
            return
        for m in sorted(moments, key=lambda x: -x["score"]):
            if hour_count >= int(det_cfg["max_clips_per_hour"]):
                activity("Límite de clips por hora alcanzado; se ignoran momentos de menor puntuación",
                         self.stream_id)
                break
            if not m["complete"] and not final:
                continue
            if m["score"] < max(0, min_score - 10):  # margen: la revisión visual puede subirla
                continue
            b = boundaries.refine(m["start"], m["end"], line_dicts, avail or t0, end,
                                  s.context_before_seconds, s.context_after_seconds,
                                  s.clip_min_seconds, s.clip_max_seconds)
            if b.clip_end + 0.5 > end and not final:
                continue  # falta contexto posterior: se reevaluará en la siguiente pasada
            text = " ".join(l["text"] for l in line_dicts if l["t_end"] > b.clip_start and l["t_start"] < b.clip_end)
            dup = boundaries.is_duplicate({"start": b.peak_start, "end": b.peak_end, "transcript": text}, existing,
                                          float(det_cfg["dedupe_similarity"]))
            if dup:
                log.info("Duplicado de clip #%s, ignorado", dup["id"])
                continue
            clip_id = self._create_clip(m, b, line_dicts, text)
            if clip_id:
                hour_count += 1
                existing.append({"start": b.peak_start, "end": b.peak_end, "title": m["title"],
                                 "transcript": text, "id": clip_id})

    def _create_clip(self, m: dict, b: boundaries.Bounds, lines: list[dict], text: str) -> int | None:
        with session_scope() as db:
            segs = db.scalars(select(Segment).where(
                Segment.stream_id == self.stream_id, Segment.deleted.is_(False),
                Segment.t_end > b.clip_start, Segment.t_start < b.clip_end).order_by(Segment.t_start)).all()
            segs = [(Path(x.path), x.t_start, x.t_end) for x in segs]
        segs = [x for x in segs if x[0].exists()]
        if not segs:
            return None
        cs = max(b.clip_start, segs[0][1])
        ce = min(b.clip_end, segs[-1][2])
        words = [w for l in lines for w in (l["words"] or []) if cs <= w["s"] and w["e"] <= ce]
        from ..services import subtitles
        rel = [{"w": w["w"], "s": round(w["s"] - cs, 3), "e": round(w["e"] - cs, 3)} for w in words]
        with session_scope() as db:
            editor_cfg = appsettings.get_section(db, "editor")
            clip = Clip(stream_id=self.stream_id, status="detected", category=m["category"], score=m["score"],
                        reason=m["reason"], title=m["title"][:300], description=m["description"],
                        hashtags=m["hashtags"], t_start=cs, t_end=ce, peak_start=b.peak_start, peak_end=b.peak_end,
                        transcript=text, subtitles=subtitles.words_to_cues(rel),
                        layout=editor_cfg["layout"], subtitle_style=editor_cfg["subtitle_style"])
            db.add(clip)
            db.flush()
            clip_id = clip.id
        dest = self.s.work_dir / "sources" / f"clip_{clip_id}.mp4"
        try:
            media.concat_segments([x[0] for x in segs], dest, cs - segs[0][1], ce - cs)
        except media.MediaError as exc:
            with session_scope() as db:
                c = db.get(Clip, clip_id)
                c.status, c.error = "failed", f"No se pudo cortar el vídeo: {exc}"
            return None
        with session_scope() as db:
            c = db.get(Clip, clip_id)
            c.source_path = str(dest)
            db.flush()
            publish("clip", tasks.clip_dict(c))
        activity(f"Momento detectado ({detector.CATEGORY_LABELS.get(m['category'], m['category'])}, "
                 f"{m['score']}/100): {m['title']}", self.stream_id)
        tasks.enqueue_process(clip_id)
        return clip_id


# ---------------------------------------------------------------- supervisor
class Supervisor:
    def __init__(self) -> None:
        self.runners: dict[int, StreamRunner] = {}
        self.instance = f"{socket.gethostname()}:{os.getpid()}"
        self.running = True

    def acquire_lock(self) -> bool:
        r = get_redis()
        if r.set(LOCK_KEY, self.instance, nx=True, ex=30):
            return True
        if r.get(LOCK_KEY) == self.instance:
            r.expire(LOCK_KEY, 30)
            return True
        return False

    def loop(self) -> None:
        log.info("Supervisor de monitoreo iniciado (%s)", self.instance)
        while self.running:
            try:
                if not self.acquire_lock():
                    log.info("Otra instancia del monitor está activa; en espera")
                    time.sleep(10)
                    continue
                get_redis().set("clipradar:monitor:heartbeat", str(time.time()), ex=60)
                with session_scope() as db:
                    rows = db.execute(select(Stream.id, Stream.desired_state)).all()
                wanted = {sid for sid, d in rows if d == "running"}
                for sid in wanted:
                    r = self.runners.get(sid)
                    if r is None or not r.is_alive():
                        self.runners[sid] = StreamRunner(sid)
                        self.runners[sid].start()
                for sid, r in list(self.runners.items()):
                    if sid not in wanted:
                        if r.is_alive():
                            r.stop()
                        else:
                            self.runners.pop(sid, None)
            except Exception:
                log.exception("supervisor")
            time.sleep(3)
        for r in self.runners.values():
            r.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    if not shutil.which("ffmpeg") or not shutil.which("yt-dlp"):
        log.warning("ffmpeg o yt-dlp no están en el PATH")
    sup = Supervisor()

    def _stop(*_):
        sup.running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    sup.loop()


if __name__ == "__main__":
    main()
