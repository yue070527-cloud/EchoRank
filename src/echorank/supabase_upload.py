from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

ENTITY_TYPES = {"songs", "albums", "artists"}
PERIOD_TYPES = {"daily", "weekly", "monthly", "yearly"}
MOVEMENT_TYPES = {"up", "down", "same", "new", "re"}
POINT_FIELDS = (
    "netease",
    "physical",
    "bilibili",
    "other",
    "legacyBonus",
    "manualAdjustment",
)
Requester = Callable[[Request, float], object]


def _open_request(request: Request, timeout: float) -> object:
    return urlopen(request, timeout=timeout)


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    secret_key: str
    user_id: str | None = None

    def for_user(self, user_id: str) -> SupabaseConfig:
        try:
            validated = str(UUID(user_id))
        except ValueError as error:
            raise ValueError("user_id 必须是有效 UUID") from error
        return SupabaseConfig(self.url, self.secret_key, validated)


@dataclass(frozen=True)
class UploadResult:
    periods: int
    entries: int


@dataclass(frozen=True)
class CollectionRequest:
    id: str
    user_id: str
    netease_uid: str


@dataclass(frozen=True)
class SnapshotUpload:
    entity_type: str
    period_type: str
    period_key: str
    payload: dict

    @property
    def label(self) -> str:
        return f"{self.entity_type}/{self.period_type}/{self.period_key}"


def _load_env(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        raise ValueError(f"缺少本机配置文件：{env_path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"{env_path} 第 {line_number} 行格式错误")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_config(
    env_file: str | Path | None = ".env",
    require_user: bool = True,
) -> SupabaseConfig:
    file_values = {} if env_file is None else _load_env(env_file)
    names = ["SUPABASE_URL", "SUPABASE_SECRET_KEY"]
    if require_user:
        names.append("SUPABASE_USER_ID")
    values = {
        name: os.environ.get(name, file_values.get(name, "")).strip()
        for name in names
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"Supabase 配置缺少：{', '.join(missing)}")
    user_id = None
    if require_user:
        try:
            user_id = str(UUID(values["SUPABASE_USER_ID"]))
        except ValueError as error:
            raise ValueError("SUPABASE_USER_ID 必须是有效 UUID") from error
    url = values["SUPABASE_URL"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("SUPABASE_URL 必须使用 https://")
    return SupabaseConfig(url, values["SUPABASE_SECRET_KEY"], user_id)


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"不允许非有限数值：{value}")
            ),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"无法读取 JSON：{path}（{error}）") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return payload


def _required_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数字")
    return number


def _required_integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} 必须是大于等于 {minimum} 的整数")
    return value


