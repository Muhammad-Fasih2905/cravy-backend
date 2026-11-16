"""One-off helper: copy files that are already in UPLOAD_DIR (products/ and receipts/) into the database.
Only adds what is missing; never overwrites. Run on a machine that has both the files and the live DATABASE_URL:
    ALLOW_REMOTE_DB_IN_DEV=true python -m app.migrate_uploads
"""
from pathlib import Path

from . import models
from .config import settings
from .db import Base, SessionLocal, engine
from .storage import content_type

Base.metadata.create_all(engine)
added = 0
with SessionLocal() as db:
    for sub in ("products", "receipts"):
        folder = Path(settings.upload_dir) / sub
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.is_file() and db.get(models.StoredFile, (sub, f.name)) is None:
                db.add(models.StoredFile(subdir=sub, name=f.name, content_type=content_type(f.name), data=f.read_bytes()))
                added += 1
    db.commit()
print(f"Copied {added} file(s) into the database.")
