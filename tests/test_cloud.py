from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.cloud import cloud_update
from echorank.supabase_upload import SupabaseConfig, UploadResult
from echorank.workflow import UpdateResult


class CloudUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.frontend = self.root / "frontend"
        self.base = SupabaseConfig("https://example.supabase.co", "secret")
        self.users = [(str(uuid4()), "123456"), (str(uuid4()), "654321")]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("echorank.cloud.load_snapshots", return_value=["snapshot"])
    @patch("echorank.cloud.update_charts")
    @patch("echorank.cloud.SupabaseStateStore")
    @patch("echorank.cloud.SupabaseUploader")
    @patch("echorank.cloud.load_config")
    def test_updates_all_users_with_isolated_paths(
        self, load_config, uploader_type, store_type, update, load_snapshots
    ) -> None:
        load_config.return_value = self.base
        uploader_type.return_value.fetch_bound_users.return_value = self.users
        uploader_type.return_value.upload.return_value = UploadResult(2, 200)
        store_type.return_value.restore.return_value = True
        update.return_value = UpdateResult(
            "2026-07-20", "2026-W30", "2026-07", "2026", {}, 100, True
        )

        result = cloud_update(self.state, self.frontend)

        self.assertEqual((result.users, result.succeeded, len(result.failed)), (2, 2, 0))
        self.assertEqual((result.periods, result.entries), (4, 400))
        state_paths = [call.args[0] for call in store_type.return_value.restore.call_args_list]
        self.assertEqual(state_paths, [self.state / user_id for user_id, _ in self.users])
        frontend_paths = [call.args[0] for call in load_snapshots.call_args_list]
        self.assertEqual(frontend_paths, [self.frontend / user_id for user_id, _ in self.users])
        self.assertEqual(
            [call.kwargs["netease_uid"] for call in update.call_args_list],
            ["123456", "654321"],
        )

    @patch("echorank.cloud.load_snapshots", return_value=["snapshot"])
    @patch("echorank.cloud.update_charts")
    @patch("echorank.cloud.SupabaseStateStore")
    @patch("echorank.cloud.SupabaseUploader")
    @patch("echorank.cloud.load_config")
    def test_failure_continues_to_next_user(
        self, load_config, uploader_type, store_type, update, load_snapshots
    ) -> None:
        load_config.return_value = self.base
        uploader_type.return_value.fetch_bound_users.return_value = self.users
        uploader_type.return_value.upload.return_value = UploadResult(1, 100)
        store_type.return_value.restore.side_effect = [ValueError("unavailable"), True]
        update.return_value = UpdateResult(
            "2026-07-20", "2026-W30", "2026-07", "2026", {}, 100, True
        )

        result = cloud_update(self.state, self.frontend)

        self.assertEqual((result.succeeded, len(result.failed)), (1, 1))
        self.assertEqual(result.failed[0][0], self.users[0][0])
        self.assertEqual(update.call_count, 1)

    @patch("echorank.cloud.SupabaseUploader")
    @patch("echorank.cloud.load_config")
    def test_no_bound_users_is_success(self, load_config, uploader_type) -> None:
        load_config.return_value = self.base
        uploader_type.return_value.fetch_bound_users.return_value = []
        result = cloud_update(self.state, self.frontend)
        self.assertEqual((result.users, result.succeeded, result.failed), (0, 0, ()))


if __name__ == "__main__":
    unittest.main()