def _validate_snapshot(
    snapshot: dict,
    entity_type: str,
    period_type: str,
    period_key: str,
    path: Path,
) -> SnapshotUpload:
    if snapshot.get("schemaVersion") != "1.0":
        raise ValueError(f"快照 schemaVersion 不受支持：{path}")
    chart = snapshot.get("chart")
    period = snapshot.get("period")
    collection = snapshot.get("collection")
    entries = snapshot.get("entries")
    if not all(isinstance(item, dict) for item in (chart, period, collection)):
        raise ValueError(f"快照缺少 chart、period 或 collection：{path}")
    if not isinstance(entries, list):
        raise ValueError(f"快照 entries 必须是数组：{path}")
    if chart.get("entityType") != entity_type:
        raise ValueError(f"快照 entityType 与 Manifest 不一致：{path}")
    if chart.get("periodType") != period_type:
        raise ValueError(f"快照 periodType 与 Manifest 不一致：{path}")
    if period.get("key") != period_key:
        raise ValueError(f"快照 periodKey 与 Manifest 不一致：{path}")
    coverage = _required_number(collection.get("coverage"), f"{path} coverage")
    if not 0 <= coverage <= 1:
        raise ValueError(f"{path} coverage 必须在 0 到 1 之间")
    status = period.get("status")
    if status not in {"pending", "collecting", "partial", "settled", "failed"}:
        raise ValueError(f"{path} period.status 不受支持：{status}")

    entity_ids: set[str] = set()
    ranks: set[int] = set()
    for index, entry in enumerate(entries, 1):
        label = f"{path} entries[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} 必须是对象")
        entity_id = entry.get("entityId")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError(f"{label}.entityId 不能为空")
        if entity_id in entity_ids:
            raise ValueError(f"{path} 存在重复 entityId：{entity_id}")
        entity_ids.add(entity_id)
        if not isinstance(entry.get("entity"), dict):
            raise ValueError(f"{label}.entity 必须是对象")

        rank = entry.get("rank")
        points = entry.get("points")
        record = entry.get("record")
        if not all(isinstance(item, dict) for item in (rank, points, record)):
            raise ValueError(f"{label} 缺少 rank、points 或 record")
        current_rank = _required_integer(rank.get("current"), f"{label}.rank.current", 1)
        if current_rank > 100 or current_rank in ranks:
            raise ValueError(f"{path} 存在无效或重复名次：{current_rank}")
        ranks.add(current_rank)
        previous = rank.get("previous")
        if previous is not None:
            previous = _required_integer(previous, f"{label}.rank.previous", 1)
            if previous > 100:
                raise ValueError(f"{label}.rank.previous 不能大于 100")
        movement = rank.get("movement")
        if not isinstance(movement, dict) or movement.get("type") not in MOVEMENT_TYPES:
            raise ValueError(f"{label}.rank.movement 无效")
        _required_integer(movement.get("value"), f"{label}.rank.movement.value")

        components = [
            _required_number(points.get(name), f"{label}.points.{name}")
            for name in POINT_FIELDS
        ]
        total = _required_number(points.get("total"), f"{label}.points.total")
        if abs(sum(components) - total) > 0.001:
            raise ValueError(f"{label} 点数分项之和与 total 不一致")
        peak = _required_integer(record.get("peak"), f"{label}.record.peak", 1)
        if peak > 100:
            raise ValueError(f"{label}.record.peak 不能大于 100")
        _required_integer(record.get("periods"), f"{label}.record.periods", 1)
        _required_integer(record.get("championships"), f"{label}.record.championships")

    return SnapshotUpload(entity_type, period_type, period_key, snapshot)


def load_snapshots(frontend_root: str | Path = "frontend") -> list[SnapshotUpload]:
    root = Path(frontend_root).resolve()
    manifest_path = root / "data" / "chart-manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("schemaVersion") != "1.0" or not isinstance(manifest.get("views"), list):
        raise ValueError("Manifest 结构或版本无效")
    uploads: list[SnapshotUpload] = []
    identities: set[tuple[str, str, str]] = set()
    for view in manifest["views"]:
        if not isinstance(view, dict):
            raise ValueError("Manifest view 必须是对象")
        entity_type = view.get("entityType")
        period_type = view.get("periodType")
        if entity_type not in ENTITY_TYPES or period_type not in PERIOD_TYPES:
            raise ValueError("Manifest 榜单类型无效")
        snapshots = view.get("snapshots")
        if not isinstance(snapshots, list):
            raise ValueError("Manifest snapshots 必须是数组")
        for reference in snapshots:
            if not isinstance(reference, dict):
                raise ValueError("Manifest snapshot 引用必须是对象")
            period_key = reference.get("periodKey")
            relative_path = reference.get("path")
            if not isinstance(period_key, str) or not period_key:
                raise ValueError("Manifest periodKey 不能为空")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("Manifest snapshot path 不能为空")
            relative = Path(relative_path.removeprefix("./"))
            if relative.is_absolute():
                raise ValueError(f"Manifest 不允许绝对路径：{relative_path}")
            snapshot_path = (root / relative).resolve()
            if root not in snapshot_path.parents:
                raise ValueError(f"Manifest 路径越出 frontend：{relative_path}")
            identity = (entity_type, period_type, period_key)
            if identity in identities:
                raise ValueError(f"Manifest 存在重复周期：{'/'.join(identity)}")
            identities.add(identity)
            uploads.append(_validate_snapshot(
                _load_json(snapshot_path),
                entity_type,
                period_type,
                period_key,
                snapshot_path,
            ))
    return uploads


