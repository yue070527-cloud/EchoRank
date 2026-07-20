from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .supabase_state import SupabaseStateStore
from .supabase_upload import SupabaseUploader, UploadResult, load_config, load_snapshots
from .workflow import UpdateResult, update_charts


@dataclass(frozen=True)
class CloudUpdateResult:
    update: UpdateResult
    upload: UploadResult | None
    restored: bool


def cloud_update(
    state_root: str | Path,
    frontend_root: str | Path,
    timeout: float = 20,
) -> CloudUpdateResult:
    state = Path(state_root)
    frontend = Path(frontend_root)
    database = state / "echorank.db"
    raw_root = state / "raw" / "netease"

    config = load_config(None)
    uploader = SupabaseUploader(config, timeout)
    store = SupabaseStateStore(config, timeout)
    uid = uploader.fetch_netease_uid()
    restored = store.restore(state)

    frontend.mkdir(parents=True, exist_ok=True)
    result = update_charts(
        config_path="",
        database_path=database,
        raw_root=raw_root,
        frontend_root=frontend,
        timeout=timeout,
        netease_uid=uid,
    )
    if result.skipped:
        return CloudUpdateResult(result, None, restored)

    store.save(state, database, raw_root)
    upload = uploader.upload(load_snapshots(frontend))
    return CloudUpdateResult(result, upload, restored)
