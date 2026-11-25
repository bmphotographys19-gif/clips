"""Trabajos en cola (RQ): enriquecer + renderizar clips y publicar en TikTok."""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rq import Queue
from sqlalchemy import func, select

from .. import appsettings, storage
from ..config import get_settings
from ..db import Clip, Publication, Stream, session_scope, utcnow
from ..events import activity, get_redis, publish
from ..services import detector, media, render, tiktok

log = logging.getLogger("clipradar.tasks")


def queue(name: str) -> Queue:
    import redis

    return Queue(name, connection=redis.Redis.from_url(get_settings().redis_url))


def enqueue_process(clip_id: int) -> None:
    queue("render").enqueue(process_clip, clip_id, job_timeout=3600, result_ttl=3600)


def enqueue_render(clip_id: int) -> None:
    queue("render").enqueue(render_only, clip_id, job_timeout=3600, result_ttl=3600)


def enqueue_publish(pub_id: int) -> None:
    queue("publish").enqueue(publish_clip, pub_id, job_timeout=3600, result_ttl=3600)


def clip_dict(c: Clip) -> dict:
    return {
        "id": c.id, "stream_id": c.stream_id, "status": c.status, "category": c.category,
        "category_label": detector.CATEGORY_LABELS.get(c.category, c.category), "score": c.score,
        "reason": c.reason, "title": c.title, "description": c.description, "hashtags": c.hashtags or [],
        "duration": round(c.duration or (c.t_end - c.t_start), 2), "recommended": c.recommended,
        "video_url": storage.url_for(c.video_key), "thumb_url": storage.url_for(c.thumb_key),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "rendered_at": c.rendered_at.isoformat() if c.rendered_at else None,
        "error": c.error, "layout": c.layout, "file_size": c.file_size,
    }


def _emit(c: Clip) -> None:
    publish("clip", clip_dict(c))


# ----------------------------------------------------------------- procesado
def process_clip(clip_id: int) -> None:
    """Verificación visual con IA → filtro de puntuación → render 9:16."""
    s = get_settings()
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        if c is None:
            return
        stream = db.get(Stream, c.stream_id)
        min_score = stream.min_score if stream else s.default_min_score
        source = Path(c.source_path)
        draft = {"category": c.category, "score": c.score, "title": c.title, "reason": c.reason}
        transcript = c.transcript
        c.status = "rendering"
        _emit(c)

    if s.ai_configured and not source.exists():
        _fail(clip_id, "El archivo fuente del clip no existe")
        return
    if s.ai_configured:
        try:
            info = media.video_info(source)
            n = max(1, s.ai_vision_frames)
            times = [info["duration"] * (i + 1) / (n + 1) for i in range(n)]
            frames = media.extract_frames(source, times, s.work_dir / f"frames_{clip_id}")
            result = detector.enrich_with_vision(frames, transcript, draft)
            shutil.rmtree(s.work_dir / f"frames_{clip_id}", ignore_errors=True)
            with session_scope() as db:
                c = db.get(Clip, clip_id)
                c.score = result["score"]
                c.category = result["category"]
                c.title = result["title"][:300]
                c.description = result["description"]
                c.hashtags = result["hashtags"]
                c.recommended = bool(result["best_to_publish"]) and bool(result["safe_for_tiktok"])
                c.reason = (c.reason + "\nVisual: " + result["visual_notes"]).strip()
                c.vision_checked = True
                if not result["safe_for_tiktok"]:
                    c.status = "discarded"
                    c.error = "Descartado: podría incumplir las normas de TikTok"
                elif c.score < min_score:
                    c.status = "discarded"
                    c.error = f"Descartado tras revisión visual (puntuación {c.score} < {min_score})"
                if c.status == "discarded":
                    _emit(c)
                    activity(f"Clip #{clip_id} descartado: {c.error}", c.stream_id)
                    return
        except detector.AIUnavailable as exc:
            activity(f"Revisión visual omitida en clip #{clip_id}: {exc}", None, "warn")
        except Exception as exc:  # la revisión visual no debe impedir el render
            log.exception("vision")
            activity(f"Revisión visual falló en clip #{clip_id}: {exc}", None, "warn")
    render_only(clip_id)


def _fail(clip_id: int, msg: str) -> None:
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        if c:
            c.status = "failed"
            c.error = msg[:2000]
            _emit(c)
            activity(f"Clip #{clip_id} falló: {msg[:300]}", c.stream_id, "error")


