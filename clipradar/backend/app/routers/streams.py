from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ActivityLog, Clip, Stream, TranscriptLine, get_db
from ..events import activity, publish
from ..security import require_auth
from ..services import detector, sources
from ..workers.monitor import stream_dict

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class StreamIn(BaseModel):
    url: str
    permission_note: str = Field(min_length=3, description="Cómo te autorizó el creador")
    credit: str = ""
    min_score: int = 60
    categories: list[str] = []
    auto_publish: bool = False
    start: bool = True


class StreamPatch(BaseModel):
    title: str | None = None
    credit: str | None = None
    permission_note: str | None = None
    min_score: int | None = None
    categories: list[str] | None = None
    auto_publish: bool | None = None


def _with_counts(db: Session, st: Stream) -> dict:
    d = stream_dict(st)
    rows = db.execute(select(Clip.status, func.count()).where(Clip.stream_id == st.id).group_by(Clip.status)).all()
    d["clip_counts"] = {k: v for k, v in rows}
    return d


@router.get("/streams")
def list_streams(db: Session = Depends(get_db)):
    return [_with_counts(db, s) for s in db.scalars(select(Stream).order_by(Stream.created_at.desc()))]


@router.post("/streams")
def create_stream(body: StreamIn, db: Session = Depends(get_db)):
    try:
        stype, vid = sources.classify(body.url)
    except sources.SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    title, channel = "", ""
    if stype == "youtube":
        try:
            meta = sources.youtube_metadata(vid)
            title, channel = meta.title, meta.channel
        except Exception as exc:  # metadatos opcionales: el monitor reintentará
            title = f"YouTube {vid}"
            activity(f"No se pudieron leer los metadatos de YouTube: {exc}", None, "warn")
    cats = [c for c in body.categories if c in detector.CATEGORIES]
    st = Stream(title=title or body.url[:120], channel_name=channel, source_type=stype, source_url=body.url.strip(),
                youtube_video_id=vid, permission_note=body.permission_note.strip(),
                credit=body.credit.strip() or channel, min_score=max(0, min(100, body.min_score)),
                categories=cats, auto_publish=body.auto_publish,
                desired_state="running" if body.start else "stopped")
    db.add(st)
    db.commit()
    publish("stream", stream_dict(st))
    return _with_counts(db, st)


@router.get("/streams/{sid}")
def get_stream(sid: int, db: Session = Depends(get_db)):
    st = db.get(Stream, sid)
    if not st:
        raise HTTPException(404, "No existe")
    return _with_counts(db, st)


@router.patch("/streams/{sid}")
def patch_stream(sid: int, body: StreamPatch, db: Session = Depends(get_db)):
    st = db.get(Stream, sid)
    if not st:
        raise HTTPException(404, "No existe")
    data = body.model_dump(exclude_none=True)
    if "categories" in data:
        data["categories"] = [c for c in data["categories"] if c in detector.CATEGORIES]
    if "min_score" in data:
        data["min_score"] = max(0, min(100, data["min_score"]))
    for k, v in data.items():
        setattr(st, k, v)
    db.commit()
    publish("stream", stream_dict(st))
    return _with_counts(db, st)


@router.post("/streams/{sid}/start")
def start_stream(sid: int, db: Session = Depends(get_db)):
    st = db.get(Stream, sid)
    if not st:
        raise HTTPException(404, "No existe")
    st.desired_state = "running"
    if st.status in ("idle", "stopped", "ended", "error"):
        st.status, st.status_message = "starting", "En cola para el monitor"
    db.commit()
    publish("stream", stream_dict(st))
    return stream_dict(st)


@router.post("/streams/{sid}/stop")
def stop_stream(sid: int, db: Session = Depends(get_db)):
    st = db.get(Stream, sid)
    if not st:
        raise HTTPException(404, "No existe")
    st.desired_state = "stopped"
    db.commit()
    publish("stream", stream_dict(st))
    return stream_dict(st)


@router.delete("/streams/{sid}")
def delete_stream(sid: int, db: Session = Depends(get_db)):
    st = db.get(Stream, sid)
    if not st:
        raise HTTPException(404, "No existe")
    if st.desired_state == "running" or st.status in ("live", "starting", "reconnecting"):
        raise HTTPException(409, "Detén la transmisión antes de eliminarla")
    from .clips import delete_clip_files

    for c in st.clips:
        delete_clip_files(c)
    db.delete(st)
    db.commit()
    publish("stream_deleted", {"id": sid})
    return {"ok": True}


@router.get("/streams/{sid}/transcript")
def transcript(sid: int, limit: int = 60, db: Session = Depends(get_db)):
    rows = db.scalars(select(TranscriptLine).where(TranscriptLine.stream_id == sid)
                      .order_by(TranscriptLine.t_start.desc()).limit(min(limit, 500))).all()
    return [{"t": r.t_start, "text": r.text, "loud": r.loud_spike, "scene": r.scene_change} for r in reversed(rows)]


@router.get("/activity")
def activity_log(stream_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = select(ActivityLog).order_by(ActivityLog.id.desc()).limit(min(limit, 300))
    if stream_id:
        q = q.where(ActivityLog.stream_id == stream_id)
    return [{"id": a.id, "stream_id": a.stream_id, "level": a.level, "message": a.message,
             "created_at": a.created_at.isoformat()} for a in db.scalars(q)]
