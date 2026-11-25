"""API + servidor del panel PWA.

Ejecutar:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import appsettings
from .config import get_settings
from .db import Clip, Publication, Stream, get_db, init_db
from .events import CHANNEL, get_redis
from .routers import clips, streams, tiktok
from .security import COOKIE, check_password, make_session_token, require_auth, verify_session_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="ClipRadar AI", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(streams.router)
app.include_router(clips.router)
app.include_router(tiktok.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if not settings.admin_password:
        logging.getLogger("clipradar").error("ADMIN_PASSWORD no está definido: nadie podrá entrar al panel")


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    password: str


_login_attempts: dict[str, list[float]] = {}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _login_attempts.get(ip, []) if time.time() - t < 600]
    if len(recent) >= 10:
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
    if not check_password(body.password):
        _login_attempts[ip] = recent + [time.time()]
        raise HTTPException(401, "Contraseña incorrecta")
    _login_attempts.pop(ip, None)
    response.set_cookie(COOKIE, make_session_token(), max_age=settings.session_days * 86400, httponly=True,
                        secure=settings.public_url.startswith("https"), samesite="lax", path="/")
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    return {"authenticated": verify_session_token(request.cookies.get(COOKIE)),
            "password_configured": bool(settings.admin_password)}


# ------------------------------------------------------------------ sistema
@app.get("/api/system/status", dependencies=[Depends(require_auth)])
def system_status(db: Session = Depends(get_db)):
    checks: dict[str, dict] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True, "detail": settings.database_url.split("://")[0]}
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": str(exc)[:200]}
    try:
        r = get_redis()
        r.ping()
        checks["redis"] = {"ok": True, "detail": "conectado"}
        hb = r.get("clipradar:monitor:heartbeat")
        age = time.time() - float(hb) if hb else None
        checks["monitor"] = {"ok": age is not None and age < 30,
                             "detail": f"latido hace {age:.0f}s" if age is not None else "sin latido: ¿está corriendo?"}
        from rq import Worker
        from redis import Redis
        workers = Worker.all(connection=Redis.from_url(settings.redis_url))
        qs = sorted({q for w in workers for q in w.queue_names()})
        checks["workers"] = {"ok": {"render", "publish"} <= set(qs),
                             "detail": f"{len(workers)} activos ({', '.join(qs) or 'ninguna cola'})"}
    except Exception as exc:
        checks["redis"] = {"ok": False, "detail": str(exc)[:200]}
    checks["ffmpeg"] = {"ok": bool(shutil.which("ffmpeg")), "detail": shutil.which("ffmpeg") or "no encontrado"}
    checks["yt_dlp"] = {"ok": bool(shutil.which("yt-dlp")), "detail": shutil.which("yt-dlp") or "no encontrado"}
    checks["ai"] = {"ok": settings.ai_configured,
                    "detail": f"{settings.ai_model} (esfuerzo {settings.ai_effort})" if settings.ai_configured
                    else "falta ANTHROPIC_API_KEY"}
    if settings.transcribe_backend == "whispercpp":
        ok = bool(shutil.which(settings.whispercpp_bin)) and Path(settings.whispercpp_model or "-").is_file()
        checks["transcription"] = {"ok": ok, "detail": f"whisper.cpp {Path(settings.whispercpp_model).name} ({settings.whisper_language})"
                                   if ok else "falta whisper.cpp o su modelo (WHISPERCPP_BIN / WHISPERCPP_MODEL)"}
    else:
        checks["transcription"] = {"ok": True, "detail": f"Whisper {settings.whisper_model} local ({settings.whisper_language})"}
    checks["tiktok"] = {"ok": settings.tiktok_configured,
                        "detail": "credenciales configuradas" if settings.tiktok_configured
                        else "faltan TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET"}
    checks["youtube_api"] = {"ok": bool(settings.youtube_api_key),
                             "detail": "YouTube Data API activa" if settings.youtube_api_key
                             else "opcional: metadatos vía yt-dlp"}
    du = shutil.disk_usage(settings.data_dir)
    return {"checks": checks, "storage_backend": settings.storage_backend, "public_url": settings.public_url,
            "disk": {"total": du.total, "used": du.used, "free": du.free}}


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


@app.get("/api/stats", dependencies=[Depends(require_auth)])
def stats(db: Session = Depends(get_db)):
    by_status = dict(db.execute(select(Clip.status, func.count()).group_by(Clip.status)).all())
    by_cat = dict(db.execute(select(Clip.category, func.count()).where(Clip.status == "ready")
                             .group_by(Clip.category)).all())
    pubs = dict(db.execute(select(Publication.status, func.count()).group_by(Publication.status)).all())
    avg = db.scalar(select(func.avg(Clip.score)).where(Clip.status == "ready"))
    live = db.scalar(select(func.count()).select_from(Stream).where(Stream.status == "live")) or 0
    monitored = db.scalar(select(func.sum(Stream.seconds_analyzed))) or 0
    clip_bytes = db.scalar(select(func.sum(Clip.file_size)).where(Clip.status == "ready")) or 0
    return {
        "clips_by_status": by_status, "clips_total": sum(by_status.values()),
        "clips_ready": by_status.get("ready", 0), "clips_by_category": by_cat,
        "publications": pubs, "avg_score": round(avg, 1) if avg is not None else None,
        "streams_live": live, "hours_analyzed": round(monitored / 3600, 2),
        "storage": {
            "clips_bytes": int(clip_bytes),
            "buffer_bytes": _dir_size(settings.segments_dir),
            "work_bytes": _dir_size(settings.work_dir),
        },
    }


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_app_settings(db: Session = Depends(get_db)):
    return {k: appsettings.get_section(db, k) for k in appsettings.DEFAULTS}


@app.put("/api/settings/{section}", dependencies=[Depends(require_auth)])
def put_app_settings(section: str, values: dict, db: Session = Depends(get_db)):
    if section not in appsettings.DEFAULTS:
        raise HTTPException(404, "Sección desconocida")
    if section == "publishing" and values.get("auto_publish") and not (
            values.get("consent_confirmed") or appsettings.get_section(db, section)["consent_confirmed"]):
        raise HTTPException(400, "Para activar la publicación automática debes confirmar tu consentimiento")
    return appsettings.set_section(db, section, values)


# ------------------------------------------------------------------ tiempo real (SSE)
@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events(request: Request):
    async def gen():
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        ps = r.pubsub()
        await ps.subscribe(CHANNEL)
        try:
            yield "retry: 3000\n\n"
            last_ping = time.time()
            while not await request.is_disconnected():
                msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    yield f"data: {msg['data']}\n\n"
                if time.time() - last_ping > 15:
                    last_ping = time.time()
                    yield f"data: {json.dumps({'type': 'ping', 'ts': last_ping})}\n\n"
                await asyncio.sleep(0.05)
        finally:
            await ps.unsubscribe(CHANNEL)
            await ps.aclose()
            await r.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ archivos
@app.get("/media/{key:path}", dependencies=[Depends(require_auth)])
def media_file(key: str):
    base = settings.clips_dir.resolve()
    path = (base / key).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "No existe")
    return FileResponse(path)


@app.get("/api/clips/{cid}/source.mp4", dependencies=[Depends(require_auth)])
def clip_source(cid: int, db: Session = Depends(get_db)):
    c = db.get(Clip, cid)
    if not c or not c.source_path or not Path(c.source_path).is_file():
        raise HTTPException(404, "Fuente no disponible")
    return FileResponse(c.source_path, media_type="video/mp4")


@app.get("/api/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ PWA
if FRONTEND.exists():
    @app.get("/sw.js")
    def sw():
        return FileResponse(FRONTEND / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
