from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .ranking import PointTotal, movement_for, rank_totals
from .scoring import NeteaseInput, allocate_netease_points

SOURCE_COLUMNS = {
    "netease": "netease",
    "physical": "physical",
    "bilibili": "bilibili",
    "other": "other",
    "legacyBonus": "legacy_bonus",
    "manualAdjustment": "manual_adjustment",
}


def _fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _upsert_song(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    album = item["album"]
    connection.execute(
        "INSERT INTO albums(id, title, cover_url, cover_color) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, cover_url=excluded.cover_url, cover_color=excluded.cover_color",
        (album["id"], album["title"], item.get("coverUrl"), item.get("coverColor", "#777777")),
    )
    connection.execute(
        "INSERT INTO songs(id, title, album_id) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, album_id=excluded.album_id",
        (item["id"], item["title"], album["id"]),
    )
    connection.execute("DELETE FROM song_artists WHERE song_id = ?", (item["id"],))
    for order, artist in enumerate(item["artists"]):
        connection.execute(
            "INSERT INTO artists(id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (artist["id"], artist["name"]),
        )
        connection.execute(
            "INSERT INTO song_artists(song_id, artist_id, credit_order) VALUES (?, ?, ?)",
            (item["id"], artist["id"], order),
        )


def import_netease_snapshot(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    entries = payload.get("entries", [])
    period_key = payload["periodKey"]
    fingerprint = _fingerprint(payload)
    ranks = [item["rank"] for item in entries]
    unique_songs = {item["id"] for item in entries}
    valid_ranks = all(isinstance(rank, int) and 1 <= rank <= 100 for rank in ranks)
    if len(ranks) != len(set(ranks)) or len(entries) != len(unique_songs) or not valid_ranks:
        raise ValueError("网易云快照包含重复歌曲、重复排名或越界排名")
    existing = connection.execute(
        "SELECT id, input_fingerprint, frozen FROM chart_periods WHERE period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()
    if existing and existing["input_fingerprint"] == fingerprint:
        return existing["id"]
    if existing and existing["frozen"]:
        raise ValueError(f"日榜周期 {period_key} 已结算，拒绝覆盖")

    complete = len(entries) == 100 and set(ranks) == set(range(1, 101))
    status = "collecting" if complete else "partial"
    coverage = min(len(set(ranks)) / 100, 1)
    with connection:
        if existing:
            period_id = existing["id"]
            connection.execute(
                "UPDATE chart_periods SET scheduled_at=?, collected_at=?, status=?, coverage=?, input_fingerprint=?, source_snapshot=? WHERE id=?",
                (
                    payload["scheduledAt"],
                    payload.get("collectedAt"),
                    status,
                    coverage,
                    fingerprint,
                    payload.get("sourceSnapshot"),
                    period_id,
                ),
            )
            connection.execute("DELETE FROM netease_snapshot_entries WHERE period_id=?", (period_id,))
        else:
            period_id = connection.execute(
                "INSERT INTO chart_periods(period_type, period_key, target_date, scheduled_at, collected_at, status, coverage, input_fingerprint, source_snapshot) "
                "VALUES ('daily', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    period_key,
                    period_key,
                    payload["scheduledAt"],
                    payload.get("collectedAt"),
                    status,
                    coverage,
                    fingerprint,
                    payload.get("sourceSnapshot"),
                ),
            ).lastrowid
        for item in entries:
            _upsert_song(connection, item)
            connection.execute(
                "INSERT INTO netease_snapshot_entries(period_id, song_id, source_rank, weekly_play_count) VALUES (?, ?, ?, ?)",
                (period_id, item["id"], item["rank"], item["weeklyPlays"]),
            )
    return period_id


def import_ledger_entries(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    period_key = payload["periodKey"]
    period = connection.execute(
        "SELECT id, frozen FROM chart_periods WHERE period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()
    if not period:
        raise ValueError(f"日榜周期 {period_key} 尚未导入")
    if period["frozen"]:
        raise ValueError(f"日榜周期 {period_key} 已结算，拒绝修改账本")

    imported = 0
    with connection:
        for item in payload.get("entries", []):
            source = item["source"]
            if source not in SOURCE_COLUMNS or source == "netease":
                raise ValueError(f"不支持的预计算来源：{source}")
            if not connection.execute("SELECT 1 FROM songs WHERE id=?", (item["songId"],)).fetchone():
                raise ValueError(f"歌曲不存在：{item['songId']}")
            existing = connection.execute(
                "SELECT period_id, song_id, points, scoring_version FROM point_ledger "
                "WHERE source=? AND external_key=?",
                (source, item["externalKey"]),
            ).fetchone()
            if existing:
                unchanged = (
                    existing["period_id"] == period["id"]
                    and existing["song_id"] == item["songId"]
                    and existing["points"] == item["points"]
                    and existing["scoring_version"] == item["scoringVersion"]
                )
                if unchanged:
                    continue
                raise ValueError(f"账本幂等键冲突：{source}/{item['externalKey']}")
            connection.execute(
                "INSERT INTO point_ledger(period_id, song_id, source, points, scoring_version, external_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    period["id"],
                    item["songId"],
                    source,
                    item["points"],
                    item["scoringVersion"],
                    item["externalKey"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            imported += 1
    return imported


def _previous_period(connection: sqlite3.Connection, period_type: str, period_key: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM chart_periods WHERE period_type=? AND period_key < ? AND frozen=1 "
        "ORDER BY period_key DESC LIMIT 1",
        (period_type, period_key),
    ).fetchone()


def _history(connection: sqlite3.Connection, period_type: str, period_key: str) -> tuple[dict[str, int], set[str], dict[str, tuple[int, int]]]:
    previous = _previous_period(connection, period_type, period_key)
    previous_ranks = {}
    if previous:
        previous_ranks = {
            row["song_id"]: row["rank"]
            for row in connection.execute("SELECT song_id, rank FROM chart_entries WHERE period_id=?", (previous["id"],))
        }
    rows = connection.execute(
        "SELECT ce.song_id, MIN(ce.rank) AS peak, COUNT(*) AS periods "
        "FROM chart_entries ce JOIN chart_periods cp ON cp.id=ce.period_id "
        "WHERE cp.period_type=? AND cp.period_key < ? GROUP BY ce.song_id",
        (period_type, period_key),
    ).fetchall()
    records = {row["song_id"]: (row["peak"], row["periods"]) for row in rows}
    return previous_ranks, set(records), records


def _write_chart_entries(connection: sqlite3.Connection, period: sqlite3.Row, totals: list[PointTotal]) -> None:
    previous_ranks, appeared_before, records = _history(connection, period["period_type"], period["period_key"])
    ranked = rank_totals(totals, previous_ranks)
    connection.execute("DELETE FROM chart_entries WHERE period_id=?", (period["id"],))
    for rank, total in enumerate(ranked, start=1):
        movement_type, movement_value, previous_rank = movement_for(
            total.song_id, rank, previous_ranks, appeared_before
        )
        old_peak, old_periods = records.get(total.song_id, (rank, 0))
        connection.execute(
            "INSERT INTO chart_entries(period_id, song_id, rank, previous_rank, movement_type, movement_value, peak, periods, "
            "netease_points, physical_points, bilibili_points, other_points, legacy_bonus, manual_adjustment, total_points) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                period["id"], total.song_id, rank, previous_rank, movement_type, movement_value,
                min(old_peak, rank), old_periods + 1, total.netease, total.physical, total.bilibili,
                total.other, total.legacy_bonus, total.manual_adjustment, total.total,
            ),
        )


def _ledger_totals(connection: sqlite3.Connection, period_ids: list[int]) -> list[PointTotal]:
    if not period_ids:
        return []
    placeholders = ",".join("?" for _ in period_ids)
    rows = connection.execute(
        f"SELECT song_id, source, SUM(points) AS points FROM point_ledger WHERE period_id IN ({placeholders}) GROUP BY song_id, source",
        period_ids,
    ).fetchall()
    values: dict[str, dict[str, float]] = {}
    for row in rows:
        values.setdefault(row["song_id"], {})[SOURCE_COLUMNS[row["source"]]] = row["points"]
    return [PointTotal(song_id=song_id, **points) for song_id, points in values.items()]


def settle_daily(connection: sqlite3.Connection, period_key: str) -> int:
    period = connection.execute(
        "SELECT * FROM chart_periods WHERE period_type='daily' AND period_key=?", (period_key,)
    ).fetchone()
    if not period:
        raise ValueError(f"日榜周期 {period_key} 不存在")
    if period["frozen"]:
        return period["id"]
    entries = [
        NeteaseInput(row["song_id"], row["source_rank"], row["weekly_play_count"])
        for row in connection.execute(
            "SELECT song_id, source_rank, weekly_play_count FROM netease_snapshot_entries WHERE period_id=?",
            (period["id"],),
        )
    ]
    points = allocate_netease_points(entries)
    with connection:
        now = datetime.now(timezone.utc).isoformat()
        for song_id, value in points.items():
            connection.execute(
                "INSERT INTO point_ledger(period_id, song_id, source, points, scoring_version, external_key, created_at) "
                "VALUES (?, ?, 'netease', ?, 'netease-streaming-v1', ?, ?)",
                (period["id"], song_id, value, f"netease:{period_key}:{song_id}", now),
            )
        _write_chart_entries(connection, period, _ledger_totals(connection, [period["id"]]))
        connection.execute(
            "UPDATE chart_periods SET status='settled', coverage=1, frozen=1 WHERE id=?", (period["id"],)
        )
    return period["id"]


def settle_weekly(connection: sqlite3.Connection, target_date: str) -> int:
    target = date.fromisoformat(target_date)
    monday = target - timedelta(days=target.weekday())
    iso_year, iso_week, _ = target.isocalendar()
    period_key = f"{iso_year}-W{iso_week:02d}"
    existing = connection.execute(
        "SELECT * FROM chart_periods WHERE period_type='weekly' AND period_key=?", (period_key,)
    ).fetchone()
    if existing and existing["frozen"]:
        return existing["id"]

    daily_periods = connection.execute(
        "SELECT id, target_date FROM chart_periods WHERE period_type='daily' AND frozen=1 AND target_date BETWEEN ? AND ? ORDER BY target_date",
        (monday.isoformat(), target.isoformat()),
    ).fetchall()
    expected_days = (target - monday).days + 1
    coverage = len({row["target_date"] for row in daily_periods}) / expected_days
    status = "settled" if target.weekday() == 6 and coverage == 1 else "collecting"
    scheduled_at = datetime.combine(target, time(22), tzinfo=timezone(timedelta(hours=8))).isoformat()
    with connection:
        if existing:
            connection.execute(
                "UPDATE chart_periods SET target_date=?, scheduled_at=?, collected_at=?, status=?, coverage=? WHERE id=?",
                (target.isoformat(), scheduled_at, scheduled_at, status, coverage, existing["id"]),
            )
            period_id = existing["id"]
        else:
            period_id = connection.execute(
                "INSERT INTO chart_periods(period_type, period_key, target_date, scheduled_at, collected_at, status, coverage) "
                "VALUES ('weekly', ?, ?, ?, ?, ?, ?)",
                (period_key, target.isoformat(), scheduled_at, scheduled_at, status, coverage),
            ).lastrowid
        period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
        _write_chart_entries(connection, period, _ledger_totals(connection, [row["id"] for row in daily_periods]))
        if status == "settled":
            connection.execute("UPDATE chart_periods SET frozen=1 WHERE id=?", (period_id,))
    return period_id
