from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PointTotal:
    song_id: str
    netease: float = 0
    physical: float = 0
    bilibili: float = 0
    other: float = 0
    legacy_bonus: float = 0
    manual_adjustment: float = 0

    @property
    def total(self) -> float:
        return (
            self.netease
            + self.physical
            + self.bilibili
            + self.other
            + self.legacy_bonus
            + self.manual_adjustment
        )


def rank_totals(
    totals: list[PointTotal],
    previous_ranks: dict[str, int],
) -> list[PointTotal]:
    eligible = [total for total in totals if total.total > 0]
    return sorted(
        eligible,
        key=lambda total: (
            -total.total,
            -total.netease,
            -total.physical,
            -total.bilibili,
            previous_ranks.get(total.song_id, 101),
            total.song_id,
        ),
    )[:100]


def movement_for(
    song_id: str,
    current_rank: int,
    previous_ranks: dict[str, int],
    appeared_before: set[str],
) -> tuple[str, int, int | None]:
    previous_rank = previous_ranks.get(song_id)
    if previous_rank is None:
        return ("re" if song_id in appeared_before else "new", 0, None)
    if current_rank < previous_rank:
        return "up", previous_rank - current_rank, previous_rank
    if current_rank > previous_rank:
        return "down", current_rank - previous_rank, previous_rank
    return "same", 0, previous_rank
