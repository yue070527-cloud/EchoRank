from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.database import connect, initialize
from echorank.export import export_period, snapshot_for_period
from echorank.ranking import PointTotal, rank_totals
from echorank.scoring import NeteaseInput, allocate_netease_points
from echorank.settlement import (
    import_ledger_entries,
    import_netease_snapshot,
    settle_daily,
    settle_weekly,
)


def snapshot_payload(day: date, start: int = 1) -> dict:
    entries = []
    for rank, number in enumerate(range(start, start + 100), start=1):
        song_id = f"song-{number:03d}"
        entries.append({
            "id": song_id,
            "title": f"Synthetic Song {number:03d}",
            "artists": [{"id": f"artist-{number:03d}", "name": f"Artist {number:03d}"}],
            "album": {"id": f"album-{number:03d}", "title": f"Album {number:03d}"},
            "coverUrl": None,
            "coverColor": f"#{number % 0xFFFFFF:06x}",
            "rank": rank,
            "weeklyPlays": 201 - rank,
        })
    key = day.isoformat()
    return {
        "periodKey": key,
        "scheduledAt": f"{key}T22:00:00+08:00",
        "collectedAt": f"{key}T22:03:00+08:00",
        "sourceSnapshot": f"synthetic://netease/{key}",
        "entries": entries,
    }


class ScoringTests(unittest.TestCase):
    def test_pool_is_exact_and_deterministic(self) -> None:
        entries = [NeteaseInput(f"song-{rank:03d}", rank, 201 - rank) for rank in range(1, 101)]
        first = allocate_netease_points(entries)
        second = allocate_netease_points(list(reversed(entries)))
        self.assertEqual(sum(first.values()), 10_000)
        self.assertEqual(first, second)
        self.assertGreater(first["song-001"], first["song-100"])

    def test_requires_complete_top_100(self) -> None:
        with self.assertRaisesRegex(ValueError, "Top 100"):
            allocate_netease_points([NeteaseInput("song-001", 1, 10)])

    def test_tie_breaks_by_sources_then_previous_rank(self) -> None:
        totals = [
            PointTotal("b", netease=80, physical=20),
            PointTotal("a", netease=80, physical=20),
            PointTotal("c", netease=70, physical=30),
        ]
        ranked = rank_totals(totals, {"b": 2, "a": 1, "c": 3})
        self.assertEqual([item.song_id for item in ranked], ["a", "b", "c"])


class SettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.connection = connect(self.root / "echorank.db")
        initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def settle(self, day: date, start: int = 1) -> int:
        import_netease_snapshot(self.connection, snapshot_payload(day, start))
        return settle_daily(self.connection, day.isoformat())

    def test_incomplete_snapshot_is_not_settled(self) -> None:
        payload = snapshot_payload(date(2026, 7, 13))
        payload["entries"] = payload["entries"][:99]
        period_id = import_netease_snapshot(self.connection, payload)
        period = self.connection.execute("SELECT status, coverage FROM chart_periods WHERE id=?", (period_id,)).fetchone()
        self.assertEqual(period["status"], "partial")
        self.assertAlmostEqual(period["coverage"], 0.99)
        with self.assertRaisesRegex(ValueError, "Top 100"):
            settle_daily(self.connection, "2026-07-13")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM point_ledger").fetchone()[0], 0)

    def test_partial_snapshot_can_be_replaced_before_settlement(self) -> None:
        day = date(2026, 7, 13)
        partial = snapshot_payload(day)
        partial["entries"] = partial["entries"][:99]
        period_id = import_netease_snapshot(self.connection, partial)
        self.assertEqual(import_netease_snapshot(self.connection, snapshot_payload(day)), period_id)
        self.assertEqual(settle_daily(self.connection, day.isoformat()), period_id)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM netease_snapshot_entries WHERE period_id=?", (period_id,)).fetchone()[0],
            100,
        )

    def test_settled_snapshot_replacement_is_rejected(self) -> None:
        day = date(2026, 7, 13)
        self.settle(day)
        replacement = snapshot_payload(day)
        replacement["entries"][0]["weeklyPlays"] += 1
        with self.assertRaisesRegex(ValueError, "已结算"):
            import_netease_snapshot(self.connection, replacement)

    def test_daily_settlement_is_idempotent_and_preserves_total(self) -> None:
        period_id = self.settle(date(2026, 7, 13))
        self.assertEqual(settle_daily(self.connection, "2026-07-13"), period_id)
        netease_total = self.connection.execute(
            "SELECT SUM(points) FROM point_ledger WHERE period_id=? AND source='netease'", (period_id,)
        ).fetchone()[0]
        self.assertEqual(netease_total, 10_000)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM chart_entries WHERE period_id=?", (period_id,)).fetchone()[0], 100)

    def test_external_points_affect_combined_rank_and_are_idempotent(self) -> None:
        day = date(2026, 7, 13)
        import_netease_snapshot(self.connection, snapshot_payload(day))
        payload = {
            "periodKey": day.isoformat(),
            "entries": [{
                "externalKey": "physical:test:1",
                "source": "physical",
                "songId": "song-100",
                "points": 5000,
                "scoringVersion": "physical-test-v1",
            }],
        }
        self.assertEqual(import_ledger_entries(self.connection, payload), 1)
        self.assertEqual(import_ledger_entries(self.connection, payload), 0)
        period_id = settle_daily(self.connection, day.isoformat())
        first = self.connection.execute(
            "SELECT song_id, physical_points FROM chart_entries WHERE period_id=? AND rank=1", (period_id,)
        ).fetchone()
        self.assertEqual(first["song_id"], "song-100")
        self.assertEqual(first["physical_points"], 5000)

    def test_external_key_conflict_is_rejected(self) -> None:
        day = date(2026, 7, 13)
        import_netease_snapshot(self.connection, snapshot_payload(day))
        payload = {
            "periodKey": day.isoformat(),
            "entries": [{
                "externalKey": "physical:test:1",
                "source": "physical",
                "songId": "song-100",
                "points": 5000,
                "scoringVersion": "physical-test-v1",
            }],
        }
        import_ledger_entries(self.connection, payload)
        payload["entries"][0]["points"] = 4000
        with self.assertRaisesRegex(ValueError, "幂等键冲突"):
            import_ledger_entries(self.connection, payload)

    def test_duplicate_snapshot_rank_is_rejected(self) -> None:
        payload = snapshot_payload(date(2026, 7, 13))
        payload["entries"][1]["rank"] = 1
        with self.assertRaisesRegex(ValueError, "重复歌曲、重复排名或越界排名"):
            import_netease_snapshot(self.connection, payload)

    def test_new_re_and_record_history(self) -> None:
        monday = date(2026, 7, 13)
        first_id = self.settle(monday, 1)
        second_id = self.settle(monday + timedelta(days=1), 2)
        third_id = self.settle(monday + timedelta(days=2), 1)

        first = self.connection.execute(
            "SELECT movement_type, periods FROM chart_entries WHERE period_id=? AND song_id='song-001'", (first_id,)
        ).fetchone()
        newcomer = self.connection.execute(
            "SELECT movement_type FROM chart_entries WHERE period_id=? AND song_id='song-101'", (second_id,)
        ).fetchone()
        returned = self.connection.execute(
            "SELECT movement_type, previous_rank, periods FROM chart_entries WHERE period_id=? AND song_id='song-001'", (third_id,)
        ).fetchone()
        self.assertEqual(first["movement_type"], "new")
        self.assertEqual(newcomer["movement_type"], "new")
        self.assertEqual(returned["movement_type"], "re")
        self.assertIsNone(returned["previous_rank"])
        self.assertEqual(returned["periods"], 2)

    def test_live_week_and_sunday_freeze(self) -> None:
        monday = date(2026, 7, 13)
        for offset in range(3):
            self.settle(monday + timedelta(days=offset))
        live_id = settle_weekly(self.connection, (monday + timedelta(days=2)).isoformat())
        live = snapshot_for_period(self.connection, live_id)
        self.assertEqual(live["period"]["key"], "2026-W29")
        self.assertEqual(live["period"]["status"], "collecting")
        self.assertEqual(live["collection"]["coverage"], 1)
        self.assertEqual(sum(item["points"]["netease"] for item in live["entries"]), 30_000)

        for offset in range(3, 7):
            self.settle(monday + timedelta(days=offset))
        frozen_id = settle_weekly(self.connection, (monday + timedelta(days=6)).isoformat())
        frozen = snapshot_for_period(self.connection, frozen_id)
        self.assertEqual(frozen_id, live_id)
        self.assertEqual(frozen["period"]["status"], "settled")
        self.assertEqual(sum(item["points"]["netease"] for item in frozen["entries"]), 70_000)
        period = self.connection.execute("SELECT frozen FROM chart_periods WHERE id=?", (frozen_id,)).fetchone()
        self.assertEqual(period["frozen"], 1)

    def test_incomplete_sunday_week_remains_live(self) -> None:
        monday = date(2026, 7, 13)
        for offset in range(6):
            self.settle(monday + timedelta(days=offset))
        period_id = settle_weekly(self.connection, (monday + timedelta(days=6)).isoformat())
        period = self.connection.execute(
            "SELECT status, coverage, frozen FROM chart_periods WHERE id=?", (period_id,)
        ).fetchone()
        self.assertEqual(period["status"], "collecting")
        self.assertAlmostEqual(period["coverage"], 6 / 7)
        self.assertEqual(period["frozen"], 0)

    def test_export_updates_manifest_without_duplicates(self) -> None:
        day = date(2026, 7, 13)
        period_id = self.settle(day)
        frontend = self.root / "frontend"
        (frontend / "data").mkdir(parents=True)
        (frontend / "data" / "chart-manifest.json").write_text(
            json.dumps({
                "schemaVersion": "1.0",
                "defaultView": {"entityType": "songs", "periodType": "daily", "periodKey": day.isoformat()},
                "views": [],
            }),
            encoding="utf-8",
        )
        destination = export_period(self.connection, period_id, frontend)
        export_period(self.connection, period_id, frontend)
        manifest = json.loads((frontend / "data" / "chart-manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(destination.exists())
        self.assertEqual(len(manifest["views"][0]["snapshots"]), 1)
        exported = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(len(exported["entries"]), 100)
        self.assertEqual(sum(entry["points"]["netease"] for entry in exported["entries"]), 10_000)


if __name__ == "__main__":
    unittest.main()
