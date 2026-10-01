"""Explicit adopted-outline binding without guessing Markdown intent."""

import hashlib
import importlib.util
import unittest

import test_story as base
import test_long_history as history_fixture


MODULE = base.ROOT / "skills/story-skill/scripts/story_outline.py"
spec = importlib.util.spec_from_file_location("story_outline", MODULE)
outline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outline)


class OutlineSyncTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    assert_error = base.StoryTests.assert_error
    delta = base.StoryTests.delta

    def readable(self, path="01_大纲细纲/第一卷 雨夜/第1章 门后的雨.md", content="状态：已采用\n沈禾交出钥匙。\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return path, hashlib.sha256(target.read_bytes()).hexdigest()

    def bind(self, path, sha):
        return outline.bind(self.book, 1, path, self.book.meta("revision"), sha)

    def test_legacy_is_unbound_and_read_only(self):
        plan = self.book.get_plan(1)
        revision = self.book.meta("revision")
        self.assertEqual(outline.verify(self.book, 1, plan), {"status": "unbound"})
        self.assertIsNone(outline.binding_for(self.book, 1))
        self.assertEqual(self.book.context(1)["outline"]["status"], "unbound")
        self.assertEqual(self.book.lint(1, self.draft)["outline"]["status"], "unbound")
        self.assertEqual(self.book.meta("revision"), revision)

    def test_cli_binding_gates_context_lint_prepare_and_commit(self):
        path, sha = self.readable()
        args = base.story.parser().parse_args([
            "outline-bind", "--book", str(self.root), "--chapter", "1", "--file", path,
            "--sha256", sha, "--expect", str(self.book.meta("revision"))])
        bound = base.story.run(args)
        self.assertEqual(bound["outline"]["status"], "adopted")
        self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")
        self.assertEqual(self.book.lint(1, self.draft)["outline"]["status"], "adopted")
        self.root.joinpath(path).write_text("状态：已采用\n细纲已经改变。\n", encoding="utf-8")
        for operation in (
                lambda: self.book.context(1),
                lambda: self.book.lint(1, self.draft),
                lambda: self.book.prepare(1, self.draft),
                lambda: self.book.commit(1, self.draft, base.StoryTests.delta(self))):
            self.assert_error("outline_plan_drift", operation)
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_binding_is_explicit_exact_and_idempotent(self):
        path, sha = self.readable()
        revision = self.book.meta("revision")
        bound = self.bind(path, sha)
        self.assertEqual(bound["revision"], revision + 1)
        self.assertEqual(bound["outline"]["status"], "adopted")
        self.assertEqual(outline.verify(self.book, 1, self.book.get_plan(1))["status"], "adopted")
        self.assertEqual(self.bind(path, sha)["revision"], bound["revision"])
        self.assertEqual(self.book.meta("revision"), bound["revision"])

    def test_both_file_and_plan_changes_are_gated_until_rebound(self):
        path, sha = self.readable()
        self.bind(path, sha)
        file = self.root / path
        file.write_text("状态：已采用\n沈禾决定留下钥匙。\n", encoding="utf-8")
        error = self.assert_error("outline_plan_drift", outline.verify, self.book, 1, self.book.get_plan(1))
        self.assertEqual(error.details["changed"], ["outline"])
        new_sha = hashlib.sha256(file.read_bytes()).hexdigest()
        self.bind(path, new_sha)
        self.book.save_plan(1, base.plan(goal="换回钥匙"), self.book.meta("revision"))
        error = self.assert_error("outline_plan_drift", outline.verify, self.book, 1, self.book.get_plan(1))
        self.assertEqual(error.details["changed"], ["plan"])
        self.bind(path, new_sha)
        self.assertEqual(outline.verify(self.book, 1, self.book.get_plan(1))["status"], "adopted")

    def test_stale_reviewed_file_hash_does_not_bind(self):
        path, sha = self.readable()
        self.root.joinpath(path).write_text("状态：已采用\n修改后的细纲\n", encoding="utf-8")
        revision = self.book.meta("revision")
        self.assert_error("stale_outline", self.bind, path, sha)
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertIsNone(outline.binding_for(self.book, 1))

    def test_neutral_filename_cannot_disguise_candidate_content(self):
        revision = self.book.meta("revision")
        for content in ("状态：候选\n待确认的章细纲。\n",
                        "# 章细纲\n- **状态**：草稿\n待确认的章细纲。\n",
                        "| 状态 | 待定 |\n| --- | --- |\n待确认的章细纲。\n"):
            with self.subTest(content=content):
                path, sha = self.readable("README.md", content)
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)

    def test_new_binding_needs_an_explicit_adopted_header(self):
        path, sha = self.readable("README.md", "这一份细纲尚未标注采用状态。\n")
        self.assert_error("invalid_input", self.bind, path, sha)
        self.assertIsNone(outline.binding_for(self.book, 1))

    def test_old_binding_without_status_is_compatible_but_candidate_is_not(self):
        path, sha = self.readable("README.md", "旧版细纲，没有状态字段。\n")
        record = {"status": "adopted", "chapter": 1, "path": path,
                  "sha256": sha, "plan_sha256": outline._plan_sha256(self.book.get_plan(1))}
        with self.book.transaction():
            self.book.set_meta("outline_binding:1", record)
        self.assertEqual(outline.verify(self.book, 1, self.book.get_plan(1))["status"], "adopted")
        path, sha = self.readable("README.md", "状态：候选\n旧记录错误指向候选稿。\n")
        record["sha256"] = sha
        with self.book.transaction():
            self.book.set_meta("outline_binding:1", record)
        self.assert_error("outline_plan_drift", outline.verify, self.book, 1, self.book.get_plan(1))
        self.assert_error("outline_plan_drift", self.book.context, 1)

    def test_unavailable_bound_file_fails_closed_without_state_write(self):
        path, sha = self.readable()
        self.bind(path, sha)
        revision = self.book.meta("revision")
        self.root.joinpath(path).unlink()
        self.assert_error("outline_plan_drift", outline.verify, self.book, 1, self.book.get_plan(1))
        self.assertEqual(self.book.meta("revision"), revision)

    def test_idempotent_commit_retry_still_checks_outline_binding(self):
        path, sha = self.readable()
        self.bind(path, sha)
        delta = self.delta()
        first = self.book.commit(1, self.draft, delta)
        self.assertTrue(first["committed"])
        self.assertTrue(self.book.commit(1, self.draft, delta)["idempotent"])
        self.root.joinpath(path).write_text("状态：已采用\n新的细纲内容。\n", encoding="utf-8")
        revision = self.book.meta("revision")
        self.assert_error("outline_plan_drift", self.book.commit, 1, self.draft, delta)
        self.assertEqual(self.book.meta("revision"), revision)

    def test_candidate_draft_prose_history_escape_and_symlink_paths_are_rejected(self):
        for relative in ("候选/第1章.md", "01_大纲细纲/草稿/第1章.md",
                         "01_大纲细纲/第一卷 雨夜/第1章 候选.md",
                         "01_大纲细纲/第一卷 雨夜/第1章 draft.md", ".story/drafts/第1章.md",
                         "chapters/第1章.md", "02_正文/第1章.md", "99_历史版本/第1章.md",
                         "../第1章.md", "/tmp/第1章.md", "C:/temp/第1章.md", "第1章.txt"):
            with self.subTest(relative=relative):
                self.assert_error("invalid_input", self.bind, relative, "0" * 64)
        path, sha = self.readable()
        linked = self.root / "linked"
        linked.symlink_to(self.root / "01_大纲细纲", target_is_directory=True)
        self.assert_error("linked_path", self.bind, "linked/第一卷 雨夜/第1章 门后的雨.md", sha)

    def test_stale_revision_is_rejected(self):
        path, sha = self.readable()
        self.assert_error("stale_revision", outline.bind, self.book, 1, path, 0, sha)
        self.assertIsNone(outline.binding_for(self.book, 1))

    def test_revision_is_required_and_file_size_is_bounded(self):
        path, sha = self.readable()
        self.assert_error("invalid_input", outline.bind, self.book, 1, path, None, sha)
        self.root.joinpath(path).write_bytes(b"x" * (outline.MAX_OUTLINE_BYTES + 1))
        large_sha = hashlib.sha256(self.root.joinpath(path).read_bytes()).hexdigest()
        self.assert_error("outline_too_large", self.bind, path, large_sha)
        self.assertIsNone(outline.binding_for(self.book, 1))

    def test_short_managed_reading_copy_cannot_be_bound_as_outline(self):
        root = self.root / "短篇"
        base.story.Book.create(root, "短篇书名", "short")
        book = base.story.Book(root)
        try:
            book.save_plan(1, base.plan(), 0)
            relative = book.short_assembly_path()
            target = root / relative
            target.write_text("短篇正文", encoding="utf-8")
            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assert_error("invalid_input", outline.bind, book, 1, relative, book.meta("revision"), sha)
            self.assertIsNone(outline.binding_for(book, 1))
        finally:
            book.close()


