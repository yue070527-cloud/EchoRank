from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .database import connect, initialize
from .export import export_period, set_default_view
from .netease import (
    CHINA_TIMEZONE,
    Fetcher,
    _default_fetcher,
    collect_weekly_snapshot,
    normalize_weekly_ranking,
    raw_snapshot_path,
)
from .settlement import import_netease_snapshot, settle_daily, settle_weekly


@dataclass(frozen=True)
class UpdateResult:
    period_key: str
    week_key: str
    daily_path: Path
    weekly_path: Path
    entry_count: int
    collected: bool


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
    return normalize_weekly_ranking(
        raw_payload,
        period_key,
        archive_path,
        collected_at,
    )


def _daily_period(
    connection: sqlite3.Connection,
    period_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, frozen FROM chart_periods "
        "WHERE period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()


def update_charts(
    config_path: str | Path = "data/echorank-config.json",
    database_path: str | Path = "data/echorank-live.db",
    raw_root: str | Path = "data/raw/netease",
    frontend_root: str | Path = "frontend",
    now: datetime | None = None,
    timeout: float = 20,
    fetcher: Fetcher = _default_fetcher,
) -> UpdateResult:
    config = load_config(config_path)
    current = now or datetime.now(CHINA_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    current = current.astimezone(CHINA_TIMEZONE)
    period_key = current.date().isoformat()
    iso_year, iso_week, _ = current.date().isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    archive_path = raw_snapshot_path(period_key, raw_root)

    connection = connect(database_path)
    initialize(connection)
    collected = False
    try:
        period = _daily_period(connection, period_key)
        if not period or not period["frozen"]:
            if archive_path.exists():
                payload = _load_archived_snapshot(
                    archive_path,
                    period_key,
                    current,
                )
            else:
                payload, archive_path = collect_weekly_snapshot(
                    config["neteaseUid"],
                    period_key,
                    raw_root,
                    timeout,
                    fetcher,
                    current,
                )
                collected = True
            import_netease_snapshot(connection, payload)
            daily_id = settle_daily(connection, period_key)
        else:
            daily_id = period["id"]

        weekly_id = settle_weekly(connection, period_key)
        daily_path = export_period(connection, daily_id, frontend_root)
        weekly_path = export_period(connection, weekly_id, frontend_root)
        set_default_view(
            Path(frontend_root) / "data" / "chart-manifest.json",
            "daily",
            period_key,
        )
        entry_count = connection.execute(
            "SELECT COUNT(*) FROM chart_entries WHERE period_id=?",
            (daily_id,),
        ).fetchone()[0]
        return UpdateResult(
            period_key,
            week_key,
            daily_path,
            weekly_path,
            entry_count,
            collected,
        )
    finally:
        connection.close()
