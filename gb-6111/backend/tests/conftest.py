import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://workbench@/workbench?host=/tmp/pgtest&port=55432",
)
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "test-password")
os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")

from scripts.seed_demo import setup_schema  # noqa: E402
from src.config.resources import engine  # noqa: E402

LIBRARY_TABLES = (
    "library_members, library_readings, library_volumes, library_visits, library_organs"
)


@pytest.fixture(scope="session")
def db_engine():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - 无数据库时跳过
        pytest.skip(f"PostgreSQL 不可用：{exc}")
    test_engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    with test_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        # 在同一连接、同一事务内建表，避免 DDL 与未提交事务互锁。
        setup_schema(conn)
    yield test_engine


@pytest.fixture()
def clean_library(db_engine):
    """在提交型测试（并发、跨连接读取）开始前清空维修库。"""
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {LIBRARY_TABLES} RESTART IDENTITY CASCADE"))
    yield db_engine


@pytest.fixture()
def db(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {LIBRARY_TABLES} RESTART IDENTITY CASCADE"))
    conn = db_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()
