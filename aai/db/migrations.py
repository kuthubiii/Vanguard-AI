"""Safe, repeatable additive migrations for the local SQLite database."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Index, MetaData, inspect, text
from sqlalchemy.dialects import sqlite

logger = logging.getLogger(__name__)
_MIGRATION_LOCK = threading.Lock()

# Ordered additions for databases created by earlier Vanguard phases.
# SQLite safely fills nullable columns with NULL and the Boolean default with 1.
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("videos", "upload_idempotency_key", "VARCHAR(36) NULL"),
    ("analysis_jobs", "inference_device", "VARCHAR(32) NULL"),
    ("analysis_jobs", "tracking_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("detections", "track_id", "INTEGER NULL"),
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_sql(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\s\"`\[\]]+", "", value).lower()


def _expected_index(index: Index) -> dict[str, Any]:
    where = index.dialect_options["sqlite"].get("where")
    where_sql = None
    if where is not None:
        where_sql = str(where.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    return {
        "name": index.name,
        "table": index.table.name,
        "columns": [column.name for column in index.columns],
        "unique": bool(index.unique),
        "where": _normalize_sql(where_sql),
    }


def _actual_indexes(connection, table_name: str) -> list[dict[str, Any]]:
    names = connection.exec_driver_sql(f"PRAGMA index_list({_quote(table_name)})").mappings().all()
    indexes: list[dict[str, Any]] = []
    for row in names:
        name = row["name"]
        columns = connection.exec_driver_sql(f"PRAGMA index_info({_quote(name)})").mappings().all()
        sql_row = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"), {"name": name}
        ).first()
        sql = sql_row[0] if sql_row else None
        where_sql = None
        if sql and re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            where_sql = re.split(r"\bWHERE\b", sql, maxsplit=1, flags=re.IGNORECASE)[1]
        indexes.append({
            "name": name,
            "table": table_name,
            "columns": [column["name"] for column in columns],
            "unique": bool(row["unique"]),
            "partial": bool(row["partial"]),
            "origin": row["origin"],
            "where": _normalize_sql(where_sql),
        })
    return indexes


def _index_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return (
        actual["columns"] == expected["columns"]
        and actual["unique"] == expected["unique"]
        and actual["where"] == expected["where"]
        and actual["partial"] == (expected["where"] is not None)
    )


def _find_named_index(indexes: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for index in indexes:
        if index["name"] == name:
            return index
    return None


def _schema_needs_migration(engine: Engine, metadata: MetaData) -> bool:
    with engine.connect() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        if any(table.name not in existing_tables for table in metadata.sorted_tables):
            return True
        for table in metadata.sorted_tables:
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            if any(column.name not in columns for column in table.columns):
                return True
            actual = _actual_indexes(connection, table.name)
            for index in table.indexes:
                expected = _expected_index(index)
                named = _find_named_index(actual, expected["name"])
                if named is not None and _index_matches(named, expected):
                    continue
                if named is None and any(_index_matches(item, expected) for item in actual):
                    continue
                return True
        if "activity_logs" in existing_tables:
            triggers = set(connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='activity_logs'"
            ).scalars().all())
            if not {"trg_activity_logs_no_update", "trg_activity_logs_no_delete"}.issubset(triggers):
                return True
    return False


def _database_snapshot(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = sorted(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        counts = {
            name: connection.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
            for name in tables
        }
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "tables": tables,
            "counts": counts,
        }
    finally:
        connection.close()


def _backup_database(engine: Engine) -> Path | None:
    raw_path = engine.url.database
    if not raw_path or raw_path == ":memory:":
        return None
    source_path = Path(raw_path).expanduser().resolve()
    if not source_path.is_file() or source_path.stat().st_size == 0:
        return None
    before = _database_snapshot(source_path)
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{source_path.stem}-before-migration-{stamp}{source_path.suffix or '.sqlite3'}"
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        destination.commit()
    except Exception:
        destination.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()
    after = _database_snapshot(backup_path)
    if not backup_path.is_file() or backup_path.stat().st_size == 0 or after != before or after["integrity"] != "ok":
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite backup verification failed for {backup_path}")
    logger.info("Verified pre-migration SQLite backup at %s", backup_path)
    return backup_path


def _add_missing_columns(connection) -> None:
    inspector = inspect(connection)
    for table_name, column_name, definition in _COLUMN_MIGRATIONS:
        if not inspector.has_table(table_name):
            continue
        existing = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if column_name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE {_quote(table_name)} ADD COLUMN {_quote(column_name)} {definition}"
            )
            logger.info("Added missing SQLite column %s.%s", table_name, column_name)


def _ensure_indexes(connection, metadata: MetaData) -> None:
    inspector = inspect(connection)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            raise RuntimeError(f"Cannot install indexes: required table {table.name!r} is missing")
        columns = {column["name"] for column in inspect(connection).get_columns(table.name)}
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            expected = _expected_index(index)
            missing = [name for name in expected["columns"] if name not in columns]
            if missing:
                raise RuntimeError(
                    f"Cannot create index {expected['name']!r}: {table.name} is missing required column(s) {missing}"
                )
            actual = _actual_indexes(connection, table.name)
            named = _find_named_index(actual, expected["name"])
            if named is not None and _index_matches(named, expected):
                continue
            if named is not None:
                if named["origin"] != "c":
                    raise RuntimeError(
                        f"Index name {expected['name']!r} is occupied by a SQLite-managed index with a different definition"
                    )
                logger.warning("Replacing malformed index %s on %s", expected["name"], table.name)
                connection.exec_driver_sql(f"DROP INDEX {_quote(expected['name'])}")
                actual = [item for item in actual if item["name"] != expected["name"]]
            if any(_index_matches(item, expected) for item in actual):
                continue
            try:
                index.create(bind=connection, checkfirst=False)
                logger.info("Created missing SQLite index %s", expected["name"])
            except Exception as error:
                raise RuntimeError(
                    f"Could not create SQLite index {expected['name']!r} on {table.name}: {error}"
                ) from error


def _ensure_activity_log_append_only(connection, metadata: MetaData) -> None:
    if "activity_logs" not in metadata.tables or not inspect(connection).has_table("activity_logs"):
        return
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_activity_logs_no_update
        BEFORE UPDATE ON activity_logs
        BEGIN SELECT RAISE(ABORT, 'activity_logs is append-only'); END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_activity_logs_no_delete
        BEFORE DELETE ON activity_logs
        BEGIN SELECT RAISE(ABORT, 'activity_logs is append-only'); END
    """)


