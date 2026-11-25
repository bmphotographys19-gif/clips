"""Integración oficial con TikTok: Login Kit (OAuth 2.0) + Content Posting API.

Documentación: https://developers.tiktok.com/doc/content-posting-api-get-started
- Publicación directa (``video.publish``): requiere consultar creator_info y que
  el usuario elija la privacidad. Las apps sin auditar solo pueden publicar en
  privado (SELF_ONLY).
- Subida a bandeja (``video.upload``): el vídeo llega como borrador a la app de
  TikTok y el usuario termina la publicación allí.
"""
from __future__ import annotations

import base64
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import TikTokAccount
from ..security import decrypt, encrypt

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


def _check(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError as exc:
        raise TikTokError(f"Respuesta no válida de TikTok (HTTP {resp.status_code})") from exc
    err = body.get("error") or {}
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise TikTokError(f"{err.get('code')}: {err.get('message', '')}")
    if resp.status_code >= 400:
        raise TikTokError(body.get("error_description") or f"HTTP {resp.status_code}")
    return body


# --------------------------------------------------------------------------- OAuth
def make_state(redis_client) -> str:
    """El estado lleva el origen del panel para que callback.html pueda reenviarlo."""
    nonce = secrets.token_urlsafe(24)
    redis_client.setex(f"tiktok:state:{nonce}", 900, "1")
    origin = base64.urlsafe_b64encode(get_settings().public_url.rstrip("/").encode()).decode().rstrip("=")
    return f"cr.{origin}.{nonce}"


def consume_state(redis_client, state: str) -> bool:
    nonce = state.rsplit(".", 1)[-1]
    return bool(redis_client.delete(f"tiktok:state:{nonce}"))


def authorize_url(state: str) -> str:
    s = get_settings()
    q = {
        "client_key": s.tiktok_client_key,
        "scope": s.tiktok_scopes,
        "response_type": "code",
        "redirect_uri": s.effective_tiktok_redirect,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(q)}"


def _token_request(data: dict) -> dict:
    s = get_settings()
    r = httpx.post(
        f"{API}/v2/oauth/token/",
        data={"client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret, **data},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    body = _check(r)
    if "access_token" not in body:
        raise TikTokError(body.get("error_description") or "TikTok no devolvió token")
    return body


def _store_tokens(db: Session, tok: dict, account: TikTokAccount | None = None) -> TikTokAccount:
    now = datetime.now(timezone.utc)
    if account is None:
        account = db.scalar(select(TikTokAccount).where(TikTokAccount.open_id == tok["open_id"]))
    if account is None:
        account = TikTokAccount(open_id=tok["open_id"], access_token_enc="", refresh_token_enc="",
                                expires_at=now, refresh_expires_at=now)
        db.add(account)
    account.access_token_enc = encrypt(tok["access_token"])
    account.refresh_token_enc = encrypt(tok["refresh_token"])
    account.expires_at = now + timedelta(seconds=int(tok.get("expires_in", 86400)) - 120)
    account.refresh_expires_at = now + timedelta(seconds=int(tok.get("refresh_expires_in", 31536000)))
    account.scopes = tok.get("scope", account.scopes)
    db.commit()
    return account


def exchange_code(db: Session, code: str) -> TikTokAccount:
    s = get_settings()
    tok = _token_request({"code": code, "grant_type": "authorization_code",
                          "redirect_uri": s.effective_tiktok_redirect})
    account = _store_tokens(db, tok)
    try:
        info = user_info(account_token(db, account))
        account.display_name = info.get("display_name", "")
        account.avatar_url = info.get("avatar_url", "")
        db.commit()
    except TikTokError:
        pass
    return account


def account_token(db: Session, account: TikTokAccount) -> str:
    """Token vigente; lo renueva si está a punto de caducar."""
    now = datetime.now(timezone.utc)
    exp = account.expires_at if account.expires_at.tzinfo else account.expires_at.replace(tzinfo=timezone.utc)
    if exp > now:
        return decrypt(account.access_token_enc)
    rexp = account.refresh_expires_at
    rexp = rexp if rexp.tzinfo else rexp.replace(tzinfo=timezone.utc)
    if rexp <= now:
        raise TikTokError("La autorización de TikTok caducó: vuelve a conectar la cuenta")
    tok = _token_request({"grant_type": "refresh_token", "refresh_token": decrypt(account.refresh_token_enc)})
    tok.setdefault("open_id", account.open_id)
    _store_tokens(db, tok, account)
    return tok["access_token"]


def revoke(db: Session, account: TikTokAccount) -> None:
    s = get_settings()
    try:
        httpx.post(f"{API}/v2/oauth/revoke/", data={
            "client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret,
            "token": decrypt(account.access_token_enc)}, timeout=20)
    finally:
        db.delete(account)
        db.commit()


def get_account(db: Session) -> TikTokAccount | None:
    return db.scalar(select(TikTokAccount).order_by(TikTokAccount.id.desc()))


# --------------------------------------------------------------------------- API
def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def user_info(token: str) -> dict:
    r = httpx.get(f"{API}/v2/user/info/", params={"fields": "open_id,avatar_url,display_name"},
                  headers=_auth(token), timeout=20)
    return _check(r).get("data", {}).get("user", {})


def creator_info(token: str) -> dict:
    r = httpx.post(f"{API}/v2/post/publish/creator_info/query/", headers=_auth(token), content=b"{}", timeout=20)
    return _check(r).get("data", {})


def _chunk_plan(size: int) -> tuple[int, int]:
    if size <= MAX_CHUNK:
        return size, 1
    chunk = 10 * 1024 * 1024
    return chunk, math.floor(size / chunk)


def init_upload(token: str, video: Path, mode: str, post_info: dict | None) -> dict:
    size = video.stat().st_size
    chunk, count = _chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    if mode == "direct":
        url = f"{API}/v2/post/publish/video/init/"
        body = {"post_info": post_info or {}, "source_info": source}
    else:
        url = f"{API}/v2/post/publish/inbox/video/init/"
        body = {"source_info": source}
    r = httpx.post(url, headers=_auth(token), content=json.dumps(body).encode(), timeout=30)
    data = _check(r).get("data", {})
    data.update(chunk_size=chunk, chunk_count=count, size=size)
    return data


def upload_file(upload_url: str, video: Path, chunk: int, count: int) -> None:
    size = video.stat().st_size
    with video.open("rb") as fh, httpx.Client(timeout=600) as client:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            r = client.put(upload_url, content=data, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            })
            if r.status_code not in (200, 201, 206):
                raise TikTokError(f"Fallo al subir el fragmento {i + 1}/{count}: HTTP {r.status_code}")


def fetch_status(token: str, publish_id: str) -> dict:
    r = httpx.post(f"{API}/v2/post/publish/status/fetch/", headers=_auth(token),
                   content=json.dumps({"publish_id": publish_id}).encode(), timeout=20)
    return _check(r).get("data", {})
