from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from statistics import median

getcontext().prec = 28
NETEASE_POOL = 10_000
NETEASE_SCORING_VERSION = "netease-streaming-v2"
BILIBILI_SCORING_VERSION = "bilibili-manual-views-v1"
PHYSICAL_SCORING_VERSION = "physical-v4"
PHYSICAL_EVENT_SCORING_VERSION = "physical-event-v2"
PHYSICAL_RANK_SCHEDULE_VERSION = "physical-rank-schedule-v2"
PHYSICAL_REFERENCE_RANKS = (
    10, 45, 48, 52, 56, 60, 64,
    69, 74, 79, 84, 90, 96, 100,
    100, 100, 100, 100, 100, 100, 100,
    100, 100, 100, 100, 100, 100, 100,
)
PHYSICAL_FORMAT_WEIGHTS = {
    "standard_cd": Decimal("1.00"),
    "deluxe_cd": Decimal("1.15"),
    "limited_cd": Decimal("1.20"),
    "cassette": Decimal("0.90"),
    "vinyl": Decimal("1.25"),
    "limited_vinyl": Decimal("1.35"),
    "box_set": Decimal("1.50"),
    "single_cd": Decimal("0.70"),
    "seven_inch_single": Decimal("0.85"),
}


def bilibili_points_for_views(view_count: int) -> int:
    if isinstance(view_count, bool) or not isinstance(view_count, int) or view_count < 0:
        raise ValueError("B站观看次数必须是非负整数")
    for upper, points in (
        (0, 0), (1, 20), (2, 35), (4, 55), (7, 80),
        (12, 110), (20, 145), (35, 180), (60, 215),
    ):
        if view_count <= upper:
            return points
    return 250


def physical_reference_rank(day_index: int) -> int:
    if isinstance(day_index, bool) or not isinstance(day_index, int) or not 1 <= day_index <= 28:
        raise ValueError("实体释放日必须在 1—28 之间")
    return PHYSICAL_REFERENCE_RANKS[day_index - 1]


def physical_format_weight(format_code: str) -> Decimal:
    try:
        return PHYSICAL_FORMAT_WEIGHTS[format_code]
    except KeyError as error:
        raise ValueError(f"不支持的实体格式：{format_code}") from error


def physical_purchase_weight(
    same_edition_count: int,
    related_edition_count: int,
    quantity: int,
) -> Decimal:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (
        same_edition_count, related_edition_count,
    )):
        raise ValueError("实体购买历史数量无效")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("实体数量必须是正整数")
    copy_weights = (
        Decimal("1.00"), Decimal("0.30"), Decimal("0.15"), Decimal("0.10"),
    )
    if same_edition_count:
        return sum(
            copy_weights[min(same_edition_count + offset, 3)]
            for offset in range(quantity)
        )
    edition_weights = (
        Decimal("1.00"), Decimal("0.60"), Decimal("0.35"), Decimal("0.20"),
    )
    edition_weight = edition_weights[min(related_edition_count, 3)]
    return edition_weight * sum(copy_weights[min(offset, 3)] for offset in range(quantity))


@dataclass(frozen=True)
class NeteaseInput:
    song_id: str
    rank: int
    weekly_plays: int


def allocate_netease_points(entries: list[NeteaseInput]) -> dict[str, Decimal]:
    if len(entries) != 100:
        raise ValueError("网易云正式结算需要完整的 Top 100")
    ranks = {entry.rank for entry in entries}
    if ranks != set(range(1, 101)) or len({entry.song_id for entry in entries}) != 100:
        raise ValueError("网易云快照必须包含唯一的歌曲和连续的 1—100 名")
    if any(entry.weekly_plays < 0 for entry in entries):
        raise ValueError("最近七日播放量不能为负数")

    median_plays = Decimal(str(median(entry.weekly_plays for entry in entries)))
    safe_median = max(median_plays, Decimal(1))
    groups: dict[int, list[NeteaseInput]] = {}
    for entry in sorted(entries, key=lambda item: (item.weekly_plays, item.song_id)):
        groups.setdefault(entry.weekly_plays, []).append(entry)

    weights: dict[str, Decimal] = {}
    for weekly_plays in sorted(groups):
        group = groups[weekly_plays]
        average_rank = sum(Decimal(entry.rank) for entry in group) / Decimal(len(group))
        rank_ratio = (Decimal(101) - average_rank) / Decimal(100)
        rank_weight = Decimal("0.08") + Decimal("0.92") * Decimal(
            str(float(rank_ratio) ** 1.35)
        )
        strength = Decimal(str((Decimal(weekly_plays) / safe_median) ** Decimal("0.25")))
        strength = max(Decimal("0.80"), min(strength, Decimal("1.25")))
        weight = rank_weight * strength
        for entry in group:
            weights[entry.song_id] = weight

    weight_total = sum(weights.values())
    allocated = {
        song_id: Decimal(NETEASE_POOL) * weight / weight_total
        for song_id, weight in weights.items()
    }
    adjustment = (Decimal(NETEASE_POOL) - sum(allocated.values())) / Decimal(len(allocated))
    return {
        song_id: points + adjustment
        for song_id, points in allocated.items()
    }
