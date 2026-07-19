from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .database import connect, initialize
from .export import export_period
from .settlement import (
    import_ledger_entries,
    import_netease_snapshot,
    settle_daily,
    settle_weekly,
)

DEMO_END_DATE = date(2026, 7, 17)
SONG_COUNT = 100

TITLES = [
    "Midnight Index",
    "白昼信号",
    "Afterimage",
    "留声机里的雨",
    "Cherry Static",
    "逆光航线",
    "Sunday Vinyl",
    "天台广播",
    "Neon Calendar",
    "晚风档案",
]
ARTISTS = [
    "Luna Archive",
    "纸飞机合唱团",
    "Northbound",
    "林屿",
    "Hotel Cinema",
    "回声档案",
    "Mika Rowe",
    "不眠电台",
    "Satellite Club",
    "潮汐邮局",
]
ALBUMS = [
    "Glass Hours",
    "城市经纬",
    "Soft Geometry",
    "漫长的星期日",
    "Channel Four",
    "零点以后",
    "Pressed Flowers",
    "南方夜班",
    "Electric Almanac",
    "海岸来信",
]
COLORS = ["#cb473d", "#356c7d", "#7a634a", "#556344", "#a24259", "#304a73", "#ad7d30", "#678094"]


def _song(number: int) -> dict:
    group = (number - 1) % len(TITLES)
    suffix = "" if number <= len(TITLES) else f" · {number:02d}"
    return {
        "id": f"demo-song-{number:03d}",
        "title": f"{TITLES[group]}{suffix}",
        "artists": [{"id": f"demo-artist-{group + 1:02d}", "name": ARTISTS[group]}],
        "album": {"id": f"demo-album-{group + 1:02d}", "title": ALBUMS[group]},
        "coverUrl": None,
        "coverColor": COLORS[group % len(COLORS)],
    }


def _snapshot(day: date, offset: int) -> dict:
    ranked_numbers = list(range(1, SONG_COUNT + 1))
    rotation = (offset * 7) % SONG_COUNT
    ranked_numbers = ranked_numbers[rotation:] + ranked_numbers[:rotation]
    if offset % 2:
        ranked_numbers[4:14] = reversed(ranked_numbers[4:14])

    entries = []
    for rank, number in enumerate(ranked_numbers, start=1):
        item = _song(number)
        item.update({
            "rank": rank,
            "relativeScore": max(1, 520 - rank * 4 + ((number * 13 + offset * 17) % 37)),
        })
        entries.append(item)
    key = day.isoformat()
    return {
        "collectionVersion": "demo-relative-score-v1",
        "periodKey": key,
        "scheduledAt": f"{key}T22:00:00+08:00",
        "collectedAt": f"{key}T22:03:00+08:00",
        "sourceSnapshot": f"demo://netease/{key}",
        "entries": entries,
    }


def _ledger(day: date, offset: int) -> dict:
    key = day.isoformat()
    entries = [
        {
            "externalKey": f"demo:physical:{key}:song-100",
            "source": "physical",
            "songId": "demo-song-100",
            "points": 1800 - offset * 120,
            "scoringVersion": "demo-physical-v1",
        },
        {
            "externalKey": f"demo:bilibili:{key}:song-042",
            "source": "bilibili",
            "songId": "demo-song-042",
            "points": 250,
            "scoringVersion": "demo-bilibili-v1",
        },
    ]
    if offset >= 2:
        entries.append({
            "externalKey": f"demo:physical:{key}:song-077",
            "source": "physical",
            "songId": "demo-song-077",
            "points": 950,
            "scoringVersion": "demo-physical-v1",
        })
    return {"periodKey": key, "entries": entries}


def generate_demo(database_path: str | Path, frontend_root: str | Path) -> list[Path]:
    database = Path(database_path)
    database.unlink(missing_ok=True)
    connection = connect(database)
    initialize(connection)
    exported: list[Path] = []
    try:
        monday = DEMO_END_DATE - timedelta(days=DEMO_END_DATE.weekday())
        daily_periods: dict[date, int] = {}
        for offset in range((DEMO_END_DATE - monday).days + 1):
            day = monday + timedelta(days=offset)
            import_netease_snapshot(connection, _snapshot(day, offset))
            import_ledger_entries(connection, _ledger(day, offset))
            daily_periods[day] = settle_daily(connection, day.isoformat())

        for day in (DEMO_END_DATE - timedelta(days=1), DEMO_END_DATE):
            exported.append(export_period(connection, daily_periods[day], frontend_root))

        weekly_period = settle_weekly(connection, DEMO_END_DATE.isoformat())
        exported.append(export_period(connection, weekly_period, frontend_root))
    finally:
        connection.close()
    return exported
