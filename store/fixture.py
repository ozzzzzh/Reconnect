"""导入手写日 fixture（M0 契约样例）。

fixture 的键与 docs/EXECUTION.md §3.8 对齐，允许 `event` / `events`、
`perception` / `perceptions` 两种写法，便于单日样例与多事件样例共存。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Diary, Entity, Event, Perception, coerce_triple
from .protocol import Store


def _normalize_source(source: str | None, unit_id: str | None) -> str:
    if source in (None, ""):
        return "world"
    if source == "unit" and unit_id:
        return f"unit:{unit_id}"
    return source


def import_fixture(store: Store, path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = data.get("meta", {}) or {}
    unit_id: str | None = meta.get("unit_id")
    day_index: int | None = meta.get("day_index")

    entities = data.get("entities", []) or []
    for item in entities:
        store.upsert_entity(Entity(**item))

    holdings = data.get("holdings", []) or []
    for item in holdings:
        t = coerce_triple(item)
        t.source = _normalize_source(t.source, unit_id)
        if t.unit_id is None and t.source.startswith(("unit:", "derived:")):
            t.unit_id = unit_id
        store.assert_triple(t)

    raw_events = data.get("events")
    if raw_events is None:
        raw_events = [data["event"]] if "event" in data else []
    for item in raw_events:
        ev = Event(**item)
        if ev.day_index is None:
            ev.day_index = day_index
        ev.source = _normalize_source(ev.source, unit_id)
        store.apply_event(ev)

    raw_perceptions = data.get("perceptions")
    if raw_perceptions is None:
        raw_perceptions = [data["perception"]] if "perception" in data else []
    for item in raw_perceptions:
        store.record_perception(Perception(**item))

    memory_delta = data.get("memory_delta") or {}
    for item in memory_delta.get("add", []):
        payload = {
            k: v for k, v in item.items() if k not in ("memory_kind", "decay_rate")
        }
        t = coerce_triple(payload)
        t.source = _normalize_source(t.source, unit_id)
        if t.unit_id is None:
            t.unit_id = unit_id
        store.assert_triple(
            t,
            memory_kind=item.get("memory_kind"),
            decay_rate=item.get("decay_rate"),
        )

    diary_data = data.get("diary")
    if diary_data:
        d = Diary(**diary_data)
        if d.day_index is None:
            d.day_index = day_index
        store.record_diary(d)

    return {
        "fixture": str(path),
        "day_index": day_index,
        "entities": len(entities),
        "holdings": len(holdings),
        "events": len(raw_events),
        "perceptions": len(raw_perceptions),
        "memory_deltas": len((memory_delta.get("add") or [])),
        "diary": bool(diary_data),
    }
