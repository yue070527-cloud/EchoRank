from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import connect, initialize
from .demo import generate_demo
from .export import export_period
from .settlement import (
    import_ledger_entries,
    import_netease_snapshot,
    settle_daily,
    settle_weekly,
)


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="echorank")
    parser.add_argument("--database", default="data/echorank.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")

    demo = commands.add_parser("demo")
    demo.add_argument("--frontend", default="frontend")

    netease = commands.add_parser("import-netease")
    netease.add_argument("input")

    ledger = commands.add_parser("import-ledger")
    ledger.add_argument("input")

    daily = commands.add_parser("settle-daily")
    daily.add_argument("period_key")

    weekly = commands.add_parser("settle-weekly")
    weekly.add_argument("target_date")

    export = commands.add_parser("export")
    export.add_argument("period_type", choices=("daily", "weekly"))
    export.add_argument("period_key")
    export.add_argument("--frontend", default="frontend")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "demo":
        paths = generate_demo(args.database, args.frontend)
        print("Generated demo charts:")
        for path in paths:
            print(path)
        return

    connection = connect(args.database)
    initialize(connection)
    try:
        if args.command == "init-db":
            print(f"Initialized {args.database}")
        elif args.command == "import-netease":
            period_id = import_netease_snapshot(connection, _load(args.input))
            print(f"Imported daily period {period_id}")
        elif args.command == "import-ledger":
            count = import_ledger_entries(connection, _load(args.input))
            print(f"Imported {count} ledger entries")
        elif args.command == "settle-daily":
            period_id = settle_daily(connection, args.period_key)
            print(f"Settled daily period {period_id}")
        elif args.command == "settle-weekly":
            period_id = settle_weekly(connection, args.target_date)
            print(f"Built weekly period {period_id}")
        elif args.command == "export":
            period = connection.execute(
                "SELECT id FROM chart_periods WHERE period_type=? AND period_key=?",
                (args.period_type, args.period_key),
            ).fetchone()
            if not period:
                raise ValueError(f"榜单周期不存在：{args.period_type}/{args.period_key}")
            path = export_period(connection, period["id"], args.frontend)
            print(f"Exported {path}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
