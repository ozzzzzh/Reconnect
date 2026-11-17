"""存储层骨架的验收测试（M0 / M1 的最小集合）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from store import (
    Diary,
    DuplicateEvent,
    Entity,
    Event,
    Perception,
    SqliteStore,
    Triple,
    UnknownEntity,
)
from store.fixture import import_fixture

ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-11-01T00:00:00Z"
T1 = "2026-11-01T10:00:00Z"
T1_5 = "2026-11-01T11:00:00Z"
T2 = "2026-11-01T12:00:00Z"
T2_5 = "2026-11-01T13:00:00Z"


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SqliteStore(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.store.close)
        self.store.upsert_entity(Entity(id="u1", type="Unit", name="林晚", aliases=["她"]))
        self.store.upsert_entity(Entity(id="p1", type="Place", name="卧室"))
        self.store.upsert_entity(Entity(id="o1", type="Object", name="电脑"))

    # ---------------------------------------------------------------- as_of
    def test_retract_is_soft_and_time_aware(self) -> None:
        self.store.assert_triple(
            Triple(
                id="t1", s="u1", p="located_in", o="p1",
                source="world", asserted_at=T1, valid_from=T1,
            )
        )
        self.assertEqual(len(self.store.query({"p": "located_in"}, as_of=T1_5)), 1)

        self.assertTrue(self.store.retract("t1", at_time=T2))
        # 撤回之前仍然相信
        self.assertEqual(len(self.store.query({"p": "located_in"}, as_of=T1_5)), 1)
        # 撤回之后不再相信
        self.assertEqual(len(self.store.query({"p": "located_in"}, as_of=T2_5)), 0)
        # 历史仍可查
        self.assertEqual(
            len(self.store.query({"p": "located_in"}, include_retracted=True)), 1
        )
        # 幂等
        self.assertFalse(self.store.retract("t1", at_time=T2_5))

    def test_world_time_interval(self) -> None:
        self.store.assert_triple(
            Triple(
                id="t2", s="u1", p="emotion", o="疲惫", o_kind="literal",
                source="unit:u1", unit_id="u1",
                valid_from=T1, valid_to=T2, asserted_at=T1,
            )
        )
        self.assertEqual(len(self.store.query({"p": "emotion"}, at=T1_5)), 1)
        self.assertEqual(len(self.store.query({"p": "emotion"}, at=T2_5)), 0)
        self.assertEqual(len(self.store.query({"p": "emotion"}, at=T0)), 0)

    def test_scope_world_unit_view(self) -> None:
        self.store.assert_triple(
            Triple(id="w1", s="u1", p="located_in", o="p1", source="world", asserted_at=T1)
        )
        self.store.assert_triple(
            Triple(
                id="b1", s="u1", p="believes", o="她累了", o_kind="literal",
                unit_id="u1", source="derived:compress", asserted_at=T1,
            )
        )
        self.assertEqual(len(self.store.query({}, scope="world")), 1)
        self.assertEqual(len(self.store.query({}, scope="unit:u1")), 1)
        self.assertEqual(len(self.store.query({}, scope="view:u1")), 2)
        self.assertEqual(len(self.store.query({"unit_id": None})), 1)

    # ---------------------------------------------------------------- revise
    def test_revise_keeps_history(self) -> None:
        self.store.assert_triple(
            Triple(
                id="r1", s="u1", p="emotion", o="疲惫", o_kind="literal",
                unit_id="u1", source="unit:u1", asserted_at=T1, valid_from=T1,
            )
        )
        new = self.store.revise(
            "r1",
            Triple(
                id="r2", s="u1", p="emotion", o="平静", o_kind="literal",
                unit_id="u1", source="unit:u1", asserted_at=T2, valid_from=T2,
            ),
            reason="情绪回落",
        )
        self.assertEqual(new.id, "r2")

        old_view = self.store.query({"p": "emotion"}, as_of=T1_5)
        self.assertEqual([t.o for t in old_view], ["疲惫"])

        new_view = self.store.query({"p": "emotion"}, as_of=T2_5)
        self.assertEqual([t.o for t in new_view], ["平静"])

        old = self.store.get_triple("r1")
        self.assertEqual(old.superseded_by, "r2")
        self.assertEqual(old.retracted_at, T2)

        revisions = self.store.list_revisions("r1")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["reason"], "情绪回落")

    # ---------------------------------------------------------------- merge
    def test_merge_entities_preserves_history_and_resolves_aliases(self) -> None:
        self.store.upsert_entity(Entity(id="boss_a", type="Person", name="王总"))
        self.store.upsert_entity(
            Entity(id="boss_b", type="Person", name="老板", aliases=["那个男的"])
        )
        self.store.assert_triple(
            Triple(
                id="rep1", s="u1", p="reports_to", o="boss_a",
                source="world", asserted_at=T1, valid_from=T1,
            )
        )

        canonical = self.store.merge_entities("boss_b", "boss_a", policy="keep_b")
        self.assertEqual(canonical, "boss_a")
        self.assertEqual(self.store.resolve_entity("老板"), "boss_a")
        self.assertEqual(self.store.resolve_entity("那个男的"), "boss_a")

        # 查询任一称呼都能命中同一条事实
        self.assertEqual(len(self.store.query({"o": "boss_b"})), 1)
        self.assertEqual(len(self.store.query({"o": "王总"})), 1)
        # 历史三元组不被重写
        self.assertEqual(self.store.get_triple("rep1").o, "boss_a")
        # 幂等
        self.assertEqual(
            self.store.merge_entities("boss_b", "boss_a", policy="keep_a"), "boss_a"
        )

    # ---------------------------------------------------------------- 事件
    def test_apply_event_is_atomic_and_replayable(self) -> None:
        self.store.assert_triple(
            Triple(id="h1", s="u1", p="located_in", o="p1", source="world", asserted_at=T0)
        )
        before = len(self.store.query({}, include_retracted=True))

        ev = Event(
            id="e1", time=T1, occurs="open", type="occurrence",
            roles={"agent": "u1", "theme": "o1"},
            delta={
                "add": [
                    {
                        "s": "o1", "p": "powered_on", "o": True, "o_kind": "literal",
                        "valid_from": T1,
                    }
                ],
                "retract": ["h1"],
            },
            source="unit:u1", day_index=1, raw_text="她打开电脑",
        )
        self.store.apply_event(ev)

        self.assertIsNotNone(self.store.get_event("e1"))
        added = self.store.get_triple("e1:a0")
        self.assertEqual(added.o, "true")
        self.assertEqual(added.event_id, "e1")
        self.assertEqual(self.store.get_triple("h1").retracted_at, T1)

        # 重复事件回滚
        with self.assertRaises(DuplicateEvent):
            self.store.apply_event(ev)

        # 非法 delta 回滚（不留下半个事件）
        count_after_first = len(self.store.query({}, include_retracted=True))
        bad = Event(
            id="e2", time=T1_5, occurs="touch",
            delta={"add": [{"s": "ghost", "p": "x", "o": "y", "o_kind": "literal"}],
                   "retract": []},
            source="world",
        )
        with self.assertRaises(UnknownEntity):
            self.store.apply_event(bad)
        self.assertIsNone(self.store.get_event("e2"))
        self.assertEqual(
            len(self.store.query({}, include_retracted=True)), count_after_first
        )
        self.assertEqual(count_after_first, before + 1)

        # 事件溯源：T1 时刻的世界状态由 delta 重放得到
        state = self.store.replay_world(T1)
        self.assertIn("e1:a0", state)
        self.assertNotIn("h1", state)
        self.assertEqual(len(state), 1)

    def test_event_chain(self) -> None:
        for eid, parent in (("e1", None), ("e2", "e1"), ("e3", "e2")):
            self.store.apply_event(
                Event(
                    id=eid, time=T1, occurs="step",
                    causal_parent=parent, source="world",
                )
            )
        self.assertEqual(
            [e.id for e in self.store.event_chain("e3")], ["e1", "e2", "e3"]
        )

    # ---------------------------------------------------------------- fixture
    def test_import_day1_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with SqliteStore(Path(tmp) / "day1.db") as store:
                summary = import_fixture(store, ROOT / "examples" / "day1.json")
                self.assertEqual(summary["events"], 3)
                self.assertEqual(summary["perceptions"], 3)
                self.assertTrue(summary["diary"])

                snap = store.day_snapshot(1)
                self.assertEqual(len(snap["events"]), 3)
                self.assertEqual(len(snap["perceptions"]), 3)
                self.assertEqual(len(snap["diaries"]), 1)
                self.assertIn("周姐", snap["diaries"][0].text)

                # deadline 被 e2 撤回，只剩新值有效
                all_deadlines = store.query({"p": "deadline"}, include_retracted=True)
                self.assertEqual(len(all_deadlines), 2)
                active = store.query({"p": "deadline"})
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].o, "2026-11-02")

                # Unit 的读视图 = 世界 + 私有信念
                view = store.query({}, scope="view:u1", as_of="2026-11-02T00:00:00Z")
                literals = {t.o for t in view if t.o_kind == "literal"}
                self.assertIn("周姐在针对我", literals)
                self.assertIn("写下第一页让我平静", literals)

                # 实体消解：别名可解析
                self.assertEqual(store.resolve_entity("周敏"), "per1")

                # 因果链 e3 -> e2 -> e1
                self.assertEqual(
                    [e.id for e in store.event_chain("e3")], ["e1", "e2", "e3"]
                )

    # ---------------------------------------------------------------- 记忆
    def test_decay_skips_imprint(self) -> None:
        self.store.assert_triple(
            Triple(
                id="sem", s="u1", p="believes", o="会好起来", o_kind="literal",
                unit_id="u1", source="derived:compress",
                salience=1.0, asserted_at=T1,
            ),
            memory_kind="semantic",
        )
        self.store.assert_triple(
            Triple(
                id="imp", s="u1", p="core_memory", o="心跳漏了一拍", o_kind="literal",
                unit_id="u1", source="derived:compress",
                salience=1.0, asserted_at=T1,
            ),
            memory_kind="imprint",
        )
        changed = self.store.decay("unit:u1", 0.5, at_time=T2)
        self.assertEqual(changed, 1)
        self.assertAlmostEqual(self.store.get_triple("sem").salience, 0.5)
        self.assertAlmostEqual(self.store.get_triple("imp").salience, 1.0)

    def test_perception_and_diary_roundtrip(self) -> None:
        self.store.apply_event(
            Event(id="e1", time=T1, occurs="open", source="world", day_index=1)
        )
        p = Perception(
            id="pe1", event_id="e1", unit_id="u1",
            attended={"o1": 0.9},
            transforms=[{"op": "amplify", "target": "o1", "factor": 1.2}],
            affect={"label": "satisfaction", "delta": 0.4, "reason": "放松"},
            asserted_at=T1,
        )
        self.store.record_perception(p)
        d = Diary(
            id="di1", unit_id="u1", text="今天终于把第一页写完了。",
            day_index=1, period_from=T1, period_to=T1_5,
            perception_ids=["pe1"],
        )
        self.store.record_diary(d)

        self.assertEqual(self.store.get_perception("pe1").affect["label"], "satisfaction")
        self.assertEqual(self.store.get_diary("di1").text, "今天终于把第一页写完了。")
        snap = self.store.day_snapshot(1)
        self.assertEqual(len(snap["perceptions"]), 1)
        self.assertEqual(len(snap["diaries"]), 1)

    def test_log_llm_call(self) -> None:
        cid = self.store.log_llm_call(
            purpose="narrate", prompt="...", response="...", model="test-model", at=T1
        )
        self.assertTrue(cid.startswith("llm_"))
        self.assertEqual(self.store.stats()["llm_call"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
