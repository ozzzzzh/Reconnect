"""人格槽位 v0（D7 / Q6）：3 个特质 + 2 个需求 + 1 个未了之事。

声明性人格存为三元组（source=god / unit:<id>），算子只读取这个结构。
v0 用 JSON 作为种子；写入存储后即可用 ``inspect asof`` 审计。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from store import Store, Triple


@dataclass
class OpenLoop:
    target: str
    label: str = ""
    urgency: float = 0.5

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("open_loop.target is required")
        self.label = self.label or self.target
        self.urgency = float(self.urgency)
        if not 0.0 <= self.urgency <= 1.0:
            raise ValueError("open_loop.urgency must be within [0, 1]")


@dataclass
class Personality:
    unit_id: str
    name: str
    traits: list[str]
    needs: list[str]
    open_loops: list[OpenLoop]
    state: dict[str, Any] = field(default_factory=dict)
    style: str = ""

    def __post_init__(self) -> None:
        if not self.unit_id or not self.name:
            raise ValueError("personality.unit_id / name are required")
        self.traits = [str(x).strip() for x in self.traits if str(x).strip()]
        self.needs = [str(x).strip() for x in self.needs if str(x).strip()]
        self.open_loops = [
            x if isinstance(x, OpenLoop) else OpenLoop(**x) for x in self.open_loops
        ]
        if len(self.traits) != 3:
            raise ValueError("personality v0 requires exactly 3 traits")
        if len(self.needs) != 2:
            raise ValueError("personality v0 requires exactly 2 needs")
        if len(self.open_loops) != 1:
            raise ValueError("personality v0 requires exactly 1 open loop")
        self.state = dict(self.state)
        energy = float(self.state.get("energy", 0.5))
        if not 0.0 <= energy <= 1.0:
            raise ValueError("personality.state.energy must be within [0, 1]")
        self.state["energy"] = energy
        self.state.setdefault("emotion", "平静")

    @classmethod
    def from_fixture(cls, path: str | Path) -> "Personality":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def has_trait(self, trait: str) -> bool:
        return trait in self.traits

    def open_loop_targets(self) -> set[str]:
        return {x.target for x in self.open_loops}

    def open_loop_urgency(self, target: str) -> float:
        for x in self.open_loops:
            if x.target == target:
                return x.urgency
        return 0.0

    def open_loop_label(self, target: str) -> str:
        for x in self.open_loops:
            if x.target == target:
                return x.label
        return target


def persist_personality(
    store: Store, personality: Personality, *, at_time: str
) -> int:
    """把人格槽位写成三元组；已存在的槽位跳过（可重复运行）。"""
    if store.get_entity(personality.unit_id) is None:
        raise ValueError(f"unknown unit: {personality.unit_id}")

    base = dict(
        unit_id=personality.unit_id,
        source="god",
        confidence=1.0,
        asserted_at=at_time,
        valid_from=at_time,
    )
    triples: list[Triple] = []
    for i, trait in enumerate(personality.traits):
        triples.append(
            Triple(
                id=f"p_trait_{i}", s=personality.unit_id, p="has_trait",
                o=trait, o_kind="literal", salience=1.0, **base,
            )
        )
    for i, need in enumerate(personality.needs):
        triples.append(
            Triple(
                id=f"p_need_{i}", s=personality.unit_id, p="has_need",
                o=need, o_kind="literal", salience=1.0, **base,
            )
        )
    for i, loop in enumerate(personality.open_loops):
        triples.append(
            Triple(
                id=f"p_loop_{i}", s=personality.unit_id, p="has_open_loop",
                o=loop.target, o_kind="entity", salience=loop.urgency, **base,
            )
        )

    state_base = dict(base)
    state_base.update(source=f"unit:{personality.unit_id}", salience=0.6)
    triples.append(
        Triple(
            id="p_state_emotion", s=personality.unit_id, p="emotion",
            o=str(personality.state["emotion"]), o_kind="literal", **state_base,
        )
    )
    triples.append(
        Triple(
            id="p_state_energy", s=personality.unit_id, p="energy",
            o=personality.state["energy"], o_kind="literal", **state_base,
        )
    )

    written = 0
    for t in triples:
        if store.get_triple(t.id) is None:
            store.assert_triple(t)
            written += 1
    return written