class OutlineHistoryPublishTests(unittest.TestCase):
    def setUp(self):
        self.fixture = history_fixture.LongHistoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book
        self.history = history_fixture.history
        self.story = history_fixture.story

    def ready_branch(self):
        fixture = self.fixture
        fixture.add(1)
        fixture.dep(1)
        relative = "01_大纲细纲/第一卷 雨夜/第1章 核对交接.md"
        target = fixture.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("状态：已采用\n沈禾交出钥匙，留下收据。\n", encoding="utf-8")
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        self.story.outline.bind(self.book, 1, relative, fixture.rev(), sha)
        changed = fixture.texts[1].replace("一张收据", "两张收据")
        staged = fixture.stage(fixture.start(), {1: changed})
        return staged["branch"], target, changed

    def assert_outline_drift(self, branch, expected_revision):
        with self.assertRaises(self.story.StoryError) as caught:
            self.history.branch_publish(self.book, branch, expected_revision)
        self.assertEqual(caught.exception.code, "outline_plan_drift")
        self.assertEqual(self.book.meta("revision"), expected_revision)

    def test_history_publish_rejects_changed_bound_outline_before_canonical_write(self):
        branch, target, _ = self.ready_branch()
        original = self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0]
        revision = self.book.meta("revision")
        target.write_text("状态：已采用\n沈禾保留钥匙。\n", encoding="utf-8")
        self.assert_outline_drift(branch, revision)
        self.assertEqual(self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0], original)
        self.assertEqual(self.history.branch_inspect(self.book, branch)["status"], "candidate")

    def test_published_history_retry_still_rejects_changed_bound_outline(self):
        branch, target, changed = self.ready_branch()
        expected = self.book.meta("revision")
        published = self.history.branch_publish(self.book, branch, expected)
        self.assertTrue(published["committed"])
        self.assertEqual(self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0], changed)
        self.assertTrue(self.history.branch_publish(self.book, branch, expected)["idempotent"])
        revision = self.book.meta("revision")
        target.write_text("状态：已采用\n收据交给了另一人。\n", encoding="utf-8")
        self.assert_outline_drift(branch, revision)
        self.assertEqual(self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0], changed)


if __name__ == "__main__":
    unittest.main()
