from __future__ import annotations

import calendar
import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .ranking import PointTotal, movement_for, rank_totals
from .scoring import (
    BILIBILI_SCORING_VERSION,
    NETEASE_COLLECTION_VERSION,
    NETEASE_SCORING_VERSION,
    PHYSICAL_EVENT_SCORING_VERSION,
    PHYSICAL_RANK_SCHEDULE_VERSION,
    PHYSICAL_SCORING_VERSION,
    NeteaseInput,
    allocate_netease_points,
    bilibili_points_for_views,
    physical_format_weight,
    physical_purchase_weight,
    physical_reference_rank,
)

SOURCE_COLUMNS = {
    "netease": "netease",
    "physical": "physical",
    "bilibili": "bilibili",
    "other": "other",
    "legacyBonus": "legacy_bonus",
    "manualAdjustment": "manual_adjustment",
}
ENTITY_TYPES = ("songs", "albums", "artists")
LONG_PERIOD_TYPES = ("weekly", "monthly", "yearly")
YEARLY_ONLY_SCORING_VERSION = "netease-alltime-yearly-v1"
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_artist(connection: sqlite3.Connection, name: str) -> dict[str, str]:
    value = name.strip()
    if not value:
        raise ValueError("艺人名称不能为空")
    artist_id = f"manual-artist-{uuid.uuid4()}"
    with connection:
        connection.execute("INSERT INTO artists(id, name) VALUES (?, ?)", (artist_id, value))
    return {"id": artist_id, "name": value}


def create_album(
    connection: sqlite3.Connection,
    title: str,
    cover_url: str | None = None,
    cover_color: str = "#777777",
) -> dict[str, str | None]:
    value = title.strip()
    if not value:
        raise ValueError("专辑名称不能为空")
    if len(cover_color) != 7 or cover_color[0] != "#" or any(
        character not in "0123456789abcdefABCDEF" for character in cover_color[1:]
    ):
        raise ValueError("封面颜色必须是六位十六进制颜色")
    album_id = f"manual-album-{uuid.uuid4()}"
    normalized_url = cover_url.strip() if cover_url else None
    with connection:
        connection.execute(
            "INSERT INTO albums(id, title, cover_url, cover_color) VALUES (?, ?, ?, ?)",
            (album_id, value, normalized_url or None, cover_color),
        )
    return {
        "id": album_id,
        "title": value,
        "coverUrl": normalized_url or None,
        "coverColor": cover_color,
    }


def create_song(
    connection: sqlite3.Connection,
    title: str,
    album_id: str,
    artist_ids: list[str],
) -> dict[str, Any]:
    value = title.strip()
    if not value:
        raise ValueError("歌曲名称不能为空")
    if not connection.execute("SELECT 1 FROM albums WHERE id=?", (album_id,)).fetchone():
        raise ValueError(f"专辑不存在：{album_id}")
    if not artist_ids or len(artist_ids) != len(set(artist_ids)):
        raise ValueError("歌曲至少需要一位且不能重复的艺人")
    placeholders = ",".join("?" for _ in artist_ids)
    existing = {
        row["id"]
        for row in connection.execute(
            f"SELECT id FROM artists WHERE id IN ({placeholders})", artist_ids
        )
    }
    missing = [artist_id for artist_id in artist_ids if artist_id not in existing]
    if missing:
        raise ValueError(f"艺人不存在：{missing[0]}")
    song_id = f"manual-song-{uuid.uuid4()}"
    with connection:
        connection.execute(
            "INSERT INTO songs(id, title, album_id) VALUES (?, ?, ?)",
            (song_id, value, album_id),
        )
        for order, artist_id in enumerate(artist_ids):
            connection.execute(
                "INSERT INTO song_artists(song_id, artist_id, credit_order) VALUES (?, ?, ?)",
                (song_id, artist_id, order),
            )
    return {"id": song_id, "title": value, "albumId": album_id, "artistIds": artist_ids}


