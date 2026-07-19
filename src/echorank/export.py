from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

ENTITY_TITLES = {
    "songs": "PERSONAL CHART 50",
    "albums": "PERSONAL ALBUM 50",
    "artists": "PERSONAL ARTIST 50",
}


def _period_label(period: sqlite3.Row) -> str:
    period_type = period["period_type"]
    if period_type == "daily":
        value = date.fromisoformat(period["period_key"])
        return f"{value.year} 年 {value.month} 月 {value.day} 日"
    if period_type == "weekly":
        year, week = period["period_key"].split("-W")
        return f"{year} 年第 {int(week)} 周"
    if period_type == "monthly":
        year, month = period["period_key"].split("-")
        return f"{year} 年 {int(month)} 月"
    return f"{period['period_key']} 年"


def _song_entity(connection: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT s.title, a.id AS album_id, a.title AS album_title, a.cover_url, a.cover_color "
        "FROM songs s JOIN albums a ON a.id=s.album_id WHERE s.id=?",
        (entity_id,),
    ).fetchone()
    artists = [
        {"id": artist["id"], "name": artist["name"]}
        for artist in connection.execute(
            "SELECT ar.id, ar.name FROM song_artists sa JOIN artists ar ON ar.id=sa.artist_id "
            "WHERE sa.song_id=? ORDER BY sa.credit_order",
            (entity_id,),
        )
    ]
    artist_names = " / ".join(artist["name"] for artist in artists)
    return {
        "title": row["title"],
        "subtitle": artist_names,
        "detail": row["album_title"],
        "artists": artists,
        "album": {"id": row["album_id"], "title": row["album_title"]},
        "coverUrl": row["cover_url"],
        "coverColor": row["cover_color"],
    }


def _album_entity(connection: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT title, cover_url, cover_color FROM albums WHERE id=?", (entity_id,)
    ).fetchone()
    artists = [
        {"id": artist["id"], "name": artist["name"]}
        for artist in connection.execute(
            "SELECT ar.id, ar.name, MIN(sa.credit_order) AS first_credit "
            "FROM songs s JOIN song_artists sa ON sa.song_id=s.id "
            "JOIN artists ar ON ar.id=sa.artist_id WHERE s.album_id=? "
            "GROUP BY ar.id, ar.name ORDER BY first_credit, ar.name, ar.id",
            (entity_id,),
        )
    ]
    track_count = connection.execute(
        "SELECT COUNT(*) FROM songs WHERE album_id=?", (entity_id,)
    ).fetchone()[0]
    return {
        "title": row["title"],
        "subtitle": " / ".join(artist["name"] for artist in artists),
        "detail": f"{track_count} 首歌曲参与统计",
        "artists": artists,
        "coverUrl": row["cover_url"],
        "coverColor": row["cover_color"],
    }


