from __future__ import annotations

import io
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from .supabase_upload import Requester, SupabaseConfig, _open_request

STATE_BUCKET = "echorank-state"


class SupabaseStateStore:
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

    @property
    def object_path(self) -> str:
        return f"{self.config.user_id}/latest.zip"

    def _request(self, method: str, data: bytes | None = None) -> bytes | None:
        object_path = quote(self.object_path, safe="/")
        url = f"{self.config.url}/storage/v1/object/{STATE_BUCKET}/{object_path}"
        headers = {
            "apikey": self.config.secret_key,
            "Authorization": f"Bearer {self.config.secret_key}",
        }
        if data is not None:
            headers["Content-Type"] = "application/zip"
            headers["x-upsert"] = "true"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self.requester(request, self.timeout)
            with response:
                return response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            missing = error.code == 404 or (
                error.code == 400
                and ("Object not found" in detail or '"error":"not_found"' in detail)
            )
            if method == "GET" and missing:
                return None
            raise ValueError(
                f"Supabase Storage {method} {STATE_BUCKET} 失败（HTTP {error.code}）：{detail}"
            ) from None
        except (URLError, TimeoutError, OSError) as error:
            raise ValueError(f"Supabase Storage {method} {STATE_BUCKET} 请求失败：{error}") from None

    def restore(self, state_root: str | Path) -> bool:
        archive = self._request("GET")
        if archive is None:
            return False
        root = Path(state_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                for info in bundle.infolist():
                    path = PurePosixPath(info.filename)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("私密状态压缩包包含越界路径")
                    destination = (root / Path(*path.parts)).resolve()
                    if destination != root and root not in destination.parents:
                        raise ValueError("私密状态压缩包包含越界路径")
                bundle.extractall(root)
        except zipfile.BadZipFile as error:
            raise ValueError("私密状态不是有效 ZIP 文件") from error
        return True

    def save(
        self,
        state_root: str | Path,
        database_path: str | Path,
        raw_root: str | Path,
    ) -> None:
        root = Path(state_root).resolve()
        database = Path(database_path).resolve()
        raw = Path(raw_root).resolve()
        if not database.is_file() or root not in database.parents:
            raise ValueError("私密状态缺少有效 SQLite 数据库")
        if raw.exists() and root not in raw.parents:
            raise ValueError("原始快照目录必须位于私密状态目录内")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(database, database.relative_to(root).as_posix())
            if raw.exists():
                for path in sorted(raw.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(root).as_posix())
        self._request("POST", buffer.getvalue())