def _normalize_catalog_song(item: dict[str, Any]) -> dict[str, Any]:
    song_id = item.get("id")
    album = item.get("album")
    artists = item.get("artists")
    if not isinstance(song_id, str) or not song_id.startswith("netease-song-"):
        raise ValueError("网易云歌曲 ID 无效")
    if not isinstance(item.get("title"), str) or not item["title"].strip():
        raise ValueError("网易云歌曲名称无效")
    if not isinstance(album, dict) or not isinstance(album.get("id"), str) or not album["id"].startswith("netease-album-"):
        raise ValueError("网易云专辑信息无效")
    if not isinstance(album.get("title"), str) or not album["title"].strip():
        raise ValueError("网易云专辑名称无效")
    if not isinstance(artists, list) or not artists:
        raise ValueError("网易云歌曲缺少艺人")
    artist_ids = []
    for artist in artists:
        if not isinstance(artist, dict) or not isinstance(artist.get("id"), str) or not artist["id"].startswith("netease-artist-"):
            raise ValueError("网易云艺人信息无效")
        if not isinstance(artist.get("name"), str) or not artist["name"].strip():
            raise ValueError("网易云艺人名称无效")
        artist_ids.append(artist["id"])
    if len(artist_ids) != len(set(artist_ids)):
        raise ValueError("网易云歌曲包含重复艺人")
    cover_color = item.get("coverColor", "#777777")
    if not isinstance(cover_color, str) or len(cover_color) != 7 or cover_color[0] != "#" or any(
        character not in "0123456789abcdefABCDEF" for character in cover_color[1:]
    ):
        raise ValueError("网易云封面颜色无效")
    cover_url = item.get("coverUrl")
    if cover_url is not None and not isinstance(cover_url, str):
        raise ValueError("网易云封面地址无效")
    return {
        "id": song_id,
        "title": item["title"].strip(),
        "artists": [{"id": artist["id"], "name": artist["name"].strip()} for artist in artists],
        "album": {"id": album["id"], "title": album["title"].strip()},
        "coverUrl": cover_url,
        "coverColor": cover_color,
    }


def import_catalog_song(connection: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_catalog_song(item)
    with connection:
        _upsert_song(connection, normalized)
    return normalized


def import_bilibili_view_event(
    connection: sqlite3.Connection,
    item: dict[str, Any],
    period_key: str,
    view_count: int,
    external_key: str,
    video_ref: str | None = None,
    notes: str | None = None,
) -> tuple[dict[str, Any], int, int]:
    normalized = _normalize_catalog_song(item)
    if isinstance(view_count, bool) or not isinstance(view_count, int) or view_count < 0:
        raise ValueError("B站观看次数必须是非负整数")
    key = external_key.strip()
    if not key:
        raise ValueError("缺少幂等键")
    reference = video_ref.strip() if video_ref else None
    note = notes.strip() if notes else None

    with connection:
        period = _ensure_open_daily_period_row(connection, period_key)
        existing = connection.execute(
            "SELECT period_id, song_id, view_count, video_ref, notes, scoring_version "
            "FROM bilibili_view_events WHERE external_key=?",
            (key,),
        ).fetchone()
        if existing:
            unchanged = (
                existing["period_id"] == period["id"]
                and existing["song_id"] == normalized["id"]
                and existing["view_count"] == view_count
                and existing["video_ref"] == reference
                and existing["notes"] == note
                and existing["scoring_version"] == BILIBILI_SCORING_VERSION
            )
            if not unchanged:
                raise ValueError(f"B站事件幂等键冲突：{key}")
            return normalized, 0, bilibili_points_for_views(view_count)
        duplicate = connection.execute(
            "SELECT 1 FROM bilibili_view_events WHERE period_id=? AND song_id=?",
            (period["id"], normalized["id"]),
        ).fetchone()
        if duplicate:
            raise ValueError("同一歌曲同一日期已有B站观看记录")
        _upsert_song(connection, normalized)
        connection.execute(
            "INSERT INTO bilibili_view_events(period_id, song_id, view_count, video_ref, notes, "
            "scoring_version, external_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                period["id"], normalized["id"], view_count, reference, note,
                BILIBILI_SCORING_VERSION, key, datetime.now(timezone.utc).isoformat(),
            ),
        )
    return normalized, 1, bilibili_points_for_views(view_count)


def _physical_purchase_weight(
    connection: sqlite3.Connection,
    album_id: str,
    edition_label: str,
    purchase_date: date,
    quantity: int,
) -> float:
    same_edition_count = connection.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM physical_events WHERE album_id=? AND edition_label=?",
        (album_id, edition_label),
    ).fetchone()[0]
    window_start = (purchase_date - timedelta(days=6)).isoformat()
    related_versions = connection.execute(
        "SELECT COUNT(*) FROM physical_events WHERE album_id=? AND edition_label<>? "
        "AND purchase_date BETWEEN ? AND ?",
        (album_id, edition_label, window_start, purchase_date.isoformat()),
    ).fetchone()[0]
    return float(physical_purchase_weight(same_edition_count, related_versions, quantity))


