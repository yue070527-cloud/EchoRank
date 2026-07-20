from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.supabase_upload import (
    SupabaseUploader,
    load_config,
    load_snapshots,
    upload_manifest,
)


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def snapshot() -> dict:
    return {
        "schemaVersion": "1.0",
        "chart": {
            "entityType": "songs",
            "periodType": "daily",
        },
        "period": {
            "key": "2026-07-19",
            "scheduledAt": "2026-07-19T22:00:00+08:00",
            "status": "settled",
        },
        "collection": {
            "collectedAt": "2026-07-19T22:05:00+08:00",
            "coverage": 1.0,
            "sourceSnapshot": "data/raw/example.json",
        },
        "entries": [{
            "entityId": "song-1",
            "entity": {
                "title": "Song",
                "subtitle": "Artist",
            },
            "rank": {
                "current": 1,
                "previous": None,
                "movement": {"type": "new", "value": 0},
            },
            "points": {
                "netease": 90.0,
                "physical": 10.0,
                "bilibili": 0.0,
                "other": 0.0,
                "legacyBonus": 0.0,
                "manualAdjustment": 0.0,
                "total": 100.0,
            },
            "record": {
                "peak": 1,
                "periods": 1,
                "championships": 1,
            },
        }],
    }


class SupabaseUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frontend = self.root / "frontend"
        chart = self.frontend / "data" / "charts" / "daily" / "songs" / "2026-07-19.json"
        chart.parent.mkdir(parents=True)
        chart.write_text(json.dumps(snapshot()), encoding="utf-8")
        (self.frontend / "data" / "chart-manifest.json").write_text(
            json.dumps({
                "schemaVersion": "1.0",
                "views": [{
                    "entityType": "songs",
                    "periodType": "daily",
                    "snapshots": [{
                        "periodKey": "2026-07-19",
                        "path": "./data/charts/daily/songs/2026-07-19.json",
                    }],
                    "historyPath": "./data/trends/daily/songs.json",
                }],
            }),
            encoding="utf-8",
        )
        self.user_id = str(uuid4())
        self.env = self.root / ".env"
        self.env.write_text(
            "\n".join([
                "SUPABASE_URL=https://example.supabase.co",
                "SUPABASE_SECRET_KEY=local-secret",
                f"SUPABASE_USER_ID={self.user_id}",
            ]),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_upload_maps_snapshot_and_replaces_entries(self) -> None:
        requests = []
        period_id = str(uuid4())

        def requester(request, timeout):
            requests.append(request)
            if request.method == "POST" and "/chart_periods?" in request.full_url:
                return Response(json.dumps([{"id": period_id}]).encode())
            return Response(b"")

        result = upload_manifest(self.frontend, self.env, requester=requester)
        self.assertEqual((result.periods, result.entries), (1, 1))
        self.assertEqual([request.method for request in requests], ["POST", "DELETE", "POST"])

        period_request, delete_request, entries_request = requests
        period_body = json.loads(period_request.data)
        self.assertEqual(period_body["user_id"], self.user_id)
        self.assertEqual(period_body["period_key"], "2026-07-19")
        self.assertTrue(period_body["frozen"])
        self.assertEqual(period_request.get_header("Apikey"), "local-secret")
        self.assertIn("resolution=merge-duplicates", period_request.get_header("Prefer"))
        self.assertEqual(
            parse_qs(urlparse(period_request.full_url).query)["on_conflict"][0],
            "user_id,entity_type,period_type,period_key",
        )
        delete_query = parse_qs(urlparse(delete_request.full_url).query)
        self.assertEqual(delete_query["period_id"], [f"eq.{period_id}"])
        self.assertEqual(delete_query["user_id"], [f"eq.{self.user_id}"])

        entry = json.loads(entries_request.data)[0]
        self.assertEqual(entry["period_id"], period_id)
        self.assertEqual(entry["entity"], {"title": "Song", "subtitle": "Artist"})
        self.assertEqual(entry["championships"], 1)
        self.assertNotIn("id", entry)

    def test_invalid_snapshot_stops_before_network(self) -> None:
        chart = self.frontend / "data" / "charts" / "daily" / "songs" / "2026-07-19.json"
        payload = snapshot()
        payload["entries"][0]["points"]["total"] = 99
        chart.write_text(json.dumps(payload), encoding="utf-8")
        calls = []
        with self.assertRaisesRegex(ValueError, "点数分项之和"):
            upload_manifest(
                self.frontend,
                self.env,
                requester=lambda request, timeout: calls.append(request),
            )
        self.assertEqual(calls, [])

    def test_manifest_rejects_path_outside_frontend(self) -> None:
        manifest = self.frontend / "data" / "chart-manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["views"][0]["snapshots"][0]["path"] = "../private.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "越出 frontend"):
            load_snapshots(self.frontend)

    def test_environment_overrides_file_and_validates_uuid(self) -> None:
        override = str(uuid4())
        with patch.dict(os.environ, {"SUPABASE_USER_ID": override}):
            self.assertEqual(load_config(self.env).user_id, override)
        self.env.write_text(
            "SUPABASE_URL=https://example.supabase.co\n"
            "SUPABASE_SECRET_KEY=secret\n"
            "SUPABASE_USER_ID=invalid\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "有效 UUID"):
            load_config(self.env)

    def test_fetch_netease_uid_uses_fixed_user_filter(self) -> None:
        requests = []

        def requester(request, timeout):
            requests.append(request)
            return Response(json.dumps([{"netease_uid": "123456"}]).encode())

        uploader = SupabaseUploader(load_config(self.env), requester=requester)
        self.assertEqual(uploader.fetch_netease_uid(), "123456")
        query = parse_qs(urlparse(requests[0].full_url).query)
        self.assertEqual(query, {
            "select": ["netease_uid"],
            "user_id": [f"eq.{self.user_id}"],
        })

    def test_fetch_netease_uid_rejects_missing_or_invalid_values(self) -> None:
        config = load_config(self.env)
        cases = [
            ([], "未找到"),
            ([{"netease_uid": None}], "尚未绑定"),
            ([{"netease_uid": "12x"}], "数字字符串"),
            ([{"netease_uid": "1"}, {"netease_uid": "2"}], "不是唯一"),
        ]
        for payload, message in cases:
            with self.subTest(payload=payload):
                uploader = SupabaseUploader(
                    config,
                    requester=lambda request, timeout, payload=payload: Response(
                        json.dumps(payload).encode()
                    ),
                )
                with self.assertRaisesRegex(ValueError, message) as context:
                    uploader.fetch_netease_uid()
                self.assertNotIn("local-secret", str(context.exception))

    def test_environment_only_config_does_not_require_env_file(self) -> None:
        values = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SECRET_KEY": "environment-secret",
            "SUPABASE_USER_ID": self.user_id,
        }
        with patch.dict(os.environ, values, clear=True):
            config = load_config(None)
        self.assertEqual(config.user_id, self.user_id)
        self.assertEqual(config.secret_key, "environment-secret")

    def test_insert_failure_is_actionable_and_hides_secret(self) -> None:
        period_id = str(uuid4())

        def requester(request, timeout):
            if request.method == "POST" and "/chart_periods?" in request.full_url:
                return Response(json.dumps([{"id": period_id}]).encode())
            if request.method == "POST" and request.full_url.endswith("/chart_entries"):
                raise HTTPError(request.full_url, 500, "failure", {}, BytesIO(b"failed"))
            return Response(b"")

        with self.assertRaisesRegex(ValueError, "重新运行 upload-supabase") as context:
            upload_manifest(self.frontend, self.env, requester=requester)
        self.assertNotIn("local-secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
