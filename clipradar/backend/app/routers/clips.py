from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import storage
from ..db import Clip, Publication, Stream, get_db
from ..events import publish
from ..security import require_auth
from ..services import detector, subtitles
from ..workers import tasks

router = APIRouter(prefix="/api/clips", dependencies=[Depends(require_auth)])


class Cue(BaseModel):
    start: float
    end: float
    text: str
    words: list[dict] = []


class ClipPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None
    subtitles: list[Cue] | None = None
    subtitles_enabled: bool | None = None
    subtitle_style: str | None = None
    layout: str | None = None
    trim_start: float | None = None
    trim_end: float | None = None


def pub_dict(p: Publication) -> dict:
    return {"id": p.id, "clip_id": p.clip_id, "mode": p.mode, "trigger": p.trigger, "status": p.status,
            "caption": p.caption, "options": p.options, "publish_id": p.publish_id, "post_id": p.post_id,
            "error": p.error, "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def delete_clip_files(c: Clip) -> None:
    storage.delete(c.video_key)
    storage.delete(c.thumb_key)
    if c.source_path:
        Path(c.source_path).unlink(missing_ok=True)


@router.get("")
def list_clips(status: str | None = None, stream_id: int | None = None, category: str | None = None,
               min_score: int = 0, recommended: bool | None = None, limit: int = 30, offset: int = 0,
               db: Session = Depends(get_db)):
    q = select(Clip)
    if status:
        q = q.where(Clip.status.in_(status.split(",")))
    if stream_id:
        q = q.where(Clip.stream_id == stream_id)
    if category:
        q = q.where(Clip.category == category)
    if min_score:
        q = q.where(Clip.score >= min_score)
    if recommended is not None:
        q = q.where(Clip.recommended.is_(recommended))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Clip.created_at.desc()).limit(min(limit, 100)).offset(offset)).all()
    stream_titles = dict(db.execute(select(Stream.id, Stream.title)).all())
    items = []
    for c in rows:
        d = tasks.clip_dict(c)
        d["stream_title"] = stream_titles.get(c.stream_id, "")
        d["published"] = any(p.status in ("published", "sent_to_inbox") for p in c.publications)
        items.append(d)
    return {"total": total, "items": items}


@router.get("/categories")
def categories():
    return [{"id": k, "label": v} for k, v in detector.CATEGORY_LABELS.items()]


@router.get("/{cid}")
def get_clip(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    d = tasks.clip_dict(c)
    st = db.get(Stream, c.stream_id)
    d.update(
        stream_title=st.title if st else "", credit=st.credit if st else "",
        transcript=c.transcript, subtitles=c.subtitles or [], subtitles_enabled=c.subtitles_enabled,
        subtitle_style=c.subtitle_style, trim_start=c.trim_start, trim_end=c.trim_end,
        source_duration=round(c.t_end - c.t_start, 2), source_available=bool(c.source_path and Path(c.source_path).exists()),
        vision_checked=c.vision_checked, publications=[pub_dict(p) for p in c.publications],
    )
    return d


@router.patch("/{cid}")
def patch_clip(cid: int, body: ClipPatch, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    data = body.model_dump(exclude_none=True)
    src_dur = c.t_end - c.t_start
    if "subtitles" in data:
        data["subtitles"] = subtitles.validate_cues(data["subtitles"], src_dur)
    if "hashtags" in data:
        data["hashtags"] = detector.clean_hashtags(data["hashtags"])
    if "layout" in data and data["layout"] not in ("auto", "track", "blur"):
        raise HTTPException(400, "Encuadre no válido")
    if "subtitle_style" in data and data["subtitle_style"] not in subtitles.STYLES:
        raise HTTPException(400, "Estilo no válido")
    for k in ("trim_start", "trim_end"):
        if k in data:
            data[k] = max(0.0, min(float(data[k]), src_dur))
    ts, te = data.get("trim_start", c.trim_start), data.get("trim_end", c.trim_end)
    if ("trim_start" in data or "trim_end" in data) and src_dur - ts - te < 3:
        raise HTTPException(400, "El recorte deja menos de 3 segundos")
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    return get_clip(cid, db)


@router.post("/{cid}/render")
def rerender(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    if not (c.source_path and Path(c.source_path).exists()):
        raise HTTPException(409, "El vídeo fuente de este clip ya no está disponible")
    if c.status == "rendering":
        raise HTTPException(409, "Ya se está renderizando")
    c.status = "rendering"
    db.commit()
    publish("clip", tasks.clip_dict(c))
    tasks.enqueue_render(cid)
    return {"ok": True}


@router.post("/{cid}/discard")
def discard(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    c.status = "discarded"
    c.error = "Descartado manualmente"
    db.commit()
    publish("clip", tasks.clip_dict(c))
    return {"ok": True}


@router.delete("/{cid}")
def delete_clip(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    delete_clip_files(c)
    db.delete(c)
    db.commit()
    publish("clip_deleted", {"id": cid})
    return {"ok": True}