def import_physical_event(
    connection: sqlite3.Connection,
    songs: list[dict[str, Any]],
    purchase_date: str,
    edition_label: str,
    format_code: str,
    quantity: int,
    selected_song_ids: list[str],
    external_key: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if not songs:
        raise ValueError("实体事件至少需要一首专辑曲目")
    normalized_songs = [_normalize_catalog_song(song) for song in songs]
    album_ids = {song["album"]["id"] for song in normalized_songs}
    if len(album_ids) != 1:
        raise ValueError("实体事件的曲目必须属于同一专辑")
    selected = set(selected_song_ids)
    available = {song["id"] for song in normalized_songs}
    if not selected or not selected <= available:
        raise ValueError("请选择至少一首有效曲目")
    target = date.fromisoformat(purchase_date)
    label = edition_label.strip()
    key = external_key.strip()
    if not label:
        raise ValueError("实体版本名称不能为空")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("实体数量必须是正整数")
    if not key:
        raise ValueError("缺少幂等键")
    edition_weight = float(physical_format_weight(format_code))
    album_id = next(iter(album_ids))

    with connection:
        _ensure_open_daily_period_row(connection, purchase_date)
        existing = connection.execute(
            "SELECT * FROM physical_events WHERE external_key=?", (key,)
        ).fetchone()
        if existing:
            event_tracks = {
                row["song_id"] for row in connection.execute(
                    "SELECT song_id FROM physical_event_tracks WHERE event_id=?", (existing["id"],)
                )
            }
            unchanged = (
                existing["album_id"] == album_id
                and existing["purchase_date"] == purchase_date
                and existing["edition_label"] == label
                and existing["format"] == format_code
                and existing["quantity"] == quantity
                and event_tracks == selected
                and existing["notes"] == (notes.strip() if notes else None)
            )
            if not unchanged:
                raise ValueError(f"实体事件幂等键冲突：{key}")
            return dict(existing)
        for song in normalized_songs:
            _upsert_song(connection, song)
        purchase_weight = _physical_purchase_weight(
            connection, album_id, label, target, quantity
        )
        event_id = connection.execute(
            "INSERT INTO physical_events(album_id, purchase_date, edition_label, format, quantity, "
            "edition_weight, purchase_weight, duration_days, rank_schedule_version, scoring_version, "
            "external_key, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 28, ?, ?, ?, ?, ?)",
            (
                album_id, purchase_date, label, format_code, quantity, edition_weight,
                purchase_weight, PHYSICAL_RANK_SCHEDULE_VERSION, PHYSICAL_EVENT_SCORING_VERSION,
                key, notes.strip() if notes else None, datetime.now(timezone.utc).isoformat(),
            ),
        ).lastrowid
        artist_shares: dict[str, float] = {}
        track_share = 1 / len(selected)
        for song in normalized_songs:
            if song["id"] not in selected:
                continue
            connection.execute(
                "INSERT INTO physical_event_tracks(event_id, song_id, track_weight) VALUES (?, ?, 1)",
                (event_id, song["id"]),
            )
            artist_share = track_share / len(song["artists"])
            for artist in song["artists"]:
                artist_shares[artist["id"]] = artist_shares.get(artist["id"], 0) + artist_share
        for artist_id in sorted(artist_shares):
            connection.execute(
                "INSERT INTO physical_event_artists(event_id, artist_id, share) VALUES (?, ?, ?)",
                (event_id, artist_id, artist_shares[artist_id]),
            )
    return dict(connection.execute("SELECT * FROM physical_events WHERE id=?", (event_id,)).fetchone())


def import_manual_adjustment(
    connection: sqlite3.Connection,
    item: dict[str, Any],
    period_key: str,
    points: float,
    reason: str,
    external_key: str,
) -> tuple[dict[str, Any], int]:
    normalized = _normalize_catalog_song(item)
    if isinstance(points, bool) or not isinstance(points, (int, float)) or points == 0:
        raise ValueError("人工调整必须是非零数字")
    explanation = reason.strip()
    if not explanation:
        raise ValueError("人工调整必须填写原因")
    key = external_key.strip()
    if not key:
        raise ValueError("缺少幂等键")
    with connection:
        period = _ensure_open_daily_period_row(connection, period_key)
        _upsert_song(connection, normalized)
        existing = connection.execute(
            "SELECT * FROM manual_adjustment_events WHERE external_key=?", (key,)
        ).fetchone()
        if existing:
            unchanged = (
                existing["period_id"] == period["id"]
                and existing["song_id"] == normalized["id"]
                and existing["points"] == points
                and existing["reason"] == explanation
            )
            if not unchanged:
                raise ValueError(f"人工修正幂等键冲突：{key}")
            return normalized, 0
        connection.execute(
            "INSERT INTO manual_adjustment_events(period_id, song_id, points, reason, external_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (period["id"], normalized["id"], points, explanation, key, datetime.now(timezone.utc).isoformat()),
        )
        imported = _insert_ledger_row(
            connection, period["id"], normalized["id"], "manualAdjustment", points,
            "manual-adjustment-v1", key,
        )
    return normalized, imported


def _ensure_open_daily_period_row(connection: sqlite3.Connection, period_key: str) -> sqlite3.Row:
    target = date.fromisoformat(period_key)
    period = connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type='songs' AND period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()
    if period and period["frozen"]:
        raise ValueError(f"日榜周期 {period_key} 已结算，拒绝修改账本")
    if not period:
        period_id = connection.execute(
            "INSERT INTO chart_periods(entity_type, period_type, period_key, target_date, scheduled_at, "
            "status, coverage) VALUES ('songs', 'daily', ?, ?, ?, 'collecting', 0)",
            (period_key, period_key, _scheduled_at(target)),
        ).lastrowid
        period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
    return period


def _insert_ledger_row(
    connection: sqlite3.Connection,
    period_id: int,
    song_id: str,
    source: str,
    points: float,
    scoring_version: str,
    external_key: str,
) -> int:
    if not external_key:
        raise ValueError("缺少幂等键")
    existing = connection.execute(
        "SELECT period_id, song_id, points, scoring_version FROM point_ledger "
        "WHERE source=? AND external_key=?",
        (source, external_key),
    ).fetchone()
    if existing:
        unchanged = (
            existing["period_id"] == period_id
            and existing["song_id"] == song_id
            and existing["points"] == points
            and existing["scoring_version"] == scoring_version
        )
        if unchanged:
            return 0
        raise ValueError(f"账本幂等键冲突：{source}/{external_key}")
    connection.execute(
        "INSERT INTO point_ledger(period_id, song_id, source, points, scoring_version, external_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (period_id, song_id, source, points, scoring_version, external_key, datetime.now(timezone.utc).isoformat()),
    )
    return 1


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
    connection.execute("DELETE FROM song_artists WHERE song_id=?", (item["id"],))
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
        "SELECT id, input_fingerprint, frozen FROM chart_periods "
        "WHERE entity_type='songs' AND period_type='daily' AND period_key=?",
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
                "UPDATE chart_periods SET scheduled_at=?, collected_at=?, status=?, coverage=?, "
                "input_fingerprint=?, source_snapshot=?, collection_version=? WHERE id=?",
                (
                    payload["scheduledAt"], payload.get("collectedAt"), status, coverage,
                    fingerprint, payload.get("sourceSnapshot"),
                    payload.get("collectionVersion", NETEASE_COLLECTION_VERSION), period_id,
                ),
            )
            connection.execute("DELETE FROM netease_snapshot_entries WHERE period_id=?", (period_id,))
        else:
            period_id = connection.execute(
                "INSERT INTO chart_periods(entity_type, period_type, period_key, target_date, scheduled_at, "
                "collected_at, status, coverage, input_fingerprint, source_snapshot, collection_version) "
                "VALUES ('songs', 'daily', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    period_key, period_key, payload["scheduledAt"], payload.get("collectedAt"),
                    status, coverage, fingerprint, payload.get("sourceSnapshot"),
                    payload.get("collectionVersion", NETEASE_COLLECTION_VERSION),
                ),
            ).lastrowid
        for item in entries:
            _upsert_song(connection, item)
            connection.execute(
                "INSERT INTO netease_snapshot_entries(period_id, song_id, source_rank, relative_score) "
                "VALUES (?, ?, ?, ?)",
                (period_id, item["id"], item["rank"], item["relativeScore"]),
            )
    return period_id


