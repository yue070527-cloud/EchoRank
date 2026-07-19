from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .database import connect, initialize
from .export import export_period, export_trend_history, set_default_view
from .netease import CHINA_TIMEZONE, normalize_weekly_ranking, raw_snapshot_path
from .settlement import ENTITY_TYPES, LONG_PERIOD_TYPES, import_netease_snapshot, settle_daily, settle_entity_daily, settle_period

REPAIR_DATES = ("2026-07-17", "2026-07-18", "2026-07-19")
PROTECTED_TRIGGERS = (
    "protect_frozen_ledger_update",
    "protect_frozen_ledger_delete",
    "protect_frozen_release_update",
    "protect_frozen_release_delete",
    "protect_reference_update",
    "protect_reference_delete",
)


def _payloads(raw_root: str | Path) -> dict[str, dict]:
    payloads = {}
    for period_key in REPAIR_DATES:
        path = raw_snapshot_path(period_key, raw_root)
        if not path.exists():
            raise ValueError(f"缺少历史原始快照：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        payloads[period_key] = normalize_weekly_ranking(
            raw,
            period_key,
            path,
            datetime.combine(date.fromisoformat(period_key), datetime.min.time(), CHINA_TIMEZONE),
        )
    return payloads


def inspect_score_repair(
    database_path: str | Path,
    raw_root: str | Path = "data/raw/netease",
) -> dict:
    payloads = _payloads(raw_root)
    connection = connect(database_path)
    initialize(connection)
    try:
        periods = connection.execute(
            "SELECT id, entity_type, period_type, period_key, frozen FROM chart_periods "
            "WHERE period_key IN (?, ?, ?) OR period_key IN ('2026-W29', '2026-07', '2026') "
            "ORDER BY period_type, entity_type, period_key",
            REPAIR_DATES,
        ).fetchall()
        legacy_bonus = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(points), 0) AS points FROM point_ledger "
            "WHERE scoring_version='netease-alltime-yearly-v1'"
        ).fetchone()
        physical_events = connection.execute(
            "SELECT id, external_key, purchase_date FROM physical_events "
            "WHERE purchase_date BETWEEN ? AND ? ORDER BY id",
            (REPAIR_DATES[0], REPAIR_DATES[-1]),
        ).fetchall()
        return {
            "scores": {
                key: {
                    "entries": len(payload["entries"]),
                    "total": sum(item["relativeScore"] for item in payload["entries"]),
                }
                for key, payload in payloads.items()
            },
            "periods": [dict(row) for row in periods],
            "legacyBonus": dict(legacy_bonus),
            "physicalEvents": [dict(row) for row in physical_events],
        }
    finally:
        connection.close()


