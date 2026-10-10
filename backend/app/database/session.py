"""Database engine and session handling.

Sync SQLAlchemy is deliberate: the real bottleneck for this service is Buffer's
rate limit (100 requests per 15 minutes), not local I/O. Sync sessions keep the
data layer simple and are executed in FastAPI's threadpool.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.database.base import Base

_settings = get_settings()


def _make_engine() -> Engine:
    kwargs: dict = {"echo": _settings.sql_echo, "future": True}
    if _settings.is_sqlite:
        # SQLite needs this to allow FastAPI's threadpool usage.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(_settings.database_url, **kwargs)


engine: Engine = _make_engine()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover
    """Enable FK enforcement and WAL so SQLite behaves predictably."""
    if not _settings.is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and startup seeding."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create missing tables, then reconcile columns on existing ones.

    `create_all` only ever ADDS tables. It never alters a table that already
    exists, so a newly added column would be silently absent from an
    established database and every query touching it would fail at runtime.
    Rather than introduce a migration tool for a handful of additive changes,
    new nullable/defaulted columns are added in place here. This is safe to
    run repeatedly and never drops or rewrites anything.
    """
    from app import models  # noqa: F401  (ensure models are imported)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


#: Tables whose schema is fully managed by `create_all` from the start, plus
#: any table holding user data. Only additive changes are ever applied.
_ADDITIVE_ONLY = True


def _add_missing_columns() -> None:
    """ALTER TABLE ... ADD COLUMN for anything the model declares but the
    database lacks.

    Intentionally conservative: a column is only added when it is nullable or
    has a default, because adding one to a populated table without either
    would fail or invent data.
    """
    import logging

    from sqlalchemy import inspect, text
    from sqlalchemy.exc import SQLAlchemyError

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all already made it

        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if column.primary_key:
                continue  # never backfill an identity column

            has_default = column.default is not None or column.server_default is not None
            nullable = bool(column.nullable)
            if not (nullable or has_default):
                # Would require inventing a value for existing rows.
                logging.getLogger("app.database").warning(
                    "db.column_missing_no_default", extra={"context": {"table": table.name, "column": column.name}}
                )
                continue

            ddl = _column_ddl(table.name, column)
            if ddl is None:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                logging.getLogger("app.database").info(
                    "db.column_added", extra={"context": {"table": table.name, "column": column.name}}
                )
            except SQLAlchemyError:
                # Never block startup on a reconciliation problem.
                logging.getLogger("app.database").warning(
                    "db.column_add_failed", extra={"context": {"table": table.name, "column": column.name}}
                )


def _column_ddl(table_name: str, column) -> str | None:
    """Render a portable ADD COLUMN statement, or None if it is not safe."""
    from sqlalchemy.dialects import postgresql

    type_text = column.type.compile(dialect=postgresql.dialect())
    lowered = type_text.lower()
    # SQLite accepts most type names but not the PostgreSQL-specific ones.
    if lowered.startswith("character varying"):
        type_text = "TEXT"
    elif lowered.startswith("timestamp with"):
        type_text = "TIMESTAMP"

    literal = _literal_default(column)

    parts = [column.name, type_text]
    if not column.nullable:
        parts.append("NOT NULL")
        # A NOT NULL column can only be added when there is something to put
        # in the existing rows.
        if literal is None:
            return None
        parts.append(f"DEFAULT {literal}")
    elif literal is not None:
        parts.append(f"DEFAULT {literal}")

    return f"ALTER TABLE {table_name} ADD COLUMN {' '.join(parts)}"


def _literal_default(column) -> str | None:
    """A SQL literal for the column's default, or None if it has none."""
    from sqlalchemy import Boolean, Integer, Numeric

    server_default = column.server_default
    if server_default is not None:
        text_value = getattr(server_default, "arg", None)
        if text_value is not None:
            return getattr(text_value, "text", str(text_value))

    python_default = column.default
    if python_default is None:
        return None

    arg = getattr(python_default, "arg", None)
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    # A callable default (uuid4, now, ...) cannot be inlined reliably, but a
    # type-appropriate zero value is safe for an additive column.
    if isinstance(column.type, Boolean):
        return "0"
    if isinstance(column.type, Integer):
        return "0"
    if isinstance(column.type, (Numeric,)):
        return "0"
    return None