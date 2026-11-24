"""叙述层（narrate）：把一天的感知与记忆写成日记。

- ``ClaudeNarrator``：调用 Claude（anthropic SDK，可选依赖）。
- ``OfflineNarrator``：无 API Key / 无 SDK 时的确定性兜底，仍使用具体事件，
  不会退化成「今天天气很好，我感到平静」。

两种实现的输入完全相同；prompt 与 response 都由 runner 写入 ``llm_call``（D11）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from store import Event, Perception, Store
from store.timeutil import parse_ts

from .operators import MemoryDraft, entity_name
from .personality import Personality

MEMORY_LABELS = {"imprint": "刻痕", "semantic": "语义记忆", "grey": "灰记忆"}

STYLE_SYSTEM = """你是一个数字生命体的日记执笔者。你在替「{name}」写第一人称日记。
风格锚点：{style}

硬性要求：
1. 只写这一天发生的事，不写回顾、计划或人生总结。
2. 只能使用给出的时间、人物、物品、动作与变化；不要新增重大事件。
3. 情绪从身体反应、动作和对话里透出来，不要直接写「我感到 X」。
4. 禁止空泛套话：不要写天气，不要写「今天过得很充实」这类总结。
5. 3～4 段，400～700 字，短句为主。
6. 只输出日记正文，不要标题、日期或解释。"""


class LLMNarrationError(RuntimeError):
    pass


@dataclass
class Narration:
    text: str
    model: str
    prompt: str
    offline: bool = False


class Narrator(Protocol):
    name: str

    def narrate(
        self,
        *,
        personality: Personality,
        events: list[Event],
        perceptions: list[Perception],
        memories: list[MemoryDraft],
        store: Store,
        day_index: int,
        display_offset: int = 8,
    ) -> Narration: ...


# ------------------------------------------------------------------ 时间/文本
_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    tens, ones = divmod(n, 10)
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def fmt_local_time(ts: str, offset: int = 8) -> str:
    """UTC 存储 → 指定时区的可读时间（默认 +08:00）。"""
    dt = parse_ts(ts).astimezone(timezone(timedelta(hours=offset)))
    hour, minute = dt.hour, dt.minute
    if hour < 6:
        period = "凌晨"
    elif hour < 12:
        period = "上午"
    elif hour == 12:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    clock = f"{_cn_num(hour % 12 or 12)}点"
    if minute:
        clock += f"{_cn_num(minute)}分"
    return period + clock


def _first_person(raw: str) -> str:
    text = (raw or "").strip().replace("她的", "我的")
    return re.sub(r"(^|[。！？!?；;\n])她", r"\1我", text)


def _sentence(raw: str) -> str:
    text = _first_person(raw)
    if not text:
        return ""
    return text if text.endswith(("。", "！", "？", "…", "!?")) else text + "。"


# --------------------------------------------------------------------- prompt
def _changes(store: Store, event: Event) -> list[str]:
    changes: list[str] = []
    for item in event.delta.get("add", []):
        subject = entity_name(store, item.get("s", ""))
        obj = item.get("o")
        if item.get("o_kind", "entity") == "entity":
            obj = entity_name(store, obj)
        changes.append(f"{subject} {item.get('p', '')} -> {obj}")
    for triple_id in event.delta.get("retract", []):
        tri = store.get_triple(triple_id)
        if tri is None:
            continue
        obj = entity_name(store, tri.o) if tri.o_kind == "entity" else tri.o
        changes.append(f"不再成立: {entity_name(store, tri.s)} {tri.p} {obj}")
    return changes


def build_prompt(
    personality: Personality,
    events: list[Event],
    perceptions: list[Perception],
    memories: list[MemoryDraft],
    store: Store,
    day_index: int,
    display_offset: int = 8,
) -> tuple[str, str]:
    event_payload = [
        {
            "时间": fmt_local_time(ev.time, display_offset),
            "发生了什么": ev.raw_text or ev.occurs,
            "角色": {
                role: entity_name(store, ref)
                for role, ref in (ev.roles or {}).items()
            },
            "变化": _changes(store, ev),
        }
        for ev in events
    ]
    perception_payload = []
    for pe in perceptions:
        transforms = []
        for tr in pe.transforms or []:
            item = dict(tr)
            target = item.pop("target", None)
            item["对象"] = entity_name(store, target) if target else ""
            transforms.append(item)
        perception_payload.append(
            {
                "事件": pe.event_id,
                "注意到了": {
                    entity_name(store, eid): round(score, 2)
                    for eid, score in (pe.attended or {}).items()
                },
                "偏差": transforms,
                "情绪": pe.affect,
            }
        )
    memory_payload = [
        {"类型": MEMORY_LABELS.get(m.memory_kind, m.memory_kind), "内容": m.triple.o}
        for m in memories
    ]

    payload = {
        "day_index": day_index,
        "unit": {
            "name": personality.name,
            "traits": personality.traits,
            "needs": personality.needs,
            "state": personality.state,
            "open_loops": [
                {"label": x.label, "urgency": x.urgency}
                for x in personality.open_loops
            ],
        },
        "事件": event_payload,
        "感知": perception_payload,
        "记忆": memory_payload,
    }
    system = STYLE_SYSTEM.format(name=personality.name, style=personality.style)
    user = (
        "这是这一天引擎产生的结构化记录：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n请写这一天的日记。"
    )
    return system, user


# -------------------------------------------------------------------- offline
class OfflineNarrator:
    """确定性兜底：用事件原文、情绪原因与记忆拼出具体日记。"""

    name = "offline-rules-v0"

    def narrate(
        self,
        *,
        personality: Personality,
        events: list[Event],
        perceptions: list[Perception],
        memories: list[MemoryDraft],
        store: Store,
        day_index: int,
        display_offset: int = 8,
    ) -> Narration:
        system, user = build_prompt(
            personality, events, perceptions, memories, store,
            day_index, display_offset,
        )
        ordered_events = sorted(events, key=lambda e: (e.time, e.id))
        by_event = {pe.event_id: pe for pe in perceptions}

        paragraphs: list[str] = []
        for ev in ordered_events:
            time_label = fmt_local_time(ev.time, display_offset)
            body = _sentence(ev.raw_text or ev.occurs)
            pe = by_event.get(ev.id)
            if pe and pe.affect.get("reason"):
                body += str(pe.affect["reason"]).rstrip("。") + "。"
            paragraphs.append(f"{time_label}，{body}")

        negative_indexes = [
            i
            for i, ev in enumerate(ordered_events)
            if (pe := by_event.get(ev.id))
            and float(pe.affect.get("delta", 0) or 0) <= -0.30
        ]
        imprint = next(
            (m for m in memories if m.memory_kind == "imprint"), None
        )
        if negative_indexes and imprint:
            i = negative_indexes[0]
            paragraphs[i] += "我盯着那行字，心跳漏了一拍。"
            transforms = by_event[ordered_events[i].id].transforms or []
            if any(tr.get("op") == "infer" for tr in transforms):
                paragraphs[i] += "很没道理地觉得对方是故意的。"

        positive = [
            m.triple.o
            for m in memories
            if m.memory_kind == "semantic"
            and any(word in m.triple.o for word in ("平静", "放松", "好受"))
        ]
        if positive and paragraphs:
            paragraphs[-1] += positive[-1].rstrip("。") + "。"

        paragraphs.append("今天先到这里。")
        text = "\n\n".join(p for p in paragraphs if p)
        return Narration(
            text=text,
            model=self.name,
            prompt=f"[system]\n{system}\n\n[user]\n{user}",
            offline=True,
        )


# --------------------------------------------------------------------- claude
class ClaudeNarrator:
    """使用官方 Anthropic SDK；SDK 是可选依赖。"""

    def __init__(self, *, model: str | None = None, client: Any | None = None):
        import anthropic  # 延迟导入：无 SDK 时仍可跑离线模式

        self.model = model or os.environ.get(
            "RECONNECT_MODEL", "claude-opus-5-5"
        )
        self._client = client or anthropic.Anthropic()

    @property
    def name(self) -> str:
        return self.model

    def narrate(
        self,
        *,
        personality: Personality,
        events: list[Event],
        perceptions: list[Perception],
        memories: list[MemoryDraft],
        store: Store,
        day_index: int,
        display_offset: int = 8,
    ) -> Narration:
        system, user = build_prompt(
            personality, events, perceptions, memories, store,
            day_index, display_offset,
        )
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4000,
            "system": [{"type": "text", "text": system}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
            "messages": [{"role": "user", "content": user}],
        }
        # Claude Opus 5.5 默认开启服务端 refusal fallback；可用环境变量关闭。
        if os.environ.get("RECONNECT_NO_FALLBACK") != "1":
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"

        response = self._client.beta.messages.create(**request)
        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            raise LLMNarrationError(f"narrate refused: {detail}")

        text = "\n".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise LLMNarrationError("narrate returned empty text")
        return Narration(
            text=text,
            model=self.model,
            prompt=f"[system]\n{system}\n\n[user]\n{user}",
            offline=False,
        )


def _has_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN"
    ):
        return True
    if os.environ.get("ANTHROPIC_PROFILE"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def make_narrator(mode: str = "auto") -> Narrator:
    """mode: auto | claude | offline。

    auto 仅在 SDK 已安装且检测到凭证时使用 Claude，否则离线；
    claude 强制使用 Claude（缺 SDK / 凭证时抛错）。
    """
    if mode == "offline":
        return OfflineNarrator()
    if mode not in {"auto", "claude"}:
        raise ValueError("narrator mode must be auto, claude or offline")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        if mode == "claude":
            raise RuntimeError(
                "Anthropic SDK is not installed; run `pip install anthropic` "
                "or use --narrator offline"
            ) from None
        return OfflineNarrator()
    if mode == "auto" and not _has_credentials():
        return OfflineNarrator()
    try:
        return ClaudeNarrator()
    except Exception:
        if mode == "claude":
            raise
        return OfflineNarrator()
