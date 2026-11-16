import pytest

from app.db import check_environment

SQLITE = "sqlite:///./cravy.db"
NEON = "postgresql+psycopg://u:p@ep-cool-123.us-east-2.aws.neon.tech/neondb?sslmode=require"
LOCAL_PG = "postgresql+psycopg://u:p@localhost:5432/cravy"


def test_production_refuses_sqlite():
    with pytest.raises(RuntimeError):
        check_environment(SQLITE, production=True)


def test_production_accepts_postgres():
    check_environment(NEON, production=True)


def test_dev_refuses_remote_database():
    with pytest.raises(RuntimeError):
        check_environment(NEON, production=False)


def test_dev_allows_sqlite_and_local_postgres():
    check_environment(SQLITE, production=False)
    check_environment(LOCAL_PG, production=False)


def test_dev_remote_only_with_explicit_override():
    check_environment(NEON, production=False, allow_remote_dev=True)
