from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.database import connect, initialize
from echorank.netease import (
    archive_raw_snapshot,
    collect_weekly_snapshot,
    fetch_weekly_ranking,
    normalize_weekly_ranking,
)
from echorank.settlement import import_netease_snapshot, settle_daily


def raw_response(count: int = 100) -> dict:
    return {
        "code": 200,
        "weekData": [
            {
                "playCount": 201 - rank,
                "score": 100 - rank,
                "song": {
                    "id": 10_000 + rank,
                    "name": f"Public Song {rank:03d}",
                    "ar": [{"id": 20_000 + rank, "name": f"Artist {rank:03d}"}],
                    "al": {
                        "id": 30_000 + rank,
                        "name": f"Album {rank:03d}",
                        "picUrl": f"https://example.test/{rank}.jpg",
                    },
                },
            }
            for rank in range(1, count + 1)
        ],
    }


class NeteaseAdapterTests(unittest.TestCase):
    def test_fetch_builds_public_weekly_request(self) -> None:
        observed = {}

        def fetcher(request, timeout):
            observed["url"] = request.full_url
            observed["referer"] = request.get_header("Referer")
            observed["timeout"] = timeout
            return raw_response()

        payload = fetch_weekly_ranking("123456", timeout=7, fetcher=fetcher)
        self.assertEqual(len(payload["weekData"]), 100)
        self.assertEqual(
            observed["url"],
            "https://music.163.com/api/v1/play/record?uid=123456&type=1",
        )
        self.assertEqual(observed["referer"], "https://music.163.com/")
        self.assertEqual(observed["timeout"], 7)

    def test_fetch_rejects_invalid_uid_and_business_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "UID"):
            fetch_weekly_ranking("not-a-number", fetcher=lambda request, timeout: raw_response())
        with self.assertRaisesRegex(ValueError, "响应代码"):
            fetch_weekly_ranking(
                "123456",
                fetcher=lambda request, timeout: {"code": 301},
            )

    def test_fetch_wraps_network_error(self) -> None:
        def fetcher(request, timeout):
            raise URLError("offline")

        with self.assertRaisesRegex(ValueError, "请求失败"):
            fetch_weekly_ranking("123456", fetcher=fetcher)

    def test_normalizes_response_in_array_rank_order(self) -> None:
        payload = normalize_weekly_ranking(
            raw_response(),
            "2026-07-17",
            "data/raw/netease/2026/07/2026-07-17.json",
            datetime(2026, 7, 17, 14, 5, tzinfo=timezone.utc),
        )
        first = payload["entries"][0]
        self.assertEqual(payload["scheduledAt"], "2026-07-17T22:00:00+08:00")
        self.assertEqual(payload["collectedAt"], "2026-07-17T22:05:00+08:00")
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["weeklyPlays"], 200)
        self.assertEqual(first["id"], "netease-song-10001")
        self.assertEqual(first["artists"][0]["id"], "netease-artist-20001")
        self.assertEqual(first["album"]["id"], "netease-album-30001")
        self.assertNotIn("score", first)

    def test_requires_complete_top_100_before_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "完整 Top 100"):
                collect_weekly_snapshot(
                    "123456",
                    "2026-07-17",
                    root,
                    fetcher=lambda request, timeout: raw_response(99),
                )
            self.assertFalse(any(root.rglob("*.json")))

    def test_archive_is_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = archive_raw_snapshot(raw_response(), "2026-07-17", root)
            second = archive_raw_snapshot(raw_response(), "2026-07-17", root)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["code"], 200)

            changed = raw_response()
            changed["weekData"][0]["playCount"] += 1
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                archive_raw_snapshot(changed, "2026-07-17", root)

    def test_collection_integrates_with_daily_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload, archive = collect_weekly_snapshot(
                "123456",
                "2026-07-17",
                root / "raw",
                fetcher=lambda request, timeout: raw_response(),
                collected_at=datetime(2026, 7, 17, 22, 5, tzinfo=timezone.utc),
            )
            connection = connect(root / "echorank.db")
            initialize(connection)
            try:
                period_id = import_netease_snapshot(connection, payload)
                settle_daily(connection, "2026-07-17")
                period = connection.execute(
                    "SELECT source_snapshot FROM chart_periods WHERE id=?",
                    (period_id,),
                ).fetchone()
                total = connection.execute(
                    "SELECT SUM(points) FROM point_ledger WHERE period_id=? AND source='netease'",
                    (period_id,),
                ).fetchone()[0]
                self.assertTrue(archive.exists())
                self.assertEqual(period["source_snapshot"], archive.as_posix())
                self.assertEqual(total, 10_000)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
