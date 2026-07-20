from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .supabase_state import SupabaseStateStore
from .supabase_upload import SupabaseUploader, load_config, load_snapshots
from .workflow import update_charts


@dataclass(frozen=True)
class CloudUpdateSummary:
    users: int
    succeeded: int
    skipped: int
    failed: tuple[tuple[str, str], ...]
    periods: int
    entries: int


def cloud_update(
    state_root: str | Path,
    frontend_root: str | Path,
    timeout: float = 20,
) -> CloudUpdateSummary:
    base_config = load_config(None, require_user=False)
    bound_users = SupabaseUploader(base_config, timeout).fetch_bound_users()
    state_base = Path(state_root)
    frontend_base = Path(frontend_root)
    succeeded = skipped = periods = entries = 0
    failed = []

    for user_id, uid in bound_users:
        label = user_id[:8]
        state = state_base / user_id
        frontend = frontend_base / user_id
        database = state / "echorank.db"
        raw_root = state / "raw" / "netease"
        config = base_config.for_user(user_id)
        uploader = SupabaseUploader(config, timeout)
        store = SupabaseStateStore(config, timeout)
        try:
            store.restore(state)
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
                skipped += 1
                print(f"Skipped user {label}: before 22:00")
                continue
            store.save(state, database, raw_root)
            upload = uploader.upload(load_snapshots(frontend))
            succeeded += 1
            periods += upload.periods
            entries += upload.entries
            print(f"Updated user {label}: {upload.periods} periods, {upload.entries} entries")
        except Exception as error:
            failed.append((user_id, str(error)))
            print(f"Failed user {label}: {error}")

    return CloudUpdateSummary(
        len(bound_users), succeeded, skipped, tuple(failed), periods, entries
    )
