import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

log = logging.getLogger("cravy.db")


def _normalize(url: str) -> str:
    url = url.strip().strip("\"'").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _usable(url: str) -> bool:
    """A URL is usable only if it is non-empty, has no unresolved ${{...}} placeholder and parses with a real host/port."""
    if not url or "${{" in url or "{{" in url:
        return False
    try:
        u = make_url(url)
    except Exception:
        return False
    if u.get_backend_name() == "sqlite":
        return True
    return bool(u.host) and bool(u.database)


def _resolve_url() -> str:
    # 1) DATABASE_URL as configured, 2) other URL variables Railway provides, 3) separate PG* variables
    candidates = [settings.database_url, os.getenv("DATABASE_PRIVATE_URL", ""), os.getenv("DATABASE_PUBLIC_URL", "")]
    for c in candidates:
        c = _normalize(c or "")
        if _usable(c):
            return c
    host, db = os.getenv("PGHOST"), os.getenv("PGDATABASE")
    if host and db:
        built = URL.create(
            "postgresql+psycopg",
            username=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD"),
            host=host,
            port=int(os.getenv("PGPORT") or 5432),
            database=db,
        ).render_as_string(hide_password=False)
        return built
    log.error(
        "DATABASE_URL is empty or invalid (value was not resolved). Falling back to a local SQLite file; "
        "data will be LOST on redeploy. In Railway set DATABASE_URL to ${{Postgres.DATABASE_URL}} "
        "(use your Postgres service's exact name)."
    )
    return "sqlite:///./cravy.db"


_url = _resolve_url()

_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Tiny forward-only migration so an already-deployed database picks up new columns and the new order statuses.
    Safe to run on every start (it only adds what is missing)."""
    from sqlalchemy import inspect, text

    sqlite = engine.dialect.name == "sqlite"
    ts = "DATETIME" if sqlite else "TIMESTAMP WITH TIME ZONE"
    flt = "FLOAT" if sqlite else "DOUBLE PRECISION"
    wanted = {
        "orders": [("updated_at", ts), ("completed_at", ts), ("delivery_distance_km", flt), ("latitude", flt), ("longitude", flt)],
        "business_settings": [("shop_latitude", flt), ("shop_longitude", flt)],
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, typ in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {typ}"))
        if insp.has_table("orders"):  # collapse the old preparation/delivery statuses into the simple flow
            conn.execute(text("UPDATE orders SET status='COMPLETED' WHERE status='DELIVERED'"))
            conn.execute(text("UPDATE orders SET status='PENDING' WHERE status IN ('PENDING_CONFIRMATION','CONFIRMED','PREPARING','READY_FOR_DISPATCH','OUT_FOR_DELIVERY')"))
            conn.execute(text("UPDATE orders SET updated_at=created_at WHERE updated_at IS NULL"))
            conn.execute(text("UPDATE orders SET completed_at=created_at WHERE status='COMPLETED' AND completed_at IS NULL"))
    try:  # one Easypaisa transaction id can pay only one order (also covers tables created before this rule existed)
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_transaction_ref ON payments (transaction_ref)"))
    except Exception:  # noqa: BLE001  (existing duplicate ids: leave the app-level check in charge)
        pass