def render_only(clip_id: int) -> None:
    s = get_settings()
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        if c is None:
            return
        c.status = "rendering"
        c.error = ""
        _emit(c)
        params = dict(source=Path(c.source_path), trim_start=c.trim_start, trim_end=c.trim_end,
                      layout=c.layout, cues=c.subtitles if c.subtitles_enabled else None,
                      style=c.subtitle_style, thumb_at=(c.peak_start + c.peak_end) / 2 - c.t_start,
                      old_video=c.video_key, old_thumb=c.thumb_key, stream_id=c.stream_id)
    if not params["source"].exists():
        _fail(clip_id, "El archivo fuente del clip ya no existe")
        return
    out_dir = s.work_dir / f"render_{clip_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    mp4, jpg = out_dir / f"clip_{clip_id}_{stamp}.mp4", out_dir / f"clip_{clip_id}_{stamp}.jpg"
    try:
        result = render.render_clip(params["source"], mp4, jpg, trim_start=params["trim_start"],
                                    trim_end=params["trim_end"], layout=params["layout"], cues=params["cues"],
                                    subtitle_style=params["style"], thumb_at=params["thumb_at"])
        vkey = storage.put_file(mp4, f"{params['stream_id']}/{mp4.name}", "video/mp4")
        tkey = storage.put_file(jpg, f"{params['stream_id']}/{jpg.name}", "image/jpeg")
    except Exception as exc:
        log.exception("render")
        _fail(clip_id, f"Error de render: {exc}")
        return
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    for old in (params["old_video"], params["old_thumb"]):
        if old and old not in (vkey, tkey):
            storage.delete(old)
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        c.video_key, c.thumb_key = vkey, tkey
        c.duration, c.file_size = result["duration"], result["size"]
        c.status, c.rendered_at = "ready", utcnow()
        _emit(c)
        activity(f"Clip #{clip_id} listo ({result['duration']:.0f}s, encuadre {result['layout']}): {c.title}",
                 c.stream_id)
    maybe_auto_publish(clip_id)


# ----------------------------------------------------------------- publicación
def caption_for(c: Clip, stream: Stream | None, template: str) -> str:
    tags = " ".join(f"#{h}" for h in (c.hashtags or []))
    credit = ""
    if stream and stream.credit:
        credit = template.replace("{credit}", stream.credit)
    parts = [c.title.strip(), c.description.strip(), credit, tags]
    return "\n".join(p for p in parts if p)[:2200]


def maybe_auto_publish(clip_id: int) -> None:
    """Publica solo si: el usuario activó la auto-publicación (global y en la transmisión),
    dio su consentimiento explícito, hay cuenta conectada y se respetan los límites."""
    with session_scope() as db:
        cfg = appsettings.get_section(db, "publishing")
        c = db.get(Clip, clip_id)
        stream = db.get(Stream, c.stream_id) if c else None
        if not (c and stream and cfg["auto_publish"] and cfg["consent_confirmed"] and stream.auto_publish):
            return
        if c.score < int(cfg["min_score"]) or not c.recommended:
            return
        account = tiktok.get_account(db)
        if account is None:
            activity("Auto-publicación omitida: no hay cuenta de TikTok conectada", c.stream_id, "warn")
            return
        if db.scalar(select(func.count()).select_from(Publication).where(Publication.clip_id == clip_id)):
            return
        since = utcnow() - timedelta(hours=24)
        today = db.scalar(select(func.count()).select_from(Publication).where(
            Publication.trigger == "auto", Publication.created_at >= since,
            Publication.status != "failed")) or 0
        if today >= int(cfg["max_per_day"]):
            activity(f"Auto-publicación omitida para clip #{clip_id}: límite diario alcanzado", c.stream_id)
            return
        last = db.scalar(select(func.max(Publication.created_at)).where(Publication.trigger == "auto"))
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if utcnow() - last < timedelta(minutes=int(cfg["min_minutes_between"])):
                activity(f"Auto-publicación del clip #{clip_id} omitida: intervalo mínimo", c.stream_id)
                return
        pub = Publication(
            clip_id=clip_id, account_id=account.id, mode=cfg["mode"], trigger="auto",
            caption=caption_for(c, stream, cfg["credit_template"]),
            options={k: cfg[k] for k in ("privacy_level", "disable_comment", "disable_duet", "disable_stitch",
                                         "is_aigc", "brand_content_toggle", "brand_organic_toggle")},
        )
        db.add(pub)
        db.flush()
        pub_id = pub.id
    enqueue_publish(pub_id)


