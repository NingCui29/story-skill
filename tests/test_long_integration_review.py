"""Independent integration cases for observed actions, not prior author plans."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("long_integration_review_runtime", ROOT / "skills/story-skill/scripts/story.py")
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class LongIntegrationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-integration-review-")
        self.root = Path(self.temp.name) / "中文状态核对"
        story.Book.create(self.root, "夜间核对", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        story.world.save(self.book, {"entities": [
            {"id": "jiang", "name": "江棠", "kind": "character", "description": "持有指定资金"},
            {"id": "du", "name": "杜承安", "kind": "character", "description": "收款人"},
            {"id": "coin", "name": "铜钱", "kind": "resource", "description": "按枚精确计量"}],
            "rules": [{"id": "listen-v1", "rule": "listen", "version": 1, "start": 0, "cooldown": 10, "hard": True,
                       "description": "听风术两次发动至少相隔十刻", "entities": ["jiang"],
                       "evidence": {"kind": "author_plan", "note": "作者明确指定的能力限制"}}]}, self.rev())

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def rev(self):
        return self.book.meta("revision")

    def evidence(self, chapter, text):
        return {"kind": "chapter", "chapter": chapter, "sha256": story.digest(text), "quote": text.splitlines()[1]}

    def prepare_input(self, chapter, text, at, changes):
        plan = {"volume_dir": "第一卷 雨夜", "title": "核对行动", "goal": "核对本次行动与代价", "stop": "行动完成后停笔", "beats": [{"choice": "作出具体选择", "change": "承担相应代价"}],
                "requires": [], "tags": [], "constraints": [], "length": [5, 200],
                "entities": ["jiang", "du"], "time": {"clock": "main", "start": at, "end": at}}
        self.book.save_plan(chapter, plan, self.rev())
        self.draft.write_bytes(text.encode("utf-8"))
        return {"book_id": self.book.meta("id"), "base_revision": self.rev(), "summary": text.splitlines()[1], "changes": [],
                "world_changes": changes, "review": {"draft_sha256": story.digest(text),
                "checks": {name: {"note": "此为事务夹具，核验引用和实际动作绑定。", "quote": text.splitlines()[1]} for name in story.CHECKS}, "issues": []}}

    def seed(self):
        text = "# 第一章\n江棠清点过身上的三枚铜钱，随后发动一次听风术。\n"
        evidence = self.evidence(1, text)
        changes = {"transfers": [{"id": "opening", "resource": "coin", "receiver": "jiang", "amount": "3", "quantity_text": "三枚", "opening": True, "at": 0, "evidence": evidence}],
                   "uses": [{"id": "first-use", "actor": "jiang", "rule": "listen", "at": 0, "evidence": evidence}]}
        self.assertTrue(self.book.commit(1, self.draft, self.prepare_input(1, text, 0, changes))["exports_complete"])

    def assert_atomic_rejection(self, delta, expected_code):
        revision = self.rev()
        events = self.book.db.execute("SELECT count(*) FROM events").fetchone()[0]
        before = (self.root / self.book.chapter_path(1)).read_bytes()
        with self.assertRaises(story.StoryError) as error:
            self.book.commit(2, self.draft, delta)
        self.assertEqual(error.exception.code, "world_constraint", error.exception.details)
        self.assertIn(expected_code, {b["code"] for b in error.exception.details["checks"]["blockers"]})
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.meta("last_chapter"), 1)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events").fetchone()[0], events)
        self.assertIsNone(self.book.db.execute("SELECT chapter FROM chapters WHERE chapter=2").fetchone())
        self.assertIsNone(self.book.db.execute("SELECT path FROM artifact_state WHERE path='chapters/第一卷 雨夜/第2章 核对行动.md'").fetchone())
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_evidence WHERE chapter=2").fetchone()[0], 0)
        self.assertFalse((self.root / "chapters/第一卷 雨夜/第2章 核对行动.md").exists())
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), before)

    def test_observed_overdraft_is_rejected_even_without_prior_planned_transfer(self):
        self.seed()
        text = "# 第二章\n江棠从那三枚铜钱中付给杜承安四枚，没有其他进项。\n"
        changes = {"transfers": [{"id": "overdraft", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "4", "quantity_text": "四枚", "at": 5, "evidence": self.evidence(2, text)}]}
        delta = self.prepare_input(2, text, 5, changes)
        self.assert_atomic_rejection(delta, "resource_overdraft")
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_transfers WHERE id='overdraft'").fetchone())

    def test_observed_cooldown_is_rejected_even_without_prior_planned_use(self):
        self.seed()
        text = "# 第二章\n只过去五刻，江棠没有获得例外便再次发动听风术。\n"
        changes = {"uses": [{"id": "too-soon", "actor": "jiang", "rule": "listen", "at": 5, "evidence": self.evidence(2, text)}]}
        delta = self.prepare_input(2, text, 5, changes)
        self.assert_atomic_rejection(delta, "cooldown_unfinished")
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_uses WHERE id='too-soon'").fetchone())

    def test_abandoned_author_plan_does_not_double_count_observed_action(self):
        self.seed()
        story.world.save(self.book, {"transfers": [{"id": "old-plan", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "4", "quantity_text": "旧拟案四枚", "at": 5,
                                                     "evidence": {"kind": "author_plan", "note": "已放弃的拟案；保留作为计划历史"}}]}, self.rev())
        text = "# 第二章\n江棠只付给杜承安一枚铜钱，把剩下的两枚收回口袋。\n"
        changes = {"transfers": [{"id": "actual-one", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "1", "quantity_text": "一枚", "at": 5, "evidence": self.evidence(2, text)}]}
        result = self.book.commit(2, self.draft, self.prepare_input(2, text, 5, changes))
        self.assertTrue(result["exports_complete"])
        packet = story.world.context(self.book, {"entities": ["jiang"], "time": {"clock": "main", "start": 6, "end": 6}}, 3)
        self.assertEqual(packet["resources"][0]["amount"], "2")
        receipt = json.loads(self.book.db.execute("SELECT receipt FROM chapters WHERE chapter=2").fetchone()[0])
        self.assertTrue(receipt["world_checks"]["ok"])

    def test_new_chapter_opening_and_payment_are_applied_once(self):
        text = "# 第一章\n江棠先清点五枚铜钱，随即付出两枚，手里留下三枚。\n"
        evidence = self.evidence(1, text)
        changes = {"transfers": [{"id": "z-opening", "resource": "coin", "receiver": "jiang", "amount": "5", "quantity_text": "五枚", "opening": True, "at": 0, "evidence": evidence},
                                  {"id": "a-payment", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "2", "quantity_text": "两枚", "at": 0, "evidence": evidence}]}
        self.assertTrue(self.book.commit(1, self.draft, self.prepare_input(1, text, 0, changes))["exports_complete"])
        packet = story.world.context(self.book, {"entities": ["jiang"], "time": {"clock": "main", "start": 1, "end": 1}}, 2)
        self.assertEqual(packet["resources"][0]["amount"], "3")

    def test_future_same_chapter_rule_does_not_legalize_earlier_use(self):
        self.seed()
        text = "# 第二章\n第五刻江棠再次发动听风术；直到第二十刻，她才学会免除冷却的方法。\n"
        evidence = self.evidence(2, text)
        changes = {"rules": [{"id": "listen-v2", "rule": "listen", "version": 2, "start": 20, "cooldown": 0, "hard": True, "description": "第二十刻后免除冷却", "entities": ["jiang"], "evidence": evidence}],
                   "uses": [{"id": "early-use", "actor": "jiang", "rule": "listen", "at": 5, "evidence": evidence}]}
        delta = self.prepare_input(2, text, 5, changes)
        self.assert_atomic_rejection(delta, "cooldown_unfinished")
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_rules WHERE id='listen-v2'").fetchone())


if __name__ == "__main__":
    unittest.main()
