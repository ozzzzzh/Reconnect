"""手写 / 脚本化世界的导入（M2：先不接 LLM 生成世界）。

只导入客观部分：entities / holdings / events。
重复运行会跳过已存在的三元组与事件，便于在同一 DB 上反复调试。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store import Entity, Event, Store
from store.models import coerce_triple


def _normalize_source(source: str | None, unit_id: str | None) -> str:
    if source in (None, ""):
        return "world"
    if source == "unit" and unit_id:
        return f"unit:{unit_id}"
    return source


def import_world(store: Store, path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = data.get("meta", {}) or {}
    unit_id: str | None = meta.get("unit_id")
    day_index: int | None = meta.get("day_index")

    entities = data.get("entities", []) or []
    for item in entities:
        store.upsert_entity(Entity(**item))

    holdings = data.get("holdings", []) or []
    holding_count = 0
    for item in holdings:
        tid = item.get("id")
        if tid and store.get_triple(tid) is not None:
            continue
        t = coerce_triple(item)
        t.source = _normalize_source(t.source, unit_id)
        if t.unit_id is None and t.source.startswith(("unit:", "derived:")):
            t.unit_id = unit_id
        store.assert_triple(t)
        holding_count += 1

    raw_events = data.get("events")
    if raw_events is None:
        raw_events = [data["event"]] if "event" in data else []
    event_count = 0
    for item in raw_events:
        eid = item.get("id")
        if eid and store.get_event(eid) is not None:
            continue
        ev = Event(**item)
        if ev.day_index is None:
            ev.day_index = day_index
        ev.source = _normalize_source(ev.source, unit_id)
        store.apply_event(ev)
        event_count += 1

    return {
        "fixture": str(path),
        "day_index": day_index,
        "entities": len(entities),
        "holdings": holding_count,
        "events": event_count,
    }
