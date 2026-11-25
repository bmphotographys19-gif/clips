"""Autenticación del panel (un único propietario) y cifrado de tokens."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from .config import get_settings

COOKIE = "cr_session"


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    exp = int(time.time()) + get_settings().session_days * 86400
    payload = f"owner.{exp}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def verify_session_token(token: str | None) -> bool:
    if not token or token.count(".") != 3:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), sig):
        return False
    try:
        exp = int(payload.split(".")[1])
    except ValueError:
        return False
    return exp > time.time()


def check_password(candidate: str) -> bool:
    real = get_settings().admin_password
    return bool(real) and hmac.compare_digest(candidate.encode(), real.encode())


def require_auth(request: Request) -> None:
    if not verify_session_token(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Sesión no válida")


def encrypt(value: str) -> str:
    return Fernet(get_settings().fernet_key).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return Fernet(get_settings().fernet_key).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("No se pudo descifrar el token (¿cambió SECRET_KEY?)") from exc