def ensure_open_daily_period(connection: sqlite3.Connection, period_key: str) -> sqlite3.Row:
    with connection:
        return _ensure_open_daily_period_row(connection, period_key)


def import_ledger_entries(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    period_key = payload["periodKey"]
    period = ensure_open_daily_period(connection, period_key)

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
                    period["id"], item["songId"], source, item["points"],
                    item["scoringVersion"], item["externalKey"], datetime.now(timezone.utc).isoformat(),
                ),
            )
            imported += 1
    return imported


def _previous_period(
    connection: sqlite3.Connection,
    entity_type: str,
    period_type: str,
    period_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type=? AND period_type=? AND period_key < ? "
        "AND frozen=1 ORDER BY period_key DESC LIMIT 1",
        (entity_type, period_type, period_key),
    ).fetchone()


def _history(
    connection: sqlite3.Connection,
    entity_type: str,
    period_type: str,
    period_key: str,
) -> tuple[dict[str, int], set[str], dict[str, tuple[int, int]]]:
    previous = _previous_period(connection, entity_type, period_type, period_key)
    previous_ranks = {}
    if previous:
        previous_ranks = {
            row["entity_id"]: row["rank"]
            for row in connection.execute(
                "SELECT entity_id, rank FROM chart_entries WHERE period_id=?",
                (previous["id"],),
            )
        }
    rows = connection.execute(
        "SELECT ce.entity_id, MIN(ce.rank) AS peak, COUNT(*) AS periods "
        "FROM chart_entries ce JOIN chart_periods cp ON cp.id=ce.period_id "
        "WHERE cp.entity_type=? AND cp.period_type=? AND cp.period_key < ? AND cp.frozen=1 "
        "GROUP BY ce.entity_id",
        (entity_type, period_type, period_key),
    ).fetchall()
    records = {row["entity_id"]: (row["peak"], row["periods"]) for row in rows}
    return previous_ranks, set(records), records


