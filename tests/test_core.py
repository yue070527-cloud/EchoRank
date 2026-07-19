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
from echorank.export import export_period, export_trend_history, snapshot_for_period
from echorank.ranking import PointTotal, rank_totals
from echorank.netease import fetch_album_tracks
from echorank.scoring import (
    NeteaseInput,
    allocate_netease_points,
    bilibili_points_for_views,
    physical_purchase_weight,
    physical_reference_rank,
)
from echorank.settlement import (
    create_album,
    create_artist,
    create_song,
    import_bilibili_view_event,
    import_catalog_song,
    import_ledger_entries,
    import_manual_adjustment,
    import_netease_snapshot,
    import_physical_event,
    settle_daily,
    settle_entity_daily,
    settle_period,
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
            "relativeScore": 201 - rank,
        })
    key = day.isoformat()
    return {
        "collectionVersion": "test-relative-score-v1",
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

    def test_equal_weekly_plays_receive_equal_points(self) -> None:
        entries = [NeteaseInput(f"song-{rank:03d}", rank, 201 - rank) for rank in range(1, 101)]
        entries[9] = NeteaseInput("song-010", 10, 190)
        entries[10] = NeteaseInput("song-011", 11, 190)
        first = allocate_netease_points(entries)
        second = allocate_netease_points(list(reversed(entries)))
        self.assertEqual(first["song-010"], first["song-011"])
        self.assertEqual(first, second)
        self.assertEqual(sum(first.values()), 10_000)

    def test_requires_complete_top_100(self) -> None:
        with self.assertRaisesRegex(ValueError, "Top 100"):
            allocate_netease_points([NeteaseInput("song-001", 1, 10)])

    def test_physical_purchase_weights_are_per_copy(self) -> None:
        self.assertEqual(float(physical_purchase_weight(0, 0, 2)), 1.30)
        self.assertEqual(float(physical_purchase_weight(2, 0, 2)), 0.25)
        self.assertEqual(float(physical_purchase_weight(0, 1, 2)), 0.78)

    def test_album_tracks_are_normalized(self) -> None:
        def fetcher(request, timeout):
            return {
                "code": 200,
                "album": {"id": 300, "name": "Album", "picUrl": "https://example.test/cover.jpg"},
                "songs": [{
                    "id": 100,
                    "name": "Track",
                    "ar": [{"id": 200, "name": "Artist"}],
                }],
            }

        tracks = fetch_album_tracks("netease-album-300", fetcher=fetcher)
        self.assertEqual(tracks[0]["id"], "netease-song-100")
        self.assertEqual(tracks[0]["album"]["id"], "netease-album-300")

    def test_tie_breaks_by_previous_rank_then_id(self) -> None:
        totals = [
            PointTotal("b", netease=80, physical=20),
            PointTotal("a", netease=70, physical=30),
            PointTotal("c", netease=60, physical=40),
        ]
        ranked = rank_totals(totals, {"b": 1, "a": 2})
        self.assertEqual([item.entity_id for item in ranked], ["b", "a", "c"])
        self.assertEqual(
            [item.entity_id for item in rank_totals(totals, {})],
            ["a", "b", "c"],
        )


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

    def test_championship_count_accumulates_in_snapshots(self) -> None:
        first = self.settle(date(2026, 7, 13))
        second = self.settle(date(2026, 7, 14))
        first_snapshot = snapshot_for_period(self.connection, first)
        second_snapshot = snapshot_for_period(self.connection, second)
        self.assertEqual(first_snapshot["entries"][0]["record"]["championships"], 1)
        self.assertEqual(second_snapshot["entries"][0]["record"]["championships"], 2)
        self.assertEqual(second_snapshot["entries"][1]["record"]["championships"], 0)

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
        replacement["entries"][0]["relativeScore"] += 1
        with self.assertRaisesRegex(ValueError, "已结算"):
            import_netease_snapshot(self.connection, replacement)

    def test_daily_settlement_is_idempotent_and_preserves_total(self) -> None:
        period_id = self.settle(date(2026, 7, 13))
        self.assertEqual(settle_daily(self.connection, "2026-07-13"), period_id)
        netease_total = self.connection.execute(
            "SELECT SUM(points) FROM point_ledger WHERE period_id=? AND source='netease'", (period_id,)
        ).fetchone()[0]
        self.assertAlmostEqual(netease_total, 10_000)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM chart_entries WHERE period_id=?", (period_id,)).fetchone()[0], 100)

    def test_bilibili_view_scoring_boundaries(self) -> None:
        expected = {0: 0, 1: 20, 2: 35, 3: 55, 5: 80, 8: 110, 13: 145, 21: 180, 36: 215, 61: 250}
        self.assertEqual({count: bilibili_points_for_views(count) for count in expected}, expected)
        self.assertEqual(physical_reference_rank(1), 10)
        self.assertEqual(physical_reference_rank(2), 45)
        self.assertEqual(physical_reference_rank(7), 64)
        self.assertEqual(physical_reference_rank(14), 100)
        self.assertEqual(physical_reference_rank(28), 100)

    def test_catalog_bilibili_and_manual_adjustment_are_separate(self) -> None:
        song = {
            "id": "netease-song-9001",
            "title": "Imported Song",
            "artists": [{"id": "netease-artist-9002", "name": "Imported Artist"}],
            "album": {"id": "netease-album-9003", "title": "Imported Album"},
            "coverUrl": None,
            "coverColor": "#123456",
        }
        import_catalog_song(self.connection, song)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM point_ledger").fetchone()[0], 0)
        normalized, imported, points = import_bilibili_view_event(
            self.connection, song, "2026-07-13", 8, "bilibili:event:test", "BV1test", "Live"
        )
        self.assertEqual((normalized["id"], imported, points), (song["id"], 1, 110))
        self.assertEqual(
            import_bilibili_view_event(
                self.connection, song, "2026-07-13", 8, "bilibili:event:test", "BV1test", "Live"
            )[1],
            0,
        )
        import_netease_snapshot(self.connection, snapshot_payload(date(2026, 7, 13)))
        daily_id = settle_daily(self.connection, "2026-07-13")
        entry = self.connection.execute(
            "SELECT bilibili_points FROM chart_entries WHERE period_id=? AND entity_id=?",
            (daily_id, song["id"]),
        ).fetchone()
        self.assertEqual(entry["bilibili_points"], 110)
        with self.assertRaisesRegex(ValueError, "已结算"):
            import_bilibili_view_event(
                self.connection, song, "2026-07-13", 1, "bilibili:late:test"
            )

        next_day = "2026-07-14"
        normalized, imported = import_manual_adjustment(
            self.connection, song, next_day, -10, "修正误录", "manual:test"
        )
        self.assertEqual((normalized["id"], imported), (song["id"], 1))
        row = self.connection.execute(
            "SELECT source, points FROM point_ledger WHERE external_key='manual:test'"
        ).fetchone()
        self.assertEqual((row["source"], row["points"]), ("manualAdjustment", -10))
        adjustment = self.connection.execute(
            "SELECT reason FROM manual_adjustment_events WHERE external_key='manual:test'"
        ).fetchone()
        self.assertEqual(adjustment["reason"], "修正误录")
        self.assertEqual(
            import_manual_adjustment(self.connection, song, next_day, -10, "修正误录", "manual:test")[1],
            0,
        )
        with self.assertRaisesRegex(ValueError, "幂等键冲突"):
            import_manual_adjustment(self.connection, song, next_day, -9, "修正误录", "manual:test")

    def test_physical_event_releases_and_aggregates_once(self) -> None:
        day = date(2026, 7, 13)
        payload = snapshot_payload(day)
        first = {
            "id": "netease-song-9101",
            "title": "Physical Track One",
            "artists": [{"id": "netease-artist-9103", "name": "Physical Artist"}],
            "album": {"id": "netease-album-9104", "title": "Physical Album"},
            "coverUrl": None,
            "coverColor": "#123456",
        }
        second = {
            "id": "netease-song-9102",
            "title": "Physical Track Two",
            "artists": [
                {"id": "netease-artist-9103", "name": "Physical Artist"},
                {"id": "netease-artist-9105", "name": "Featured Artist"},
            ],
            "album": dict(first["album"]),
            "coverUrl": None,
            "coverColor": "#123456",
        }
        import_netease_snapshot(self.connection, payload)
        event = import_physical_event(
            self.connection,
            [first, second],
            day.isoformat(),
            "限定版 A",
            "limited_cd",
            1,
            [first["id"], second["id"]],
            "physical:event:test",
        )
        self.assertEqual(event["scoring_version"], "physical-event-v2")
        daily_id = settle_daily(self.connection, day.isoformat())
        release = self.connection.execute(
            "SELECT reference_rank, points, scoring_version, rank_schedule_version "
            "FROM physical_releases WHERE event_id=? AND period_id=?",
            (event["id"], daily_id),
        ).fetchone()
        self.assertEqual(release["reference_rank"], 10)
        self.assertEqual(release["scoring_version"], "physical-v4")
        self.assertEqual(release["rank_schedule_version"], "physical-rank-schedule-v2")
        ledger_versions = self.connection.execute(
            "SELECT DISTINCT scoring_version FROM point_ledger WHERE period_id=? AND source='physical'",
            (daily_id,),
        ).fetchall()
        self.assertEqual([row["scoring_version"] for row in ledger_versions], ["physical-v4"])
        song_points = self.connection.execute(
            "SELECT SUM(physical_points) FROM chart_entries WHERE period_id=? AND entity_id IN (?, ?)",
            (daily_id, first["id"], second["id"]),
        ).fetchone()[0]
        self.assertAlmostEqual(song_points, release["points"] * 2)
        album_period = settle_entity_daily(self.connection, "albums", day.isoformat())
        album_points = self.connection.execute(
            "SELECT physical_points FROM chart_entries WHERE period_id=? AND entity_id=?",
            (album_period, first["album"]["id"]),
        ).fetchone()[0]
        self.assertAlmostEqual(album_points, release["points"])
        artist_period = settle_entity_daily(self.connection, "artists", day.isoformat())
        artist_points = {
            row["entity_id"]: row["physical_points"]
            for row in self.connection.execute(
                "SELECT entity_id, physical_points FROM chart_entries WHERE period_id=?",
                (artist_period,),
            )
        }
        self.assertAlmostEqual(artist_points[first["artists"][0]["id"]], release["points"] * 0.75)
        self.assertAlmostEqual(artist_points[second["artists"][1]["id"]], release["points"] * 0.25)
        self.assertAlmostEqual(sum(artist_points.values()), release["points"])

    def test_manual_catalog_and_bilibili_entry_before_snapshot(self) -> None:
        day = date(2026, 7, 13)
        artist = create_artist(self.connection, "Manual Artist")
        album = create_album(self.connection, "Manual Album")
        song = create_song(self.connection, "Manual Song", album["id"], [artist["id"]])
        payload = {
            "periodKey": day.isoformat(),
            "entries": [{
                "externalKey": "bilibili:manual:test",
                "source": "bilibili",
                "songId": song["id"],
                "points": 750,
                "scoringVersion": "bilibili-manual-v1",
            }],
        }
        self.assertEqual(import_ledger_entries(self.connection, payload), 1)
        self.assertEqual(import_ledger_entries(self.connection, payload), 0)
        import_netease_snapshot(self.connection, snapshot_payload(day))
        daily_id = settle_daily(self.connection, day.isoformat())
        entry = self.connection.execute(
            "SELECT bilibili_points FROM chart_entries WHERE period_id=? AND entity_id=?",
            (daily_id, song["id"]),
        ).fetchone()
        self.assertEqual(entry["bilibili_points"], 750)
        album_id = settle_entity_daily(self.connection, "albums", day.isoformat())
        artist_id = settle_entity_daily(self.connection, "artists", day.isoformat())
        self.assertEqual(
            self.connection.execute(
                "SELECT bilibili_points FROM chart_entries WHERE period_id=? AND entity_id=?",
                (album_id, album["id"]),
            ).fetchone()[0],
            750,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT bilibili_points FROM chart_entries WHERE period_id=? AND entity_id=?",
                (artist_id, artist["id"]),
            ).fetchone()[0],
            750,
        )
        with self.assertRaisesRegex(ValueError, "已结算"):
            import_ledger_entries(self.connection, {
                **payload,
                "entries": [{**payload["entries"][0], "externalKey": "bilibili:late:test"}],
            })

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
            "SELECT entity_id, physical_points FROM chart_entries WHERE period_id=? AND rank=1", (period_id,)
        ).fetchone()
        self.assertEqual(first["entity_id"], "song-100")
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
            "SELECT movement_type, periods FROM chart_entries WHERE period_id=? AND entity_id='song-001'", (first_id,)
        ).fetchone()
        newcomer = self.connection.execute(
            "SELECT movement_type FROM chart_entries WHERE period_id=? AND entity_id='song-101'", (second_id,)
        ).fetchone()
        returned = self.connection.execute(
            "SELECT movement_type, previous_rank, periods FROM chart_entries WHERE period_id=? AND entity_id='song-001'", (third_id,)
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
        self.assertAlmostEqual(live["collection"]["coverage"], 3 / 7)
        self.assertAlmostEqual(sum(item["points"]["netease"] for item in live["entries"]), 30_000)

        for offset in range(3, 7):
            self.settle(monday + timedelta(days=offset))
        frozen_id = settle_weekly(self.connection, (monday + timedelta(days=6)).isoformat())
        frozen = snapshot_for_period(self.connection, frozen_id)
        self.assertEqual(frozen_id, live_id)
        self.assertEqual(frozen["period"]["status"], "settled")
        self.assertAlmostEqual(sum(item["points"]["netease"] for item in frozen["entries"]), 70_000)
        period = self.connection.execute("SELECT frozen FROM chart_periods WHERE id=?", (frozen_id,)).fetchone()
        self.assertEqual(period["frozen"], 1)

    def test_long_period_netease_points_sum_daily_pools(self) -> None:
        first_day = date(2026, 7, 6)
        daily_ids = [self.settle(first_day + timedelta(days=offset)) for offset in range(7)]

        low_day = first_day + timedelta(days=7)
        low_payload = snapshot_payload(low_day)
        for entry in low_payload["entries"]:
            entry["relativeScore"] = 1
        low_id = import_netease_snapshot(self.connection, low_payload)
        import_ledger_entries(self.connection, {
            "periodKey": low_day.isoformat(),
            "entries": [{
                "externalKey": "physical:activity-test",
                "source": "physical",
                "songId": "song-001",
                "points": 500,
                "scoringVersion": "physical-test-v1",
            }],
        })
        settle_daily(self.connection, low_day.isoformat())

        high_day = low_day + timedelta(days=1)
        high_payload = snapshot_payload(high_day)
        for entry in high_payload["entries"]:
            entry["relativeScore"] = 10_000
        high_id = import_netease_snapshot(self.connection, high_payload)
        settle_daily(self.connection, high_day.isoformat())

        for period_id in [*daily_ids, low_id, high_id]:
            daily_total = self.connection.execute(
                "SELECT SUM(points) FROM point_ledger WHERE period_id=? AND source='netease'",
                (period_id,),
            ).fetchone()[0]
            self.assertAlmostEqual(daily_total, 10_000)

        weekly_id = settle_weekly(self.connection, high_day.isoformat())
        totals = self.connection.execute(
            "SELECT SUM(netease_points) AS netease, SUM(physical_points) AS physical "
            "FROM chart_entries WHERE period_id=?",
            (weekly_id,),
        ).fetchone()
        self.assertAlmostEqual(totals["netease"], 20_000)
        self.assertEqual(totals["physical"], 500)

    def test_incomplete_sunday_week_freezes_partial(self) -> None:
        monday = date(2026, 7, 13)
        for offset in range(6):
            self.settle(monday + timedelta(days=offset))
        period_id = settle_weekly(self.connection, (monday + timedelta(days=6)).isoformat())
        period = self.connection.execute(
            "SELECT status, coverage, frozen FROM chart_periods WHERE id=?", (period_id,)
        ).fetchone()
        self.assertEqual(period["status"], "partial")
        self.assertAlmostEqual(period["coverage"], 6 / 7)
        self.assertEqual(period["frozen"], 1)

    def test_yearly_only_legacy_bonus_isolated_from_shorter_periods(self) -> None:
        day = date(2026, 7, 13)
        import_netease_snapshot(self.connection, snapshot_payload(day))
        period_id = self.connection.execute(
            "SELECT id FROM chart_periods WHERE entity_type='songs' AND period_type='daily' AND period_key=?",
            (day.isoformat(),),
        ).fetchone()["id"]
        with self.connection:
            self.connection.execute(
                "INSERT INTO point_ledger(period_id, song_id, source, points, scoring_version, external_key, created_at) "
                "VALUES (?, 'song-001', 'legacyBonus', 20000, 'netease-alltime-yearly-v1', "
                "'netease-alltime:test:2026:1', '2026-07-13T14:00:00+00:00')",
                (period_id,),
            )
        daily_id = settle_daily(self.connection, day.isoformat())
        self.assertEqual(
            self.connection.execute(
                "SELECT legacy_bonus FROM chart_entries WHERE period_id=? AND entity_id='song-001'",
                (daily_id,),
            ).fetchone()["legacy_bonus"],
            0,
        )
        settle_entity_daily(self.connection, "albums", day.isoformat())
        settle_entity_daily(self.connection, "artists", day.isoformat())

        for period_type in ("weekly", "monthly"):
            for entity_type in ("songs", "albums", "artists"):
                aggregate_id = settle_period(
                    self.connection, entity_type, period_type, day.isoformat()
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COALESCE(SUM(legacy_bonus), 0) FROM chart_entries WHERE period_id=?",
                        (aggregate_id,),
                    ).fetchone()[0],
                    0,
                )

        yearly_ids = {
            entity_type: settle_period(
                self.connection, entity_type, "yearly", day.isoformat()
            )
            for entity_type in ("songs", "albums", "artists")
        }
        for yearly_id in yearly_ids.values():
            self.assertAlmostEqual(
                self.connection.execute(
                    "SELECT SUM(legacy_bonus) FROM chart_entries WHERE period_id=?",
                    (yearly_id,),
                ).fetchone()[0],
                20_000,
            )
        song = self.connection.execute(
            "SELECT entity_id, legacy_bonus FROM chart_entries WHERE period_id=? AND rank=1",
            (yearly_ids["songs"],),
        ).fetchone()
        album = self.connection.execute(
            "SELECT entity_id, legacy_bonus FROM chart_entries WHERE period_id=? AND rank=1",
            (yearly_ids["albums"],),
        ).fetchone()
        artist = self.connection.execute(
            "SELECT entity_id, legacy_bonus FROM chart_entries WHERE period_id=? AND rank=1",
            (yearly_ids["artists"],),
        ).fetchone()
        self.assertEqual((song["entity_id"], song["legacy_bonus"]), ("song-001", 20_000))
        self.assertEqual((album["entity_id"], album["legacy_bonus"]), ("album-001", 20_000))
        self.assertEqual((artist["entity_id"], artist["legacy_bonus"]), ("artist-001", 20_000))
        self.assertEqual(
            settle_period(self.connection, "songs", "yearly", day.isoformat()),
            yearly_ids["songs"],
        )

    def test_trend_export_tracks_periods_and_appearances(self) -> None:
        first_day = date(2026, 7, 13)
        self.settle(first_day, 1)
        self.settle(first_day + timedelta(days=1), 2)
        self.settle(first_day + timedelta(days=2), 1)
        frontend = self.root / "frontend"
        (frontend / "data").mkdir(parents=True)
        (frontend / "data" / "chart-manifest.json").write_text(
            json.dumps({
                "schemaVersion": "1.0",
                "defaultView": {
                    "entityType": "songs",
                    "periodType": "daily",
                    "periodKey": first_day.isoformat(),
                },
                "views": [{
                    "entityType": "songs",
                    "periodType": "daily",
                    "snapshots": [],
                }],
            }),
            encoding="utf-8",
        )
        destination = export_trend_history(self.connection, "songs", "daily", frontend)
        first = destination.read_bytes()
        export_trend_history(self.connection, "songs", "daily", frontend)
        self.assertEqual(destination.read_bytes(), first)
        history = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            [period["key"] for period in history["periods"]],
            ["2026-07-13", "2026-07-14", "2026-07-15"],
        )
        self.assertTrue(all(period["frozen"] for period in history["periods"]))
        self.assertEqual(
            [point["periodKey"] for point in history["series"]["song-001"]],
            ["2026-07-13", "2026-07-15"],
        )
        manifest = json.loads((frontend / "data" / "chart-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["views"][0]["historyPath"], "./data/trends/daily/songs.json")

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
        self.assertAlmostEqual(sum(entry["points"]["netease"] for entry in exported["entries"]), 10_000)


if __name__ == "__main__":
    unittest.main()
