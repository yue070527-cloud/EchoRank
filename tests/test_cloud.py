from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
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
        self.config = SupabaseConfig(
            "https://example.supabase.co", "secret", str(uuid4())
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("echorank.cloud.load_snapshots", return_value=["snapshot"])
    @patch("echorank.cloud.update_charts")
    @patch("echorank.cloud.SupabaseStateStore")
    @patch("echorank.cloud.SupabaseUploader")
    @patch("echorank.cloud.load_config")
    def test_updates_state_before_uploading_snapshots(
        self, load_config, uploader_type, store_type, update, load_snapshots
    ) -> None:
        load_config.return_value = self.config
        uploader = uploader_type.return_value
        uploader.fetch_netease_uid.return_value = "123456"
        uploader.upload.return_value = UploadResult(2, 200)
        store = store_type.return_value
        store.restore.return_value = True
        result_value = UpdateResult(
            "2026-07-20", "2026-W30", "2026-07", "2026", {}, 100, True
        )
        update.return_value = result_value
        order = []
        store.save.side_effect = lambda *args: order.append("save")
        uploader.upload.side_effect = lambda *args: order.append("upload") or UploadResult(2, 200)

        result = cloud_update(self.state, self.frontend)

        self.assertEqual(order, ["save", "upload"])
        self.assertTrue(result.restored)
        self.assertEqual(result.upload, UploadResult(2, 200))
        self.assertEqual(update.call_args.kwargs["netease_uid"], "123456")
        load_snapshots.assert_called_once_with(self.frontend)

    @patch("echorank.cloud.update_charts")
    @patch("echorank.cloud.SupabaseStateStore")
    @patch("echorank.cloud.SupabaseUploader")
    @patch("echorank.cloud.load_config")
    def test_skip_does_not_save_or_upload(
        self, load_config, uploader_type, store_type, update
    ) -> None:
        load_config.return_value = self.config
        uploader = uploader_type.return_value
        uploader.fetch_netease_uid.return_value = "123456"
        store = store_type.return_value
        store.restore.return_value = False
        update.return_value = UpdateResult("", "", "", "", {}, 0, False, True)

        result = cloud_update(self.state, self.frontend)

        self.assertIsNone(result.upload)
        store.save.assert_not_called()
        uploader.upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