def _pub_update(pub_id: int, **fields) -> None:
    with session_scope() as db:
        p = db.get(Publication, pub_id)
        for k, v in fields.items():
            setattr(p, k, v)
        db.flush()
        publish("publication", {"id": p.id, "clip_id": p.clip_id, "status": p.status, "error": p.error,
                                "post_id": p.post_id, "mode": p.mode})


def publish_clip(pub_id: int) -> None:
    s = get_settings()
    with session_scope() as db:
        p = db.get(Publication, pub_id)
        if p is None or p.status not in ("queued", "failed"):
            return
        c = db.get(Clip, p.clip_id)
        account = tiktok.get_account(db)
        if account is None:
            _pub_update(pub_id, status="failed", error="No hay cuenta de TikTok conectada")
            return
        if c.status != "ready" or not c.video_key:
            _pub_update(pub_id, status="failed", error="El clip no está renderizado")
            return
        try:
            token = tiktok.account_token(db, account)
        except Exception as exc:
            _pub_update(pub_id, status="failed", error=str(exc))
            return
        mode, caption, opts, video_key = p.mode, p.caption, dict(p.options or {}), c.video_key
        clip_dur, stream_id = c.duration, c.stream_id

    tmp = s.work_dir / f"pub_{pub_id}.mp4"
    try:
        _pub_update(pub_id, status="uploading", error="")
        video = storage.fetch_local(video_key, tmp)
        post_info = None
        if mode == "direct":
            info = tiktok.creator_info(token)
            allowed = info.get("privacy_level_options") or []
            privacy = opts.get("privacy_level")
            if privacy not in allowed:
                raise tiktok.TikTokError(
                    f"Privacidad '{privacy}' no permitida para esta cuenta. Opciones: {', '.join(allowed)}")
            max_dur = info.get("max_video_post_duration_sec")
            if max_dur and clip_dur > max_dur:
                raise tiktok.TikTokError(f"El clip dura {clip_dur:.0f}s y TikTok permite {max_dur}s")
            post_info = {
                "title": caption,
                "privacy_level": privacy,
                "disable_comment": bool(opts.get("disable_comment")) or bool(info.get("comment_disabled")),
                "disable_duet": bool(opts.get("disable_duet")) or bool(info.get("duet_disabled")),
                "disable_stitch": bool(opts.get("disable_stitch")) or bool(info.get("stitch_disabled")),
                "brand_content_toggle": bool(opts.get("brand_content_toggle")),
                "brand_organic_toggle": bool(opts.get("brand_organic_toggle")),
                "is_aigc": bool(opts.get("is_aigc")),
                "video_cover_timestamp_ms": 1000,
            }
        init = tiktok.init_upload(token, video, mode, post_info)
        _pub_update(pub_id, publish_id=init["publish_id"])
        tiktok.upload_file(init["upload_url"], video, init["chunk_size"], init["chunk_count"])
        _pub_update(pub_id, status="processing")
        deadline = time.time() + 20 * 60
        while time.time() < deadline:
            time.sleep(8)
            st = tiktok.fetch_status(token, init["publish_id"])
            status = st.get("status", "")
            if status == "PUBLISH_COMPLETE":
                ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                _pub_update(pub_id, status="published", post_id=str(ids[0]) if ids else "")
                activity(f"Clip publicado en TikTok (publicación #{pub_id})", stream_id)
                return
            if status == "SEND_TO_USER_INBOX":
                _pub_update(pub_id, status="sent_to_inbox")
                activity(f"Clip enviado a tu bandeja de TikTok (publicación #{pub_id}). Termina de publicarlo "
                         "desde la app de TikTok.", stream_id)
                return
            if status == "FAILED":
                raise tiktok.TikTokError(st.get("fail_reason") or "TikTok rechazó el vídeo")
        raise tiktok.TikTokError("TikTok no confirmó la publicación a tiempo; revisa el estado más tarde")
    except Exception as exc:
        log.exception("publish")
        _pub_update(pub_id, status="failed", error=str(exc)[:1000])
        activity(f"Publicación #{pub_id} falló: {exc}", stream_id, "error")
    finally:
        if get_settings().storage_backend == "s3":
            tmp.unlink(missing_ok=True)