def _write_chart_entries(
    connection: sqlite3.Connection,
    period: sqlite3.Row,
    totals: list[PointTotal],
) -> None:
    previous_ranks, appeared_before, records = _history(
        connection, period["entity_type"], period["period_type"], period["period_key"]
    )
    ranked = rank_totals(totals, previous_ranks)
    connection.execute("DELETE FROM chart_entries WHERE period_id=?", (period["id"],))
    for rank, total in enumerate(ranked, start=1):
        movement_type, movement_value, previous_rank = movement_for(
            total.entity_id, rank, previous_ranks, appeared_before
        )
        old_peak, old_periods = records.get(total.entity_id, (rank, 0))
        connection.execute(
            "INSERT INTO chart_entries(period_id, entity_id, rank, previous_rank, movement_type, movement_value, "
            "peak, periods, netease_points, physical_points, bilibili_points, other_points, legacy_bonus, "
            "manual_adjustment, total_points) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                period["id"], total.entity_id, rank, previous_rank, movement_type, movement_value,
                min(old_peak, rank), old_periods + 1, total.netease, total.physical, total.bilibili,
                total.other, total.legacy_bonus, total.manual_adjustment, total.total,
            ),
        )


def _point_totals(rows: list[sqlite3.Row]) -> list[PointTotal]:
    values: dict[str, dict[str, float]] = {}
    for row in rows:
        values.setdefault(row["entity_id"], {})[SOURCE_COLUMNS[row["source"]]] = row["points"]
    return [PointTotal(entity_id=entity_id, **points) for entity_id, points in values.items()]


