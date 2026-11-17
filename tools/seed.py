"""把 examples/*.json 导入一个 SQLite 库，便于 inspect。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from store.fixture import import_fixture  # noqa: E402
from store.sqlite_store import SqliteStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a Reconnect SQLite store from a fixture")
    parser.add_argument("--db", default=str(ROOT / "build" / "day1.db"))
    parser.add_argument("--fixture", default=str(ROOT / "examples" / "day1.json"))
    parser.add_argument("--reset", action="store_true", help="delete the db file first")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()

    with SqliteStore(db_path) as store:
        summary = import_fixture(store, args.fixture)
        stats = store.stats()

    print(json.dumps({"imported": summary, "stats": stats}, ensure_ascii=False, indent=2))
    print(f"\ndb: {db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
