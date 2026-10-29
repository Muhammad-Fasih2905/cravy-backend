from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

# Railway gives postgres:// or postgresql:// -> make SQLAlchemy use the psycopg (v3) driver
_url = settings.database_url
if _url.startswith("postgres://"):
    _url = "postgresql+psycopg://" + _url[len("postgres://"):]
elif _url.startswith("postgresql://"):
    _url = "postgresql+psycopg://" + _url[len("postgresql://"):]

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