def _ledger_totals(
    connection: sqlite3.Connection,
    period_ids: list[int],
    entity_type: str,
    period_type: str,
) -> list[PointTotal]:
    if not period_ids:
        return []
    placeholders = ",".join("?" for _ in period_ids)
    yearly_filter = "" if period_type == "yearly" else "AND scoring_version != ? "
    parameters: list[Any] = [*period_ids]
    if yearly_filter:
        parameters.append(YEARLY_ONLY_SCORING_VERSION)
    if entity_type == "songs":
        rows = connection.execute(
            f"SELECT song_id AS entity_id, source, SUM(points) AS points "
            f"FROM point_ledger WHERE period_id IN ({placeholders}) "
            f"{yearly_filter}GROUP BY song_id, source",
            parameters,
        ).fetchall()
    elif entity_type == "albums":
        rows = list(connection.execute(
            f"SELECT s.album_id AS entity_id, pl.source, SUM(pl.points) AS points "
            f"FROM point_ledger pl JOIN songs s ON s.id=pl.song_id WHERE pl.period_id IN ({placeholders}) "
            f"AND pl.source<>'physical' {yearly_filter.replace('scoring_version', 'pl.scoring_version')}"
            f"GROUP BY s.album_id, pl.source",
            parameters,
        ).fetchall())
        rows.extend(connection.execute(
            f"SELECT s.album_id AS entity_id, 'physical' AS source, SUM(pl.points) AS points "
            f"FROM point_ledger pl JOIN songs s ON s.id=pl.song_id WHERE pl.period_id IN ({placeholders}) "
            f"AND pl.source='physical' AND pl.external_key NOT LIKE 'physical-event:%' GROUP BY s.album_id",
            period_ids,
        ).fetchall())
        event_rows = connection.execute(
            f"SELECT pe.album_id AS entity_id, 'physical' AS source, SUM(pr.points) AS points "
            f"FROM physical_releases pr JOIN physical_events pe ON pe.id=pr.event_id "
            f"WHERE pr.period_id IN ({placeholders}) GROUP BY pe.album_id",
            period_ids,
        ).fetchall()
        event_points = {row["entity_id"]: row["points"] for row in event_rows}
        for row in rows:
            if row["source"] == "physical" and row["entity_id"] in event_points:
                event_points[row["entity_id"]] += row["points"]
        rows = [row for row in rows if row["source"] != "physical"] + [
            {"entity_id": entity_id, "source": "physical", "points": points}
            for entity_id, points in event_points.items()
        ]
    elif entity_type == "artists":
        rows = list(connection.execute(
            f"WITH credits AS (SELECT song_id, COUNT(*) AS count FROM song_artists GROUP BY song_id) "
            f"SELECT sa.artist_id AS entity_id, pl.source, "
            f"SUM(pl.points / credits.count) AS points "
            f"FROM point_ledger pl JOIN credits ON credits.song_id=pl.song_id "
            f"JOIN song_artists sa ON sa.song_id=pl.song_id WHERE pl.period_id IN ({placeholders}) "
            f"AND pl.source<>'physical' {yearly_filter.replace('scoring_version', 'pl.scoring_version')}"
            f"GROUP BY sa.artist_id, pl.source",
            parameters,
        ).fetchall())
        legacy_rows = connection.execute(
            f"WITH credits AS (SELECT song_id, COUNT(*) AS count FROM song_artists GROUP BY song_id) "
            f"SELECT sa.artist_id AS entity_id, 'physical' AS source, SUM(pl.points / credits.count) AS points "
            f"FROM point_ledger pl JOIN credits ON credits.song_id=pl.song_id "
            f"JOIN song_artists sa ON sa.song_id=pl.song_id WHERE pl.period_id IN ({placeholders}) "
            f"AND pl.source='physical' AND pl.external_key NOT LIKE 'physical-event:%' GROUP BY sa.artist_id",
            period_ids,
        ).fetchall()
        event_rows = connection.execute(
            f"SELECT pea.artist_id AS entity_id, 'physical' AS source, SUM(pr.points * pea.share) AS points "
            f"FROM physical_releases pr JOIN physical_event_artists pea ON pea.event_id=pr.event_id "
            f"WHERE pr.period_id IN ({placeholders}) GROUP BY pea.artist_id",
            period_ids,
        ).fetchall()
        physical_points: dict[str, float] = {}
        for row in [*legacy_rows, *event_rows]:
            physical_points[row["entity_id"]] = physical_points.get(row["entity_id"], 0) + row["points"]
        rows.extend({"entity_id": entity_id, "source": "physical", "points": points} for entity_id, points in physical_points.items())
    else:
        raise ValueError(f"不支持的实体类型：{entity_type}")
    return _point_totals(rows)


def _scheduled_at(target: date) -> str:
    return datetime.combine(target, time(22), tzinfo=CHINA_TIMEZONE).isoformat()


def _create_derived_period(
    connection: sqlite3.Connection,
    entity_type: str,
    period_type: str,
    period_key: str,
    target: date,
    status: str,
    coverage: float,
    source_period: sqlite3.Row | None = None,
) -> int:
    existing = connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type=? AND period_type=? AND period_key=?",
        (entity_type, period_type, period_key),
    ).fetchone()
    if existing and existing["frozen"]:
        return existing["id"]
    scheduled_at = _scheduled_at(target)
    if existing:
        connection.execute(
            "UPDATE chart_periods SET target_date=?, scheduled_at=?, collected_at=?, status=?, coverage=?, "
            "source_snapshot=?, collection_version=?, netease_scoring_version=? WHERE id=?",
            (
                target.isoformat(), scheduled_at, scheduled_at, status, coverage,
                source_period["source_snapshot"] if source_period else None,
                source_period["collection_version"] if source_period else None,
                source_period["netease_scoring_version"] if source_period else None,
                existing["id"],
            ),
        )
        return existing["id"]
    return connection.execute(
        "INSERT INTO chart_periods(entity_type, period_type, period_key, target_date, scheduled_at, "
        "collected_at, status, coverage, source_snapshot, collection_version, netease_scoring_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_type, period_type, period_key, target.isoformat(), scheduled_at, scheduled_at,
            status, coverage, source_period["source_snapshot"] if source_period else None,
            source_period["collection_version"] if source_period else None,
            source_period["netease_scoring_version"] if source_period else None,
        ),
    ).lastrowid


