from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..config import get_settings
from ..db import Clip, Publication, Stream, get_db
from ..events import activity, get_redis
from ..security import require_auth
from ..services import tiktok
from ..workers import tasks
from .clips import pub_dict

router = APIRouter()
auth = [Depends(require_auth)]


class PublishIn(BaseModel):
    mode: str  # direct | inbox
    caption: str
    privacy_level: str | None = None
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False
    is_aigc: bool = False
    consent: bool = False  # el usuario aceptó explícitamente publicar este clip


@router.get("/api/tiktok/status", dependencies=auth)
def status(db: Session = Depends(get_db)):
    s = get_settings()
    acc = tiktok.get_account(db)
    return {
        "configured": s.tiktok_configured,
        "redirect_uri": s.effective_tiktok_redirect,
        "scopes": s.tiktok_scopes.split(","),
        "account": None if acc is None else {
            "display_name": acc.display_name, "avatar_url": acc.avatar_url, "open_id": acc.open_id,
            "scopes": acc.scopes, "refresh_expires_at": acc.refresh_expires_at.isoformat(),
        },
    }


@router.get("/api/tiktok/connect", dependencies=auth)
def connect():
    if not get_settings().tiktok_configured:
        raise HTTPException(400, "Configura TIKTOK_CLIENT_KEY y TIKTOK_CLIENT_SECRET en el servidor")
    return {"url": tiktok.authorize_url(tiktok.make_state(get_redis()))}


@router.get("/api/tiktok/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             error_description: str | None = None, db: Session = Depends(get_db)):
    # Protección CSRF: el "state" debe haber sido emitido por este servidor (caduca en 15 min).
    if not state or not tiktok.consume_state(get_redis(), state):
        return RedirectResponse("/#/publicaciones?tiktok=state_error")
    if error or not code:
        activity(f"TikTok denegó la autorización: {error_description or error}", None, "warn")
        return RedirectResponse("/#/publicaciones?tiktok=denied")
    try:
        acc = tiktok.exchange_code(db, code)
    except tiktok.TikTokError as exc:
        activity(f"Error al conectar TikTok: {exc}", None, "error")
        return RedirectResponse("/#/publicaciones?tiktok=error")
    activity(f"Cuenta de TikTok conectada: {acc.display_name or acc.open_id}")
    return RedirectResponse("/#/publicaciones?tiktok=ok")


@router.post("/api/tiktok/disconnect", dependencies=auth)
def disconnect(db: Session = Depends(get_db)):
    acc = tiktok.get_account(db)
    if acc:
        tiktok.revoke(db, acc)
        activity("Cuenta de TikTok desconectada")
    return {"ok": True}


@router.get("/api/tiktok/creator-info", dependencies=auth)
def creator_info(db: Session = Depends(get_db)):
    acc = tiktok.get_account(db)
    if acc is None:
        raise HTTPException(400, "No hay cuenta de TikTok conectada")
    try:
        return tiktok.creator_info(tiktok.account_token(db, acc))
    except tiktok.TikTokError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/clips/{cid}/caption", dependencies=auth)
def default_caption(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    cfg = appsettings.get_section(db, "publishing")
    return {"caption": tasks.caption_for(c, db.get(Stream, c.stream_id), cfg["credit_template"])}


@router.post("/api/clips/{cid}/publish", dependencies=auth)
def publish_clip(cid: int, body: PublishIn, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c:
        raise HTTPException(404, "No existe")
    if c.status != "ready":
        raise HTTPException(409, "El clip aún no está listo")
    if not body.consent:
        raise HTTPException(400, "Debes confirmar que quieres publicar este clip")
    if body.mode not in ("direct", "inbox"):
        raise HTTPException(400, "Modo no válido")
    if body.mode == "direct" and not body.privacy_level:
        raise HTTPException(400, "Elige la privacidad de la publicación")
    acc = tiktok.get_account(db)
    if acc is None:
        raise HTTPException(400, "Conecta tu cuenta de TikTok primero")
    active = db.scalar(select(Publication).where(Publication.clip_id == cid,
                                                 Publication.status.in_(("queued", "uploading", "processing"))))
    if active:
        raise HTTPException(409, "Este clip ya se está publicando")
    p = Publication(clip_id=cid, account_id=acc.id, mode=body.mode, trigger="manual", caption=body.caption[:2200],
                    options=body.model_dump(exclude={"mode", "caption", "consent"}))
    db.add(p)
    db.commit()
    tasks.enqueue_publish(p.id)
    return pub_dict(p)


@router.get("/api/publications", dependencies=auth)
def publications(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(select(Publication).order_by(Publication.id.desc()).limit(min(limit, 200))).all()
    out = []
    for p in rows:
        d = pub_dict(p)
        d["clip_title"] = p.clip.title if p.clip else ""
        from .. import storage
        d["thumb_url"] = storage.url_for(p.clip.thumb_key) if p.clip else ""
        out.append(d)
    return out


@router.post("/api/publications/{pid}/retry", dependencies=auth)
def retry(pid: int, db: Session = Depends(get_db)):
    p = db.get(Publication, pid)
    if not p:
        raise HTTPException(404, "No existe")
    if p.status != "failed":
        raise HTTPException(409, "Solo se pueden reintentar publicaciones fallidas")
    p.status, p.error, p.publish_id = "queued", "", ""
    db.commit()
    tasks.enqueue_publish(pid)
    return pub_dict(p)
