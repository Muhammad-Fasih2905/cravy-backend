import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .db import Base, SessionLocal, engine, ensure_schema
from .payments import ensure_payment_defaults
from .routers import admin, catalog, orders

logging.basicConfig(level=logging.INFO)



class NoStore:
    """Orders and admin data must never be served from a browser/proxy cache, or a refresh could show stale state."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/api/admin", "/api/orders")):
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start" and not any(k.lower() == b"cache-control" and b"no-cache" in v for k, v in message.get("headers", [])):
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped)


app = FastAPI(title="Cravy API", version="0.3.0")
app.add_middleware(NoStore)
app.add_middleware(GZipMiddleware, minimum_size=500)
# Websites allowed to call this API from the browser. The live shop is always allowed, on top of whatever
# CORS_ORIGINS lists (comma separated). A trailing "/" is ignored: browsers send the origin without it.
LIVE_SITE_ORIGINS = {"https://eatcravy.com", "https://www.eatcravy.com", "http://eatcravy.com", "http://www.eatcravy.com", "https://eatcravy.netlify.app"}
CORS_ALLOWED = sorted({o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()} | LIVE_SITE_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED,
    allow_origin_regex=r"https://[a-z0-9-]+--eatcravy\.netlify\.app",  # Netlify deploy previews of the same site
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
)
if settings.storage_backend.strip().lower() == "db":  # photos live in the database, so a redeploy never loses them
    from fastapi import HTTPException
    from fastapi.responses import Response
    from . import storage

    @app.get("/media/products/{name}", include_in_schema=False)
    def _product_media(name: str):
        got = storage.get("products", name)
        if got is None:
            raise HTTPException(404, "Not found")
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "public, max-age=31536000, immutable"})  # names are random: a changed photo gets a new name
else:
    Path(settings.upload_dir, "products").mkdir(parents=True, exist_ok=True)
    app.mount("/media/products", StaticFiles(directory=Path(settings.upload_dir) / "products"), name="product-media")  # receipts are NOT served
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(admin.router)
try:  # Django-style admin site at /admin (super admin only); if it cannot start, the API still runs
    from .superadmin import setup_superadmin
    setup_superadmin(app)
except Exception as _exc:  # noqa: BLE001
    logging.exception("Super admin site (/admin) could not start; the API is not affected")
    _admin_err = f"{type(_exc).__name__}: {_exc}"

    from fastapi.responses import HTMLResponse

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    def _admin_unavailable():
        return HTMLResponse(
            "<h2>Super admin site could not start</h2>"
            f"<pre>{_admin_err}</pre>"
            "<p>Fix: stop the server, then run <code>pip install -r requirements.txt</code> "
            "(needs sqladmin, itsdangerous, starlette==0.41.3, greenlet) and start again.</p>",
            status_code=503,
        )

@app.on_event("startup")
def _create_tables_for_dev():
    # Dev convenience. In production use Alembic: `alembic upgrade head`.
    Base.metadata.create_all(engine)
    ensure_schema()
    with SessionLocal() as db:  # make sure COD + Easypaisa are configured even on a fresh database
        ensure_payment_defaults(db)
    _bootstrap_data()


def _bootstrap_data():
    """Fresh database? Add the starter menu and the admin account so the site works without a manual seed step.
    Only fills what is missing; never overwrites or re-adds things you changed or deleted."""
    from sqlalchemy import select
    from . import models, seed
    from .security import hash_password

    with SessionLocal() as db:
        if db.scalar(select(models.Category.id).limit(1)) is None and not settings.is_production:
            seed.run()  # demo menu + DEMO10 coupon: local/dev only, never on the live database
        try:  # the real Cravy menu; only if it is not in yet (see app/menu_seed.py)
            from . import menu_seed
            menu_seed.ensure_menu(db)
            menu_seed.ensure_default_pairings(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logging.exception("Menu import failed (the site keeps running)")
        email = (settings.admin_email or "").strip().lower()
        if email and settings.admin_password:
            if db.scalar(select(models.AdminUser).where(models.AdminUser.email == email)) is None:
                db.add(models.AdminUser(email=email, password_hash=hash_password(settings.admin_password)))
                db.commit()
                logging.info("Created admin user %s", email)
        elif db.scalar(select(models.AdminUser.id).limit(1)) is None:
            logging.warning("No admin user exists and ADMIN_EMAIL / ADMIN_PASSWORD are not set: admin login will fail.")


@app.get("/health")
def health():
    return {"ok": True}