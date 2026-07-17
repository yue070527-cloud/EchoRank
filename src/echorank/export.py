from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


def _period_label(period: sqlite3.Row) -> str:
    if period["period_type"] == "daily":
        value = date.fromisoformat(period["period_key"])
        return f"{value.year} 年 {value.month} 月 {value.day} 日"
    year, week = period["period_key"].split("-W")
    return f"{year} 年第 {int(week)} 周"


def snapshot_for_period(connection: sqlite3.Connection, period_id: int) -> dict[str, Any]:
    period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
    if not period:
        raise ValueError(f"榜单周期不存在：{period_id}")
    entries = []
    rows = connection.execute(
        "SELECT ce.*, s.title, a.id AS album_id, a.title AS album_title, a.cover_url, a.cover_color "
        "FROM chart_entries ce JOIN songs s ON s.id=ce.song_id JOIN albums a ON a.id=s.album_id "
        "WHERE ce.period_id=? ORDER BY ce.rank",
        (period_id,),
    ).fetchall()
    for row in rows:
        artists = [
            {"id": artist["id"], "name": artist["name"]}
            for artist in connection.execute(
                "SELECT ar.id, ar.name FROM song_artists sa JOIN artists ar ON ar.id=sa.artist_id "
                "WHERE sa.song_id=? ORDER BY sa.credit_order",
                (row["song_id"],),
            )
        ]
        entries.append({
            "entityId": row["song_id"],
            "entity": {
                "title": row["title"],
                "artists": artists,
                "album": {"id": row["album_id"], "title": row["album_title"]},
                "coverUrl": row["cover_url"],
                "coverColor": row["cover_color"],
            },
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
            "record": {"peak": row["peak"], "periods": row["periods"]},
        })
    status = period["status"] if period["status"] in {"collecting", "settled", "missing", "failed"} else "missing"
    return {
        "schemaVersion": "1.0",
        "chart": {
            "id": f"songs-{period['period_type']}-{period['period_key']}",
            "entityType": "songs",
            "periodType": period["period_type"],
            "title": "PERSONAL CHART 50",
        },
        "period": {
            "key": period["period_key"],
            "label": _period_label(period),
            "scheduledAt": period["scheduled_at"],
            "status": status,
        },
        "collection": {
            "collectedAt": period["collected_at"],
            "coverage": period["coverage"],
            "status": "success" if period["coverage"] == 1 else "partial",
            "sourceSnapshot": period["source_snapshot"],
        },
        "scoringVersions": {
            "netease": "netease-streaming-v1",
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


def export_period(connection: sqlite3.Connection, period_id: int, frontend_root: str | Path) -> Path:
    snapshot = snapshot_for_period(connection, period_id)
    root = Path(frontend_root)
    period_type = snapshot["chart"]["periodType"]
    period_key = snapshot["period"]["key"]
    relative = Path("data") / "charts" / period_type / "songs" / f"{period_key}.json"
    destination = root / relative
    _atomic_json(destination, snapshot)
    update_manifest(root / "data" / "chart-manifest.json", period_type, period_key, f"./{relative.as_posix()}")
    return destination


def set_default_view(
    path: Path,
    period_type: str,
    period_key: str,
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["defaultView"] = {
        "entityType": "songs",
        "periodType": period_type,
        "periodKey": period_key,
    }
    _atomic_json(path, manifest)


def update_manifest(path: Path, period_type: str, period_key: str, snapshot_path: str) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schemaVersion": "1.0",
        "defaultView": {"entityType": "songs", "periodType": period_type, "periodKey": period_key},
        "views": [],
    }
    view = next((item for item in manifest["views"] if item["entityType"] == "songs" and item["periodType"] == period_type), None)
    if view is None:
        view = {"entityType": "songs", "periodType": period_type, "snapshots": []}
        manifest["views"].append(view)
    snapshots = {item["periodKey"]: item for item in view["snapshots"]}
    snapshots[period_key] = {"periodKey": period_key, "path": snapshot_path}
    view["snapshots"] = [snapshots[key] for key in sorted(snapshots)]
    _atomic_json(path, manifest)
