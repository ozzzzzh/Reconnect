"""M2 单日闭环验收测试（离线叙述，不触网）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine import OfflineNarrator, Personality, attend, make_narrator, run_day
from store import SqliteStore

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "examples" / "world_day1.json"
PERSONA = ROOT / "examples" / "personality_u1.json"


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SqliteStore(Path(self.tmp.name) / "life.db")
        self.addCleanup(self.store.close)

    def _run(self):
        return run_day(
            self.store,
            world_path=WORLD,
            personality_path=PERSONA,
            narrator=OfflineNarrator(),
        )

    # ------------------------------------------------------------- end-to-end
    def test_day1_end_to_end(self) -> None:
        result = self._run()

        self.assertEqual(result.day_index, 1)
        self.assertEqual(result.events, 3)
        self.assertEqual(result.perceptions, 3)
        self.assertEqual(result.memories, 3)
        self.assertEqual(result.narrator, "offline-rules-v0")
        self.assertGreater(result.diary_chars, 150)

        # 事件 → 感知 → affect 的链
        e2_perception = self.store.perceptions_for_event("e2")[0]
        self.assertEqual(e2_perception.affect["label"], "anxiety")
        self.assertLessEqual(e2_perception.affect["delta"], -0.4)
        self.assertTrue(
            any(t.get("op") == "infer" for t in e2_perception.transforms)
        )
        self.assertIn("goal_congruence", e2_perception.affect["appraisal"])

        # 记忆：语义信念 + 刻痕
        beliefs = self.store.query({"p": "believes"}, scope="unit:u1")
        self.assertEqual(
            sorted(t.o for t in beliefs),
            ["周姐在针对我", "把季度汇报写下去让我平静"],
        )
        imprints = self.store.query({"p": "core_memory"}, scope="unit:u1")
        self.assertEqual(len(imprints), 1)
        self.assertIn("心跳漏了一拍", imprints[0].o)

        # 日记：具体、有细节、不是 LLM 腔
        diary = self.store.get_diary(result.diary_id)
        self.assertIsNotNone(diary)
        self.assertIn("周姐", diary.text)
        self.assertIn("第一页", diary.text)
        self.assertNotIn("天气很好", diary.text)
        self.assertNotIn("我感到平静", diary.text)

        # 全链路持久化 + inspect 视图
        snap = self.store.day_snapshot(1)
        self.assertEqual(len(snap["events"]), 3)
        self.assertEqual(len(snap["perceptions"]), 3)
        self.assertEqual(len(snap["diaries"]), 1)
        self.assertEqual([e.id for e in self.store.event_chain("e3")], ["e1", "e2", "e3"])
        self.assertEqual(self.store.stats()["llm_call"], 1)

        view = self.store.query({}, scope="view:u1", as_of="2026-11-02T00:00:00Z")
        literals = {t.o for t in view if t.o_kind == "literal"}
        self.assertIn("周姐在针对我", literals)

    # ------------------------------------------------------------- idempotent
    def test_rerun_is_idempotent(self) -> None:
        first = self._run()
        stats_first = self.store.stats()
        second = self._run()

        self.assertEqual(first.diary_id, second.diary_id)
        self.assertEqual(first.perceptions, second.perceptions)
        self.assertEqual(second.narrator, "existing")
        self.assertEqual(self.store.stats(), stats_first)
        self.assertEqual(
            len(self.store.query({"p": "believes"}, scope="unit:u1")), 2
        )

    # ------------------------------------------------------------- attention
    def test_attention_budget_and_bias(self) -> None:
        self._run()
        personality = Personality.from_fixture(PERSONA)
        e2 = self.store.get_event("e2")
        self.assertIsNotNone(e2)

        attention = attend(personality, e2, self.store)
        self.assertLessEqual(len(attention.attended), 4)
        self.assertIn("c1", attention.attended)  # 未了之事
        self.assertIn("per1", attention.attended)  # 敏感 → 注意发送者
        self.assertGreater(attention.attended["c1"], 0.8)
        self.assertTrue(
            any(t.get("op") == "amplify" and t.get("target") == "c1"
                for t in attention.transforms)
        )
        self.assertTrue(
            any(t.get("op") == "infer" and t.get("target") == "per1"
                for t in attention.transforms)
        )

    # ------------------------------------------------------------- state
    def test_state_is_revised_at_day_close(self) -> None:
        self._run()
        old_emotion = self.store.get_triple("p_state_emotion")
        self.assertIsNotNone(old_emotion)
        self.assertIsNotNone(old_emotion.retracted_at)
        self.assertEqual(old_emotion.superseded_by, "p_state_emotion_d1")

        emotion = self.store.get_triple("p_state_emotion_d1")
        self.assertEqual(emotion.o, "专注")
        energy = self.store.get_triple("p_state_energy_d1")
        self.assertEqual(energy.o, "0.25")

        revisions = self.store.list_revisions("p_state_emotion")
        self.assertEqual(len(revisions), 1)

    # ------------------------------------------------------------- personality
    def test_personality_v0_shape_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            Personality(
                unit_id="u1",
                name="某人",
                traits=["只有一个"],
                needs=["a", "b"],
                open_loops=[{"target": "c1"}],
            )

    def test_personality_slots_persisted(self) -> None:
        self._run()
        self.assertEqual(
            len(self.store.query({"p": "has_trait"}, scope="unit:u1")), 3
        )
        self.assertEqual(
            len(self.store.query({"p": "has_need"}, scope="unit:u1")), 2
        )
        loops = self.store.query({"p": "has_open_loop"}, scope="unit:u1")
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0].o, "c1")

    # ------------------------------------------------------------- narrator
    def test_offline_narrator_selection(self) -> None:
        narrator = make_narrator("offline")
        self.assertEqual(narrator.name, "offline-rules-v0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
