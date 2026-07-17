from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    schema = files("echorank").joinpath("schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema)
    connection.commit()
