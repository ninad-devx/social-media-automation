"""Migration safety.

The application has no migration framework: `init_db()` calls `create_all`
(tables only) and then an additive-only reconciliation that issues
`ALTER TABLE ... ADD COLUMN` for anything the models declare but the database
lacks. This module proves that reconciliation is:

* additive - it never drops, rewrites or retypes an existing column;
* idempotent - running it twice changes nothing the second time;
* data-preserving - existing rows survive with their values intact;
* safe against a realistic pre-existing dataset.

Every test runs against a throwaway SQLite file. Nothing here touches any
configured or production database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database.base import Base


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

NEW_COLUMNS = ("channels.archived_at", "channels.archived_reason", "publish_logs.post_type")
NEW_ATTEMPTS_COLUMNS = ("idempotency_records.attempts", "idempotency_records.last_error_code")


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """A database shaped like the pre-fix schema, with real rows in it.

    Deliberately built by hand rather than by importing the current models, so
    the columns this work added are genuinely absent - which is the situation
    an existing production deployment is in.
    """
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(160) NOT NULL,
            type VARCHAR(64) NOT NULL,
            description TEXT,
            logo VARCHAR(512),
            color VARCHAR(16),
            active BOOLEAN NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE channels (
            id VARCHAR(64) PRIMARY KEY,
            account_id VARCHAR(64) NOT NULL,
            platform VARCHAR(32) NOT NULL,
            display_name VARCHAR(120) NOT NULL,
            username VARCHAR(160),
            avatar VARCHAR(512),
            description TEXT,
            active BOOLEAN NOT NULL,
            sort_order INTEGER NOT NULL,
            integration_id VARCHAR(64),
            external_channel_id VARCHAR(64),
            external_service VARCHAR(32),
            external_username VARCHAR(160),
            connection_status VARCHAR(24) NOT NULL,
            is_locked BOOLEAN NOT NULL,
            queue_paused BOOLEAN NOT NULL,
            capabilities_json TEXT,
            last_synced_at DATETIME,
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE publish_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id VARCHAR(64) NOT NULL,
            n8n_request_id VARCHAR(64),
            idempotency_key VARCHAR(128),
            account_id VARCHAR(64) NOT NULL,
            channel_id VARCHAR(64) NOT NULL,
            platform VARCHAR(32) NOT NULL,
            content TEXT NOT NULL,
            content_preview VARCHAR(280) NOT NULL,
            media_count INTEGER NOT NULL,
            publish_mode VARCHAR(16) NOT NULL,
            scheduled_at DATETIME,
            timezone VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL,
            buffer_publication_id VARCHAR(64),
            external_link VARCHAR(1024),
            error_code VARCHAR(64),
            error_message TEXT,
            duration_ms INTEGER,
            source VARCHAR(32) NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE idempotency_records (
            key VARCHAR(128) PRIMARY KEY,
            request_fingerprint VARCHAR(64) NOT NULL,
            state VARCHAR(16) NOT NULL,
            response_status INTEGER,
            response_body TEXT,
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE integrations (
            id VARCHAR(64) PRIMARY KEY,
            provider VARCHAR(32) NOT NULL,
            display_name VARCHAR(160) NOT NULL,
            credential_encrypted TEXT NOT NULL,
            credential_hint VARCHAR(32),
            external_organization_id VARCHAR(64),
            external_organization_name VARCHAR(160),
            discovered_organizations_json TEXT,
            enabled BOOLEAN NOT NULL,
            status VARCHAR(24) NOT NULL,
            last_tested_at DATETIME,
            last_error_code VARCHAR(64),
            last_error_message TEXT,
            last_sync_at DATETIME,
            created_at DATETIME,
            updated_at DATETIME
        );
        """
    )
    conn.executescript(
        """
        INSERT INTO accounts
            (id, name, type, description, logo, color, active, sort_order, created_at, updated_at)
        VALUES
            ('company', 'Geekssort', 'brand', 'Our real brand', NULL, '#123456', 1, 0,
             '2024-01-01 00:00:00', '2024-01-01 00:00:00');
        INSERT INTO channels
            (id, account_id, platform, display_name, username, active, sort_order,
             connection_status, is_locked, queue_paused, created_at, updated_at)
        VALUES
            ('company_facebook', 'company', 'facebook', 'Facebook', 'realpage', 1, 1,
             'connected', 0, 0, '2024-01-01 00:00:00', '2024-01-01 00:00:00');
        INSERT INTO publish_logs
            (request_id, account_id, channel_id, platform, content, content_preview,
             media_count, publish_mode, timezone, status, source, created_at, updated_at)
        VALUES
            ('req_old_1', 'company', 'company_facebook', 'facebook',
             'a real post from last year', 'a real post from last year', 0, 'NOW',
             'Asia/Dhaka', 'published', 'dashboard',
             '2024-05-01 10:00:00', '2024-05-01 10:00:00');
        INSERT INTO idempotency_records
            (key, request_fingerprint, state, response_status, response_body,
             created_at, updated_at)
        VALUES
            ('old_key', 'abc123', 'completed', 200, '{"success": true}',
             '2024-05-01 10:00:00', '2024-05-01 10:00:00');
        """
    )
    conn.commit()
    conn.close()
    return path


