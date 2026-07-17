from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NETEASE_WEEKLY_URL = "https://music.163.com/api/v1/play/record?uid={uid}&type=1"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
Fetcher = Callable[[Request, float], dict[str, Any]]


def _default_fetcher(request: Request, timeout: float) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f"网易云周排行请求失败：{error}") from error


def fetch_weekly_ranking(
    uid: str,
    timeout: float = 20,
    fetcher: Fetcher = _default_fetcher,
) -> dict[str, Any]:
    if not uid.isdecimal():
        raise ValueError("网易云 UID 必须是数字")
    if timeout <= 0:
        raise ValueError("请求超时必须大于 0")

    request = Request(
        NETEASE_WEEKLY_URL.format(uid=uid),
        headers={
            "User-Agent": "Mozilla/5.0 EchoRank/0.1",
            "Referer": "https://music.163.com/",
            "Accept": "application/json",
        },
    )
    try:
        payload = fetcher(request, timeout)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f"网易云周排行请求失败：{error}") from error
    if not isinstance(payload, dict):
        raise ValueError("网易云周排行响应不是 JSON 对象")
    if payload.get("code") != 200:
        raise ValueError(f"网易云周排行不可用，响应代码：{payload.get('code')}")
    if not isinstance(payload.get("weekData"), list):
        raise ValueError("网易云周排行响应缺少 weekData")
    return payload


def raw_snapshot_path(period_key: str, raw_root: str | Path) -> Path:
    target_date = date.fromisoformat(period_key)
    return (
        Path(raw_root)
        / f"{target_date.year:04d}"
        / f"{target_date.month:02d}"
        / f"{period_key}.json"
    )


def archive_raw_snapshot(
    payload: dict[str, Any],
    period_key: str,
    raw_root: str | Path,
) -> Path:
    destination = raw_snapshot_path(period_key, raw_root)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        if destination.read_bytes() == content:
            return destination
        raise ValueError(f"原始快照已存在且内容不同，拒绝覆盖：{destination}")

    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise ValueError(f"原始快照已存在且内容不同，拒绝覆盖：{destination}")
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def _required_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"网易云周排行字段类型错误：{field}")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"网易云周排行字段缺失：{field}")
    return value


def _cover_color(album_id: int) -> str:
    digest = hashlib.sha256(str(album_id).encode("ascii")).hexdigest()
    return f"#{digest[:6]}"


def normalize_weekly_ranking(
    raw_payload: dict[str, Any],
    period_key: str,
    source_snapshot: str | Path,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    target_date = date.fromisoformat(period_key)
    week_data = raw_payload.get("weekData")
    if not isinstance(week_data, list) or len(week_data) != 100:
        count = len(week_data) if isinstance(week_data, list) else 0
        raise ValueError(f"网易云周排行不是完整 Top 100：当前 {count} 条")

    entries = []
    seen_song_ids: set[int] = set()
    for rank, record in enumerate(week_data, start=1):
        if not isinstance(record, dict) or not isinstance(record.get("song"), dict):
            raise ValueError(f"网易云周排行第 {rank} 条缺少歌曲信息")
        song = record["song"]
        song_id = _required_integer(song.get("id"), f"weekData[{rank}].song.id")
        if song_id in seen_song_ids:
            raise ValueError(f"网易云周排行包含重复歌曲：{song_id}")
        seen_song_ids.add(song_id)

        album = song.get("al")
        artists = song.get("ar")
        if not isinstance(album, dict) or not isinstance(artists, list) or not artists:
            raise ValueError(f"网易云周排行第 {rank} 条缺少专辑或艺人信息")
        album_id = _required_integer(album.get("id"), f"weekData[{rank}].song.al.id")
        normalized_artists = []
        for artist_index, artist in enumerate(artists):
            if not isinstance(artist, dict):
                raise ValueError(f"网易云周排行第 {rank} 条艺人信息错误")
            artist_id = _required_integer(
                artist.get("id"),
                f"weekData[{rank}].song.ar[{artist_index}].id",
            )
            normalized_artists.append({
                "id": f"netease-artist-{artist_id}",
                "name": _required_text(
                    artist.get("name"),
                    f"weekData[{rank}].song.ar[{artist_index}].name",
                ),
            })

        play_count = _required_integer(record.get("playCount"), f"weekData[{rank}].playCount")
        if play_count < 0:
            raise ValueError(f"网易云周排行第 {rank} 条播放次数不能为负数")
        cover_url = album.get("picUrl")
        if cover_url is not None and not isinstance(cover_url, str):
            raise ValueError(f"网易云周排行第 {rank} 条封面地址类型错误")

        entries.append({
            "id": f"netease-song-{song_id}",
            "title": _required_text(song.get("name"), f"weekData[{rank}].song.name"),
            "artists": normalized_artists,
            "album": {
                "id": f"netease-album-{album_id}",
                "title": _required_text(album.get("name"), f"weekData[{rank}].song.al.name"),
            },
            "coverUrl": cover_url,
            "coverColor": _cover_color(album_id),
            "rank": rank,
            "weeklyPlays": play_count,
        })

    collected = collected_at or datetime.now(CHINA_TIMEZONE)
    if collected.tzinfo is None:
        raise ValueError("采集时间必须包含时区")
    scheduled = datetime.combine(target_date, time(22), tzinfo=CHINA_TIMEZONE)
    return {
        "periodKey": period_key,
        "scheduledAt": scheduled.isoformat(),
        "collectedAt": collected.astimezone(CHINA_TIMEZONE).isoformat(),
        "sourceSnapshot": Path(source_snapshot).as_posix(),
        "entries": entries,
    }


def collect_weekly_snapshot(
    uid: str,
    period_key: str,
    raw_root: str | Path = "data/raw/netease",
    timeout: float = 20,
    fetcher: Fetcher = _default_fetcher,
    collected_at: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    raw_payload = fetch_weekly_ranking(uid, timeout, fetcher)
    archive_path = raw_snapshot_path(period_key, raw_root)
    normalized = normalize_weekly_ranking(
        raw_payload,
        period_key,
        archive_path,
        collected_at,
    )
    archive_raw_snapshot(raw_payload, period_key, raw_root)
    return normalized, archive_path