def _artist_entity(connection: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT name FROM artists WHERE id=?", (entity_id,)).fetchone()
    representative = connection.execute(
        "SELECT a.cover_url, a.cover_color FROM song_artists sa "
        "JOIN songs s ON s.id=sa.song_id JOIN albums a ON a.id=s.album_id "
        "WHERE sa.artist_id=? ORDER BY sa.credit_order, s.id LIMIT 1",
        (entity_id,),
    ).fetchone()
    song_count = connection.execute(
        "SELECT COUNT(*) FROM song_artists WHERE artist_id=?", (entity_id,)
    ).fetchone()[0]
    return {
        "title": row["name"],
        "subtitle": "艺人综合榜",
        "detail": f"{song_count} 首歌曲参与统计",
        "coverUrl": representative["cover_url"] if representative else None,
        "coverColor": representative["cover_color"] if representative else "#777777",
    }


def _entity_payload(
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    if entity_type == "songs":
        return _song_entity(connection, entity_id)
    if entity_type == "albums":
        return _album_entity(connection, entity_id)
    return _artist_entity(connection, entity_id)


def snapshot_for_period(connection: sqlite3.Connection, period_id: int) -> dict[str, Any]:
    period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
    if not period:
        raise ValueError(f"榜单周期不存在：{period_id}")
    entries = []
    rows = connection.execute(
        "SELECT * FROM chart_entries WHERE period_id=? ORDER BY rank", (period_id,)
    ).fetchall()
    championships = {
        row["entity_id"]: row["championships"]
        for row in connection.execute(
            "SELECT ce.entity_id, COUNT(*) AS championships FROM chart_entries ce "
            "JOIN chart_periods cp ON cp.id=ce.period_id "
            "WHERE cp.entity_type=? AND cp.period_type=? AND cp.period_key<=? "
            "AND cp.frozen=1 AND ce.rank=1 GROUP BY ce.entity_id",
            (period["entity_type"], period["period_type"], period["period_key"]),
        )
    }
    for row in rows:
        entries.append({
            "entityId": row["entity_id"],
            "entity": _entity_payload(connection, period["entity_type"], row["entity_id"]),
            "rank": {
                "current": row["rank"],
                "previous": row["previous_rank"],
                "movement": {"type": row["movement_type"], "value": row["movement_value"]},
            },
            "points": {
                "netease": row["netease_points"],
                "physical": row["physical_points"],
                "bilibili": row["bilibili_points"],
                "other": row["other_points"],
                "legacyBonus": row["legacy_bonus"],
                "manualAdjustment": row["manual_adjustment"],
                "total": row["total_points"],
            },
            "record": {
                "peak": row["peak"],
                "periods": row["periods"],
                "championships": championships.get(row["entity_id"], 0),
            },
        })
    return {
        "schemaVersion": "1.0",
        "chart": {
            "id": f"{period['entity_type']}-{period['period_type']}-{period['period_key']}",
            "entityType": period["entity_type"],
            "periodType": period["period_type"],
            "title": ENTITY_TITLES[period["entity_type"]],
        },
        "period": {
            "key": period["period_key"],
            "label": _period_label(period),
            "scheduledAt": period["scheduled_at"],
            "status": period["status"],
        },
        "collection": {
            "collectedAt": period["collected_at"],
            "coverage": period["coverage"],
            "status": (
                "success" if period["status"] == "settled"
                else "partial" if period["status"] in {"collecting", "partial"}
                else period["status"]
            ),
            "sourceSnapshot": period["source_snapshot"],
            "version": period["collection_version"],
        },
        "scoringVersions": {
            "netease": period["netease_scoring_version"] or "unknown",
            "physical": "external-ledger-v1",
            "bilibili": "external-ledger-v1",
            "combined": "combined-v1",
        },
        "entries": entries,
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def export_period(
    connection: sqlite3.Connection,
    period_id: int,
    frontend_root: str | Path,
) -> Path:
    snapshot = snapshot_for_period(connection, period_id)
    root = Path(frontend_root)
    entity_type = snapshot["chart"]["entityType"]
    period_type = snapshot["chart"]["periodType"]
    period_key = snapshot["period"]["key"]
    relative = Path("data") / "charts" / period_type / entity_type / f"{period_key}.json"
    destination = root / relative
    _atomic_json(destination, snapshot)
    update_manifest(
        root / "data" / "chart-manifest.json",
        entity_type,
        period_type,
        period_key,
        f"./{relative.as_posix()}",
    )
    return destination


def export_trend_history(
    connection: sqlite3.Connection,
    entity_type: str,
    period_type: str,
    frontend_root: str | Path,
) -> Path:
    periods = connection.execute(
        "SELECT id, period_type, period_key, status, coverage, frozen FROM chart_periods "
        "WHERE entity_type=? AND period_type=? ORDER BY period_key",
        (entity_type, period_type),
    ).fetchall()
    period_ids = {period["id"]: period["period_key"] for period in periods}
    series: dict[str, list[dict[str, Any]]] = {}
    if period_ids:
        placeholders = ",".join("?" for _ in period_ids)
        rows = connection.execute(
            f"SELECT period_id, entity_id, rank, movement_type, movement_value, total_points "
            f"FROM chart_entries WHERE period_id IN ({placeholders}) ORDER BY period_id, rank",
            list(period_ids),
        ).fetchall()
        for row in rows:
            series.setdefault(row["entity_id"], []).append({
                "periodKey": period_ids[row["period_id"]],
                "rank": row["rank"],
                "points": row["total_points"],
                "movement": {"type": row["movement_type"], "value": row["movement_value"]},
            })
    payload = {
        "schemaVersion": "1.0",
        "entityType": entity_type,
        "periodType": period_type,
        "periods": [
            {
                "key": period["period_key"],
                "label": _period_label(period),
                "status": period["status"],
                "coverage": period["coverage"],
                "frozen": bool(period["frozen"]),
            }
            for period in periods
        ],
        "series": series,
    }
    root = Path(frontend_root)
    relative = Path("data") / "trends" / period_type / f"{entity_type}.json"
    destination = root / relative
    _atomic_json(destination, payload)
    update_history_manifest(
        root / "data" / "chart-manifest.json",
        entity_type,
        period_type,
        f"./{relative.as_posix()}",
    )
    return destination


def set_default_view(
    path: Path,
    entity_type: str,
    period_type: str,
    period_key: str,
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["defaultView"] = {
        "entityType": entity_type,
        "periodType": period_type,
        "periodKey": period_key,
    }
    _atomic_json(path, manifest)


def update_history_manifest(
    path: Path,
    entity_type: str,
    period_type: str,
    history_path: str,
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    view = next(
        (
            item for item in manifest["views"]
            if item["entityType"] == entity_type and item["periodType"] == period_type
        ),
        None,
    )
    if view is None:
        view = {"entityType": entity_type, "periodType": period_type, "snapshots": []}
        manifest["views"].append(view)
    view["historyPath"] = history_path
    manifest["views"].sort(key=lambda item: (item["entityType"], item["periodType"]))
    _atomic_json(path, manifest)


def update_manifest(
    path: Path,
    entity_type: str,
    period_type: str,
    period_key: str,
    snapshot_path: str,
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schemaVersion": "1.0",
        "defaultView": {
            "entityType": entity_type,
            "periodType": period_type,
            "periodKey": period_key,
        },
        "views": [],
    }
    view = next(
        (
            item for item in manifest["views"]
            if item["entityType"] == entity_type and item["periodType"] == period_type
        ),
        None,
    )
    if view is None:
        view = {"entityType": entity_type, "periodType": period_type, "snapshots": []}
        manifest["views"].append(view)
    snapshots = {item["periodKey"]: item for item in view["snapshots"]}
    snapshots[period_key] = {"periodKey": period_key, "path": snapshot_path}
    view["snapshots"] = [snapshots[key] for key in sorted(snapshots)]
    manifest["views"].sort(key=lambda item: (item["entityType"], item["periodType"]))
    _atomic_json(path, manifest)
