from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, getcontext
from statistics import median

getcontext().prec = 28
NETEASE_POOL = 10_000


@dataclass(frozen=True)
class NeteaseInput:
    song_id: str
    rank: int
    weekly_plays: int


def allocate_netease_points(entries: list[NeteaseInput]) -> dict[str, int]:
    if len(entries) != 100:
        raise ValueError("网易云正式结算需要完整的 Top 100")
    ranks = {entry.rank for entry in entries}
    if ranks != set(range(1, 101)) or len({entry.song_id for entry in entries}) != 100:
        raise ValueError("网易云快照必须包含唯一的歌曲和连续的 1—100 名")
    if any(entry.weekly_plays < 0 for entry in entries):
        raise ValueError("最近七日播放量不能为负数")

    median_plays = Decimal(str(median(entry.weekly_plays for entry in entries)))
    safe_median = max(median_plays, Decimal(1))
    weights: dict[str, Decimal] = {}
    for entry in entries:
        rank_ratio = Decimal(101 - entry.rank) / Decimal(100)
        rank_weight = Decimal("0.08") + Decimal("0.92") * (
            Decimal(str(float(rank_ratio) ** 1.35))
        )
        strength = Decimal(str((Decimal(entry.weekly_plays) / safe_median) ** Decimal("0.25")))
        strength = max(Decimal("0.80"), min(strength, Decimal("1.25")))
        weights[entry.song_id] = rank_weight * strength

    weight_total = sum(weights.values())
    exact = {
        song_id: Decimal(NETEASE_POOL) * weight / weight_total
        for song_id, weight in weights.items()
    }
    allocated = {
        song_id: int(points.to_integral_value(rounding=ROUND_FLOOR))
        for song_id, points in exact.items()
    }
    remaining = NETEASE_POOL - sum(allocated.values())
    by_song = {entry.song_id: entry for entry in entries}
    remainders = sorted(
        exact,
        key=lambda song_id: (
            -(exact[song_id] - Decimal(allocated[song_id])),
            by_song[song_id].rank,
            song_id,
        ),
    )
    for song_id in remainders[:remaining]:
        allocated[song_id] += 1
    return allocated
