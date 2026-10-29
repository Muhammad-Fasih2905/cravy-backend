"""Password hashing (scrypt) and signed expiring admin tokens, standard library only."""
import hashlib, hmac, os, time

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .db import get_db


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    return f"{salt.hex()}:{hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":")
        return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex(), h)
    except Exception:
        return False


def _sig(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(uid: int, ttl: int = 8 * 3600) -> str:
    p = f"{uid}.{int(time.time()) + ttl}"
    return f"{p}.{_sig(p)}"


def _admin_from_token(raw: str, db: Session) -> models.AdminUser:
    try:
        uid, exp, sig = raw.removeprefix("Bearer ").strip().split(".")
        if not hmac.compare_digest(sig, _sig(f"{uid}.{exp}")) or int(exp) < time.time():
            raise ValueError
        admin = db.get(models.AdminUser, int(uid))
    except Exception:
        raise HTTPException(401, "Not authenticated")
    if admin is None or not admin.is_active:
        raise HTTPException(401, "Not authenticated")
    return admin


def get_admin(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> models.AdminUser:
    """Every admin route depends on this: authorization is enforced on the server, not the UI."""
    return _admin_from_token(authorization or "", db)


def get_admin_flex(authorization: str | None = Header(default=None), token: str | None = Query(default=None),
                   db: Session = Depends(get_db)) -> models.AdminUser:
    """Same check, but the token may also come as ?token=... for things a browser opens without custom headers
    (the live EventSource stream, the printable slip, receipt images)."""
    return _admin_from_token(authorization or token or "", db)
