from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.netease import archive_raw_snapshot
from echorank.workflow import load_config, update_charts


def raw_response() -> dict:
    return {
        "code": 200,
        "weekData": [
            {
                "playCount": 0,
                "score": 201 - rank,
                "song": {
                    "id": 1000 + rank,
                    "name": f"Workflow Song {rank:03d}",
                    "ar": [{"id": 2000 + rank, "name": f"Artist {rank:03d}"}],
                    "al": {
                        "id": 3000 + rank,
                        "name": f"Album {rank:03d}",
                        "picUrl": None,
                    },
                },
            }
            for rank in range(1, 101)
        ],
    }


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.json"
        self.database = self.root / "echorank.db"
        self.raw_root = self.root / "raw"
        self.frontend = self.root / "frontend"
        (self.frontend / "data").mkdir(parents=True)
        (self.frontend / "data" / "chart-manifest.json").write_text(
            json.dumps({
                "schemaVersion": "1.0",
                "defaultView": {
                    "entityType": "songs",
                    "periodType": "daily",
                    "periodKey": "2026-07-17",
                },
                "views": [],
            }),
            encoding="utf-8",
        )
        self.config.write_text(
            json.dumps({"neteaseUid": "123456"}),
            encoding="utf-8",
        )
        self.now = datetime(2026, 7, 18, 14, 5, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_first_update_and_repeat_are_idempotent(self) -> None:
        calls = 0

        def fetcher(request, timeout):
            nonlocal calls
            calls += 1
            return raw_response()

        first = update_charts(
            self.config,
            self.database,
            self.raw_root,
            self.frontend,
            self.now,
            fetcher=fetcher,
        )
        second = update_charts(
            self.config,
            self.database,
            self.raw_root,
            self.frontend,
            self.now,
            fetcher=lambda request, timeout: self.fail("repeat fetched network"),
        )
        self.assertTrue(first.collected)
        self.assertFalse(second.collected)
        self.assertEqual(calls, 1)
        self.assertEqual(first.entry_count, 100)
        self.assertTrue(first.daily_path.exists())
        self.assertTrue(first.weekly_path.exists())
        for entity_type in ("songs", "albums", "artists"):
            for period_type in ("daily", "weekly", "monthly", "yearly"):
                self.assertTrue(
                    (self.frontend / "data" / "trends" / period_type / f"{entity_type}.json").exists()
                )

        manifest = json.loads(
            (self.frontend / "data" / "chart-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["defaultView"]["periodKey"], "2026-07-18")
        self.assertEqual(manifest["defaultView"]["periodType"], "daily")

    def test_before_22_does_not_collect_or_modify_charts(self) -> None:
        official_run = datetime(2026, 7, 17, 14, 5, tzinfo=timezone.utc)
        official = update_charts(
            self.config,
            self.database,
            self.raw_root,
            self.frontend,
            official_run,
            fetcher=lambda request, timeout: raw_response(),
        )
        daily_before = official.daily_path.read_bytes()
        before_cutoff = datetime(2026, 7, 17, 16, 10, tzinfo=timezone.utc)
        result = update_charts(
            self.config,
            self.database,
            self.raw_root,
            self.frontend,
            before_cutoff,
            fetcher=lambda request, timeout: self.fail("early update fetched network"),
        )
        self.assertTrue(result.skipped)
        self.assertFalse(result.collected)
        self.assertEqual(result.period_key, "2026-07-17")
        self.assertEqual(result.daily_path.read_bytes(), daily_before)
        self.assertFalse(
            (self.raw_root / "2026" / "07" / "2026-07-18.json").exists()
        )
        manifest = json.loads(
            (self.frontend / "data" / "chart-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["defaultView"]["periodKey"], "2026-07-17")

    def test_archived_snapshot_recovers_without_network(self) -> None:
        archive_raw_snapshot(raw_response(), "2026-07-18", self.raw_root)
        result = update_charts(
            self.config,
            self.database,
            self.raw_root,
            self.frontend,
            self.now,
            fetcher=lambda request, timeout: self.fail("archive recovery fetched network"),
        )
        self.assertFalse(result.collected)
        self.assertEqual(result.entry_count, 100)

    def test_direct_uid_does_not_require_local_config(self) -> None:
        result = update_charts(
            self.root / "missing.json",
            self.database,
            self.raw_root,
            self.frontend,
            self.now,
            fetcher=lambda request, timeout: raw_response(),
            netease_uid="123456",
        )
        self.assertEqual(result.entry_count, 100)
        self.assertTrue(self.database.exists())

    def test_invalid_direct_uid_creates_no_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "UID 必须是数字字符串"):
            update_charts(
                self.root / "missing.json",
                self.database,
                self.raw_root,
                self.frontend,
                self.now,
                netease_uid="12x",
            )
        self.assertFalse(self.database.exists())

    def test_config_errors_are_clear_and_create_no_database(self) -> None:
        missing = self.root / "missing.json"
        with self.assertRaisesRegex(ValueError, "缺少本机配置"):
            update_charts(
                missing,
                self.database,
                self.raw_root,
                self.frontend,
                self.now,
            )
        self.assertFalse(self.database.exists())

        self.config.write_text(json.dumps({"neteaseUid": 123456}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "数字字符串"):
            load_config(self.config)


if __name__ == "__main__":
    unittest.main()
