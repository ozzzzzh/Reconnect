"""跑完 Reconnect 的一天（M2 单日闭环）。

用法：
    python tools/run_day.py --reset
    python tools/run_day.py --narrator offline --reset
    python tools/run_day.py --narrator claude     # 需要 pip install anthropic 与凭证
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import make_narrator, run_day  # noqa: E402
from store import SqliteStore  # noqa: E402


def _reset_db(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Reconnect day")
    parser.add_argument("--db", default=str(ROOT / "build" / "life.db"))
    parser.add_argument(
        "--world", default=str(ROOT / "examples" / "world_day1.json")
    )
    parser.add_argument(
        "--personality", default=str(ROOT / "examples" / "personality_u1.json")
    )
    parser.add_argument(
        "--narrator", choices=("auto", "claude", "offline"), default="auto"
    )
    parser.add_argument("--day", type=int, default=None)
    parser.add_argument("--display-offset", type=int, default=8)
    parser.add_argument("--reset", action="store_true", help="delete the db first")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if args.reset:
        _reset_db(db_path)

    narrator = make_narrator(args.narrator)
    with SqliteStore(db_path) as store:
        result = run_day(
            store,
            world_path=args.world,
            personality_path=args.personality,
            narrator=narrator,
            day_index=args.day,
            display_offset=args.display_offset,
        )
        diary = store.get_diary(result.diary_id)
        stats = store.stats()

    print(json.dumps({"result": result.to_dict(), "stats": stats},
                     ensure_ascii=False, indent=2))
    print(f"\ndb: {db_path.resolve()}")
    if diary is not None:
        print(f"\n--- {diary.id} ---\n{diary.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
