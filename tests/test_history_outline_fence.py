"""Historical reviews stay bound to every affected chapter's adopted outline."""
import copy
import hashlib
import unittest

import test_long_history as fixture


history, story = fixture.history, fixture.story


class HistoryOutlineFenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.LongHistoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book

    def revision(self):
        return self.book.meta("revision")

    def snapshot(self):
        files = {path.relative_to(self.fixture.root).as_posix(): path.read_bytes()
                 for path in self.fixture.root.rglob("*")
                 if path.is_file() and not path.name.startswith("state.sqlite3")}
        return self.revision(), tuple(self.book.db.iterdump()), self.book.path.read_bytes(), files

    def assert_code(self, code, call):
        before = self.snapshot()
        with self.assertRaises(story.StoryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.snapshot(), before)
        return caught.exception

    def seed(self, consumer=False, independent=False):
        f = self.fixture
        f.add(1)
        f.dep(1)
        if consumer or independent:
            f.add(2)
            f.dep(2, [1] if consumer else [])
        return self.stage(f.start())

    def stage(self, packet):
        f = self.fixture
        revised = f.texts[1].replace("一张收据", "两张收据")
        candidates = []
        for item in packet["affected"]:
            chapter = item["chapter"]
            text = revised if chapter == 1 else f.texts[chapter]
            dependencies = [{"kind": "chapter", "ref": "1", "sha": story.digest(revised)}] if chapter == 2 else []
            candidates.append(f.candidate(chapter, text, dependencies))
        staged = history.branch_update(self.book, packet["branch"], {"chapters": candidates}, self.revision())
        review = {**staged["review_template"], "note": "复核各章最终正文及其计划。",
                  "state_review": "交接之后的事实状态已经逐项核对。",
                  "coverage_review": "受影响章节及未声明的关系均已核对。"}
        return history.branch_update(self.book, packet["branch"], {"semantic_review": review}, self.revision())

    def rebind(self, chapter, *, change=True, relative=None):
        binding = story.outline.binding_for(self.book, chapter)
        original = self.fixture.root / binding["path"] if binding else None
        text = original.read_text(encoding="utf-8") if original else "# 章细纲\n状态：已采用\n"
        if change:
            text += "\n场景补充：交接完成后由同行者熄灯。\n"
        relative = relative or (binding["path"] if binding else "01_大纲细纲/第1章 交接细纲.md")
        target = self.fixture.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return story.outline.bind(self.book, chapter, relative, self.revision(),
                                  hashlib.sha256(target.read_bytes()).hexdigest())

    def assert_rebind_blocks(self, packet, chapter):
        branch = packet["branch"]
        error = self.assert_code("stale_dependency", lambda: history.branch_refresh(self.book, branch, self.revision()))
        self.assertEqual(error.details["dependency"], "outline:" + str(chapter))
        # Neither skipping refresh nor an empty update may renew the old receipt.
        self.assert_code("stale_branch", lambda: history.branch_update(self.book, branch, {}, self.revision()))
        self.assert_code("stale_branch", lambda: history.branch_publish(self.book, branch, self.revision()))
        self.assertEqual(history.branch_inspect(self.book, branch)["status"], "candidate")

    def test_rebound_target_requires_a_new_branch_and_fresh_reviews(self):
        staged = self.seed()
        old_plan = self.book.get_plan(1)
        self.rebind(1)
        self.assertEqual(self.book.get_plan(1), old_plan)
        self.assert_rebind_blocks(staged, 1)

        fresh = self.fixture.start()
        self.assertFalse(fresh["semantic_reviewed"])
        self.assertFalse(fresh["affected"][0]["reviewed"])
        self.assert_code("review_incomplete", lambda: history.branch_publish(self.book, fresh["branch"], self.revision()))
        reviewed = self.stage(fresh)
        result = history.branch_publish(self.book, reviewed["branch"], self.revision())
        self.assertTrue(result["committed"])
        self.assertTrue(result["exports_complete"])

    def test_unchanged_consumer_body_still_fences_its_outline(self):
        staged = self.seed(consumer=True)
        self.assertEqual([item["chapter"] for item in staged["affected"]], [1, 2])
        consumer = history.branch_inspect(self.book, staged["branch"], chapter=2)
        self.assertEqual(consumer["candidate"]["text"], self.fixture.texts[2])
        self.rebind(2)
        self.assert_rebind_blocks(staged, 2)

    def test_same_outline_bytes_at_a_different_path_require_new_review(self):
        staged = self.seed()
        before = story.outline.binding_for(self.book, 1)
        self.rebind(1, change=False, relative="01_大纲细纲/第一卷 雨夜/第1章 已采用细纲.md")
        after = story.outline.binding_for(self.book, 1)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertNotEqual(before["path"], after["path"])
        self.assert_rebind_blocks(staged, 1)

    def test_unbound_imported_chapter_cannot_adopt_an_outline_after_review(self):
        f = self.fixture
        text = "第1章 核对交接1\n沈禾在渡口交出钥匙，留下一张收据。灯还亮着。\n"
        f.draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(1, f.draft, "交出钥匙并留下收据。", self.revision(), "第一卷 雨夜")
        f.texts[1] = text
        self.book.save_plan(1, {"volume_dir": "第一卷 雨夜", "title": "核对交接1",
            "goal": "完成交接", "stop": "留在渡口", "beats": [{"choice": "交出钥匙", "change": "保留收据"}],
            "length": [10, 200], "length_exception": {"source": "user_request", "quote": "测试需要10至200字。"}}, self.revision())
        f.dep(1)
        self.assertIsNone(story.outline.binding_for(self.book, 1))
        staged = self.stage(f.start())
        self.rebind(1)
        self.assert_rebind_blocks(staged, 1)

    def test_independent_chapter_rebinding_preserves_existing_review(self):
        staged = self.seed(independent=True)
        branch = staged["branch"]
        self.assertEqual([item["chapter"] for item in staged["affected"]], [1])
        previous = history.branch_saved(self.book, branch)["saved"]
        self.rebind(2)
        refreshed = history.branch_refresh(self.book, branch, self.revision())
        self.assertTrue(refreshed["semantic_reviewed"])
        self.assertEqual(history.branch_saved(self.book, branch)["saved"], previous)
        result = history.branch_publish(self.book, branch, self.revision())
        self.assertEqual(result["chapters"], [1])

    def test_same_path_same_bytes_rebinding_does_not_expire_review(self):
        staged = self.seed()
        branch = staged["branch"]
        revision = self.revision()
        previous = history.branch_saved(self.book, branch)["saved"]
        self.rebind(1, change=False)
        self.assertEqual(self.revision(), revision)
        refreshed = history.branch_refresh(self.book, branch, self.revision())
        self.assertTrue(refreshed["semantic_reviewed"])
        self.assertEqual(history.branch_saved(self.book, branch)["saved"], previous)
        self.assertTrue(history.branch_publish(self.book, branch, self.revision())["committed"])

    def remove_outline_fences(self, branch, chapters):
        # Reproduce persisted pre-fence branch data; no production command edits it.
        _, data = history._branch(self.book, branch)
        for chapter in chapters:
            data["fences"].pop("outline:" + str(chapter), None)
        if data["semantic_review"]:
            data["semantic_review"]["manifest_sha256"] = history._manifest(data)
        with self.book.transaction():
            self.book.db.execute("UPDATE history_branches SET data=? WHERE id=?", (story.dumps(data), branch))
        return copy.deepcopy(data)

    def test_legacy_candidate_missing_any_outline_fence_stays_readable_but_cannot_resume(self):
        staged = self.seed(consumer=True)
        branch = staged["branch"]
        legacy = self.remove_outline_fences(branch, [2])
        before = self.snapshot()
        inspected = history.branch_inspect(self.book, branch, chapter=2)
        saved = history.branch_saved(self.book, branch)
        self.assertEqual(inspected["candidate"]["text"], self.fixture.texts[2])
        self.assertEqual(saved["saved"]["semantic_review"], legacy["semantic_review"])
        self.assertEqual(self.snapshot(), before)
        for call in (lambda: history.branch_update(self.book, branch, {}, self.revision()),
                     lambda: history.branch_refresh(self.book, branch, self.revision()),
                     lambda: history.branch_publish(self.book, branch, self.revision())):
            error = self.assert_code("stale_branch", call)
            self.assertEqual(error.details["recovery_command"], "history-start")
        fresh = self.stage(self.fixture.start())
        self.assertTrue(history.branch_publish(self.book, fresh["branch"], self.revision())["committed"])

    def test_published_legacy_branch_remains_idempotent_without_outline_fences(self):
        staged = self.seed()
        branch = staged["branch"]
        published = history.branch_publish(self.book, branch, self.revision())
        self.remove_outline_fences(branch, [1])
        revision = self.revision()
        versions = self.book.db.execute("SELECT count(*) FROM history_versions").fetchone()[0]
        publications = self.book.db.execute("SELECT count(*) FROM events WHERE kind='history_publish'").fetchone()[0]
        retry = history.branch_publish(self.book, branch, staged["revision"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["revision"], published["revision"])
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM history_versions").fetchone()[0], versions)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events WHERE kind='history_publish'").fetchone()[0], publications)


if __name__ == "__main__":
    unittest.main()
