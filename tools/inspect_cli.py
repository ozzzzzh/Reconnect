"""inspect CLI：查看某日状态、事件因果链、实体事实、as_of 视图。

用法（--db 可放在子命令前后）：
    python tools/inspect_cli.py day 1 --db build/day1.db
    python tools/inspect_cli.py event e2 --db build/day1.db
    python tools/inspect_cli.py entity 周姐 --db build/day1.db
    python tools/inspect_cli.py asof 2026-11-01T15:00:00Z --scope view:u1 --db build/day1.db
    python tools/inspect_cli.py stats --db build/day1.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from store.models import Diary, Event, Perception, Triple  # noqa: E402
from store.sqlite_store import SqliteStore  # noqa: E402

DEFAULT_DB = str(ROOT / "build" / "day1.db")


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fmt_triple(t: Triple) -> str:
    obj = t.o if t.o_kind == "entity" else f'"{t.o}"'
    owner = t.unit_id or "world"
    if t.superseded_by:
        status = f"superseded -> {t.superseded_by}"
    elif t.retracted_at:
        status = f"retracted @ {t.retracted_at}"
    else:
        status = "active"
    valid_to = t.valid_to or "∞"
    return (
        f"  {t.s} --{t.p}--> {obj}  "
        f"[{owner} | {t.source}] "
        f"conf={t.confidence:.2f} sal={t.salience:.2f} "
        f"valid=[{t.valid_from}, {valid_to}) asserted={t.asserted_at} "
        f"({status}) id={t.id}"
    )


def fmt_event(e: Event) -> str:
    parent = f" parent={e.causal_parent}" if e.causal_parent else ""
    return (
        f"  [{e.id}] {e.time}  {e.type}/{e.occurs}  source={e.source}"
        f" day={e.day_index}{parent}\n"
        f"      roles={_j(e.roles)}\n"
        f"      add={len(e.delta['add'])} retract={e.delta['retract']}"
        + (f"\n      \"{e.raw_text}\"" if e.raw_text else "")
    )


def fmt_perception(p: Perception) -> str:
    return (
        f"  [{p.id}] unit={p.unit_id} <- {p.event_id}  "
        f"conf={p.confidence:.2f}\n"
        f"      attended={_j(p.attended)}\n"
        f"      affect={_j(p.affect)}\n"
        f"      transforms={_j(p.transforms)}"
    )


def fmt_diary(d: Diary) -> str:
    return (
        f"  [{d.id}] unit={d.unit_id} day={d.day_index} "
        f"period=[{d.period_from}, {d.period_to}]\n"
        f"  perceptions={_j(d.perception_ids)}\n\n"
        + "\n".join("    " + line for line in d.text.splitlines())
    )


def cmd_day(store: SqliteStore, args: argparse.Namespace) -> None:
    snap = store.day_snapshot(args.day)
    print(f"=== day {args.day} ===")
    print(
        f"events={len(snap['events'])} perceptions={len(snap['perceptions'])} "
        f"diaries={len(snap['diaries'])} triples={len(snap['triples'])}"
    )

    print("\n-- events --")
    for e in snap["events"]:
        print(fmt_event(e))

    print("\n-- perceptions --")
    for p in snap["perceptions"]:
        print(fmt_perception(p))

    print("\n-- diaries --")
    if not snap["diaries"]:
        print("  (none)")
    for d in snap["diaries"]:
        print(fmt_diary(d))

    print("\n-- triples asserted this day --")
    if not snap["triples"]:
        print("  (none)")
    for t in snap["triples"]:
        print(fmt_triple(t))


def cmd_event(store: SqliteStore, args: argparse.Namespace) -> None:
    e = store.get_event(args.event)
    if e is None:
        raise SystemExit(f"event not found: {args.event}")
    print(f"=== event {e.id} ===")
    print(fmt_event(e))

    print("\n-- causal chain (root -> leaf) --")
    chain = store.event_chain(e.id)
    if len(chain) <= 1:
        print("  (no parent)")
    else:
        for idx, item in enumerate(chain):
            arrow = "  " if idx == 0 else "  -> "
            print(f"{arrow}{item.id}  {item.time}  {item.occurs}")

    print("\n-- perceptions --")
    rows = store.perceptions_for_event(e.id)
    if not rows:
        print("  (none)")
    for p in rows:
        print(fmt_perception(p))

    print("\n-- delta triples --")
    triples = store.query({"event_id": e.id}, include_retracted=True)
    if not triples:
        print("  (none)")
    for t in triples:
        print(fmt_triple(t))


def cmd_entity(store: SqliteStore, args: argparse.Namespace) -> None:
    resolved = store.resolve_entity(args.entity)
    if resolved is None:
        raise SystemExit(f"entity not found: {args.entity}")
    entity = store.get_entity(resolved)
    print(f"=== entity {entity.id} ({entity.type}) {entity.name} ===")
    print(f"  aliases={_j(entity.aliases)}")
    if entity.merged_into:
        print(f"  merged_into={entity.merged_into} @ {entity.merged_at}")

    as_subject = store.query(
        {"s": resolved}, include_retracted=args.include_retracted
    )
    as_object = store.query(
        {"o": resolved, "o_kind": "entity"},
        include_retracted=args.include_retracted,
    )
    seen: set[str] = set()
    facts: list[Triple] = []
    for t in as_subject + as_object:
        if t.id not in seen:
            seen.add(t.id)
            facts.append(t)

    print(f"\n-- facts ({len(facts)}) --")
    if not facts:
        print("  (none)")
    for t in facts:
        print(fmt_triple(t))


def cmd_asof(store: SqliteStore, args: argparse.Namespace) -> None:
    triples = store.query(
        {},
        as_of=args.as_of,
        at=args.at,
        scope=args.scope,
        include_retracted=args.include_retracted,
    )
    print(
        f"=== belief view as_of={args.as_of or '-'} "
        f"at={args.at or '-'} scope={args.scope or 'all'} ==="
    )
    print(f"triples={len(triples)}\n")
    for t in triples:
        print(fmt_triple(t))


def cmd_stats(store: SqliteStore, args: argparse.Namespace) -> None:
    print(_j(store.stats()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a Reconnect store")
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def with_db(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--db", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return p

    p_day = with_db(sub.add_parser("day", help="show one day"))
    p_day.add_argument("day", type=int)
    p_day.set_defaults(func=cmd_day)

    p_event = with_db(sub.add_parser("event", help="show an event and its chain"))
    p_event.add_argument("event")
    p_event.set_defaults(func=cmd_event)

    p_entity = with_db(sub.add_parser("entity", help="show an entity and its facts"))
    p_entity.add_argument("entity")
    p_entity.add_argument("--include-retracted", action="store_true")
    p_entity.set_defaults(func=cmd_entity)

    p_asof = with_db(sub.add_parser("asof", help="query a belief view"))
    p_asof.add_argument("as_of", help="assertion time, e.g. 2026-11-01T15:00:00Z")
    p_asof.add_argument("--at", default=None, help="world time for valid interval")
    p_asof.add_argument("--scope", default=None, help="world | unit:<id> | view:<id>")
    p_asof.add_argument("--include-retracted", action="store_true")
    p_asof.set_defaults(func=cmd_asof)

    p_stats = with_db(sub.add_parser("stats", help="row counts"))
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(
            f"db not found: {db_path}\n先运行: python tools/seed.py --db {db_path}"
        )
    with SqliteStore(db_path, init=False) as store:
        args.func(store, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
