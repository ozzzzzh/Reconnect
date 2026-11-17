"""SQLite 实现：哑存储 + 统一写入协议。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .models import Diary, Entity, Event, Perception, Triple, new_id
from .protocol import Store
from .timeutil import norm_ts, utc_now

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# query(pattern=...) 允许的键（白名单，防止 SQL 注入）
_FILTER_KEYS = {"s", "p", "o", "o_kind", "source", "event_id", "unit_id"}


class StoreError(RuntimeError):
    pass


class UnknownEntity(StoreError):
    pass


class DuplicateEvent(StoreError):
    pass


def _norm_alias(text: str) -> str:
    return " ".join(text.casefold().split())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _to_triple(value: Triple | dict[str, Any], *, forced_id: str | None = None) -> Triple:
    """接受 dataclass 或 dict；缺 id 时生成（或使用 forced_id）。"""
    if isinstance(value, Triple):
        if not value.id:
            value.id = forced_id or new_id("tr")
        return value
    d = dict(value)
    if not d.get("id"):
        d["id"] = forced_id or new_id("tr")
    return Triple(**d)


class SqliteStore(Store):
    """单文件三元组存储。

    用法：
        store = SqliteStore("build/life.db")
        store.upsert_entity(...)
        store.apply_event(...)
        store.query({}, as_of="2026-11-01T20:00:00Z", scope="view:u1")
    """

    def __init__(self, path: str | Path = ":memory:", *, init: bool = True):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        if init:
            self.init_schema()

    # ------------------------------------------------------------------ 基建
    def init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '0.1')"
        )

    @contextmanager
    def _tx(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def _row(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    # -------------------------------------------------------------- 实体
    def upsert_entity(self, entity: Entity | dict[str, Any]) -> Entity:
        e = entity if isinstance(entity, Entity) else Entity(**entity)
        with self._tx():
            self._conn.execute(
                """
                INSERT INTO entity (id, type, name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET type = excluded.type, name = excluded.name
                """,
                (e.id, e.type, e.name, e.created_at),
            )
            for alias in e.aliases:
                self._insert_alias(e.id, alias)
        return e

    def _insert_alias(self, entity_id: str, alias: str) -> None:
        alias = alias.strip()
        if not alias:
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO entity_alias (entity_id, alias, norm) VALUES (?, ?, ?)",
            (entity_id, alias, _norm_alias(alias)),
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self._row("SELECT * FROM entity WHERE id = ?", (entity_id,))
        if row is None:
            return None
        e = Entity.from_row(row)
        e.aliases = [
            r["alias"]
            for r in self._rows(
                "SELECT alias FROM entity_alias WHERE entity_id = ? ORDER BY alias",
                (entity_id,),
            )
        ]
        return e

    def _canonical(self, entity_id: str) -> str:
        seen: set[str] = set()
        cur = entity_id
        while cur and cur not in seen:
            seen.add(cur)
            row = self._row("SELECT merged_into FROM entity WHERE id = ?", (cur,))
            if row is None:
                raise UnknownEntity(f"unknown entity: {entity_id}")
            if not row["merged_into"]:
                return cur
            cur = row["merged_into"]
        raise StoreError(f"entity merge cycle detected at {entity_id}")

    def resolve_entity(self, ref: str) -> str | None:
        row = self._row("SELECT id FROM entity WHERE id = ?", (ref,))
        if row is not None:
            return self._canonical(row["id"])
        row = self._row(
            "SELECT entity_id FROM entity_alias WHERE norm = ? ORDER BY entity_id LIMIT 1",
            (_norm_alias(ref),),
        )
        if row is not None:
            return self._canonical(row["entity_id"])
        return None

    def _alias_closure(self, ref: str) -> set[str]:
        """返回 ref 所解析到的规范实体，以及所有被合并进它的实体 ID。"""
        canonical = self.resolve_entity(ref)
        if canonical is None:
            return set()
        ids = {canonical}
        frontier = [canonical]
        while frontier:
            parent = frontier.pop()
            for row in self._rows(
                "SELECT id FROM entity WHERE merged_into = ?", (parent,)
            ):
                child = row["id"]
                if child not in ids:
                    ids.add(child)
                    frontier.append(child)
        return ids

    def _triple_count(self, entity_id: str) -> int:
        row = self._row(
            """
            SELECT COUNT(*) AS n FROM triple
            WHERE s = ? OR (o_kind = 'entity' AND o = ?)
            """,
            (entity_id, entity_id),
        )
        return int(row["n"]) if row else 0

    def merge_entities(self, a: str, b: str, policy: str = "keep_a") -> str:
        ea = self.resolve_entity(a)
        eb = self.resolve_entity(b)
        if ea is None:
            raise UnknownEntity(f"unknown entity: {a}")
        if eb is None:
            raise UnknownEntity(f"unknown entity: {b}")
        if ea == eb:
            return ea

        if policy == "keep_a":
            keep, drop = ea, eb
        elif policy == "keep_b":
            keep, drop = eb, ea
        elif policy == "prefer_richer":
            na, nb = self._triple_count(ea), self._triple_count(eb)
            keep, drop = (ea, eb) if na >= nb else (eb, ea)
        else:
            raise ValueError(f"unknown merge policy: {policy!r}")

        dropped = self.get_entity(drop)
        with self._tx():
            self._conn.execute(
                "UPDATE entity SET merged_into = ?, merged_at = ? WHERE id = ?",
                (keep, utc_now(), drop),
            )
            aliases = self._rows(
                "SELECT alias, norm FROM entity_alias WHERE entity_id = ?", (drop,)
            )
            self._conn.execute("DELETE FROM entity_alias WHERE entity_id = ?", (drop,))
            for row in aliases:
                self._conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (entity_id, alias, norm) "
                    "VALUES (?, ?, ?)",
                    (keep, row["alias"], row["norm"]),
                )
            if dropped is not None and dropped.name:
                self._insert_alias(keep, dropped.name)
        return keep

    # -------------------------------------------------------------- 写入
    def _validate_triple(self, t: Triple) -> None:
        if self._row("SELECT 1 FROM entity WHERE id = ?", (t.s,)) is None:
            raise UnknownEntity(f"triple.s references unknown entity: {t.s}")
        if t.o_kind == "entity":
            if self._row("SELECT 1 FROM entity WHERE id = ?", (t.o,)) is None:
                raise UnknownEntity(f"triple.o references unknown entity: {t.o}")
        if t.unit_id is not None:
            if self._row("SELECT 1 FROM entity WHERE id = ?", (t.unit_id,)) is None:
                raise UnknownEntity(f"triple.unit_id references unknown entity: {t.unit_id}")
        if t.event_id is not None:
            if self._row("SELECT 1 FROM event WHERE id = ?", (t.event_id,)) is None:
                raise UnknownEntity(f"triple.event_id references unknown event: {t.event_id}")

    def _insert_triple(self, t: Triple) -> None:
        self._conn.execute(
            """
            INSERT INTO triple (
                id, s, p, o, o_kind, unit_id, event_id, source,
                confidence, salience, valid_from, valid_to, asserted_at,
                retracted_at, superseded_by, supersedes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.id, t.s, t.p, t.o, t.o_kind, t.unit_id, t.event_id, t.source,
                t.confidence, t.salience, t.valid_from, t.valid_to, t.asserted_at,
                t.retracted_at, t.superseded_by, t.supersedes,
            ),
        )

    def _insert_memory(self, triple_id: str, kind: str, decay_rate: float | None) -> None:
        if decay_rate is not None and not 0.0 <= float(decay_rate) <= 1.0:
            raise ValueError("decay_rate must be within [0, 1]")
        self._conn.execute(
            """
            INSERT INTO memory (triple_id, kind, decay_rate, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(triple_id) DO UPDATE SET
                kind = excluded.kind,
                decay_rate = excluded.decay_rate,
                updated_at = excluded.updated_at
            """,
            (triple_id, kind, decay_rate, utc_now()),
        )

    def assert_triple(
        self,
        triple: Triple | dict[str, Any],
        *,
        memory_kind: str | None = None,
        decay_rate: float | None = None,
        validate: bool = True,
    ) -> Triple:
        t = _to_triple(triple)
        if validate:
            self._validate_triple(t)
        with self._tx():
            self._insert_triple(t)
            if memory_kind:
                self._insert_memory(t.id, memory_kind, decay_rate)
        return t

    def assert_many(
        self, triples: Iterable[Triple | dict[str, Any]]
    ) -> list[Triple]:
        items = [_to_triple(t) for t in triples]
        with self._tx():
            for t in items:
                self._validate_triple(t)
                self._insert_triple(t)
        return items

    def retract(self, triple_id: str, at_time: str | None = None) -> bool:
        at = norm_ts(at_time or utc_now())
        with self._tx():
            row = self._row(
                "SELECT retracted_at FROM triple WHERE id = ?", (triple_id,)
            )
            if row is None:
                raise KeyError(f"unknown triple: {triple_id}")
            if row["retracted_at"] is not None:
                return False
            self._conn.execute(
                "UPDATE triple SET retracted_at = ? WHERE id = ?", (at, triple_id)
            )
        return True

    def revise(
        self,
        old_id: str,
        new_triple: Triple | dict[str, Any],
        reason: str = "",
    ) -> Triple:
        old = self.get_triple(old_id)
        if old is None:
            raise KeyError(f"unknown triple: {old_id}")
        if old.retracted_at is not None:
            raise StoreError(f"cannot revise retracted triple: {old_id}")

        t = _to_triple(new_triple)
        if t.asserted_at < old.asserted_at:
            raise StoreError("new version must be asserted at or after the old one")
        t.supersedes = old_id
        self._validate_triple(t)

        with self._tx():
            self._conn.execute(
                "UPDATE triple SET retracted_at = ?, superseded_by = ? WHERE id = ?",
                (t.asserted_at, t.id, old_id),
            )
            self._insert_triple(t)
            self._conn.execute(
                "INSERT INTO revision (old_id, new_id, reason, at) VALUES (?, ?, ?, ?)",
                (old_id, t.id, reason, t.asserted_at),
            )
        return t

    def _scope_clause(self, scope: str | None) -> tuple[str, list[Any]]:
        if scope is None:
            return "", []
        if scope == "world":
            return "unit_id IS NULL", []
        if scope.startswith("unit:"):
            return "unit_id = ?", [scope[len("unit:"):]]
        if scope.startswith("view:"):
            return "(unit_id IS NULL OR unit_id = ?)", [scope[len("view:"):]]
        raise ValueError(
            "scope must be None, 'world', 'unit:<id>' or 'view:<id>'"
        )

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
        pattern = pattern or {}
        unknown = set(pattern) - _FILTER_KEYS
        if unknown:
            raise ValueError(f"unsupported query keys: {sorted(unknown)}")

        where: list[str] = []
        params: list[Any] = []

        # s / o 支持别名与合并后的实体闭包
        for key in ("s", "o"):
            if key not in pattern:
                continue
            refs = pattern[key]
            if isinstance(refs, (list, tuple, set)):
                refs = list(refs)
            else:
                refs = [refs]
            ids: set[str] = set()
            for ref in refs:
                closure = self._alias_closure(str(ref))
                ids |= closure if closure else {str(ref)}  # 解析不到则按字面量匹配
            placeholders = ",".join("?" for _ in ids)
            where.append(f"{key} IN ({placeholders})")
            params.extend(sorted(ids))

        for key in ("p", "o_kind", "source", "event_id", "unit_id"):
            if key not in pattern:
                continue
            value = pattern[key]
            if isinstance(value, (list, tuple, set)):
                values = list(value)
                placeholders = ",".join("?" for _ in values)
                where.append(f"{key} IN ({placeholders})")
                params.extend(values)
            else:
                where.append(f"{key} = ?")
                params.append(value)

        if as_of is not None:
            T = norm_ts(as_of)
            where.append("asserted_at <= ?")
            params.append(T)
            if not include_retracted:
                where.append("(retracted_at IS NULL OR retracted_at > ?)")
                params.append(T)
        elif not include_retracted:
            where.append("retracted_at IS NULL")

        if at is not None:
            A = norm_ts(at)
            where.append("(valid_from <= ? AND (valid_to IS NULL OR valid_to > ?))")
            params.extend([A, A])

        scope_sql, scope_params = self._scope_clause(scope)
        if scope_sql:
            where.append(scope_sql)
            params.extend(scope_params)

        sql = "SELECT * FROM triple"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY salience DESC, asserted_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        return [Triple.from_row(r) for r in self._rows(sql, params)]

    def decay(
        self,
        scope: str | None,
        rate: float,
        at_time: str | None = None,
    ) -> int:
        rate = float(rate)
        if not 0.0 <= rate <= 1.0:
            raise ValueError("rate must be within [0, 1]")
        factor = 1.0 - rate
        scope_sql, scope_params = self._scope_clause(scope)
        where = [
            "retracted_at IS NULL",
            "salience > 0",
            "id NOT IN (SELECT triple_id FROM memory WHERE kind = 'imprint')",
        ]
        params: list[Any] = [factor]
        if scope_sql:
            where.append(scope_sql)
            params.extend(scope_params)
        cur = self._conn.execute(
            "UPDATE triple SET salience = MIN(1.0, MAX(0.0, salience * ?)) "
            "WHERE " + " AND ".join(where),
            params,
        )
        return cur.rowcount

    def get_triple(self, triple_id: str) -> Triple | None:
        row = self._row("SELECT * FROM triple WHERE id = ?", (triple_id,))
        return Triple.from_row(row) if row else None

    def get_memory(self, triple_id: str) -> dict[str, Any] | None:
        row = self._row("SELECT * FROM memory WHERE triple_id = ?", (triple_id,))
        return dict(row) if row else None

    # -------------------------------------------------------------- 事件
    def apply_event(self, event: Event | dict[str, Any]) -> Event:
        ev = event if isinstance(event, Event) else Event(**event)
        with self._tx():
            try:
                self._conn.execute(
                    """
                    INSERT INTO event (
                        id, time, day_index, type, occurs, roles, delta,
                        causal_parent, source, raw_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.id, ev.time, ev.day_index, ev.type, ev.occurs,
                        _json_dumps(ev.roles), _json_dumps(ev.delta),
                        ev.causal_parent, ev.source, ev.raw_text, ev.created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateEvent(f"event already exists: {ev.id}") from exc

            for idx, item in enumerate(ev.delta["add"]):
                d = dict(item)
                d.setdefault("source", ev.source)
                d.setdefault("event_id", ev.id)
                d.setdefault("asserted_at", ev.time)
                t = _to_triple(d, forced_id=f"{ev.id}:a{idx}")
                self._validate_triple(t)
                self._insert_triple(t)

            for triple_id in ev.delta["retract"]:
                row = self._row(
                    "SELECT retracted_at FROM triple WHERE id = ?", (triple_id,)
                )
                if row is None:
                    raise KeyError(f"retract target not found: {triple_id}")
                if row["retracted_at"] is None:
                    self._conn.execute(
                        "UPDATE triple SET retracted_at = ? WHERE id = ?",
                        (ev.time, triple_id),
                    )
        return ev

    def get_event(self, event_id: str) -> Event | None:
        row = self._row("SELECT * FROM event WHERE id = ?", (event_id,))
        return Event.from_row(row) if row else None

    def list_events(self, day_index: int | None = None) -> list[Event]:
        if day_index is None:
            rows = self._rows("SELECT * FROM event ORDER BY time ASC, id ASC")
        else:
            rows = self._rows(
                "SELECT * FROM event WHERE day_index = ? ORDER BY time ASC, id ASC",
                (day_index,),
            )
        return [Event.from_row(r) for r in rows]

    def event_chain(self, event_id: str, max_depth: int = 32) -> list[Event]:
        rows = self._rows(
            """
            WITH RECURSIVE chain(id, depth) AS (
                SELECT id, 0 FROM event WHERE id = ?
                UNION ALL
                SELECT e.causal_parent, c.depth + 1
                FROM event e JOIN chain c ON e.id = c.id
                WHERE e.causal_parent IS NOT NULL AND c.depth < ?
            )
            SELECT e.* FROM event e
            JOIN (SELECT id, MIN(depth) AS depth FROM chain GROUP BY id) c
              ON c.id = e.id
            ORDER BY c.depth DESC
            """,
            (event_id, max_depth),
        )
        return [Event.from_row(r) for r in rows]

    # ------------------------------------------------- 感知 / 日记 / LLM
    def record_perception(
        self, perception: Perception | dict[str, Any]
    ) -> Perception:
        p = perception if isinstance(perception, Perception) else Perception(**perception)
        with self._tx():
            self._conn.execute(
                """
                INSERT INTO perception (
                    id, event_id, unit_id, attended, transforms, affect,
                    confidence, asserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.id, p.event_id, p.unit_id,
                    _json_dumps(p.attended), _json_dumps(p.transforms),
                    _json_dumps(p.affect), p.confidence, p.asserted_at,
                ),
            )
        return p

    def record_diary(self, diary: Diary | dict[str, Any]) -> Diary:
        d = diary if isinstance(diary, Diary) else Diary(**diary)
        with self._tx():
            self._conn.execute(
                """
                INSERT INTO diary (
                    id, unit_id, day_index, period_from, period_to,
                    perception_ids, text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d.id, d.unit_id, d.day_index, d.period_from, d.period_to,
                    _json_dumps(d.perception_ids), d.text, d.created_at,
                ),
            )
        return d

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
    ) -> str:
        cid = call_id or new_id("llm")
        with self._tx():
            self._conn.execute(
                """
                INSERT INTO llm_call (
                    id, at, purpose, model, unit_id, event_id, prompt, response, meta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid, norm_ts(at or utc_now()), purpose, model, unit_id,
                    event_id, prompt, response,
                    _json_dumps(meta) if meta is not None else None,
                ),
            )
        return cid

    # -------------------------------------------------------------- 视图
    def day_snapshot(self, day_index: int) -> dict[str, Any]:
        events = self.list_events(day_index)
        perceptions: list[Perception] = []
        if events:
            event_ids = [e.id for e in events]
            placeholders = ",".join("?" for _ in event_ids)
            perceptions = [
                Perception.from_row(r)
                for r in self._rows(
                    f"SELECT * FROM perception WHERE event_id IN ({placeholders}) "
                    "ORDER BY asserted_at ASC",
                    event_ids,
                )
            ]

        diaries = [
            Diary.from_row(r)
            for r in self._rows(
                "SELECT * FROM diary WHERE day_index = ? ORDER BY created_at ASC",
                (day_index,),
            )
        ]

        triples: list[Triple] = []
        if events:
            lo = min(e.time for e in events)
            hi = max(e.time for e in events)
            triples = [
                Triple.from_row(r)
                for r in self._rows(
                    "SELECT * FROM triple WHERE asserted_at >= ? AND asserted_at <= ? "
                    "ORDER BY asserted_at ASC, salience DESC",
                    (lo, hi),
                )
            ]

        return {
            "day_index": day_index,
            "events": events,
            "perceptions": perceptions,
            "diaries": diaries,
            "triples": triples,
        }

    def replay_world(self, at_time: str) -> dict[str, Triple]:
        """只由 event.delta 重建客观世界状态。

        注意：算子产物（compress 生成的信念）不是事件，不在重放范围内；
        它们由 triple 表本身作为日志保存（D9）。本方法用于验证事件溯源。
        """
        T = norm_ts(at_time)
        state: dict[str, Triple] = {}
        for row in self._rows(
            "SELECT * FROM event WHERE time <= ? ORDER BY time ASC, id ASC", (T,)
        ):
            ev = Event.from_row(row)
            for triple_id in ev.delta["retract"]:
                state.pop(triple_id, None)
            for idx, item in enumerate(ev.delta["add"]):
                d = dict(item)
                d.setdefault("source", ev.source)
                d.setdefault("event_id", ev.id)
                d.setdefault("asserted_at", ev.time)
                t = _to_triple(d, forced_id=f"{ev.id}:a{idx}")
                state[t.id] = t
        return state

    # -------------------------------------------------------------- 统计
    def stats(self) -> dict[str, int]:
        tables = ("entity", "triple", "event", "perception", "diary", "memory")
        return {
            table: int(self._row(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for table in tables
        }
