from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from .netease import CHINA_TIMEZONE
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


@dataclass(frozen=True)
class CollectionRequestSummary:
    requests: int
    succeeded: int
    failed: tuple[tuple[str, str], ...]
    periods: int
    entries: int


def initial_chart_date(now: datetime | None = None):
    current = (now or datetime.now(CHINA_TIMEZONE)).astimezone(CHINA_TIMEZONE)
    next_settlement = current.date() if current.time() < time(22) else current.date() + timedelta(days=1)
    return next_settlement - timedelta(days=1)


def process_collection_requests(
    state_root: str | Path,
    frontend_root: str | Path,
    timeout: float = 20,
    now: datetime | None = None,
) -> CollectionRequestSummary:
    base_config = load_config(None, require_user=False)
    request_client = SupabaseUploader(base_config, timeout)
    requests = request_client.fetch_pending_collection_requests()
    state_base = Path(state_root)
    frontend_base = Path(frontend_root)
    succeeded = periods = entries = 0
    failed = []

    for request in requests:
        if not request_client.claim_collection_request(request.id):
            continue
        label = request.user_id[:8]
        state = state_base / request.user_id
        frontend = frontend_base / request.user_id
        database = state / "echorank.db"
        raw_root = state / "raw" / "netease"
        config = base_config.for_user(request.user_id)
        uploader = SupabaseUploader(config, timeout)
        store = SupabaseStateStore(config, timeout)
        try:
            restored = store.restore(state)
            if not restored and uploader.has_chart_periods():
                raise ValueError("该账户已有云端榜单但缺少私有历史状态，请先恢复历史后再首采")
            frontend.mkdir(parents=True, exist_ok=True)
            update_charts(
                config_path="",
                database_path=database,
                raw_root=raw_root,
                frontend_root=frontend,
                now=now,
                timeout=timeout,
                netease_uid=request.netease_uid,
                target_date=initial_chart_date(now),
            )
            store.save(state, database, raw_root)
            upload = uploader.upload(load_snapshots(frontend))
            request_client.finish_collection_request(request.id)
            succeeded += 1
            periods += upload.periods
            entries += upload.entries
            print(f"Initial chart {label}: {upload.periods} periods, {upload.entries} entries")
        except Exception as error:
            message = str(error)
            failed.append((request.user_id, message))
            try:
                request_client.finish_collection_request(request.id, message)
            except Exception as finish_error:
                failed[-1] = (request.user_id, f"{message}; request status: {finish_error}")
            print(f"Failed initial chart {label}: {message}")

    return CollectionRequestSummary(
        len(requests), succeeded, tuple(failed), periods, entries
    )


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
