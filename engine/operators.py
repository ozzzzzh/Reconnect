"""M2 算子 v0：attend / appraise / compress。

D5：LLM 只做语义叙述（narrate）；这三个算子用可解释规则实现。
D9：输出全部落盘——attend/appraise 的结果落在 ``Perception``，
compress 的结果落在 ``triple`` + ``memory``。

v0 的规则刻意保持确定性、可审计。以后可以逐项替换为更细的评估
（相关性 / 目标一致性 / 归因 / 新奇 / 应对能力），存储层不需要改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from store import Event, Perception, Store, Triple

from .personality import Personality

ATTENTION_BUDGET = 4

ROLE_WEIGHTS = {
    "agent": 0.55,
    "sender": 0.60,
    "recipient": 0.45,
    "theme": 0.60,
    "place": 0.30,
    "instrument": 0.30,
}

WORK_EVENTS = {"work_on", "organize", "plan", "finish", "write"}
RELAX_EVENTS = {"open", "rest", "play", "relax"}

NEGATIVE_HINTS = ("提前", "催", "加班", "改期", "来不及", "批评", "扣钱")
POSITIVE_HINTS = ("完成", "搞定", "通过", "表扬", "写完", "顺利", "认可")


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def entity_name(store: Store, ref: str) -> str:
    rid = store.resolve_entity(str(ref)) or str(ref)
    entity = store.get_entity(rid)
    return entity.name if entity else str(ref)


def _as_entity(store: Store, ref: Any) -> str | None:
    if not isinstance(ref, str) or not ref:
        return None
    return store.resolve_entity(ref)


def _theme_name(store: Store, event: Event | None) -> str:
    if event is None:
        return ""
    for role in ("theme", "object", "instrument"):
        ref = (event.roles or {}).get(role)
        if ref:
            return entity_name(store, ref)
    return ""


@dataclass
class Attention:
    attended: dict[str, float]
    transforms: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Appraisal:
    affect: dict[str, Any]
    dimensions: dict[str, Any]
    attribution: str
    confidence: float


@dataclass
class MemoryDraft:
    triple: Triple
    memory_kind: str
    decay_rate: float | None = None


# --------------------------------------------------------------------- attend
def attend(personality: Personality, event: Event, store: Store) -> Attention:
    """有限注意力的有偏透镜：只留下 3～4 个实体，并施加扭曲。"""
    candidates: dict[str, float] = {}

    for role, ref in (event.roles or {}).items():
        if role == "channel":
            continue
        eid = _as_entity(store, ref)
        if eid:
            candidates[eid] = max(
                candidates.get(eid, 0.0), ROLE_WEIGHTS.get(role, 0.40)
            )

    for item in event.delta.get("add", []):
        for key in ("s", "o"):
            if key == "o" and item.get("o_kind", "entity") != "entity":
                continue
            eid = _as_entity(store, item.get(key))
            if eid:
                candidates[eid] = max(candidates.get(eid, 0.0), 0.40)

    for triple_id in event.delta.get("retract", []):
        tri = store.get_triple(triple_id)
        if tri is None:
            continue
        eid = _as_entity(store, tri.s)
        if eid:
            candidates[eid] = max(candidates.get(eid, 0.0), 0.35)
        if tri.o_kind == "entity":
            eid = _as_entity(store, tri.o)
            if eid:
                candidates[eid] = max(candidates.get(eid, 0.0), 0.35)

    loop_targets = personality.open_loop_targets()
    for eid in list(candidates):
        score = candidates[eid]
        if eid in loop_targets:
            score += 0.25 * personality.open_loop_urgency(eid)

        entity = store.get_entity(eid)
        if entity and entity.type == "Person" and personality.has_trait("敏感"):
            score += 0.08

        facts = store.query(
            {"s": eid}, as_of=event.time, at=event.time, limit=10
        )
        facts += store.query(
            {"o": eid, "o_kind": "entity"},
            as_of=event.time, at=event.time, limit=10,
        )
        if facts:
            score += min(0.12, max(t.salience for t in facts) * 0.10)
        candidates[eid] = _clamp(score)

    ranked = sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0]))[
        :ATTENTION_BUDGET
    ]
    attended = {eid: round(score, 2) for eid, score in ranked}

    transforms: list[dict[str, Any]] = []
    for target in sorted(loop_targets & set(attended)):
        transforms.append(
            {
                "op": "amplify",
                "target": target,
                "factor": round(1.0 + 0.5 * personality.open_loop_urgency(target), 2),
                "reason": f"未了之事:{personality.open_loop_label(target)}",
            }
        )

    if personality.has_trait("敏感") and event.type == "message":
        for eid in attended:
            entity = store.get_entity(eid)
            if entity and entity.type == "Person":
                transforms.append(
                    {
                        "op": "infer",
                        "target": eid,
                        "to": f"{entity.name}是故意的",
                        "confidence": 0.35,
                    }
                )
                break

    if "放松" in personality.needs and event.occurs in RELAX_EVENTS:
        for eid in attended:
            entity = store.get_entity(eid)
            if entity and entity.type in {"Object", "Place"}:
                transforms.append(
                    {
                        "op": "amplify",
                        "target": eid,
                        "factor": 1.2,
                        "reason": "放松需求长期未满足",
                    }
                )
                break

    theme = _as_entity(store, (event.roles or {}).get("theme"))
    if (
        personality.has_trait("尽责")
        and event.occurs in WORK_EVENTS
        and theme in attended
    ):
        transforms.append({"op": "rewrite", "target": theme, "to": "控制感回归"})

    return Attention(attended=attended, transforms=transforms)


# -------------------------------------------------------------------- appraise
def appraise(
    personality: Personality,
    event: Event,
    store: Store,
    attention: Attention | None = None,
) -> Appraisal:
    """五维评估的 v0 近似：相关性、目标一致性、归因、新奇、应对能力。"""
    attended = attention.attended if attention else {}
    relevance = max(attended.values(), default=0.30)
    text = event.raw_text or ""
    loop_targets = personality.open_loop_targets()

    goal = 0.50
    for hint in NEGATIVE_HINTS:
        if hint in text:
            goal -= 0.30 if hint == "提前" else 0.20
            break

    if event.delta.get("retract"):
        loss = 0.0
        for triple_id in event.delta["retract"]:
            tri = store.get_triple(triple_id)
            if tri is None:
                continue
            if tri.salience >= 0.6:
                loss += 0.10
            if tri.s in loop_targets or (
                tri.o_kind == "entity" and tri.o in loop_targets
            ):
                loss += 0.10
        goal -= min(0.25, loss)

    for item in event.delta.get("add", []):
        touched_loop = item.get("s") in loop_targets or (
            item.get("o_kind", "entity") == "entity"
            and item.get("o") in loop_targets
        )
        if touched_loop:
            goal += 0.10
            break

    if event.occurs in WORK_EVENTS and personality.has_trait("尽责"):
        goal += 0.15
    if "放松" in personality.needs and event.occurs in RELAX_EVENTS:
        goal += 0.20
    if "被认可" in personality.needs and any(
        hint in text for hint in ("表扬", "认可", "做得好")
    ):
        goal += 0.20
    if any(hint in text for hint in POSITIVE_HINTS):
        goal += 0.15
    goal = _clamp(goal)

    person_ids = [
        eid
        for eid in attended
        if (entity := store.get_entity(eid)) and entity.type == "Person"
    ]
    negative = goal < 0.45
    attribution = (
        "external_intentional"
        if negative and person_ids and personality.has_trait("敏感")
        else "situational"
    )

    new_entities = 0
    for eid in attended:
        prior = 0
        for key in ("s", "o"):
            pattern: dict[str, Any] = {key: eid}
            if key == "o":
                pattern["o_kind"] = "entity"
            for tri in store.query(
                pattern, as_of=event.time, at=event.time, limit=20
            ):
                if tri.event_id != event.id and tri.asserted_at < event.time:
                    prior += 1
        if prior == 0:
            new_entities += 1
    novelty = _clamp(0.20 + 0.20 * new_entities)

    energy = float(personality.state.get("energy", 0.5))
    coping = _clamp(
        0.15
        + 0.60 * energy
        + (0.10 if personality.has_trait("尽责") else 0.0)
        + (0.05 if personality.has_trait("克制") else 0.0)
    )

    if goal < 0.45 and relevance >= 0.5:
        label = "anxiety" if coping < 0.65 else "压力"
        delta = -(0.25 + 0.60 * relevance * (0.50 - goal))
        if "提前" in text:
            reason = "截止日期提前,失控感"
        elif event.delta.get("retract"):
            reason = "熟悉的东西被拿走了"
        else:
            reason = "事情偏离了预期"
    elif goal >= 0.60 and relevance >= 0.35:
        if "放松" in personality.needs and event.occurs in RELAX_EVENTS:
            label, delta, reason = "satisfaction", 0.35, "终于可以放松一下"
        elif event.occurs in WORK_EVENTS:
            label, delta, reason = "focus", 0.20, "通过行动恢复控制感"
        else:
            label, delta, reason = "relief", 0.20, "事情比预想的顺"
    else:
        label, delta, reason = "neutral", (goal - 0.50) * 0.30, "没什么特别"

    dimensions = {
        "relevance": round(relevance, 2),
        "goal_congruence": round(goal, 2),
        "attribution": attribution,
        "novelty": round(novelty, 2),
        "coping": round(coping, 2),
    }
    affect = {
        "label": label,
        "delta": round(_clamp(delta, -1.0, 1.0), 2),
        "reason": reason,
        "attribution": attribution,
        "appraisal": dimensions,
        "operator": "appraise-v0",
    }
    confidence = round(_clamp(0.55 + 0.30 * relevance), 2)
    return Appraisal(
        affect=affect,
        dimensions=dimensions,
        attribution=attribution,
        confidence=confidence,
    )


# -------------------------------------------------------------------- compress
def _memory_triple(
    personality: Personality,
    *,
    predicate: str,
    text: str,
    confidence: float,
    salience: float,
    at_time: str,
) -> Triple:
    return Triple(
        id="tmp_mem",
        s=personality.unit_id,
        p=predicate,
        o=text,
        o_kind="literal",
        unit_id=personality.unit_id,
        source="derived:compress",
        confidence=confidence,
        salience=salience,
        asserted_at=at_time,
        valid_from=at_time,
    )


def compress(
    personality: Personality,
    perceptions: list[Perception],
    events_by_id: dict[str, Event],
    store: Store,
    *,
    at_time: str,
    day_index: int,
) -> list[MemoryDraft]:
    """把感知压成信念：刻痕（永不衰减）+ 语义记忆（会衰减）。

    压缩是有损的：只留下与需求/未了之事相关的几件事，其余坍缩掉。
    """
    candidates: list[tuple[int, float, str, MemoryDraft]] = []

    for pe in sorted(perceptions, key=lambda p: (p.asserted_at, p.id)):
        affect = pe.affect or {}
        delta = float(affect.get("delta", 0) or 0)
        event = events_by_id.get(pe.event_id)
        attended = list((pe.attended or {}).keys())
        person_name = None
        for eid in attended:
            entity = store.get_entity(eid)
            if entity and entity.type == "Person":
                person_name = entity.name
                break
        theme_name = _theme_name(store, event)

        if delta <= -0.30:
            if affect.get("attribution") == "external_intentional" and person_name:
                text = f"{person_name}在针对我"
                confidence, salience = 0.45, 0.70
            else:
                text = f"{theme_name or '那件事'}让我不安"
                confidence, salience = 0.40, 0.60
            candidates.append(
                (
                    3,
                    abs(delta),
                    pe.asserted_at,
                    MemoryDraft(
                        triple=_memory_triple(
                            personality, predicate="believes", text=text,
                            confidence=confidence, salience=salience,
                            at_time=at_time,
                        ),
                        memory_kind="semantic",
                        decay_rate=0.05,
                    ),
                )
            )
            if delta <= -0.50:
                body = (
                    f"{person_name}消息跳出来时,心跳漏了一拍"
                    if person_name
                    else "那天有一下,心跳漏了一拍"
                )
                candidates.append(
                    (
                        4,
                        abs(delta),
                        pe.asserted_at,
                        MemoryDraft(
                            triple=_memory_triple(
                                personality, predicate="core_memory", text=body,
                                confidence=0.90, salience=1.0, at_time=at_time,
                            ),
                            memory_kind="imprint",
                            decay_rate=None,
                        ),
                    )
                )
        elif delta >= 0.30:
            if "放松" in personality.needs and event and event.occurs in RELAX_EVENTS:
                text = f"{theme_name or '电脑'}让我放松"
                priority, confidence, salience = 1, 0.65, 0.70
            elif event and event.occurs in WORK_EVENTS:
                text = f"把{theme_name or '事情'}写下去让我平静"
                priority, confidence, salience = 2, 0.65, 0.75
            else:
                text = f"{theme_name or '这件事'}让我好受一点"
                priority, confidence, salience = 1, 0.55, 0.60
            candidates.append(
                (
                    priority,
                    abs(delta),
                    pe.asserted_at,
                    MemoryDraft(
                        triple=_memory_triple(
                            personality, predicate="believes", text=text,
                            confidence=confidence, salience=salience,
                            at_time=at_time,
                        ),
                        memory_kind="semantic",
                        decay_rate=0.05,
                    ),
                )
            )

    semantic = sorted(
        (c for c in candidates if c[3].memory_kind == "semantic"),
        key=lambda c: (-c[0], -c[1], c[2], c[3].triple.id),
    )[:2]
    semantic.sort(key=lambda c: (c[2], c[3].triple.id))

    imprints = sorted(
        (c for c in candidates if c[3].memory_kind == "imprint"),
        key=lambda c: (-c[1], c[2]),
    )[:1]

    drafts: list[MemoryDraft] = []
    for i, (_, _, _, draft) in enumerate(semantic):
        draft.triple.id = f"mem_d{day_index}_s{i}"
        drafts.append(draft)
    for i, (_, _, _, draft) in enumerate(imprints):
        draft.triple.id = f"mem_d{day_index}_i{i}"
        drafts.append(draft)
    drafts.sort(key=lambda d: (d.triple.asserted_at, d.triple.id))
    return drafts