def _materialize_bilibili_events(
    connection: sqlite3.Connection,
    period: sqlite3.Row,
) -> None:
    for event in connection.execute(
        "SELECT * FROM bilibili_view_events WHERE period_id=?", (period["id"],)
    ):
        _insert_ledger_row(
            connection,
            period["id"],
            event["song_id"],
            "bilibili",
            bilibili_points_for_views(event["view_count"]),
            event["scoring_version"],
            f"bilibili-event:{event['id']}",
        )


def _materialize_physical_events(
    connection: sqlite3.Connection,
    period: sqlite3.Row,
    netease_points: dict[str, int],
) -> None:
    target = date.fromisoformat(period["period_key"])
    rank_points = {
        row["source_rank"]: netease_points[row["song_id"]]
        for row in connection.execute(
            "SELECT song_id, source_rank FROM netease_snapshot_entries WHERE period_id=?",
            (period["id"],),
        )
    }
    events = connection.execute(
        "SELECT * FROM physical_events WHERE purchase_date BETWEEN ? AND ?",
        ((target - timedelta(days=27)).isoformat(), target.isoformat()),
    ).fetchall()
    for event in events:
        day_index = (target - date.fromisoformat(event["purchase_date"])).days + 1
        if day_index == 1:
            for rank, value in rank_points.items():
                connection.execute(
                    "INSERT OR IGNORE INTO physical_reference_points(event_id, rank, points) VALUES (?, ?, ?)",
                    (event["id"], rank, float(value)),
                )
        reference_rank = physical_reference_rank(day_index)
        reference = connection.execute(
            "SELECT points FROM physical_reference_points WHERE event_id=? AND rank=?",
            (event["id"], reference_rank),
        ).fetchone()
        if not reference:
            continue
        event_points = (
            float(reference["points"])
            * 1.25
            * event["edition_weight"]
            * event["purchase_weight"]
        )
        connection.execute(
            "INSERT OR IGNORE INTO physical_releases(event_id, period_id, day_index, reference_rank, "
            "reference_points, points, scoring_version, rank_schedule_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"], period["id"], day_index, reference_rank, reference["points"],
                event_points, PHYSICAL_SCORING_VERSION, PHYSICAL_RANK_SCHEDULE_VERSION,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        for track in connection.execute(
            "SELECT song_id, track_weight FROM physical_event_tracks WHERE event_id=?",
            (event["id"],),
        ):
            _insert_ledger_row(
                connection,
                period["id"],
                track["song_id"],
                "physical",
                event_points * track["track_weight"],
                PHYSICAL_SCORING_VERSION,
                f"physical-event:{event['id']}:{period['period_key']}:{track['song_id']}",
            )


def settle_daily(connection: sqlite3.Connection, period_key: str) -> int:
    period = connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type='songs' AND period_type='daily' AND period_key=?",
        (period_key,),
    ).fetchone()
    if not period:
        raise ValueError(f"日榜周期 {period_key} 不存在")
    if period["frozen"]:
        return period["id"]
    entries = [
        NeteaseInput(row["song_id"], row["source_rank"], row["relative_score"])
        for row in connection.execute(
            "SELECT song_id, source_rank, relative_score FROM netease_snapshot_entries WHERE period_id=?",
            (period["id"],),
        )
    ]
    points = allocate_netease_points(entries)
    with connection:
        for song_id, value in points.items():
            _insert_ledger_row(
                connection,
                period["id"],
                song_id,
                "netease",
                float(value),
                NETEASE_SCORING_VERSION,
                f"netease:{period_key}:{song_id}",
            )
        _materialize_bilibili_events(connection, period)
        _materialize_physical_events(connection, period, points)
        _write_chart_entries(
            connection,
            period,
            _ledger_totals(connection, [period["id"]], "songs", "daily"),
        )
        connection.execute(
            "UPDATE chart_periods SET status='settled', coverage=1, frozen=1, "
            "netease_scoring_version=? WHERE id=?",
            (NETEASE_SCORING_VERSION, period["id"]),
        )
    return period["id"]


def settle_entity_daily(
    connection: sqlite3.Connection,
    entity_type: str,
    period_key: str,
) -> int:
    if entity_type == "songs":
        return settle_daily(connection, period_key)
    source = connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type='songs' AND period_type='daily' "
        "AND period_key=? AND frozen=1",
        (period_key,),
    ).fetchone()
    if not source:
        raise ValueError(f"歌曲日榜 {period_key} 尚未结算")
    target = date.fromisoformat(period_key)
    with connection:
        period_id = _create_derived_period(
            connection, entity_type, "daily", period_key, target, "settled", 1, source
        )
        period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
        if not period["frozen"]:
            _write_chart_entries(
                connection,
                period,
                _ledger_totals(connection, [source["id"]], entity_type, "daily"),
            )
            connection.execute("UPDATE chart_periods SET frozen=1 WHERE id=?", (period_id,))
    return period_id


