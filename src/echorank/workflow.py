from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from .database import connect, initialize
from .export import export_period, export_trend_history, set_default_view
from .netease import (
    CHINA_TIMEZONE,
    Fetcher,
    _default_fetcher,
    collect_weekly_snapshot,
    normalize_weekly_ranking,
    raw_snapshot_path,
)
from .settlement import (
    ENTITY_TYPES,
    LONG_PERIOD_TYPES,
    import_netease_snapshot,
    previous_period_end,
    settle_daily,
    settle_entity_daily,
    settle_period,
)


@dataclass(frozen=True)
class UpdateResult:
    period_key: str
    week_key: str
    month_key: str
    year_key: str
    paths: dict[tuple[str, str, str], Path]
    entry_count: int
    collected: bool
    skipped: bool = False

    @property
    def daily_path(self) -> Path:
        return self.paths[("songs", "daily", self.period_key)]

    @property
    def weekly_path(self) -> Path:
        return self.paths[("songs", "weekly", self.week_key)]

    @property
    def monthly_path(self) -> Path:
        return self.paths[("songs", "monthly", self.month_key)]

    @property
    def yearly_path(self) -> Path:
        return self.paths[("songs", "yearly", self.year_key)]


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ValueError(f"缺少本机配置：{config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"本机配置不是有效 JSON：{config_path}") from error
    if not isinstance(config, dict):
        raise ValueError("本机配置必须是 JSON 对象")
    uid = config.get("neteaseUid")
    if not isinstance(uid, str) or not uid.isdecimal():
        raise ValueError("本机配置中的 neteaseUid 必须是数字字符串")
    return config


def _load_archived_snapshot(
    archive_path: Path,
    period_key: str,
    collected_at: datetime,
) -> dict[str, Any]:
    try:
        raw_payload = json.loads(archive_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"原始快照不是有效 JSON：{archive_path}") from error
    return normalize_weekly_ranking(raw_payload, period_key, archive_path, collected_at)


def _daily_period(
    connection: sqlite3.Connection,
    period_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, frozen FROM chart_periods WHERE entity_type='songs' "
        "AND period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()


def _latest_daily_period(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, period_key FROM chart_periods WHERE entity_type='songs' "
        "AND period_type='daily' AND frozen=1 ORDER BY period_key DESC LIMIT 1"
    ).fetchone()


def _skipped_update_result(
    connection: sqlite3.Connection,
    frontend_root: str | Path,
) -> UpdateResult:
    period = _latest_daily_period(connection)
    if not period:
        return UpdateResult("", "", "", "", {}, 0, False, True)
    chart_date = date.fromisoformat(period["period_key"])
    iso_year, iso_week, _ = chart_date.isocalendar()
    paths: dict[tuple[str, str, str], Path] = {}
    daily_periods = connection.execute(
        "SELECT entity_type FROM chart_periods WHERE period_type='daily' "
        "AND period_key=? AND frozen=1",
        (period["period_key"],),
    ).fetchall()
    for row in daily_periods:
        key = (row["entity_type"], "daily", period["period_key"])
        paths[key] = (
            Path(frontend_root)
            / "data"
            / "charts"
            / "daily"
            / row["entity_type"]
            / f"{period['period_key']}.json"
        )
    entry_count = connection.execute(
        "SELECT COUNT(*) FROM chart_entries WHERE period_id=?", (period["id"],)
    ).fetchone()[0]
    return UpdateResult(
        period["period_key"],
        f"{iso_year}-W{iso_week:02d}",
        chart_date.strftime("%Y-%m"),
        str(chart_date.year),
        paths,
        entry_count,
        False,
        True,
    )


def _exported_period(
    connection: sqlite3.Connection,
    period_id: int,
    frontend_root: str | Path,
    paths: dict[tuple[str, str, str], Path],
) -> None:
    period = connection.execute(
        "SELECT entity_type, period_type, period_key FROM chart_periods WHERE id=?",
        (period_id,),
    ).fetchone()
    paths[(period["entity_type"], period["period_type"], period["period_key"])] = export_period(
        connection, period_id, frontend_root
    )


def update_charts(
    config_path: str | Path = "data/echorank-config.json",
    database_path: str | Path = "data/echorank-live.db",
    raw_root: str | Path = "data/raw/netease",
    frontend_root: str | Path = "frontend",
    now: datetime | None = None,
    timeout: float = 20,
    fetcher: Fetcher = _default_fetcher,
    netease_uid: str | None = None,
    target_date: date | None = None,
) -> UpdateResult:
    if netease_uid is None:
        netease_uid = load_config(config_path)["neteaseUid"]
    elif not isinstance(netease_uid, str) or not netease_uid.isdecimal():
        raise ValueError("网易云 UID 必须是数字字符串")
    current = now or datetime.now(CHINA_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    current = current.astimezone(CHINA_TIMEZONE)
    current_date = current.date()
    chart_date = target_date or current_date
    period_key = chart_date.isoformat()
    iso_year, iso_week, _ = chart_date.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    month_key = chart_date.strftime("%Y-%m")
    year_key = str(chart_date.year)
    archive_path = raw_snapshot_path(period_key, raw_root)

    connection = connect(database_path)
    initialize(connection)
    if target_date is None and current.time() < time(22):
        try:
            return _skipped_update_result(connection, frontend_root)
        finally:
            connection.close()
    collected = False
    paths: dict[tuple[str, str, str], Path] = {}
    try:
        period = _daily_period(connection, period_key)
        if not period or not period["frozen"]:
            if archive_path.exists():
                payload = _load_archived_snapshot(archive_path, period_key, current)
            else:
                payload, archive_path = collect_weekly_snapshot(
                    netease_uid, period_key, raw_root, timeout, fetcher, current
                )
                collected = True
            import_netease_snapshot(connection, payload)
            daily_id = settle_daily(connection, period_key)
        else:
            daily_id = period["id"]

        period_ids = [daily_id]
        for entity_type in ENTITY_TYPES[1:]:
            period_ids.append(settle_entity_daily(connection, entity_type, period_key))

        for period_type in LONG_PERIOD_TYPES:
            previous_end = previous_period_end(period_type, chart_date)
            if connection.execute(
                "SELECT 1 FROM chart_periods WHERE entity_type='songs' AND period_type='daily' "
                "AND frozen=1 AND target_date BETWEEN ? AND ? LIMIT 1",
                (
                    date(previous_end.year, 1, 1).isoformat() if period_type == "yearly"
                    else previous_end.replace(day=1).isoformat() if period_type == "monthly"
                    else (previous_end - timedelta(days=6)).isoformat(),
                    previous_end.isoformat(),
                ),
            ).fetchone():
                for entity_type in ENTITY_TYPES:
                    period_ids.append(
                        settle_period(connection, entity_type, period_type, previous_end.isoformat())
                    )
            for entity_type in ENTITY_TYPES:
                period_ids.append(
                    settle_period(connection, entity_type, period_type, period_key)
                )

        for period_id in dict.fromkeys(period_ids):
            _exported_period(connection, period_id, frontend_root, paths)

        for entity_type, period_type in {
            (entity_type, period_type) for entity_type, period_type, _ in paths
        }:
            export_trend_history(connection, entity_type, period_type, frontend_root)

        set_default_view(
            Path(frontend_root) / "data" / "chart-manifest.json",
            "songs",
            "daily",
            period_key,
        )
        entry_count = connection.execute(
            "SELECT COUNT(*) FROM chart_entries WHERE period_id=?", (daily_id,)
        ).fetchone()[0]
        return UpdateResult(
            period_key, week_key, month_key, year_key, paths, entry_count, collected
        )
    finally:
        connection.close()
