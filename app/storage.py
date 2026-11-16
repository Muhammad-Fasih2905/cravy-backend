"""Where uploaded files (product photos, payment screenshots) are kept.

STORAGE_BACKEND=disk (default, your PC): files go to UPLOAD_DIR.
STORAGE_BACKEND=db   (live host)       : files go into the Postgres table `stored_files`, so they are as safe as the
                                         orders themselves and survive every redeploy (the host's disk is wiped).
Reading looks in the database first (db mode) and then on disk, so nothing already on disk is orphaned."""
from pathlib import Path

from .config import settings

_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}


def use_db() -> bool:
    return settings.storage_backend.strip().lower() == "db"


def content_type(name: str) -> str:
    return _TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def put(subdir: str, name: str, data: bytes) -> None:
    if use_db():
        from . import models
        from .db import SessionLocal

        with SessionLocal() as db:
            db.merge(models.StoredFile(subdir=subdir, name=name, content_type=content_type(name), data=data))
            db.commit()
        return
    folder = Path(settings.upload_dir) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def get(subdir: str, name: str) -> tuple[bytes, str] | None:
    name = Path(name).name  # never allow a path
    if use_db():
        from . import models
        from .db import SessionLocal

        with SessionLocal() as db:
            row = db.get(models.StoredFile, (subdir, name))
            if row is not None:
                return bytes(row.data), row.content_type
    f = Path(settings.upload_dir) / subdir / name
    if f.is_file():
        return f.read_bytes(), content_type(name)
    return None