def _period_window(period_type: str, target: date) -> tuple[str, date, date]:
    if period_type == "weekly":
        start = target - timedelta(days=target.weekday())
        end = start + timedelta(days=6)
        year, week, _ = target.isocalendar()
        return f"{year}-W{week:02d}", start, end
    if period_type == "monthly":
        start = target.replace(day=1)
        end = target.replace(day=calendar.monthrange(target.year, target.month)[1])
        return target.strftime("%Y-%m"), start, end
    if period_type == "yearly":
        return str(target.year), date(target.year, 1, 1), date(target.year, 12, 31)
    raise ValueError(f"不支持的周期类型：{period_type}")


def settle_period(
    connection: sqlite3.Connection,
    entity_type: str,
    period_type: str,
    target_date: str,
) -> int:
    if entity_type not in ENTITY_TYPES or period_type not in LONG_PERIOD_TYPES:
        raise ValueError(f"不支持的榜单：{entity_type}/{period_type}")
    target = date.fromisoformat(target_date)
    period_key, start, end = _period_window(period_type, target)
    existing = connection.execute(
        "SELECT * FROM chart_periods WHERE entity_type=? AND period_type=? AND period_key=?",
        (entity_type, period_type, period_key),
    ).fetchone()
    if existing and existing["frozen"]:
        return existing["id"]

    effective_end = min(target, end)
    completed = target >= end
    if target > end:
        later_dates = connection.execute(
            "SELECT 1 FROM chart_periods WHERE entity_type='songs' AND period_type='daily' "
            "AND frozen=1 AND target_date > ? LIMIT 1",
            (end.isoformat(),),
        ).fetchone()
        if not later_dates:
            completed = False
    daily_periods = connection.execute(
        "SELECT id, target_date FROM chart_periods WHERE entity_type='songs' AND period_type='daily' "
        "AND frozen=1 AND target_date BETWEEN ? AND ? ORDER BY target_date",
        (start.isoformat(), effective_end.isoformat()),
    ).fetchall()
    expected_days = (end - start).days + 1
    coverage = len({row["target_date"] for row in daily_periods}) / expected_days
    status = "settled" if completed and coverage == 1 else "partial" if completed else "collecting"
    period_target = end if completed else target
    with connection:
        period_id = _create_derived_period(
            connection, entity_type, period_type, period_key, period_target, status, coverage
        )
        period = connection.execute("SELECT * FROM chart_periods WHERE id=?", (period_id,)).fetchone()
        input_ids = [row["id"] for row in daily_periods]
        _write_chart_entries(
            connection,
            period,
            _ledger_totals(
                connection,
                input_ids,
                entity_type,
                period_type,
            ),
        )
        if input_ids:
            placeholders = ",".join("?" for _ in input_ids)
            collection_versions = [
                row[0] for row in connection.execute(
                    f"SELECT DISTINCT collection_version FROM chart_periods "
                    f"WHERE id IN ({placeholders}) AND collection_version IS NOT NULL ORDER BY 1",
                    input_ids,
                )
            ]
            scoring_versions = [
                row[0] for row in connection.execute(
                    f"SELECT DISTINCT scoring_version FROM point_ledger "
                    f"WHERE period_id IN ({placeholders}) AND source='netease' ORDER BY 1",
                    input_ids,
                )
            ]
            connection.execute(
                "UPDATE chart_periods SET collection_version=?, netease_scoring_version=? WHERE id=?",
                (
                    ",".join(collection_versions) or None,
                    ",".join(scoring_versions) or None,
                    period_id,
                ),
            )
        if completed:
            connection.execute("UPDATE chart_periods SET frozen=1 WHERE id=?", (period_id,))
    return period_id


def settle_weekly(connection: sqlite3.Connection, target_date: str) -> int:
    return settle_period(connection, "songs", "weekly", target_date)


def settle_monthly(connection: sqlite3.Connection, target_date: str) -> int:
    return settle_period(connection, "songs", "monthly", target_date)


def settle_yearly(connection: sqlite3.Connection, target_date: str) -> int:
    return settle_period(connection, "songs", "yearly", target_date)


def previous_period_end(period_type: str, target: date) -> date:
    if period_type == "weekly":
        return target - timedelta(days=target.weekday() + 1)
    if period_type == "monthly":
        return target.replace(day=1) - timedelta(days=1)
    if period_type == "yearly":
        return date(target.year - 1, 12, 31)
    raise ValueError(f"不支持的周期类型：{period_type}")
