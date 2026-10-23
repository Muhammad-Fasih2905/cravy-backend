import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .db import Base, engine
from .routers import admin, catalog, orders

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Cravy API", version="0.2.0")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
)
Path(settings.upload_dir, "products").mkdir(parents=True, exist_ok=True)
app.mount("/media/products", StaticFiles(directory=Path(settings.upload_dir) / "products"), name="product-media")  # receipts are NOT served
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.on_event("startup")
def _create_tables_for_dev():
    # Dev convenience. In production use Alembic: `alembic upgrade head`.
    Base.metadata.create_all(engine)


@app.get("/health")
def health():
    return {"ok": True}