def _reconcile(path: Path) -> None:
    """Run the application's own additive reconciliation against `path`."""
    from app import models  # noqa: F401  (ensure every model is registered)
    from app.database.session import _add_missing_columns

    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(bind=engine)
    import app.database.session as session_module

    original = session_module.engine
    session_module.engine = engine
    try:
        _add_missing_columns()
    finally:
        session_module.engine = original
        engine.dispose()


def test_legacy_database_is_really_legacy(legacy_db: Path) -> None:
    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("channels")}
        assert "archived_at" not in columns
        assert "archived_reason" not in columns
    finally:
        engine.dispose()


def test_reconciliation_adds_the_new_columns(legacy_db: Path) -> None:
    _reconcile(legacy_db)
    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        inspector = inspect(engine)
        channels = {c["name"] for c in inspector.get_columns("channels")}
        logs = {c["name"] for c in inspector.get_columns("publish_logs")}
        idempotency = {c["name"] for c in inspector.get_columns("idempotency_records")}

        for column in ("archived_at", "archived_reason"):
            assert column in channels, f"{column} was not added"
        assert "post_type" in logs
        for column in ("attempts", "last_error_code"):
            assert column in idempotency, f"{column} was not added"

        # Nothing was removed.
        for column in ("id", "account_id", "platform", "display_name", "active"):
            assert column in channels
    finally:
        engine.dispose()


def test_reconciliation_preserves_every_existing_row(legacy_db: Path) -> None:
    _reconcile(legacy_db)
    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        with engine.connect() as conn:
            account = conn.execute(
                text("SELECT name, color, description FROM accounts WHERE id='company'")
            ).one()
            assert account.name == "Geekssort"
            assert account.color == "#123456"
            assert account.description == "Our real brand"

            channel = conn.execute(
                text(
                    "SELECT display_name, username, connection_status FROM channels "
                    "WHERE id='company_facebook'"
                )
            ).one()
            assert channel.display_name == "Facebook"
            assert channel.username == "realpage"
            assert channel.connection_status == "connected"
            # New columns default to NULL, never invented values.
            archived = conn.execute(
                text("SELECT archived_at FROM channels WHERE id='company_facebook'")
            ).scalar()
            assert archived is None

            log = conn.execute(
                text(
                    "SELECT content, post_type FROM publish_logs WHERE request_id='req_old_1'"
                )
            ).one()
            assert log.content == "a real post from last year"
            assert log.post_type is None

            record = conn.execute(
                text("SELECT state, attempts FROM idempotency_records WHERE key='old_key'")
            ).one()
            assert record.state == "completed"
            assert record.attempts == 1
    finally:
        engine.dispose()


def test_reconciliation_is_idempotent(legacy_db: Path) -> None:
    _reconcile(legacy_db)
    first = _schema_fingerprint(legacy_db)
    _reconcile(legacy_db)
    _reconcile(legacy_db)
    assert _schema_fingerprint(legacy_db) == first


def test_reconciliation_never_drops_a_table(legacy_db: Path) -> None:
    _reconcile(legacy_db)
    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        names = set(inspect(engine).get_table_names())
        for table in ("accounts", "channels", "publish_logs", "idempotency_records", "integrations"):
            assert table in names, f"{table} disappeared"
    finally:
        engine.dispose()


def test_reconciliation_is_additive_only_by_construction() -> None:
    """The renderer must only ever emit ADD COLUMN."""
    from app.database.session import _column_ddl

    class Col:
        def __init__(self, name, type_, nullable=True, default=None, primary_key=False):
            self.name = name
            self.type = type_
            self.nullable = nullable
            self.default = default
            self.server_default = default
            self.primary_key = primary_key

    ddl = _column_ddl(
        "channels",
        Col("archived_at", type("T", (), {"compile": lambda self, dialect: "TIMESTAMP"})()),
    )
    assert ddl is not None
    assert ddl.upper().startswith("ALTER TABLE CHANNELS ADD COLUMN")
    for forbidden in ("DROP", "RENAME", "ALTER COLUMN", "DELETE", "TRUNCATE", "UPDATE"):
        assert forbidden not in ddl.upper(), f"{forbidden} appears in an additive DDL statement"