class SupabaseUploader:
    def __init__(
        self,
        config: SupabaseConfig,
        timeout: float = 20,
        requester: Requester = _open_request,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Supabase 请求超时必须大于 0")
        self.config = config
        self.timeout = timeout
        self.requester = requester

    def _request(
        self,
        method: str,
        resource: str,
        *,
        query: dict[str, str] | None = None,
        body: object | None = None,
        prefer: str | None = None,
        expect_json: bool = False,
    ) -> object | None:
        url = f"{self.config.url}/rest/v1/{resource}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "apikey": self.config.secret_key,
            "Authorization": f"Bearer {self.config.secret_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        data = None if body is None else json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self.requester(request, self.timeout)
            with response:
                raw = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ValueError(f"Supabase {method} {resource} 失败（HTTP {error.code}）：{detail}") from None
        except (URLError, TimeoutError, OSError) as error:
            raise ValueError(f"Supabase {method} {resource} 请求失败：{error}") from None
        if not expect_json:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Supabase {resource} 返回了无效 JSON") from error

    def fetch_bound_users(self) -> list[tuple[str, str]]:
        users = []
        offset = 0
        while True:
            response = self._request(
                "GET",
                "user_settings",
                query={
                    "select": "user_id,netease_uid",
                    "netease_uid": "not.is.null",
                    "order": "user_id",
                    "offset": str(offset),
                    "limit": "1000",
                },
                expect_json=True,
            )
            if not isinstance(response, list):
                raise ValueError("Supabase user_settings 返回格式无效")
            for row in response:
                if not isinstance(row, dict):
                    raise ValueError("Supabase user_settings 包含无效记录")
                try:
                    user_id = str(UUID(row.get("user_id")))
                except (ValueError, TypeError, AttributeError) as error:
                    raise ValueError("Supabase user_settings 包含无效 user_id") from error
                uid = row.get("netease_uid")
                if not isinstance(uid, str) or not uid.isdecimal():
                    raise ValueError(f"用户 {user_id[:8]} 的 netease_uid 必须是数字字符串")
                users.append((user_id, uid))
            if len(response) < 1000:
                return users
            offset += 1000

    def fetch_pending_collection_requests(self) -> list[CollectionRequest]:
        response = self._request(
            "GET",
            "collection_requests",
            query={
                "select": "id,user_id",
                "request_type": "eq.initial",
                "status": "eq.pending",
                "order": "requested_at",
                "limit": "100",
            },
            expect_json=True,
        )
        if not isinstance(response, list):
            raise ValueError("Supabase collection_requests 返回格式无效")
        requests = []
        for row in response:
            if not isinstance(row, dict):
                raise ValueError("Supabase collection_requests 包含无效记录")
            try:
                request_id = str(UUID(row.get("id")))
                user_id = str(UUID(row.get("user_id")))
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("Supabase collection_requests 包含无效 UUID") from error
            uid = SupabaseUploader(self.config.for_user(user_id), self.timeout, self.requester).fetch_netease_uid()
            requests.append(CollectionRequest(request_id, user_id, uid))
        return requests

    def claim_collection_request(self, request_id: str) -> bool:
        response = self._request(
            "PATCH",
            "collection_requests",
            query={"id": f"eq.{request_id}", "status": "eq.pending"},
            body={
                "status": "processing",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            },
            prefer="return=representation",
            expect_json=True,
        )
        return isinstance(response, list) and len(response) == 1

    def finish_collection_request(self, request_id: str, error: str | None = None) -> None:
        body = {
            "status": "failed" if error else "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": error[:1000] if error else None,
        }
        self._request(
            "PATCH",
            "collection_requests",
            query={"id": f"eq.{request_id}", "status": "eq.processing"},
            body=body,
            prefer="return=minimal",
        )

    def has_chart_periods(self) -> bool:
        if self.config.user_id is None:
            raise ValueError("当前 Supabase 配置未指定 user_id")
        response = self._request(
            "GET",
            "chart_periods",
            query={
                "select": "id",
                "user_id": f"eq.{self.config.user_id}",
                "limit": "1",
            },
            expect_json=True,
        )
        if not isinstance(response, list):
            raise ValueError("Supabase chart_periods 返回格式无效")
        return bool(response)

    def fetch_netease_uid(self) -> str:
        if self.config.user_id is None:
            raise ValueError("当前 Supabase 配置未指定 user_id")
        response = self._request(
            "GET",
            "user_settings",
            query={
                "select": "netease_uid",
                "user_id": f"eq.{self.config.user_id}",
            },
            expect_json=True,
        )
        if not isinstance(response, list):
            raise ValueError("Supabase user_settings 返回格式无效")
        if not response:
            raise ValueError("未找到该用户的网易云设置")
        if len(response) != 1 or not isinstance(response[0], dict):
            raise ValueError("该用户的网易云设置不是唯一记录")
        uid = response[0].get("netease_uid")
        if uid is None or uid == "":
            raise ValueError("该用户尚未绑定网易云 UID")
        if not isinstance(uid, str) or not uid.isdecimal():
            raise ValueError("Supabase 中的 netease_uid 必须是数字字符串")
        return uid

    def _period_row(self, upload: SnapshotUpload) -> dict:
        snapshot = upload.payload
        return {
            "user_id": self.config.user_id,
            "entity_type": upload.entity_type,
            "period_type": upload.period_type,
            "period_key": upload.period_key,
            "scheduled_at": snapshot["period"].get("scheduledAt"),
            "collected_at": snapshot["collection"].get("collectedAt"),
            "status": snapshot["period"]["status"],
            "coverage": snapshot["collection"]["coverage"],
            "frozen": snapshot["period"]["status"] == "settled",
            "source_snapshot": snapshot["collection"].get("sourceSnapshot"),
        }

    def _entry_rows(self, upload: SnapshotUpload, period_id: str) -> list[dict]:
        rows = []
        for entry in upload.payload["entries"]:
            rows.append({
                "period_id": period_id,
                "user_id": self.config.user_id,
                "entity_id": entry["entityId"],
                "entity": entry["entity"],
                "rank": entry["rank"]["current"],
                "previous_rank": entry["rank"]["previous"],
                "movement_type": entry["rank"]["movement"]["type"],
                "movement_value": entry["rank"]["movement"]["value"],
                "netease_points": entry["points"]["netease"],
                "physical_points": entry["points"]["physical"],
                "bilibili_points": entry["points"]["bilibili"],
                "other_points": entry["points"]["other"],
                "legacy_bonus": entry["points"]["legacyBonus"],
                "manual_adjustment": entry["points"]["manualAdjustment"],
                "total_points": entry["points"]["total"],
                "peak": entry["record"]["peak"],
                "periods": entry["record"]["periods"],
                "championships": entry["record"]["championships"],
            })
        return rows

    def upload(self, uploads: list[SnapshotUpload]) -> UploadResult:
        entry_count = 0
        for upload in uploads:
            response = self._request(
                "POST",
                "chart_periods",
                query={"on_conflict": "user_id,entity_type,period_type,period_key"},
                body=self._period_row(upload),
                prefer="resolution=merge-duplicates,return=representation",
                expect_json=True,
            )
            if not isinstance(response, list) or len(response) != 1:
                raise ValueError(f"{upload.label} 未返回唯一 Supabase 周期")
            period_id = response[0].get("id") if isinstance(response[0], dict) else None
            try:
                period_id = str(UUID(period_id))
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError(f"{upload.label} 返回了无效周期 UUID") from error
            self._request(
                "DELETE",
                "chart_entries",
                query={
                    "period_id": f"eq.{period_id}",
                    "user_id": f"eq.{self.config.user_id}",
                },
            )
            rows = self._entry_rows(upload, period_id)
            if rows:
                try:
                    self._request(
                        "POST",
                        "chart_entries",
                        body=rows,
                        prefer="return=minimal",
                    )
                except ValueError as error:
                    raise ValueError(
                        f"{upload.label} 条目写入失败；重新运行 upload-supabase 可修复。{error}"
                    ) from None
            entry_count += len(rows)
            print(f"Uploaded {upload.label}: {len(rows)} entries")
        return UploadResult(len(uploads), entry_count)


def upload_manifest(
    frontend_root: str | Path = "frontend",
    env_file: str | Path = ".env",
    timeout: float = 20,
    requester: Requester = _open_request,
) -> UploadResult:
    config = load_config(env_file)
    uploads = load_snapshots(frontend_root)
    return SupabaseUploader(config, timeout, requester).upload(uploads)
