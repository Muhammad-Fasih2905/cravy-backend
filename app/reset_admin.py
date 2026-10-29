"""Create the admin from ADMIN_EMAIL / ADMIN_PASSWORD, or reset its password if it already exists.
Run: python -m app.reset_admin"""
from sqlalchemy import select

from . import models
from .config import settings
from .db import Base, SessionLocal, engine
from .security import hash_password

Base.metadata.create_all(engine)
email = (settings.admin_email or "").strip().lower()
if not email or not settings.admin_password:
    raise SystemExit("Set ADMIN_EMAIL and ADMIN_PASSWORD first.")
with SessionLocal() as db:
    a = db.scalar(select(models.AdminUser).where(models.AdminUser.email == email))
    if a is None:
        a = models.AdminUser(email=email, password_hash=hash_password(settings.admin_password))
        db.add(a)
    else:
        a.password_hash, a.is_active = hash_password(settings.admin_password), True
    db.commit()
print("Admin ready:", email)