def test_a_column_without_a_default_is_skipped_not_invented(legacy_db: Path) -> None:
    """A NOT NULL column with no default cannot be added safely, so it is left
    out and reported rather than filled with a made-up value."""
    from app.database.session import _column_ddl

    class NoDefault:
        type = type("T", (), {"compile": lambda self, dialect: "TEXT"})()
        nullable = False
        default = None
        server_default = None
        primary_key = False
        name = "required_thing"

    assert _column_ddl("channels", NoDefault()) is None


def test_a_row_count_is_unchanged_by_the_migration(legacy_db: Path) -> None:
    engine = create_engine(f"sqlite:///{legacy_db}")
    with engine.connect() as conn:
        before = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ("accounts", "channels", "publish_logs", "idempotency_records")
        }
    engine.dispose()

    _reconcile(legacy_db)

    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        with engine.connect() as conn:
            after = {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in before
            }
        assert after == before
    finally:
        engine.dispose()


def test_the_campaign_post_format_column_reaches_an_existing_database(
    legacy_db: Path,
) -> None:
    """`campaign_posts.post_type` has to be added to a database that already
    exists, not only to a freshly created one.

    It is nullable, which is what makes the additive reconciliation safe to run
    against live rows: every post that predates the column keeps working, and
    reads as "use the platform default".
    """
    engine = create_engine(f"sqlite:///{legacy_db}")
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS campaign_posts"))
        conn.execute(
            text(
                "CREATE TABLE campaign_posts ("
                "id VARCHAR(64) PRIMARY KEY, campaign_id VARCHAR(64) NOT NULL, "
                "account_id VARCHAR(64) NOT NULL, channel_id VARCHAR(64) NOT NULL, "
                "platform VARCHAR(32) NOT NULL, timezone VARCHAR(64) NOT NULL, "
                "generation_status VARCHAR(16) NOT NULL, publishing_status VARCHAR(16) NOT NULL, "
                "is_edited BOOLEAN NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO campaign_posts (id, campaign_id, account_id, channel_id, "
                "platform, timezone, generation_status, publishing_status, is_edited) "
                "VALUES ('cp_1', 'cmp_1', 'company', 'company_facebook', 'facebook', "
                "'UTC', 'generated', 'draft', 0)"
            )
        )
        conn.commit()
    engine.dispose()

    _reconcile(legacy_db)

    engine = create_engine(f"sqlite:///{legacy_db}")
    try:
        inspector = inspect(engine)
        assert "post_type" in {c["name"] for c in inspector.get_columns("campaign_posts")}
        with engine.connect() as conn:
            # The existing post survives untouched, with no invented format.
            row = conn.execute(
                text("SELECT post_type, generation_status FROM campaign_posts WHERE id='cp_1'")
            ).one()
        assert row[0] is None
        assert row[1] == "generated"
    finally:
        engine.dispose()


def test_a_fresh_database_gets_everything_at_once(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(bind=engine)
    try:
        inspector = inspect(engine)
        channels = {c["name"] for c in inspector.get_columns("channels")}
        assert {"archived_at", "archived_reason"} <= channels
        # The unique index that guarantees one owner per Buffer profile.
        indexes = {ix["name"] for ix in inspector.get_indexes("channels")}
        assert any("external_channel_id" in name for name in indexes), indexes
    finally:
        engine.dispose()


def test_the_unique_index_refuses_a_second_claim_of_one_profile(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'u.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        from app.models.entities import Account, Channel

        db.add_all(
            [
                Account(id="a1", name="A", type="brand", active=True, sort_order=0),
                Account(id="a2", name="B", type="brand", active=True, sort_order=1),
            ]
        )
        db.commit()
        db.add_all(
            [
                Channel(
                    id="ch_1", account_id="a1", platform="facebook",
                    display_name="One", external_channel_id="buf_x",
                ),
            ]
        )
        db.commit()

        # A second row claiming the same Buffer profile must be refused.
        db.add(
            Channel(
                id="ch_2", account_id="a2", platform="facebook",
                display_name="Two", external_channel_id="buf_x",
            )
        )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

        # Legacy rows with no external id are unaffected: NULLs do not conflict.
        db.add_all(
            [
                Channel(id="ch_3", account_id="a1", platform="facebook", display_name="Three"),
                Channel(id="ch_4", account_id="a1", platform="facebook", display_name="Four"),
            ]
        )
        db.commit()
        assert db.query(Channel).count() == 3
    engine.dispose()


def _schema_fingerprint(path: Path) -> dict[str, list[str]]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        return {
            table: sorted(c["name"] for c in inspector.get_columns(table))
            for table in inspector.get_table_names()
        }
    finally:
        engine.dispose()


# Keep the PNG referenced so the constant is not flagged as unused.
assert PNG[:4] == b"\x89PNG"