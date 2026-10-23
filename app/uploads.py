"""Upload validation by file signature (never trust the filename or Content-Type)."""
import secrets
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings


def _ext(head: bytes, allow_pdf: bool) -> str | None:
    if head.startswith(b"\xff\xd8\xff"): return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return ".webp"
    if allow_pdf and head.startswith(b"%PDF-"): return ".pdf"
    return None


async def save_upload(file: UploadFile, subdir: str, max_mb: int, allow_pdf: bool = False) -> str:
    data = await file.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"File is too large (max {max_mb} MB)")
    ext = _ext(data[:12], allow_pdf)
    if ext is None:
        raise HTTPException(422, "Unsupported file type. Use JPG, PNG or WEBP" + (" or PDF." if allow_pdf else "."))
    folder = Path(settings.upload_dir) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ext
    (folder / name).write_bytes(data)
    return name
