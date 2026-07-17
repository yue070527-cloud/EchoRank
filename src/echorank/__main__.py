from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .database import connect, initialize
from .demo import generate_demo
from .export import export_period
from .netease import collect_weekly_snapshot
from .settlement import (
    import_ledger_entries,
    import_netease_snapshot,
    settle_daily,
    settle_weekly,
)
from .workflow import update_charts


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="echorank")
    parser.add_argument("--database", default="data/echorank.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")

    update = commands.add_parser("update")
    update.add_argument("--config", default="data/echorank-config.json")
    update.add_argument("--raw-root", default="data/raw/netease")
    update.add_argument("--frontend", default="frontend")
    update.add_argument("--timeout", type=float, default=20)

    demo = commands.add_parser("demo")
    demo.add_argument("--frontend", default="frontend")

    netease = commands.add_parser("import-netease")
    netease.add_argument("input")

    collect_netease = commands.add_parser("collect-netease")
    collect_netease.add_argument("uid")
    collect_netease.add_argument("--period-key", default=date.today().isoformat())
    collect_netease.add_argument("--raw-root", default="data/raw/netease")
    collect_netease.add_argument("--timeout", type=float, default=20)

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
    if args.command == "update":
        result = update_charts(
            args.config,
            args.database,
            args.raw_root,
            args.frontend,
            timeout=args.timeout,
        )
        action = "已采集并更新" if result.collected else "已使用今日数据更新"
        print(f"{action} EchoRank")
        print(f"日榜：{result.period_key}（{result.entry_count} 条）")
        print(f"周榜：{result.week_key}")
        print(f"日榜文件：{result.daily_path}")
        print(f"周榜文件：{result.weekly_path}")
        return
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
        elif args.command == "collect-netease":
            payload, archive_path = collect_weekly_snapshot(
                args.uid,
                args.period_key,
                args.raw_root,
                args.timeout,
            )
            period_id = import_netease_snapshot(connection, payload)
            print(f"Collected NetEase weekly ranking for UID {args.uid}")
            print(f"Period: {args.period_key}")
            print(f"Entries: {len(payload['entries'])}")
            print(f"Raw snapshot: {archive_path}")
            print(f"Imported daily period {period_id}")
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