def apply_sqlite_migrations(engine: Engine, metadata: MetaData) -> Path | None:
    """Add missing modeled columns/tables/indexes atomically and idempotently."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(f"SQLite migration runner received unsupported dialect {engine.dialect.name!r}")
    with _MIGRATION_LOCK:
        needs_migration = _schema_needs_migration(engine, metadata)
        backup_path = _backup_database(engine) if needs_migration else None
        label = engine.url.database or "in-memory SQLite database"
        try:
            with engine.connect() as connection:
                # Acquire the database write lock before the migration snapshot
                # to serialize startup from multiple backend processes.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                _add_missing_columns(connection)
                metadata.create_all(bind=connection)
                for table in metadata.sorted_tables:
                    existing = {column["name"] for column in inspect(connection).get_columns(table.name)}
                    missing = [column.name for column in table.columns if column.name not in existing]
                    if missing:
                        raise RuntimeError(f"Schema mismatch in {table.name}: missing modeled columns {missing}")
                _ensure_indexes(connection, metadata)
                _ensure_activity_log_append_only(connection, metadata)
                connection.commit()
        except Exception as error:
            raise RuntimeError(f"SQLite schema migration failed for {label}: {error}") from error
        if needs_migration:
            logger.info("SQLite schema migration completed for %s (backup: %s)", label, backup_path or "not applicable")
        return backup_path