def repair_score_history(
    database_path: str | Path,
    raw_root: str | Path = "data/raw/netease",
    frontend_root: str | Path = "frontend",
) -> Path:
    database = Path(database_path)
    payloads = _payloads(raw_root)
    backup = database.with_name(
        f"{database.name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
    )
    shutil.copy2(database, backup)

    connection = connect(database)
    initialize(connection)
    legacy_before = [
        tuple(row)
        for row in connection.execute(
            "SELECT period_id, song_id, source, points, scoring_version, external_key "
            "FROM point_ledger WHERE scoring_version='netease-alltime-yearly-v1' ORDER BY external_key"
        )
    ]
    try:
        with connection:
            for trigger in PROTECTED_TRIGGERS:
                connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")

            period_rows = connection.execute(
                "SELECT id, entity_type, period_type, period_key FROM chart_periods "
                "WHERE period_key IN (?, ?, ?) OR period_key IN ('2026-W29', '2026-07', '2026')",
                REPAIR_DATES,
            ).fetchall()
            period_ids = [row["id"] for row in period_rows]
            if period_ids:
                placeholders = ",".join("?" for _ in period_ids)
                connection.execute(
                    f"UPDATE chart_periods SET frozen=0 WHERE id IN ({placeholders})",
                    period_ids,
                )
                connection.execute(
                    f"DELETE FROM chart_entries WHERE period_id IN ({placeholders})",
                    period_ids,
                )

            daily_rows = connection.execute(
                "SELECT id, period_key FROM chart_periods WHERE entity_type='songs' "
                "AND period_type='daily' AND period_key IN (?, ?, ?)",
                REPAIR_DATES,
            ).fetchall()
            daily_ids = [row["id"] for row in daily_rows]
            if len(daily_ids) != len(REPAIR_DATES):
                raise ValueError("目标三天的歌曲日榜周期不完整")
            placeholders = ",".join("?" for _ in daily_ids)
            connection.execute(
                f"DELETE FROM point_ledger WHERE period_id IN ({placeholders}) AND source='netease'",
                daily_ids,
            )
            connection.execute(
                f"DELETE FROM point_ledger WHERE period_id IN ({placeholders}) "
                "AND source='physical' AND external_key LIKE 'physical-event:%'",
                daily_ids,
            )
            connection.execute(
                f"DELETE FROM physical_releases WHERE period_id IN ({placeholders})",
                daily_ids,
            )
            event_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM physical_events WHERE purchase_date BETWEEN ? AND ?",
                    (REPAIR_DATES[0], REPAIR_DATES[-1]),
                )
            ]
            if event_ids:
                event_placeholders = ",".join("?" for _ in event_ids)
                connection.execute(
                    f"DELETE FROM physical_reference_points WHERE event_id IN ({event_placeholders})",
                    event_ids,
                )
            connection.execute(
                f"DELETE FROM netease_snapshot_entries WHERE period_id IN ({placeholders})",
                daily_ids,
            )
            connection.execute(
                f"UPDATE chart_periods SET input_fingerprint=NULL, status='collecting', coverage=0, "
                f"collection_version=NULL, netease_scoring_version=NULL WHERE id IN ({placeholders})",
                daily_ids,
            )

        for period_key in REPAIR_DATES:
            import_netease_snapshot(connection, payloads[period_key])
            settle_daily(connection, period_key)
            for entity_type in ENTITY_TYPES[1:]:
                settle_entity_daily(connection, entity_type, period_key)

        target = REPAIR_DATES[-1]
        for period_type in LONG_PERIOD_TYPES:
            for entity_type in ENTITY_TYPES:
                settle_period(connection, entity_type, period_type, target)

        for row in connection.execute(
            "SELECT id FROM chart_periods WHERE "
            "(period_type='daily' AND period_key IN (?, ?, ?)) OR "
            "(period_type='weekly' AND period_key='2026-W29') OR "
            "(period_type='monthly' AND period_key='2026-07') OR "
            "(period_type='yearly' AND period_key='2026')",
            REPAIR_DATES,
        ):
            export_period(connection, row["id"], frontend_root)
        for period_type in ("daily", "weekly", "monthly", "yearly"):
            for entity_type in ENTITY_TYPES:
                export_trend_history(connection, entity_type, period_type, frontend_root)
        set_default_view(
            Path(frontend_root) / "data" / "chart-manifest.json",
            "songs",
            "daily",
            REPAIR_DATES[-1],
        )

        legacy_after = [
            tuple(row)
            for row in connection.execute(
                "SELECT period_id, song_id, source, points, scoring_version, external_key "
                "FROM point_ledger WHERE scoring_version='netease-alltime-yearly-v1' ORDER BY external_key"
            )
        ]
        if legacy_after != legacy_before:
            raise RuntimeError("历史年榜加分在修复过程中发生变化")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"历史修复后外键检查失败：{violations}")
    except BaseException:
        connection.close()
        shutil.copy2(backup, database)
        raise
    finally:
        if connection:
            connection.close()

    verification = connect(database)
    initialize(verification)
    verification.close()
    return backup
