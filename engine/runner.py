"""run_day：M2 单日闭环。

事件 → 感知（attend + appraise）→ 记忆（compress）→ 日记（narrate），
每一步都写入 Store；重复运行会跳过已存在的产物。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from store import Diary, Event, Perception, Store, Triple
from store.timeutil import norm_ts, parse_ts

from .narrative import Narrator, OfflineNarrator
from .operators import appraise, attend, compress
from .personality import Personality, persist_personality
from .world import import_world

EMOTION_LABELS = {
    "anxiety": "焦虑",
    "压力": "压力",
    "focus": "专注",
    "satisfaction": "满足",
    "relief": "释然",
    "neutral": "平静",
}


@dataclass
class RunResult:
    day_index: int
    unit_id: str
    events: int
    perceptions: int
    memories: int
    diary_id: str
    diary_chars: int
    narrator: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plus_hours(ts: str, hours: int) -> str:
    return norm_ts(parse_ts(ts) + timedelta(hours=hours))


def _revise_state(
    store: Store,
    personality: Personality,
    *,
    name: str,
    value: Any,
    day_index: int,
    at_time: str,
) -> None:
    new_id = f"p_state_{name}_d{day_index}"
    if store.get_triple(new_id) is not None:
        return
    new = Triple(
        id=new_id,
        s=personality.unit_id,
        p=name,
        o=value,
        o_kind="literal",
        unit_id=personality.unit_id,
        source=f"unit:{personality.unit_id}",
        confidence=0.8,
        salience=0.6,
        asserted_at=at_time,
        valid_from=at_time,
    )
    old = store.get_triple(f"p_state_{name}")
    if old is not None and old.retracted_at is None:
        store.revise(old.id, new, reason=f"day {day_index} close")
    else:
        store.assert_triple(new)


def update_state(
    store: Store,
    personality: Personality,
    perceptions: list[Perception],
    *,
    day_index: int,
    at_time: str,
) -> None:
    if not perceptions:
        return
    last = max(perceptions, key=lambda p: (p.asserted_at, p.id))
    emotion = EMOTION_LABELS.get(str(last.affect.get("label", "neutral")), "平静")

    energy = float(personality.state.get("energy", 0.5))
    for pe in perceptions:
        delta = float(pe.affect.get("delta", 0) or 0)
        energy -= 0.04
        if delta < 0:
            energy -= 0.08
        elif delta > 0:
            energy += 0.05
    energy = round(max(0.0, min(1.0, energy)), 2)

    _revise_state(
        store, personality, name="emotion", value=emotion,
        day_index=day_index, at_time=at_time,
    )
    _revise_state(
        store, personality, name="energy", value=energy,
        day_index=day_index, at_time=at_time,
    )


def run_day(
    store: Store,
    *,
    world_path: str | Path,
    personality_path: str | Path,
    narrator: Narrator | None = None,
    day_index: int | None = None,
    display_offset: int = 8,
) -> RunResult:
    world = import_world(store, world_path)
    day_index = day_index if day_index is not None else world["day_index"]
    if day_index is None:
        raise ValueError("day_index is required (fixture meta or argument)")

    events = store.list_events(day_index)
    if not events:
        raise ValueError(f"no events for day {day_index}")

    personality = Personality.from_fixture(personality_path)
    start = events[0].time
    end = _plus_hours(events[-1].time, 2)
    persist_personality(store, personality, at_time=start)

    perceptions: list[Perception] = []
    for event in events:
        existing = store.perceptions_for_event(event.id)
        if existing:
            perceptions.extend(existing)
            continue
        attention = attend(personality, event, store)
        appraisal = appraise(personality, event, store, attention)
        perception = Perception(
            id=f"pe_{event.id}_{personality.unit_id}",
            event_id=event.id,
            unit_id=personality.unit_id,
            attended=attention.attended,
            transforms=attention.transforms,
            affect=appraisal.affect,
            confidence=appraisal.confidence,
            asserted_at=event.time,
        )
        store.record_perception(perception)
        perceptions.append(perception)

    events_by_id = {event.id: event for event in events}
    memories = compress(
        personality,
        perceptions,
        events_by_id,
        store,
        at_time=end,
        day_index=day_index,
    )
    for memory in memories:
        if store.get_triple(memory.triple.id) is None:
            store.assert_triple(
                memory.triple,
                memory_kind=memory.memory_kind,
                decay_rate=memory.decay_rate,
            )

    diary_id = f"di_{personality.unit_id}_{day_index}"
    diary = store.get_diary(diary_id)
    narrator_name = "existing"
    if diary is None:
        narrator = narrator or OfflineNarrator()
        narration = narrator.narrate(
            personality=personality,
            events=events,
            perceptions=perceptions,
            memories=memories,
            store=store,
            day_index=day_index,
            display_offset=display_offset,
        )
        store.log_llm_call(
            purpose="narrate",
            prompt=narration.prompt,
            response=narration.text,
            model=narration.model,
            unit_id=personality.unit_id,
            meta={
                "day_index": day_index,
                "offline": narration.offline,
                "prompt_version": "m2.v0",
                "operator_version": "rules-v0",
                "events": [event.id for event in events],
            },
            at=end,
        )
        diary = Diary(
            id=diary_id,
            unit_id=personality.unit_id,
            day_index=day_index,
            period_from=start,
            period_to=end,
            perception_ids=[pe.id for pe in perceptions],
            text=narration.text,
            created_at=end,
        )
        store.record_diary(diary)
        narrator_name = narration.model

    update_state(
        store,
        personality,
        perceptions,
        day_index=day_index,
        at_time=end,
    )

    return RunResult(
        day_index=day_index,
        unit_id=personality.unit_id,
        events=len(events),
        perceptions=len(perceptions),
        memories=len(memories),
        diary_id=diary.id,
        diary_chars=len(diary.text),
        narrator=narrator_name,
    )


def load_events_day(store: Store, day_index: int) -> list[Event]:
    """便捷函数：给 CLI / 测试查看当天事件。"""
    return store.list_events(day_index)
