"""数据模型：与 schema.sql 一一对应的 dataclass。

这些类只做校验与序列化，不持有数据库连接。所有时间字段在构造时归一化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .timeutil import norm_ts, utc_now

ENTITY_TYPES = ("Unit", "Person", "Place", "Object", "Concept", "Time")
EVENT_TYPES = ("occurrence", "state_change", "message", "god")
O_KINDS = ("entity", "literal")
MEMORY_KINDS = ("imprint", "semantic", "grey")


def _as_json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _check_unit_interval(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value)!r}")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within [0, 1], got {value}")
    return value


@dataclass
class Entity:
    id: str
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    created_at: str | None = None
    merged_into: str | None = None
    merged_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entity.id is required")
        if self.type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity type: {self.type!r}")
        if not self.name:
            raise ValueError("entity.name is required")
        self.created_at = norm_ts(self.created_at or utc_now())
        if self.merged_at is not None:
            self.merged_at = norm_ts(self.merged_at)
        # 去重并保留顺序
        self.aliases = list(dict.fromkeys(a.strip() for a in self.aliases if a.strip()))

    @classmethod
    def from_row(cls, row: Any) -> "Entity":
        d = dict(row)
        return cls(
            id=d["id"],
            type=d["type"],
            name=d["name"],
            created_at=d["created_at"],
            merged_into=d.get("merged_into"),
            merged_at=d.get("merged_at"),
        )


@dataclass
class Triple:
    """一条 Holding（带有效区间的状态断言）。

    `unit_id` 为 NULL 表示客观世界；非 NULL 表示某个 Unit 的私有信念。
    `o_kind='literal'` 时 `o` 是字面量（如 true / "她累了"），不解析为实体。
    """

    id: str
    s: str
    p: str
    o: str
    o_kind: str = "entity"
    unit_id: str | None = None
    event_id: str | None = None
    source: str = "world"
    confidence: float = 1.0
    salience: float = 1.0
    valid_from: str | None = None
    valid_to: str | None = None
    asserted_at: str | None = None
    retracted_at: str | None = None
    superseded_by: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("triple.id is required")
        if not self.s or not self.p:
            raise ValueError("triple.s / triple.p are required")
        if self.o is None:
            raise ValueError("triple.o is required")
        if self.o_kind not in O_KINDS:
            raise ValueError(f"unknown o_kind: {self.o_kind!r}")
        if not self.source:
            raise ValueError("triple.source is required")
        self.o = _as_json_text(self.o)
        self.confidence = _check_unit_interval(self.confidence, "confidence")
        self.salience = _check_unit_interval(self.salience, "salience")

        now = utc_now()
        if self.asserted_at is None:
            self.asserted_at = now
        if self.valid_from is None:
            self.valid_from = self.asserted_at

        self.asserted_at = norm_ts(self.asserted_at)
        self.valid_from = norm_ts(self.valid_from)
        if self.valid_to is not None:
            self.valid_to = norm_ts(self.valid_to)
        if self.retracted_at is not None:
            self.retracted_at = norm_ts(self.retracted_at)

        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be >= valid_from")

    @classmethod
    def from_row(cls, row: Any) -> "Triple":
        d = dict(row)
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: d[k] for k in names if k in d})


@dataclass
class Event:
    """一次事件 = 状态快照的差分 + 元数据（§3.3）。"""

    id: str
    time: str
    occurs: str
    type: str = "occurrence"
    roles: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, list] = field(default_factory=lambda: {"add": [], "retract": []})
    causal_parent: str | None = None
    source: str = "world"
    raw_text: str | None = None
    day_index: int | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("event.id is required")
        if not self.occurs:
            raise ValueError("event.occurs is required")
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {self.type!r}")
        if not self.source:
            raise ValueError("event.source is required")
        self.time = norm_ts(self.time)
        self.created_at = norm_ts(self.created_at or utc_now())
        if self.day_index is not None and not isinstance(self.day_index, int):
            raise TypeError("day_index must be int or None")
        self.delta = {
            "add": list(self.delta.get("add", []) or []),
            "retract": list(self.delta.get("retract", []) or []),
        }

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        d = dict(row)
        return cls(
            id=d["id"],
            time=d["time"],
            occurs=d["occurs"],
            type=d["type"],
            roles=json.loads(d["roles"] or "{}"),
            delta=json.loads(d["delta"] or '{"add":[],"retract":[]}'),
            causal_parent=d.get("causal_parent"),
            source=d["source"],
            raw_text=d.get("raw_text"),
            day_index=d.get("day_index"),
            created_at=d["created_at"],
        )


@dataclass
class Perception:
    """Unit 对某个客观事件的有偏投射（§3.4）。"""

    id: str
    event_id: str
    unit_id: str
    attended: dict[str, float] = field(default_factory=dict)
    transforms: list[dict[str, Any]] = field(default_factory=list)
    affect: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    asserted_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("perception.id is required")
        if not self.event_id or not self.unit_id:
            raise ValueError("perception.event_id / unit_id are required")
        self.confidence = _check_unit_interval(self.confidence, "confidence")
        self.asserted_at = norm_ts(self.asserted_at or utc_now())

    @classmethod
    def from_row(cls, row: Any) -> "Perception":
        d = dict(row)
        return cls(
            id=d["id"],
            event_id=d["event_id"],
            unit_id=d["unit_id"],
            attended=json.loads(d["attended"] or "{}"),
            transforms=json.loads(d["transforms"] or "[]"),
            affect=json.loads(d["affect"] or "{}"),
            confidence=d["confidence"],
            asserted_at=d["asserted_at"],
        )


@dataclass
class Diary:
    """日记：只写不读的原始记录（§3.5）。"""

    id: str
    unit_id: str
    text: str
    day_index: int | None = None
    period_from: str | None = None
    period_to: str | None = None
    perception_ids: list[str] = field(default_factory=list)
    created_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("diary.id is required")
        if not self.unit_id:
            raise ValueError("diary.unit_id is required")
        if not self.text or not self.text.strip():
            raise ValueError("diary.text is required")
        now = utc_now()
        self.period_from = norm_ts(self.period_from or now)
        self.period_to = norm_ts(self.period_to or self.period_from)
        if self.period_to < self.period_from:
            raise ValueError("period_to must be >= period_from")
        self.created_at = norm_ts(self.created_at or now)

    @classmethod
    def from_row(cls, row: Any) -> "Diary":
        d = dict(row)
        return cls(
            id=d["id"],
            unit_id=d["unit_id"],
            text=d["text"],
            day_index=d.get("day_index"),
            period_from=d["period_from"],
            period_to=d["period_to"],
            perception_ids=json.loads(d["perception_ids"] or "[]"),
            created_at=d["created_at"],
        )


@dataclass
class Memory:
    """对一条 triple 的记忆分类（§3.7）。"""

    triple_id: str
    kind: str
    decay_rate: float | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {self.kind!r}")
        if self.decay_rate is not None and not 0.0 <= float(self.decay_rate) <= 1.0:
            raise ValueError("decay_rate must be within [0, 1]")
        self.updated_at = norm_ts(self.updated_at or utc_now())
