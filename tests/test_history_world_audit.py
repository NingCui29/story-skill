"""Regression cases for complete dependency declarations and action-time ledgers."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("history_world_audit_runtime", PATH)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class HistoryWorldAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-history-world-audit-")
        self.root = Path(self.temp.name) / "book"
        story.Book.create(self.root, "行动时点回归夹具", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        self.book.save_notes([{"id": "key", "text": "钥匙留在库房。", "source": "作者设定"}], self.rev())
        story.world.save(self.book, {"entities": [
            {"id": "holder", "name": "江棠", "kind": "character", "description": "指定资金持有人"},
            {"id": "coin", "name": "铜钱", "kind": "resource", "description": "以枚计数"}]}, self.rev())

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def rev(self):
        return self.book.meta("revision")

    def text(self, chapter):
        return f"# 第{chapter}章\n江棠核对账本，逐笔记录铜钱的来去，最后留下收据。\n"

    def evidence(self, chapter):
        return {"kind": "chapter", "chapter": chapter, "sha256": story.digest(self.text(chapter)), "quote": "逐笔记录铜钱的来去"}

    def transfer(self, rid, amount, at, chapter=None, opening=False, income=False):
        return {"id": rid, "resource": "coin", "sender": None if income or opening else "holder",
                "receiver": "holder" if income or opening else None, "amount": amount,
                "quantity_text": amount + "枚", "at": at, "opening": opening,
                "evidence": self.evidence(chapter) if chapter else {"kind": "author_plan", "note": "拟写的收支动作"}}

    def prepare(self, chapter, transfers=(), time=None, requires=()):
        plan = {"volume_dir": "第一卷 雨夜", "title": "核对交接", "goal": "核对交接", "stop": "留下收据", "requires": list(requires), "tags": [],
                "constraints": [], "beats": [{"choice": "核对账本", "change": "留下收据"}], "length": [10, 200]}
        if time is not None:
            plan["time"] = {"clock": "main", "start": time[0], "end": time[1]}
        self.book.save_plan(chapter, plan, self.rev())
        text = self.text(chapter)
        self.draft.write_bytes(text.encode("utf-8"))
        return {"book_id": self.book.meta("id"), "base_revision": self.rev(), "summary": "核对账本后留下收据。", "changes": [],
                **({"world_changes": {"transfers": list(transfers)}} if transfers else {}),
                "review": {"draft_sha256": story.digest(text), "checks": {
                    name: {"note": "已核对夹具的动作与后果。", "quote": "最后留下收据"} for name in story.CHECKS}, "issues": []}}

    def publish(self, chapter, **kwargs):
        self.book.commit(chapter, self.draft, self.prepare(chapter, **kwargs))

    def assert_code(self, code, call, *args):
        with self.assertRaises(story.StoryError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def test_complete_history_dependencies_cannot_omit_required_card(self):
        self.publish(1, requires=["key"])
        revision = self.rev()
        head = story.history._head(self.book, 1)["id"]
        payload = {"chapter": 1, "chapter_sha": story.digest(self.text(1)), "dependencies": [],
                   "complete": True, "note": "此声明遗漏了必读卡。"}
        self.assert_code("missing_required_dependencies", story.history.save_dependencies, self.book, payload, revision)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(story.history._head(self.book, 1)["id"], head)
        payload["dependencies"] = [{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(self.book.cards(["key"])["key"]))}]
        self.assertTrue(story.history.save_dependencies(self.book, payload, self.rev())["complete"])

    def test_complete_history_candidate_cannot_omit_current_required_card(self):
        self.publish(1)
        self.book.save_plan(1, {**self.book.get_plan(1), "requires": ["key"]}, self.rev())
        packet = story.history.branch_start(self.book, 1, self.rev())
        revision = self.rev()
        payload = {"chapters": [{"chapter": 1, "text": self.text(1), "summary": "核对账本后留下收据。", "dependencies": [], "complete": True}]}
        self.assert_code("missing_required_dependencies", story.history.branch_update, self.book, packet["branch"], payload, revision)
        self.assertEqual(self.rev(), revision)
        self.assertIsNone(story.history.branch_inspect(self.book, packet["branch"], chapter=1)["candidate"])

    def test_cache_read_has_one_snapshot_when_dependency_review_changes(self):
        self.publish(1)
        payload = {"chapter": 1, "chapter_sha": story.digest(self.text(1)), "dependencies": [],
                   "complete": True, "note": "已完整核查本章依赖。"}
        story.history.save_dependencies(self.book, payload, self.rev())
        story.history.cache_put(self.book, 1, "review", {"review": "证据已核查"}, self.rev())
        # WAL makes the reader/writer interleaving deterministic without threads;
        # a rollback-journal writer instead waits for the same read snapshot.
        self.book.db.execute("PRAGMA journal_mode=WAL")
        writer = story.Book(self.root)
        original = story.history._cache_key

        def change_after_key(book, chapter, kind):
            key = original(book, chapter, kind)
            story.history.save_dependencies(writer, {**payload, "complete": False, "note": "补查发现依赖未完成。"}, writer.meta("revision"))
            return key

        try:
            with patch.object(story.history, "_cache_key", side_effect=change_after_key):
                result = story.history.cache_get(self.book, 1, "review")
            self.assertTrue(result["hit"])
            self.assertTrue(result["dependency_review_declared_complete"], result)
            self.assertFalse(self.book.db.in_transaction)
            self.assertFalse(story.history.cache_get(self.book, 1, "review")["hit"])
        finally:
            writer.close()

    def assert_overdraft_rollback(self, transfers, time=None):
        raw = self.prepare(2, transfers=transfers, time=time)
        revision = self.rev()
        error = self.assert_code("world_constraint", self.book.commit, 2, self.draft, raw)
        self.assertIn("resource_overdraft", {b["code"] for b in error.details["checks"]["blockers"]})
        self.assertEqual(self.rev(), revision)
        self.assertIsNone(self.book.db.execute("SELECT chapter FROM chapters WHERE chapter=2").fetchone())
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_evidence WHERE chapter=2").fetchone()[0], 0)
        self.assertFalse((self.root / "chapters/第一卷 雨夜/第2章 核对交接.md").exists())

    def test_explicit_action_time_checks_overdraft_without_plan_time(self):
        self.publish(1, transfers=[self.transfer("opening", "3", 0, 1, opening=True)])
        self.assert_overdraft_rollback([self.transfer("spend", "4", 5, 2)])

    def test_later_plan_start_cannot_lend_future_income_to_earlier_action(self):
        self.publish(1, transfers=[self.transfer("opening", "3", 0, 1, opening=True), self.transfer("later-income", "10", 20, 1, income=True)])
        self.assert_overdraft_rollback([self.transfer("spend", "4", 5, 2)], time=(30, 30))

    def test_observed_income_inside_scene_is_available_to_later_planned_action(self):
        self.publish(1, transfers=[self.transfer("opening", "3", 0, 1, opening=True), self.transfer("income", "4", 10, 1, income=True)])
        story.world.save(self.book, {"transfers": [self.transfer("spend", "5", 20)]}, self.rev())
        checked = story.world.check(self.book, {"entities": ["holder"], "time": {"start": 5, "end": 20}}, 2)
        self.assertTrue(checked["ok"], checked)

    def test_successive_actions_share_balance_and_cannot_reset_to_observed_baseline(self):
        self.publish(1, transfers=[self.transfer("opening", "3", 0, 1, opening=True)])
        self.assert_overdraft_rollback([self.transfer("spend-one", "2", 5, 2), self.transfer("spend-two", "2", 6, 2)], time=(5, 6))

    def test_hook_payoff_cannot_hide_a_stale_seed_after_body_replacement(self):
        self.publish(1)
        seed = {"id": "seed", "hook": "receipt", "state": "seeded", "description": "记下交接承诺", "at": 0,
                "entities": ["holder"], "evidence": self.evidence(1)}
        story.world.save(self.book, {"hooks": [seed]}, self.rev())
        original_text = self.text
        revised_text = self.text(1).replace("逐笔记录", "再次逐笔记录")
        with patch.object(self, "text", side_effect=lambda c: revised_text if c == 1 else original_text(c)):
            raw = self.prepare(1)
            revision = self.rev()
            self.assert_code("history_revision_required", lambda: self.book.commit(1, self.draft, raw, replace_last=True))
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.chapter_read(1)["text"], original_text(1))
        self.assertIsNotNone(story.world.resolve_dependency(self.book, "hooks", "seed"))
        # Preserve coverage of stale evidence left by older runtimes. Only this
        # isolated fixture bypasses publication to reproduce that legacy state.
        with self.book.transaction():
            self.book.db.execute("UPDATE chapters SET text=?,sha=? WHERE chapter=1", (revised_text, story.digest(revised_text)))
            self.book.queue_artifact(self.book.chapter_path(1), revised_text)
        self.book.export()
        self.publish(2)
        revision = self.rev()
        payoff = {**seed, "id": "payoff", "state": "fulfilled", "at": 5, "evidence": self.evidence(2)}
        self.assert_code("stale_world_evidence", story.world.save, self.book, {"hooks": [payoff]}, revision)
        self.assertEqual(self.rev(), revision)
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_hooks WHERE id='payoff'").fetchone())


if __name__ == "__main__":
    unittest.main()
