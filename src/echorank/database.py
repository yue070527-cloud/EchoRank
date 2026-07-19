from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

SCHEMA_VERSION = 5


def connect(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _execute_schema(connection: sqlite3.Connection, schema: str) -> None:
    for statement in schema.split(";"):
        sql = statement.strip()
        if sql and not sql.startswith("PRAGMA foreign_keys"):
            connection.execute(sql)


def _migrate_legacy_charts(connection: sqlite3.Connection, schema: str) -> None:
    if "entity_type" in _table_columns(connection, "chart_periods"):
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ALTER TABLE chart_periods RENAME TO legacy_chart_periods")
        connection.execute("ALTER TABLE netease_snapshot_entries RENAME TO legacy_netease_snapshot_entries")
        connection.execute("ALTER TABLE point_ledger RENAME TO legacy_point_ledger")
        connection.execute("ALTER TABLE chart_entries RENAME TO legacy_chart_entries")
        _execute_schema(connection, schema)
        connection.execute(
            "INSERT INTO chart_periods(id, entity_type, period_type, period_key, target_date, scheduled_at, "
            "collected_at, status, coverage, input_fingerprint, source_snapshot, frozen) "
            "SELECT id, 'songs', period_type, period_key, target_date, scheduled_at, collected_at, status, "
            "coverage, input_fingerprint, source_snapshot, frozen FROM legacy_chart_periods"
        )
        connection.execute(
            "INSERT INTO netease_snapshot_entries(period_id, song_id, source_rank, weekly_play_count) "
            "SELECT period_id, song_id, source_rank, weekly_play_count FROM legacy_netease_snapshot_entries"
        )
        connection.execute(
            "INSERT INTO point_ledger(id, period_id, song_id, source, points, scoring_version, external_key, created_at) "
            "SELECT id, period_id, song_id, source, points, scoring_version, external_key, created_at "
            "FROM legacy_point_ledger"
        )
        connection.execute(
            "INSERT INTO chart_entries(period_id, entity_id, rank, previous_rank, movement_type, movement_value, "
            "peak, periods, netease_points, physical_points, bilibili_points, other_points, legacy_bonus, "
            "manual_adjustment, total_points) SELECT period_id, song_id, rank, previous_rank, movement_type, "
            "movement_value, peak, periods, netease_points, physical_points, bilibili_points, other_points, "
            "legacy_bonus, manual_adjustment, total_points FROM legacy_chart_entries"
        )
        connection.execute("DROP TABLE legacy_chart_entries")
        connection.execute("DROP TABLE legacy_point_ledger")
        connection.execute("DROP TABLE legacy_netease_snapshot_entries")
        connection.execute("DROP TABLE legacy_chart_periods")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"数据库迁移后外键检查失败：{violations}")


def initialize(connection: sqlite3.Connection) -> None:
    schema = files("echorank").joinpath("schema.sql").read_text(encoding="utf-8")
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "chart_periods" in tables:
        _migrate_legacy_charts(connection, schema)
    if "physical_releases" in tables and "rank_schedule_version" not in _table_columns(
        connection, "physical_releases"
    ):
        connection.execute(
            "ALTER TABLE physical_releases ADD COLUMN rank_schedule_version "
            "TEXT NOT NULL DEFAULT 'physical-rank-schedule-v1'"
        )
    connection.executescript(schema)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
