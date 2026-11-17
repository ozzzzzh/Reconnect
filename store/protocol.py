"""存储层接口（port）。

上层算子、LLM 语义层、inspect CLI 只依赖这里的契约，不直接依赖 SQLite。
将来换 Postgres / Kùzu 时，只替换 `sqlite_store.SqliteStore`。

对应 docs/EXECUTION.md §4 的六个写入操作：
    assert / retract / revise / decay / merge_entities / query
以及事件、感知、日记的落盘入口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Sequence

from .models import Diary, Entity, Event, Perception, Triple


class Store(ABC):
    # ---- 实体 ------------------------------------------------------------
    @abstractmethod
    def upsert_entity(self, entity: Entity | dict[str, Any]) -> Entity: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Entity | None: ...

    @abstractmethod
    def resolve_entity(self, ref: str) -> str | None:
        """把实体 ID 或别名解析为当前规范 ID（跟随 merged_into 链）。"""

    @abstractmethod
    def merge_entities(self, a: str, b: str, policy: str = "keep_a") -> str:
        """实体消解。返回规范 ID；被合并方的历史三元组不重写。"""

    # ---- 写入协议 §4 -----------------------------------------------------
    @abstractmethod
    def assert_triple(
        self,
        triple: Triple | dict[str, Any],
        *,
        memory_kind: str | None = None,
        decay_rate: float | None = None,
        validate: bool = True,
    ) -> Triple: ...

    @abstractmethod
    def assert_many(self, triples: Iterable[Triple | dict[str, Any]]) -> list[Triple]: ...

    @abstractmethod
    def retract(self, triple_id: str, at_time: str | None = None) -> bool:
        """软删除。返回 True 表示本次确实撤回，False 表示早已撤回。"""

    @abstractmethod
    def revise(
        self,
        old_id: str,
        new_triple: Triple | dict[str, Any],
        reason: str = "",
    ) -> Triple:
        """用新版本替代旧版本；旧版本 retracted_at/superseded_by 落痕，历史保留。"""

    @abstractmethod
    def decay(
        self,
        scope: str | None,
        rate: float,
        at_time: str | None = None,
    ) -> int:
        """显著性衰减；imprint 永不衰减。返回受影响行数。"""

    @abstractmethod
    def query(
        self,
        pattern: dict[str, Any] | None = None,
        *,
        as_of: str | None = None,
        at: str | None = None,
        scope: str | None = None,
        include_retracted: bool = False,
        limit: int | None = None,
    ) -> list[Triple]:
        """读视图。

        pattern: s / p / o / o_kind / source / event_id / unit_id 的精确过滤。
        as_of:   断言时间 T —— 第 T 天它相信什么。
        at:      世界时间 t —— 时刻 t 客观成立什么。
        scope:   None | 'world' | 'unit:<id>' | 'view:<id>'
        """

    # ---- 事件 ------------------------------------------------------------
    @abstractmethod
    def apply_event(self, event: Event | dict[str, Any]) -> Event:
        """原子地落盘一个事件及其 delta。"""

    @abstractmethod
    def get_event(self, event_id: str) -> Event | None: ...

    @abstractmethod
    def list_events(self, day_index: int | None = None) -> list[Event]: ...

    @abstractmethod
    def event_chain(self, event_id: str, max_depth: int = 32) -> list[Event]:
        """沿 causal_parent 回溯，按从根到叶返回。"""

    # ---- 感知 / 日记 / LLM 归档 -----------------------------------------
    @abstractmethod
    def record_perception(self, perception: Perception | dict[str, Any]) -> Perception: ...

    @abstractmethod
    def record_diary(self, diary: Diary | dict[str, Any]) -> Diary: ...

    @abstractmethod
    def log_llm_call(
        self,
        *,
        purpose: str,
        prompt: str,
        response: str,
        model: str | None = None,
        unit_id: str | None = None,
        event_id: str | None = None,
        meta: dict[str, Any] | None = None,
        at: str | None = None,
        call_id: str | None = None,
    ) -> str: ...

    # ---- 视图 ------------------------------------------------------------
    @abstractmethod
    def day_snapshot(self, day_index: int) -> dict[str, Any]: ...

    @abstractmethod
    def replay_world(self, at_time: str) -> dict[str, Triple]:
        """仅由 event.delta 重放客观世界状态（不含算子产物）。"""
