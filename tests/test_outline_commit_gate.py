"""First native commit requires an adopted outline; historical reads stay usable."""

from contextlib import contextmanager
import importlib.util
import unittest

import test_story as base
from outline_fixture import bind_adopted_outline


story = base.story
legacy_spec = importlib.util.spec_from_file_location(
    "outline_gate_schema1", base.ROOT / "tests/fixtures/schema1_runtime.py")
legacy = importlib.util.module_from_spec(legacy_spec)
legacy_spec.loader.exec_module(legacy)


class OutlineCommitGateTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    delta = base.StoryTests.delta
    assert_error = base.StoryTests.assert_error

    def snapshot(self):
        rows = "\n".join(self.book.db.iterdump())
        files = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in self.root.rglob("*")
                 if p.is_file() and not p.name.startswith("state.sqlite3")}
        return rows, files

    @contextmanager
    def legacy_native(self):
        """Build real old-format state through the retained runtime, then migrate it."""
        root = self.root.parent / "旧版未绑定小说"
        legacy.Book.create(root, "门后的雨", "long")
        draft = root / "旧章.md"
        draft.write_bytes(base.DRAFT.encode("utf-8"))
        book = legacy.Book(root)
        try:
            book.save_notes([base.card()], book.meta("revision"))
            book.save_plan(1, base.plan(), book.meta("revision"))
            raw = self.delta()
            raw.update(book_id=book.meta("id"), base_revision=book.meta("revision"))
            book.commit(1, draft, raw)
        finally:
            book.close()
        story.storage.migrate(story, root)
        current = story.Book(root)
        try:
            yield current, draft, raw
        finally:
            current.close()

    def test_unbound_new_commit_rejects_without_database_or_file_changes(self):
        raw = self.delta(changes=[{"id": "hero", "text": "沈禾已交出钥匙。",
                                  "quote": "沈禾把唯一的钥匙交给守门人。"}])
        before = self.snapshot()
        error = self.assert_error("outline_binding_required", self.book.commit,
                                  1, self.draft, raw)
        self.assertEqual(error.details["chapter"], 1)
        self.assertEqual(error.details["recovery_command"], "outline-bind")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.book.meta("last_chapter"), 0)

        bind_adopted_outline(story, self.book, 1)
        self.assert_error("stale_revision", self.book.commit, 1, self.draft, raw)
        raw["base_revision"] = self.book.meta("revision")
        result = self.book.commit(1, self.draft, raw)
        self.assertTrue(result["committed"])
        self.assertTrue(result["exports_complete"])
        self.assertTrue(self.book.commit(1, self.draft, raw)["idempotent"])

    def test_unbound_candidate_reads_and_prepare_remain_read_only(self):
        before = self.snapshot()
        self.assertEqual(self.book.context(1)["outline"], {"status": "unbound"})
        lint = self.book.lint(1, self.draft)
        self.assertTrue(lint["ok"])
        self.assertEqual(lint["outline"], {"status": "unbound"})
        prepared = self.book.prepare(1, self.draft)
        self.assertFalse(prepared["ready_to_commit"])
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(story.outline.binding_for(self.book, 1))

    def test_migrated_unbound_native_retry_and_latest_revision_stay_compatible(self):
        with self.legacy_native() as (book, draft, raw):
            self.assertIsNone(story.outline.binding_for(book, 1))
            self.assertTrue(book.commit(1, draft, raw)["idempotent"])
            revised = base.DRAFT + "她没有回头。\n"
            draft.write_bytes(revised.encode("utf-8"))
            updated = self.delta(revised)
            updated.update(book_id=book.meta("id"), base_revision=book.meta("revision"))
            self.assertTrue(book.commit(1, draft, updated, replace_last=True)["committed"])
            self.assertIsNone(story.outline.binding_for(book, 1))

    def test_migrated_book_next_native_chapter_requires_binding(self):
        with self.legacy_native() as (book, draft, _):
            book.save_plan(2, base.plan(title="第二夜"), book.meta("revision"))
            text = base.DRAFT.replace("第1章 门后的雨", "第2章 第二夜")
            draft.write_bytes(text.encode("utf-8"))
            raw = self.delta(text)
            raw.update(book_id=book.meta("id"), base_revision=book.meta("revision"))
            self.assert_error("outline_binding_required", book.commit, 2, draft, raw)
            self.assertEqual(book.meta("last_chapter"), 1)
            bind_adopted_outline(story, book, 2)
            raw["base_revision"] = book.meta("revision")
            self.assertTrue(book.commit(2, draft, raw)["committed"])

    def test_migrated_unbound_history_replay_and_publication_remain_compatible(self):
        with self.legacy_native() as (book, draft, _):
            # A reviewed historical plan may be restored without inventing an old binding.
            book.save_plan(1, base.plan(title="门后的雨"), book.meta("revision"))
            packet = story.history.branch_start(book, 1, book.meta("revision"))
            revised = base.DRAFT + "她没有回头。\n"
            candidate = {"sha": story.digest(revised), "summary": "沈禾交钥匙后进入门内。",
                         "dependencies": [], "complete": False}
            review = self.delta(revised)["review"]
            review["candidate_sha256"] = story.history.candidate_fingerprint(candidate)
            staged = story.history.branch_update(book, packet["branch"], {"chapters": [{
                "chapter": 1, "text": revised, "summary": candidate["summary"],
                "dependencies": [], "complete": False, "review": review}]}, book.meta("revision"))
            semantic = {**staged["review_template"], "note": "合成旧章兼容测试已核对修订范围。",
                        "state_review": "未变更旧章事实卡。", "coverage_review": "仅有第1章，无其他后文。"}
            story.history.branch_update(book, packet["branch"], {"semantic_review": semantic},
                                        book.meta("revision"))
            result = story.history.branch_publish(book, packet["branch"], book.meta("revision"))
            self.assertTrue(result["committed"])
            self.assertTrue(result["exports_complete"])
            self.assertIsNone(story.outline.binding_for(book, 1))
            replay = story.history.history_state(book, 1)
            self.assertEqual(replay["cards"][0]["text"], base.card()["text"])

    def test_unbound_adoption_and_backfill_keep_imported_identity(self):
        root = self.root.parent / "导入短篇"
        story.Book.create(root, "导入短篇", "short")
        book = story.Book(root)
        try:
            book.adopt(3, self.draft, "旧稿第3章的实际衔接。", book.meta("revision"),
                       volume_dir="第一卷 雨夜")
            for chapter in (1, 2):
                result = book.adopt_backfill(chapter, self.draft, "补齐作者提供的原章。",
                                            book.meta("revision"), volume_dir="第一卷 雨夜")
                self.assertEqual(result["quality"], "imported_unverified")
                self.assertIsNone(story.outline.binding_for(book, chapter))
            self.assertEqual(book.meta("last_chapter"), 3)
            self.assertEqual(book.meta("imported_through"), 3)
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
