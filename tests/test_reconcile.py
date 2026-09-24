import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_reconcile_tests", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 第1章 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
REVISED = "# 第1章 门后的雨\n沈禾收回了唯一的钥匙。\n她决定另找入口，守门人退回雨里。\n"
ORIGINAL_QUOTE = "沈禾把唯一的钥匙交给守门人。"
REVISED_QUOTE = "沈禾收回了唯一的钥匙。"


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-reconcile-test-")
        self.root = Path(self.temp.name) / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.hero = {"id": "hero", "kind": "character", "text": "沈禾持有唯一的钥匙。",
                     "source": "用户设定", "tags": ["沈禾"]}
        self.book.save_notes([self.hero], self.book.meta("revision"))
        self.save_plan(1)
        self.draft = self.root / ".story/drafts/revision.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        delta = self.delta(DRAFT, ORIGINAL_QUOTE)
        delta["changes"] = [
            {"id": "hero", "text": "沈禾已交出钥匙。", "quote": ORIGINAL_QUOTE},
            {"id": "debt", "kind": "hook", "text": "天亮前带回账本。", "tags": ["沈禾"],
             "due": 1, "quote": "她答应在天亮之前带回账本。"},
        ]
        self.book.commit(1, self.draft, delta)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def save_plan(self, chapter):
        plan = {"volume_dir": "第一卷 雨夜", "goal": "选择是否交出钥匙", "stop": "确定入口选择后停笔",
                "beats": [{"choice": "沈禾决定钥匙的去向", "change": "与守门人的关系改变"}],
                "constraints": [], "requires": ["hero"], "tags": ["沈禾"], "length": [20, 120]}
        self.book.save_plan(chapter, plan, self.book.meta("revision"))

    def delta(self, text, quote):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾重新选择钥匙的去向。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "选择及其后果在正文中可见。", "quote": quote}
                    for check in story.CHECKS}, "issues": []}}

    def edited_chapter(self, chapter=1, text=REVISED):
        target = self.root / self.book.chapter_path(chapter)
        target.write_bytes(text.encode("utf-8"))
        return target

    def reconcile_input(self, packet, text=REVISED):
        self.draft.write_bytes(text.encode("utf-8"))
        delta = self.delta(text, REVISED_QUOTE)
        delta["base_revision"] = packet["revision"]
        delta["external_sha256"] = packet["external_edit"]["sha256"]
        return delta

    def snapshot(self):
        database = {table: [tuple(row) for row in self.book.db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    for table in ("meta", "cards", "plans", "chapters", "events", "artifacts")}
        files = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in (self.root / "chapters").rglob("*.md")}
        return database, files

    def assert_rejected_without_changes(self, function, expected_code=None):
        before = self.snapshot()
        with self.assertRaises(story.StoryError) as result:
            function()
        if expected_code is not None:
            self.assertEqual(result.exception.code, expected_code)
        self.assertEqual(self.snapshot(), before)
        return result.exception

    def commit_second_chapter(self):
        self.save_plan(2)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        self.book.commit(2, self.draft, self.delta(DRAFT, ORIGINAL_QUOTE))

    def test_reconcile_packet_commit_card_rollback_and_idempotent_retry(self):
        target = self.edited_chapter()
        before_packet = self.snapshot()
        packet = self.book.reconcile(1)
        self.assertEqual(self.snapshot(), before_packet)
        self.assertEqual(packet["mode"], "reconcile_last")
        self.assertEqual(packet["chapter"], 1)
        self.assertEqual(packet["revision"], self.book.meta("revision"))
        self.assertTrue(packet["external_edit"]["path"])
        self.assertEqual(packet["external_edit"]["sha256"], story.digest(REVISED))
        cards = {card["id"]: card for card in packet["required_cards"] + packet["optional_cards"]}
        self.assertEqual(cards["hero"]["text"], self.hero["text"])
        self.assertNotIn("debt", cards)

        reviewed_text = REVISED + "她把钥匙藏进衣襟。\n"
        delta = self.reconcile_input(packet, reviewed_text)
        delta["changes"] = [{"id": "hero", "text": "沈禾收回钥匙，另找入口。", "quote": REVISED_QUOTE}]
        result = self.book.reconcile(1, self.draft, delta)

        self.assertTrue(result["committed"])
        self.assertTrue(result["exports_complete"])
        self.assertEqual(target.read_bytes(), reviewed_text.encode("utf-8"))
        self.assertEqual(self.book.cards()["hero"]["text"], "沈禾收回钥匙，另找入口。")
        self.assertNotIn("debt", self.book.cards())
        self.assertEqual(self.book.meta("revision"), packet["revision"] + 1)
        state_after_commit = self.snapshot()
        retried = self.book.reconcile(1, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])
        self.assertEqual(self.snapshot(), state_after_commit)

    def test_editor_save_after_packet_rejects_stale_external_hash(self):
        target = self.edited_chapter()
        packet = self.book.reconcile(1)
        delta = self.reconcile_input(packet)
        new_edit = REVISED + "这是用户后来追加的句子。\n"
        target.write_bytes(new_edit.encode("utf-8"))
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(1, self.draft, delta), "stale_external")
        self.assertEqual(target.read_bytes(), new_edit.encode("utf-8"))

    def test_missing_or_wrong_external_hash_cannot_authorize_overwrite(self):
        self.edited_chapter()
        packet = self.book.reconcile(1)
        valid = self.reconcile_input(packet)
        for value in (None, "0" * 64, "invalid-hash"):
            with self.subTest(external_sha256=value):
                delta = copy.deepcopy(valid)
                if value is None:
                    delta.pop("external_sha256")
                else:
                    delta["external_sha256"] = value
                self.assert_rejected_without_changes(lambda: self.book.reconcile(1, self.draft, delta))

    def test_other_chapter_drift_blocks_reconcile_packet_and_submission(self):
        self.commit_second_chapter()
        self.edited_chapter(2)
        packet = self.book.reconcile(2)
        delta = self.reconcile_input(packet)
        self.edited_chapter(1, "另一章的用户修改必须先处理。\n")
        self.assert_rejected_without_changes(lambda: self.book.reconcile(2), "exports_unresolved")
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(2, self.draft, delta), "exports_unresolved")

    def test_pending_export_blocks_reconcile_packet_and_submission(self):
        self.commit_second_chapter()
        self.edited_chapter(2)
        packet = self.book.reconcile(2)
        delta = self.reconcile_input(packet)
        (self.root / self.book.chapter_path(1)).unlink()
        self.assertEqual(self.book.status()["pending_export_count"], 1)
        self.assert_rejected_without_changes(lambda: self.book.reconcile(2), "exports_unresolved")
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(2, self.draft, delta), "exports_unresolved")

    def test_reconcile_cannot_replace_older_chapter(self):
        self.commit_second_chapter()
        self.edited_chapter(1)
        self.assert_rejected_without_changes(lambda: self.book.reconcile(1), "chapter_order")

    def test_reconcile_cannot_replace_imported_latest_chapter(self):
        root = Path(self.temp.name) / "imported"
        story.Book.create(root, "已导入的书", "long")
        imported = story.Book(root)
        try:
            imported.adopt(108, self.draft, "导入最后完整章", imported.meta("revision"), volume_dir="第一卷 雨夜")
            target = root / imported.chapter_path(108)
            target.write_bytes(REVISED.encode("utf-8"))
            revision = imported.meta("revision")
            with self.assertRaises(story.StoryError) as result:
                imported.reconcile(108)
            self.assertEqual(result.exception.code, "chapter_order")
            self.assertEqual(imported.meta("revision"), revision)
            self.assertEqual(target.read_bytes(), REVISED.encode("utf-8"))
        finally:
            imported.close()

    def test_reconcile_still_enforces_lint_and_exact_draft_review(self):
        self.edited_chapter()
        packet = self.book.reconcile(1)
        oversized = REVISED + "雨" * 150
        bad_length = self.reconcile_input(packet, oversized)
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(1, self.draft, bad_length), "lint_failed")

        delta = self.reconcile_input(packet)
        self.draft.write_bytes((REVISED + "她停了一步。\n").encode("utf-8"))
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(1, self.draft, delta), "stale_review")

        blocked = self.reconcile_input(packet)
        blocked["review"]["issues"] = [{"severity": "blocker", "issue": "还有未解决的连续性问题。"}]
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(1, self.draft, blocked), "review_blocker")

    def test_reconcile_rejects_state_revision_changed_after_packet(self):
        self.edited_chapter()
        packet = self.book.reconcile(1)
        delta = self.reconcile_input(packet)
        self.book.save_notes([{"id": "new-rule", "kind": "world", "text": "雨夜不能点灯。",
                              "source": "用户新增设定"}], self.book.meta("revision"))
        self.assert_rejected_without_changes(
            lambda: self.book.reconcile(1, self.draft, delta), "stale_revision")

    def test_later_card_edit_routes_revision_and_external_edit_to_history(self):
        self.book.save_notes([{**self.hero, "text": "沈禾收回钥匙，自行选择入口。", "source": "作者修订"}],
                             self.book.meta("revision"))
        current_hero = self.book.cards(["hero"])["hero"]
        for operation in (lambda: self.book.context(1), lambda: self.book.prepare(1, self.draft),
                          lambda: self.book.commit(1, self.draft, self.delta(DRAFT, ORIGINAL_QUOTE), replace_last=True)):
            error = self.assert_rejected_without_changes(operation, "revised_state_conflict")
            self.assertEqual(error.details, {"card": "hero", "chapter": 1, "recovery_command": "history-start"})

        target = self.edited_chapter()
        error = self.assert_rejected_without_changes(lambda: self.book.reconcile(1), "revised_state_conflict")
        self.assertEqual(error.details["recovery_command"], "history-start")
        packet = story.history.branch_start(self.book, error.details["chapter"], self.book.meta("revision"))
        self.assertEqual(set(packet["required_state_ids"]), {"hero", "debt"})
        candidate = {"sha": story.digest(REVISED), "summary": "沈禾收回钥匙，决定另找入口。",
                     "dependencies": [{"kind": "card", "ref": "hero", "sha": story.digest(story.dumps(current_hero))}],
                     "complete": True, "external_sha256": packet["affected"][0]["external_edit"]["sha256"]}
        review = {"draft_sha256": candidate["sha"], "candidate_sha256": story.history.candidate_fingerprint(candidate),
                  "checks": {name: {"note": "新稿收回钥匙，与作者修订的状态一致。", "quote": REVISED_QUOTE}
                             for name in story.CHECKS}, "issues": []}
        decisions = [{"id": item["id"], "before_sha": item["before_sha"],
                      "after": current_hero if item["id"] == "hero" else None,
                      "chapter": 1, "quote": REVISED_QUOTE,
                      "note": "保留作者修订后的钥匙状态，并撤回旧稿中已删除的取账本承诺。"}
                     for item in packet["state_review_template"]]
        staged = story.history.branch_update(self.book, packet["branch"], {
            "chapters": [{"chapter": 1, "text": REVISED, **candidate, "review": review}],
            "state_changes": decisions}, self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "新旧稿及作者修订均已核对。",
                    "state_review": "保留作者的钥匙状态，撤回已从正文删除的承诺。",
                    "coverage_review": "本书只有本章；全部相关卡片与外部稿都已复核。"}
        story.history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.book.meta("revision"))
        result = story.history.branch_publish(self.book, packet["branch"], self.book.meta("revision"))
        self.assertTrue(result["exports_complete"], result)
        self.assertEqual(target.read_text(encoding="utf-8"), REVISED)
        self.assertEqual(self.book.cards(["hero"])["hero"], current_hero)
        self.assertNotIn("debt", self.book.cards())


if __name__ == "__main__":
    unittest.main()
