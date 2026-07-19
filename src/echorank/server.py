from __future__ import annotations

import json
import sqlite3
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .database import connect, initialize
from .netease import fetch_album_tracks, search_songs
from .settlement import (
    create_album,
    create_artist,
    create_song,
    import_bilibili_view_event,
    import_catalog_song,
    import_manual_adjustment,
    import_physical_event,
)


class AdminHandler(SimpleHTTPRequestHandler):
    frontend_root = ""
    admin_root = ""
    database_path = ""
    write_lock = threading.Lock()

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=self.frontend_root, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/admin/"):
            self._handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        if parsed.path == "/admin" or parsed.path.startswith("/admin/"):
            self._serve_admin(parsed.path)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/admin/"):
            self._json_error(404, "not_found", "接口不存在")
            return
        if self.headers.get_content_type() != "application/json":
            self._json_error(415, "unsupported_media_type", "请求必须使用 JSON")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是 JSON 对象")
        except (ValueError, json.JSONDecodeError):
            self._json_error(400, "invalid_json", "请求 JSON 无效")
            return
        try:
            with self.write_lock:
                result, status = self._mutate(path, payload)
            self._send_json(status, result)
        except ValueError as error:
            message = str(error)
            status = 409 if "已结算" in message or "幂等键冲突" in message else 400
            self._json_error(status, "validation_error", message)
        except sqlite3.IntegrityError:
            self._json_error(409, "conflict", "数据与现有记录冲突")

    def _connection(self) -> sqlite3.Connection:
        connection = connect(self.database_path)
        initialize(connection)
        return connection

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        connection = self._connection()
        try:
            if path == "/api/admin/catalog":
                term = query.get("query", [""])[0].strip()
                self._send_json(200, self._catalog(connection, term))
            elif path == "/api/admin/netease/search":
                term = query.get("query", [""])[0]
                try:
                    self._send_json(200, {"songs": search_songs(term)})
                except ValueError as error:
                    status = 400 if "关键词长度" in str(error) else 502
                    self._json_error(status, "netease_search_error", str(error))
            elif path.startswith("/api/admin/netease/albums/") and path.endswith("/tracks"):
                album_id = path.removeprefix("/api/admin/netease/albums/").removesuffix("/tracks")
                try:
                    self._send_json(200, {"songs": fetch_album_tracks(album_id)})
                except ValueError as error:
                    status = 400 if "专辑 ID 无效" in str(error) else 502
                    self._json_error(status, "netease_album_error", str(error))
            elif path == "/api/admin/periods":
                rows = connection.execute(
                    "SELECT period_key, status, coverage, frozen, source_snapshot "
                    "FROM chart_periods WHERE entity_type='songs' AND period_type='daily' "
                    "ORDER BY period_key DESC LIMIT 14"
                ).fetchall()
                self._send_json(200, {"periods": [{
                    "periodKey": row["period_key"],
                    "status": row["status"],
                    "coverage": row["coverage"],
                    "frozen": bool(row["frozen"]),
                    "ledgerWritable": not bool(row["frozen"]),
                    "hasSnapshot": bool(row["source_snapshot"]),
                } for row in rows]})
            else:
                self._json_error(404, "not_found", "接口不存在")
        finally:
            connection.close()

    def _catalog(self, connection: sqlite3.Connection, term: str) -> dict[str, Any]:
        pattern = f"%{term}%"
        songs = connection.execute(
            "SELECT s.id, s.title, a.id AS album_id, a.title AS album_title "
            "FROM songs s JOIN albums a ON a.id=s.album_id "
            "WHERE s.title LIKE ? ORDER BY s.title, s.id LIMIT 50",
            (pattern,),
        ).fetchall()
        song_items = []
        for song in songs:
            artists = connection.execute(
                "SELECT a.id, a.name FROM song_artists sa JOIN artists a ON a.id=sa.artist_id "
                "WHERE sa.song_id=? ORDER BY sa.credit_order",
                (song["id"],),
            ).fetchall()
            song_items.append({
                "id": song["id"],
                "title": song["title"],
                "album": {"id": song["album_id"], "title": song["album_title"]},
                "artists": [dict(artist) for artist in artists],
            })
        albums = connection.execute(
            "SELECT id, title, cover_url AS coverUrl, cover_color AS coverColor "
            "FROM albums WHERE title LIKE ? ORDER BY title, id LIMIT 50",
            (pattern,),
        ).fetchall()
        artists = connection.execute(
            "SELECT id, name FROM artists WHERE name LIKE ? ORDER BY name, id LIMIT 50",
            (pattern,),
        ).fetchall()
        return {
            "songs": song_items,
            "albums": [dict(album) for album in albums],
            "artists": [dict(artist) for artist in artists],
        }

    def _mutate(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        connection = self._connection()
        try:
            if path == "/api/admin/bilibili/events":
                song = payload.get("song")
                if not isinstance(song, dict):
                    raise ValueError("缺少网易云歌曲数据")
                normalized, imported, points = import_bilibili_view_event(
                    connection,
                    song,
                    str(payload.get("periodKey", "")),
                    payload.get("viewCount"),
                    str(payload.get("externalKey", "")),
                    payload.get("videoRef"),
                    payload.get("notes"),
                )
                return {
                    "song": normalized,
                    "eventImported": bool(imported),
                    "estimatedPoints": points,
                    "publication": "等待 22:00 结算",
                }, 201 if imported else 200
            if path == "/api/admin/physical/events":
                songs = payload.get("songs")
                selected_song_ids = payload.get("selectedSongIds")
                if not isinstance(songs, list) or not all(isinstance(song, dict) for song in songs):
                    raise ValueError("缺少专辑曲目数据")
                if not isinstance(selected_song_ids, list) or not all(isinstance(item, str) for item in selected_song_ids):
                    raise ValueError("selectedSongIds 必须是歌曲 ID 数组")
                event = import_physical_event(
                    connection,
                    songs,
                    str(payload.get("purchaseDate", "")),
                    str(payload.get("editionLabel", "")),
                    str(payload.get("format", "")),
                    payload.get("quantity"),
                    selected_song_ids,
                    str(payload.get("externalKey", "")),
                    payload.get("notes"),
                )
                return {"event": event, "publication": "将在未来 28 天按日释放"}, 201
            if path == "/api/admin/manual-adjustments":
                song = payload.get("song")
                if not isinstance(song, dict):
                    raise ValueError("缺少网易云歌曲数据")
                normalized, imported = import_manual_adjustment(
                    connection,
                    song,
                    str(payload.get("periodKey", "")),
                    payload.get("points"),
                    str(payload.get("reason", "")),
                    str(payload.get("externalKey", "")),
                )
                return {"song": normalized, "imported": bool(imported)}, 201 if imported else 200
            if path == "/api/admin/catalog/netease" or path == "/api/admin/netease/import":
                song = payload.get("song")
                if not isinstance(song, dict):
                    raise ValueError("缺少网易云歌曲数据")
                return {"song": import_catalog_song(connection, song)}, 201
            if path == "/api/admin/artists":
                return {"artist": create_artist(connection, str(payload.get("name", "")))}, 201
            if path == "/api/admin/albums":
                album = create_album(
                    connection,
                    str(payload.get("title", "")),
                    payload.get("coverUrl"),
                    str(payload.get("coverColor", "#777777")),
                )
                return {"album": album}, 201
            if path == "/api/admin/songs":
                artist_ids = payload.get("artistIds")
                if not isinstance(artist_ids, list) or not all(isinstance(item, str) for item in artist_ids):
                    raise ValueError("artistIds 必须是艺人 ID 数组")
                song = create_song(
                    connection,
                    str(payload.get("title", "")),
                    str(payload.get("albumId", "")),
                    artist_ids,
                )
                return {"song": song}, 201
            if path in ("/api/admin/bilibili/import", "/api/admin/ledger/bilibili"):
                raise ValueError("旧的B站点数接口已停用，请录入当日观看次数")
            raise ValueError("接口不存在")
        finally:
            connection.close()

    def _serve_admin(self, path: str) -> None:
        relative = "index.html" if path in ("/admin", "/admin/") else path.removeprefix("/admin/")
        root = Path(self.admin_root).resolve()
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8"
        if target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})


def serve_frontend(
    directory: str | Path = "frontend",
    port: int = 8765,
    database: str | Path = "data/echorank-live.db",
    admin_directory: str | Path | None = None,
) -> None:
    AdminHandler.frontend_root = str(Path(directory).resolve())
    AdminHandler.database_path = str(Path(database).resolve())
    AdminHandler.admin_root = str(
        Path(admin_directory).resolve()
        if admin_directory
        else Path(__file__).with_name("admin").resolve()
    )
    ThreadingHTTPServer(("127.0.0.1", port), AdminHandler).serve_forever()
