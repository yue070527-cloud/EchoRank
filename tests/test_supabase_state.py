from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echorank.supabase_state import SupabaseStateStore
from echorank.supabase_upload import SupabaseConfig


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class SupabaseStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = SupabaseConfig(
            "https://example.supabase.co",
            "server-secret",
            str(uuid4()),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_state_is_first_run(self) -> None:
        def requester(request, timeout):
            raise HTTPError(request.full_url, 404, "missing", {}, BytesIO(b"missing"))

        restored = SupabaseStateStore(self.config, requester=requester).restore(
            self.root / "state"
        )
        self.assertFalse(restored)

    def test_save_and_restore_only_database_and_raw_files(self) -> None:
        requests = []
        state = self.root / "state"
        database = state / "echorank.db"
        raw = state / "raw" / "netease"
        raw.mkdir(parents=True)
        database.write_bytes(b"sqlite")
        (raw / "snapshot.json").write_text("{}", encoding="utf-8")
        (state / "private.txt").write_text("not included", encoding="utf-8")

        def upload_requester(request, timeout):
            requests.append(request)
            return Response(b"")

        SupabaseStateStore(self.config, requester=upload_requester).save(
            state, database, raw
        )
        archive = requests[0].data
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            self.assertEqual(
                sorted(bundle.namelist()),
                ["echorank.db", "raw/netease/snapshot.json"],
            )

        restored = self.root / "restored"
        store = SupabaseStateStore(
            self.config,
            requester=lambda request, timeout: Response(archive),
        )
        self.assertTrue(store.restore(restored))
        self.assertEqual((restored / "echorank.db").read_bytes(), b"sqlite")
        self.assertEqual(
            (restored / "raw" / "netease" / "snapshot.json").read_text(), "{}"
        )
        self.assertFalse((restored / "private.txt").exists())

    def test_restore_rejects_path_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("../../outside.db", b"bad")
        store = SupabaseStateStore(
            self.config,
            requester=lambda request, timeout: Response(buffer.getvalue()),
        )
        with self.assertRaisesRegex(ValueError, "越界路径"):
            store.restore(self.root / "state")
        self.assertFalse((self.root / "outside.db").exists())

    def test_storage_error_hides_secret(self) -> None:
        def requester(request, timeout):
            raise HTTPError(request.full_url, 500, "failure", {}, BytesIO(b"failed"))

        with self.assertRaisesRegex(ValueError, "Storage GET") as context:
            SupabaseStateStore(self.config, requester=requester).restore(self.root / "state")
        self.assertNotIn("server-secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
