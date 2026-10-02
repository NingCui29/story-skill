import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_chapter_layout", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

QUOTE = "沈禾把唯一的钥匙交给守门人。"
BODY = QUOTE + "\n她答应在天亮之前带回账本。\n"
DRAFT = "第1章 门后的雨\n" + BODY


class ChapterLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-chapter-layout-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def plan(self, volume_dir="第一卷 雨夜", **fields):
        return {"goal": "决定钥匙的去向", "stop": "选择入口后停笔", "constraints": [],
                "requires": [], "tags": [], "length": [20, 120],
                "length_exception": {"source": "user_request", "quote": "测试章节布局需要20至120字。"},
                "beats": [{"choice": "沈禾决定是否交出钥匙", "change": "失去或保留退路"}],
                **({"volume_dir": volume_dir} if volume_dir is not None else {}),
                **fields}

    def save_plan(self, chapter, **fields):
        self.book.save_plan(chapter, self.plan(**fields), self.book.meta("revision"))

    def delta(self, text):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾交出钥匙，承诺天亮前返回。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "人物选择与后果在正文中可见。", "quote": QUOTE}
                    for check in story.CHECKS}, "issues": []}}

    def write_draft(self, text):
        # Preserve the fixture's LF bytes on Windows so review hashes bind the file.
        self.draft.write_bytes(text.encode("utf-8"))

    def commit(self, chapter, text=DRAFT, replace_last=False):
        self.write_draft(text)
        delta = self.delta(text)
        return self.book.commit(chapter, self.draft, delta, replace_last=replace_last), delta

    def assert_error(self, code, operation):
        with self.assertRaises(story.StoryError) as result:
            operation()
        self.assertEqual(result.exception.code, code)

    def state_snapshot(self):
        return {table: [tuple(row) for row in self.book.db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in ("meta", "plans", "cards", "chapter_state", "artifact_state", "events", "core_objects")}

    def stage_history(self, packet, texts):
        candidates = []
        for affected in packet["affected"]:
            chapter = affected["chapter"]
            text = texts[chapter]
            candidate = {"chapter": chapter, "text": text, "sha": story.digest(text),
                         "summary": "沈禾交出钥匙，重新确认返回的路线。", "dependencies": [], "complete": True}
            if affected.get("external_edit"):
                candidate["external_sha256"] = affected["external_edit"]["sha256"]
            candidate["review"] = {
                "draft_sha256": candidate["sha"],
                "candidate_sha256": story.history.candidate_fingerprint(candidate),
                "checks": {name: {"note": "逐段核对选择、连续性与代价。", "quote": QUOTE}
                           for name in story.CHECKS}, "issues": []}
            candidates.append(candidate)
        staged = story.history.branch_update(self.book, packet["branch"], {"chapters": candidates},
                                             self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "全部受影响章节已经逐段复核。",
                    "state_review": "最终状态与正文结局一致。",
                    "coverage_review": "已核对未声明的人物与规则关联，正文没有遗漏依赖。"}
        return story.history.branch_update(self.book, packet["branch"], {"semantic_review": semantic},
                                           self.book.meta("revision"))

    def test_chapters_use_volume_directories_and_exact_number_title_names(self):
        self.save_plan(1)
        first, _ = self.commit(1)
        self.save_plan(2, volume_dir="第二卷 旧城", title="账本归来")
        second, _ = self.commit(2, "第2章 账本归来\n" + BODY)

        expected = {"chapters/第一卷 雨夜/第1章 门后的雨.md": DRAFT,
                    "chapters/第二卷 旧城/第2章 账本归来.md": "第2章 账本归来\n" + BODY}
        self.assertEqual({p.relative_to(self.root).as_posix()
                          for p in (self.root / "chapters").rglob("*.md")}, set(expected))
        for relative, content in expected.items():
            self.assertEqual((self.root / relative).read_bytes(), content.encode("utf-8"))
        self.assertEqual(self.book.chapter_path(1), "chapters/第一卷 雨夜/第1章 门后的雨.md")
        self.assertEqual(self.book.chapter_path(2), "chapters/第二卷 旧城/第2章 账本归来.md")
        self.assertEqual(first["path"], str(self.root / self.book.chapter_path(1)))
        self.assertEqual(second["path"], str(self.root / self.book.chapter_path(2)))

    def test_complete_native_heading_must_match_number_and_planned_title(self):
        self.save_plan(1, title="门后的雨")
        text = "第2章 草稿标题\n" + BODY
        self.write_draft(text)
        before = self.state_snapshot()

        lint = self.book.lint(1, self.draft)
        self.assertFalse(lint["ok"])
        self.assertEqual({error["code"] for error in lint["errors"]},
                         {"chapter_heading_number", "chapter_heading_title"})
        self.assert_error("lint_failed", lambda: self.book.prepare(1, self.draft))
        self.assert_error("lint_failed", lambda: self.book.commit(1, self.draft, self.delta(text)))
        self.assertEqual(self.state_snapshot(), before)

    def test_new_heading_rejects_noncanonical_numbers_but_old_numerals_still_parse(self):
        self.save_plan(1, title="开 门")
        for opening in ("第一章 开　门", "第01章 开　门", "第１章 开　门"):
            with self.subTest(opening=opening):
                text = opening + "\n" + BODY
                self.write_draft(text)
                lint = self.book.lint(1, self.draft)
                self.assertIn("chapter_heading_format", {item["code"] for item in lint["errors"]})
                self.assert_error("lint_failed", lambda: self.book.prepare(1, self.draft))
                self.assert_error("lint_failed", lambda: self.book.commit(1, self.draft, self.delta(text)))
        self.assertEqual(story.chapter_number("一百零二"), 102)
        self.assertEqual(story.chapter_number("１２"), 12)

    def test_nondefault_saved_length_is_visible_but_explicit_override_is_allowed(self):
        self.save_plan(1, title="门后的雨", length=[20, 120])
        self.write_draft("第1章 门后的雨\n" + BODY)
        lint = self.book.lint(1, self.draft)
        self.assertTrue(lint["ok"])
        warning = next(item for item in lint["warnings"] if item["code"] == "nondefault_plan_length")
        self.assertEqual(warning["saved"], [20, 120])
        self.assertEqual(warning["default"], [2400, 2800])
        prepared = self.book.prepare(1, self.draft)
        self.assertTrue(prepared["lint"]["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(self.draft.read_text()))["exports_complete"])

    def test_new_chapter_requires_a_plain_complete_heading_but_imports_remain_compatible(self):
        self.save_plan(1, title="门后的雨")
        for text, code in (("# 第一章 门后的雨\n" + BODY, "chapter_heading_format"),
                           ("第1章：门后的雨\n" + BODY, "chapter_heading_format"),
                           ("第一章\n" + BODY, "chapter_heading_required"),
                           (BODY, "chapter_heading_required")):
            with self.subTest(opening=text.splitlines()[0]):
                self.write_draft(text)
                lint = self.book.lint(1, self.draft)
                self.assertFalse(lint["ok"])
                self.assertIn(code, {item["code"] for item in lint["errors"]})
                self.assert_error("lint_failed", lambda: self.book.prepare(1, self.draft))
                self.assert_error("lint_failed", lambda: self.book.commit(1, self.draft, self.delta(text)))
        imported = "第二章 旧稿另名\n" + BODY
        self.write_draft(imported)
        self.book.adopt(1, self.draft, "保留旧稿基线。", self.book.meta("revision"), volume_dir="第一卷 雨夜")
        lint = self.book.lint(1, self.draft)
        self.assertTrue(lint["ok"])
        self.assertNotIn("nondefault_plan_length", {item["code"] for item in lint["warnings"]})

    def test_unchanged_legacy_heading_is_compatible_but_plan_rename_requires_alignment(self):
        self.save_plan(1)
        legacy = "# 第1章 门后的雨\n" + BODY
        with patch.object(story, "chapter_heading_errors", return_value=[]):
            self.commit(1, legacy)
        self.write_draft(legacy)
        lint = self.book.lint(1, self.draft)
        self.assertTrue(lint["ok"])
        self.assertTrue(self.book.prepare(1, self.draft)["lint"]["ok"])
        revised = legacy + "她将空手藏进衣袖。\n"
        self.assertTrue(self.commit(1, revised, replace_last=True)[0]["exports_complete"])
        self.write_draft(revised)
        # The old first line is tolerated only while the adopted title stays put.
        self.save_plan(1, title="修订计划名")
        lint = self.book.lint(1, self.draft)
        self.assertFalse(lint["ok"])
        self.assertIn("chapter_heading_title", {item["code"] for item in lint["errors"]})
        self.assert_error("lint_failed", lambda: self.book.commit(1, self.draft, self.delta(revised), replace_last=True))

    def test_replacement_rechecks_every_changed_first_line(self):
        self.save_plan(1, title="门后的雨")
        self.commit(1)
        for text, code in (("第2章 门后的雨\n" + BODY, "chapter_heading_number"),
                           ("第1章 别的标题\n" + BODY, "chapter_heading_title"),
                           ("# 第1章 门后的雨\n" + BODY, "chapter_heading_format"),
                           (BODY, "chapter_heading_required")):
            with self.subTest(first_line=text.splitlines()[0]):
                self.write_draft(text)
                lint = self.book.lint(1, self.draft)
                self.assertIn(code, {item["code"] for item in lint["errors"]})
                self.assert_error("lint_failed", lambda: self.book.prepare(1, self.draft))
                self.assert_error("lint_failed", lambda: self.book.commit(1, self.draft, self.delta(text), replace_last=True))

    def test_chapter_titles_are_unique_across_volumes_and_draft_headings(self):
        self.save_plan(1, title="门后的雨")
        self.commit(1)
        before = self.state_snapshot()
        self.assert_error("duplicate_chapter_title", lambda: self.save_plan(
            2, title="门后的雨", volume_dir="第二卷 旧城"))
        self.assertEqual(self.state_snapshot(), before)
        self.assert_error("duplicate_chapter_title", lambda: self.save_plan(
            2, title="门后的雨　", volume_dir="第二卷 旧城"))
        self.assertEqual(self.state_snapshot(), before)

        self.save_plan(2, volume_dir="第二卷 旧城")
        self.write_draft("第2章 门后的雨\n" + BODY)
        before = self.state_snapshot()
        self.assert_error("duplicate_chapter_title", lambda: self.book.lint(2, self.draft))
        self.assertEqual(self.state_snapshot(), before)

    def test_plan_title_rejects_chapter_number_prefix_before_saving(self):
        before = self.state_snapshot()
        for title in ("第1章 门后的雨", "第一章 门后的雨", "第１章门后的雨",
                      "# 第1章 门后的雨", "第 1 章 门后的雨"):
            with self.subTest(title=title):
                self.assert_error("chapter_title_prefix", lambda: self.save_plan(1, title=title))
                self.assertEqual(self.state_snapshot(), before)

        self.save_plan(1, title="门后的雨")
        result, _ = self.commit(1)
        self.assertEqual(Path(result["path"]).name, "第1章 门后的雨.md")

    def test_legacy_prefixed_plan_title_cannot_export_a_double_prefix(self):
        self.save_plan(1, title="门后的雨")
        plan = self.book.get_plan(1)
        plan["title"] = "第1章 门后的雨"
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(plan),))
        self.write_draft(DRAFT)
        before = self.state_snapshot()
        self.assert_error("chapter_title_prefix", lambda: self.book.lint(1, self.draft))
        self.assertEqual(self.state_snapshot(), before)
        self.assertFalse((self.root / "chapters/第一卷 雨夜/第1章 第1章 门后的雨.md").exists())

    def test_volume_names_are_unique_but_shared_within_one_volume(self):
        self.save_plan(1, title="入城")
        self.save_plan(2, title="旧信")
        before = self.state_snapshot()
        self.assert_error("duplicate_volume_title", lambda: self.save_plan(
            3, title="过河", volume_dir="第二卷 雨夜"))
        self.assertEqual(self.state_snapshot(), before)
        self.assert_error("duplicate_volume_title", lambda: self.save_plan(
            3, title="过河", volume_dir="第二卷　雨夜"))
        self.assertEqual(self.state_snapshot(), before)
        self.save_plan(3, title="过河", volume_dir="第二卷 旧城")

    def test_plain_chapter_heading_exports_as_plain_text_and_is_excluded_from_body_count(self):
        text = "第1章 门后的雨\n" + BODY
        body_count = story.manuscript_counts(BODY)["visible_nonspace_v1"]
        self.save_plan(1, length=[body_count, body_count])
        result, _ = self.commit(1, text)

        relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        self.assertEqual(result["path"], str(self.root / relative))
        self.assertEqual((self.root / relative).read_bytes(), text.encode("utf-8"))
        self.assertEqual(self.book.lint(1, self.draft)["length_count"], body_count)
        self.assertEqual(story.manuscript_counts(text, True)["visible_nonspace_v1"], body_count + 7)

    def test_plain_heading_prefix_is_removed_and_path_survives_retry_and_reopen(self):
        text = "第1章 门后的雨\n" + BODY
        self.save_plan(1, volume_dir="第一卷 雨夜")
        original, delta = self.commit(1, text)
        relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        revision = self.book.meta("revision")
        self.book.close()
        self.book = story.Book(self.root, integrity="local")

        self.assertEqual(self.book.chapter_path(1), relative)
        retried = self.book.commit(1, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["scope_exports_complete"])
        self.assertEqual(retried["path"], original["path"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual((self.root / relative).read_bytes(), text.encode("utf-8"))

    def test_missing_nested_export_is_recovered_at_persisted_path(self):
        self.save_plan(1, volume_dir="第三卷 渡口", title="过河")
        text = "第1章 过河\n" + BODY
        self.commit(1, text)
        relative = "chapters/第三卷 渡口/第1章 过河.md"
        target = self.root / relative
        target.unlink()
        target.parent.rmdir()
        revision = self.book.meta("revision")
        self.book.close()
        self.book = story.Book(self.root, integrity="local")

        result = self.book.export(safe_only=True)
        self.assertTrue(result["scope_exports_complete"])
        self.assertEqual(result["exported"], [relative])
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))
        self.assertEqual(self.book.meta("revision"), revision)

    def test_replacing_title_and_volume_moves_export_and_preserves_backup(self):
        self.save_plan(1)
        self.commit(1)
        old = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        revised = "第1章 新的入口\n" + BODY + "她没有回头。\n"
        self.save_plan(1, volume_dir="第二卷 渡口")

        result, _ = self.commit(1, revised, replace_last=True)
        relative = "chapters/第二卷 渡口/第1章 新的入口.md"
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.book.chapter_path(1), relative)
        self.assertFalse(old.exists())
        self.assertEqual((self.root / relative).read_bytes(), revised.encode("utf-8"))
        self.assertTrue(any(Path(path).read_bytes() == DRAFT.encode("utf-8")
                            for path in result["backups"]))
        self.assertEqual(self.book.export(safe_only=True)["exported"], [])

    def test_failed_move_keeps_old_export_until_new_export_succeeds(self):
        self.save_plan(1)
        self.commit(1)
        old = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        self.save_plan(1, title="新的入口", volume_dir="第二卷 渡口")
        new = self.root / "chapters/第二卷 渡口/第1章 新的入口.md"
        revised = "第1章 新的入口\n" + BODY + "她没有回头。\n"
        write = story.atomic_write

        def fail_new_export(target, *args, **kwargs):
            if Path(target) == new:
                raise OSError("new volume temporarily unavailable")
            return write(target, *args, **kwargs)

        with patch.object(story, "atomic_write", side_effect=fail_new_export):
            result, delta = self.commit(1, revised, replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(old.read_bytes(), DRAFT.encode("utf-8"))
        self.assertFalse(new.exists())
        revision = self.book.meta("revision")
        self.book.close()
        self.book = story.Book(self.root)

        retried = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])
        self.assertFalse(old.exists())
        self.assertEqual(new.read_bytes(), revised.encode("utf-8"))
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertTrue(any(Path(path).read_bytes() == DRAFT.encode("utf-8")
                            for path in retried["backups"]))

    def test_reconcile_uses_current_nested_path_and_can_publish_new_title(self):
        self.save_plan(1, volume_dir="第一卷 雨夜")
        self.commit(1)
        old = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        external = "第1章 新的入口\n" + BODY + "她没有回头。\n"
        old.write_bytes(external.encode("utf-8"))
        packet = self.book.reconcile(1)
        self.assertEqual(Path(packet["external_edit"]["path"]), old)
        self.draft.write_bytes(external.encode("utf-8"))
        delta = self.delta(external)
        delta["external_sha256"] = packet["external_edit"]["sha256"]

        result = self.book.reconcile(1, self.draft, delta)
        relative = "chapters/第一卷 雨夜/第1章 新的入口.md"
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.book.chapter_path(1), relative)
        self.assertFalse(old.exists())
        self.assertEqual((self.root / relative).read_bytes(), external.encode("utf-8"))
        self.assertTrue(any(Path(path).read_bytes() == external.encode("utf-8")
                            for path in result["backups"]))
        retried = self.book.reconcile(1, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])

    def test_legacy_flat_export_path_remains_resolvable_after_reopen(self):
        relative = "chapters/0001.md"
        self.save_plan(1)
        receipt = {"input": self.delta(DRAFT), "before": {}, "after": {},
                   "lint": story.lint_text(DRAFT, self.book.get_plan(1))}
        with self.book.transaction():
            self.book.queue_artifact(relative, DRAFT)
            self.book.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,0)",
                                 (1, DRAFT, story.digest(DRAFT), "已存旧版章节。",
                                  story.dumps(receipt), "legacy-input"))
            self.book.set_meta("last_chapter", 1)
            self.book.index_chapter(1, DRAFT, "已存旧版章节。")
        self.assertEqual(self.book.chapter_path(1), relative)
        self.assertIsNone(self.book.adopted_chapter_title(1))
        self.assertEqual(self.book.export()["exported"], [relative])
        self.book.close()
        self.book = story.Book(self.root)
        self.assertEqual(self.book.chapter_path(1), relative)
        (self.root / relative).unlink()
        self.assertEqual(self.book.export(safe_only=True)["exported"], [relative])
        self.assertEqual((self.root / relative).read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(list((self.root / "chapters").iterdir()), [self.root / relative])
        revised = DRAFT + "她没有回头。\n"
        replaced, _ = self.commit(1, revised, replace_last=True)
        self.assertTrue(replaced["exports_complete"])
        self.assertEqual(self.book.chapter_path(1), relative)
        self.assertEqual((self.root / relative).read_bytes(), revised.encode("utf-8"))
        self.save_plan(2, title="账本归来")
        self.commit(2, "第2章 账本归来\n" + BODY)
        self.assertEqual(self.book.chapter_path(2), "chapters/第一卷 雨夜/第2章 账本归来.md")

    def test_flat_path_uses_last_published_plan_title_for_heading_rename_check(self):
        self.save_plan(1, title="门后的雨")
        self.commit(1)
        with self.book.transaction():
            self.book.set_meta("chapter_path:1", "chapters/0001.md")
        self.assertEqual(self.book.adopted_chapter_title(1), "门后的雨")
        self.save_plan(1, title="新的入口")
        checked = story.lint_text(DRAFT, self.book.get_plan(1), 1, previous_text=DRAFT,
                                  previous_title=self.book.adopted_chapter_title(1))
        self.assertIn("chapter_heading_title", {item["code"] for item in checked["errors"]})

    def test_new_title_collision_preserves_both_files_and_original_chapter_state(self):
        self.save_plan(1)
        self.commit(1)
        old_relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        old = self.root / old_relative
        self.save_plan(1, title="新的入口")
        occupied = self.root / "chapters/第一卷 雨夜/第1章 新的入口.md"
        outside = "用户另存的正文，必须保留。\n".encode("utf-8")
        occupied.write_bytes(outside)
        revision = self.book.meta("revision")

        self.assert_error("export_conflict", lambda: self.commit(1, "第1章 新的入口\n" + BODY + "她没有回头。\n",
                                                                 replace_last=True))
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.chapter_path(1), old_relative)
        self.assertEqual(old.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(occupied.read_bytes(), outside)

    def test_case_insensitive_title_alias_is_rejected_before_chapter_commit(self):
        self.save_plan(1, title="AI来客")
        original = "第1章 AI来客\n" + BODY
        self.commit(1, original)
        relative = "chapters/第一卷 雨夜/第1章 AI来客.md"
        target = self.root / relative
        alias = self.root / "chapters/第一卷 雨夜/第1章 Ai来客.md"
        if not alias.exists() or not alias.samefile(target):
            self.skipTest("Filesystem distinguishes names that differ only in letter case")
        self.save_plan(1, title="Ai来客")
        revision = self.book.meta("revision")
        before = tuple(self.book.db.execute("SELECT * FROM chapters WHERE chapter=1").fetchone())

        self.assert_error("export_path_alias", lambda: self.commit(1, "第1章 Ai来客\n" + BODY + "她没有回头。\n",
                                                                   replace_last=True))
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(tuple(self.book.db.execute("SELECT * FROM chapters WHERE chapter=1").fetchone()), before)
        self.assertEqual(self.book.chapter_path(1), relative)
        self.assertEqual(target.read_bytes(), original.encode("utf-8"))
        self.assertEqual([path.name for path in target.parent.iterdir()], [target.name])

    def test_unreviewed_edit_to_retired_path_is_preserved_during_recovery(self):
        self.save_plan(1)
        self.commit(1)
        old_relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        old = self.root / old_relative
        self.save_plan(1, title="新的入口")
        with patch.object(story, "atomic_write", side_effect=OSError("publication interrupted")):
            failed, _ = self.commit(1, "第1章 新的入口\n" + BODY + "她没有回头。\n",
                                    replace_last=True)
        self.assertFalse(failed["exports_complete"])
        outside = "用户在导出中断后修改了旧文件。\n".encode("utf-8")
        old.write_bytes(outside)

        result = self.book.export(safe_only=True)
        self.assertFalse(result["exports_complete"])
        self.assertIn(old_relative, result["changed_exports"])
        self.assertEqual(old.read_bytes(), outside)

    def test_reconcile_recovers_edited_retired_path_and_retargets_further_rename(self):
        self.save_plan(1)
        self.commit(1)
        old_relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        old = self.root / old_relative
        intermediate_relative = "chapters/第一卷 雨夜/第1章 新的入口.md"
        intermediate = self.root / intermediate_relative
        revised = "第1章 新的入口\n" + BODY + "她没有回头。\n"
        with patch.object(story, "atomic_write", side_effect=OSError("publication interrupted")):
            failed, _ = self.commit(1, revised, replace_last=True)
        self.assertFalse(failed["exports_complete"])
        self.assertEqual(old.read_bytes(), DRAFT.encode("utf-8"))
        self.assertFalse(intermediate.exists())
        external = "第1章 渡口的灯\n" + BODY + "她停步看向河对岸的灯。\n"
        old.write_bytes(external.encode("utf-8"))

        recovered = self.book.export(safe_only=True)
        self.assertFalse(recovered["exports_complete"])
        self.assertIn(old_relative, recovered["changed_exports"])
        self.assertEqual(intermediate.read_bytes(), revised.encode("utf-8"))
        self.assertEqual(old.read_bytes(), external.encode("utf-8"))
        packet = self.book.reconcile(1)
        self.assertEqual(Path(packet["external_edit"]["path"]), old)
        self.assertEqual(packet["external_edit"]["sha256"], story.digest(external))
        self.draft.write_bytes(external.encode("utf-8"))
        delta = self.delta(external)
        delta["external_sha256"] = packet["external_edit"]["sha256"]

        result = self.book.reconcile(1, self.draft, delta)
        latest_relative = "chapters/第一卷 雨夜/第1章 渡口的灯.md"
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.book.chapter_path(1), latest_relative)
        self.assertEqual((self.root / latest_relative).read_bytes(), external.encode("utf-8"))
        self.assertFalse(old.exists())
        self.assertFalse(intermediate.exists())
        backed_up = {Path(path).read_bytes() for path in result["backups"]}
        self.assertIn(external.encode("utf-8"), backed_up)
        self.assertIn(revised.encode("utf-8"), backed_up)
        self.assertEqual(self.book.db.execute(
            "SELECT count(*) FROM meta WHERE key GLOB 'chapter_retired:*'").fetchone()[0], 0)
        repeated = self.book.reconcile(1, self.draft, delta)
        self.assertTrue(repeated["idempotent"])
        self.assertTrue(repeated["exports_complete"])

    def test_volume_without_directory_uses_named_world_title_and_persisted_mapping(self):
        volume = {"id": "rain", "title": "第一卷 雨夜", "goal": "查清账本去向",
                  "entry_condition": "唯一的钥匙仍在手中", "exit_condition": "带回账本",
                  "cost": "失去退路", "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        story.world.save(self.book, {"volumes": [volume]}, self.book.meta("revision"))
        self.save_plan(1, volume="rain", volume_dir=None)
        self.commit(1)
        self.assertEqual(self.book.chapter_path(1), "chapters/第一卷 雨夜/第1章 门后的雨.md")

        story.world.save(self.book, {"volumes": [{**volume, "title": "修订后的卷纲名称"}]},
                         self.book.meta("revision"))
        self.book.close()
        self.book = story.Book(self.root)
        self.save_plan(2, volume="rain", volume_dir=None, title="第二夜")
        self.commit(2, "第2章 第二夜\n" + BODY)
        self.assertEqual(self.book.chapter_path(2), "chapters/第一卷 雨夜/第2章 第二夜.md")

        self.save_plan(3, volume="crossing", volume_dir="第2卷 渡口", title="第三夜")
        self.commit(3, "第3章 第三夜\n" + BODY)
        self.save_plan(4, volume="crossing", volume_dir=None, title="第四夜")
        self.commit(4, "第4章 第四夜\n" + BODY)
        self.assertEqual(self.book.chapter_path(3), "chapters/第2卷 渡口/第3章 第三夜.md")
        self.assertEqual(self.book.chapter_path(4), "chapters/第2卷 渡口/第4章 第四夜.md")

    def test_new_commit_and_adopt_require_named_volume_without_changing_state(self):
        self.save_plan(1, volume_dir=None)
        before = self.state_snapshot()
        self.assert_error("volume_title_missing", lambda: self.commit(1))
        self.assertEqual(self.state_snapshot(), before)
        self.draft.write_bytes(BODY.encode("utf-8"))
        self.assert_error("volume_title_missing", lambda: self.book.adopt(
            108, self.draft, "接入已有正文", self.book.meta("revision")))
        self.assertEqual(self.state_snapshot(), before)
        self.assertEqual(list((self.root / "chapters").rglob("*.md")), [])

    def test_bare_volume_number_title_and_identifier_are_rejected(self):
        for value in ("第一卷", "第2卷", "第一卷 ", "雨夜", "rain"):
            with self.subTest(volume_dir=value):
                before = self.state_snapshot()
                self.assert_error("invalid_input", lambda: self.save_plan(1, volume_dir=value))
                self.assertEqual(self.state_snapshot(), before)

    def test_unregistered_volume_identifier_cannot_supply_a_directory_name(self):
        for value in ("rain", "第二卷 旧城"):
            with self.subTest(volume=value):
                self.save_plan(1, volume=value, volume_dir=None)
                before = self.state_snapshot()
                self.assert_error("volume_title_missing", lambda: self.commit(1))
                self.assertEqual(self.state_snapshot(), before)
                self.assertEqual(list((self.root / "chapters").rglob("*.md")), [])

    def test_world_volume_requires_both_number_and_name_for_directory_fallback(self):
        for index, title in enumerate(("第一卷", "雨夜")):
            volume = {"id": f"volume-{index}", "title": title, "goal": "查清账本去向",
                      "entry_condition": "唯一的钥匙仍在手中", "exit_condition": "带回账本",
                      "cost": "失去退路", "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
            story.world.save(self.book, {"volumes": [volume]}, self.book.meta("revision"))
            self.save_plan(1, volume=volume["id"], volume_dir=None)
            before = self.state_snapshot()
            self.assert_error("volume_title_missing", lambda: self.commit(1))
            self.assertEqual(self.state_snapshot(), before)

    def test_native_heading_is_required_even_with_a_planned_title(self):
        self.save_plan(1)
        self.draft.write_bytes(BODY.encode("utf-8"))
        revision = self.book.meta("revision")
        self.assert_error("lint_failed",
                          lambda: self.book.commit(1, self.draft, self.delta(BODY)))
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.meta("last_chapter"), 0)
        self.save_plan(1, title="门后的雨")
        self.assert_error("lint_failed", lambda: self.commit(1, BODY))
        result, _ = self.commit(1)
        self.assertTrue(result["exports_complete"])
        target = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        self.assertEqual(target.read_bytes(), DRAFT.encode("utf-8"))

    def test_untitled_import_uses_body_fallback_in_named_volume(self):
        self.draft.write_bytes(BODY.encode("utf-8"))
        result = self.book.adopt(108, self.draft, "接入已有正文", self.book.meta("revision"),
                                 volume_dir="第一卷 雨夜")
        self.assertTrue(result["exports_complete"])
        relative = "chapters/第一卷 雨夜/第108章 正文.md"
        self.assertEqual(self.book.chapter_path(108), relative)
        self.assertEqual((self.root / relative).read_bytes(), BODY.encode("utf-8"))

    def test_titleless_import_keeps_body_filename_during_history_publication(self):
        self.save_plan(108)
        self.draft.write_bytes(BODY.encode("utf-8"))
        self.book.adopt(108, self.draft, "接入已有正文", self.book.meta("revision"))
        relative = "chapters/第一卷 雨夜/第108章 正文.md"
        for revised in (BODY + "她没有回头。\n", BODY + "她停步看向河对岸的灯。\n"):
            packet = story.history.branch_start(self.book, 108, self.book.meta("revision"))
            staged = self.stage_history(packet, {108: revised})
            result = story.history.branch_publish(self.book, staged["branch"], self.book.meta("revision"))
            self.assertTrue(result["exports_complete"])
            self.assertEqual(self.book.chapter_path(108), relative)
            self.assertEqual((self.root / relative).read_bytes(), revised.encode("utf-8"))
        self.assertEqual([path.relative_to(self.root).as_posix()
                          for path in (self.root / "chapters").rglob("*.md")], [relative])

    def test_history_candidate_rechecks_changed_or_removed_heading(self):
        self.save_plan(1, title="门后的雨")
        self.commit(1)
        story.history.save_dependencies(self.book, {
            "chapter": 1, "chapter_sha": story.digest(DRAFT), "dependencies": [],
            "complete": True, "note": "这一章没有外部依赖，修订后仍逐段核对。"},
            self.book.meta("revision"))
        packet = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        for text in ("第2章 门后的雨\n" + BODY, "第1章 旧标题\n" + BODY,
                     "# 第1章 门后的雨\n" + BODY, BODY):
            with self.subTest(first_line=text.splitlines()[0]):
                self.assert_error("lint_failed", lambda: self.stage_history(packet, {1: text}))
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(), DRAFT)

    def test_history_publish_rechecks_staged_heading_against_current_plan(self):
        self.save_plan(1, title="门后的雨")
        self.commit(1)
        story.history.save_dependencies(self.book, {
            "chapter": 1, "chapter_sha": story.digest(DRAFT), "dependencies": [],
            "complete": True, "note": "这一章没有外部依赖，修订后仍逐段核对。"},
            self.book.meta("revision"))
        packet = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        staged = self.stage_history(packet, {1: DRAFT + "她记住了门的方向。\n"})
        # Simulate a stale legacy plan write that did not advance the branch revision.
        plan = self.book.get_plan(1)
        plan["title"] = "改过的标题"
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(plan),))
        with patch.object(story.history, "_check_fences"):
            self.assert_error("lint_failed", lambda: story.history.branch_publish(
                self.book, staged["branch"], self.book.meta("revision")))
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(), DRAFT)

    def test_historical_revision_recovers_edited_retired_path_of_older_chapter(self):
        for chapter in (1, 2):
            self.save_plan(chapter, **({"title": "渡口另约"} if chapter == 2 else {}))
            text = DRAFT if chapter == 1 else "第2章 渡口另约\n" + BODY
            self.commit(chapter, text)
            story.history.save_dependencies(
                self.book, {"chapter": chapter, "chapter_sha": story.digest(text), "dependencies": [],
                            "complete": True, "note": "逐章核对因果与视角，两章没有相互依赖。"},
                self.book.meta("revision"))
        old_relative = "chapters/第一卷 雨夜/第1章 门后的雨.md"
        old = self.root / old_relative
        later_relative = self.book.chapter_path(2)
        later = self.root / later_relative
        packet = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        revised = "第1章 新的入口\n" + BODY + "她没有回头。\n"
        staged = self.stage_history(packet, {1: revised})
        with patch.object(story, "atomic_write", side_effect=OSError("publication interrupted")):
            failed = story.history.branch_publish(self.book, staged["branch"], self.book.meta("revision"))
        self.assertFalse(failed["exports_complete"])
        intermediate = self.root / "chapters/第一卷 雨夜/第1章 新的入口.md"
        external = "第1章 渡口的灯\n" + BODY + "她停步看向河对岸的灯。\n"
        old.write_bytes(external.encode("utf-8"))
        recovered = self.book.export(safe_only=True)
        self.assertFalse(recovered["exports_complete"])
        self.assertIn(old_relative, recovered["changed_exports"])
        self.assertEqual(intermediate.read_bytes(), revised.encode("utf-8"))

        packet = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        self.assertEqual([item["chapter"] for item in packet["affected"]], [1])
        self.assertEqual(packet["affected"][0]["external_edit"]["path"], old_relative)
        self.assertEqual(packet["affected"][0]["external_edit"]["sha256"], story.digest(external))
        staged = self.stage_history(packet, {1: external})
        result = story.history.branch_publish(self.book, staged["branch"], self.book.meta("revision"))
        self.assertTrue(result["exports_complete"])
        latest_relative = "chapters/第一卷 雨夜/第1章 渡口的灯.md"
        self.assertEqual(self.book.chapter_path(1), latest_relative)
        self.assertEqual((self.root / latest_relative).read_bytes(), external.encode("utf-8"))
        self.assertFalse(old.exists())
        self.assertFalse(intermediate.exists())
        self.assertEqual(later.read_bytes(), ("第2章 渡口另约\n" + BODY).encode("utf-8"))
        self.assertEqual(self.book.chapter_path(2), later_relative)
        self.assertTrue(any(Path(path).read_bytes() == external.encode("utf-8") for path in result["backups"]))
        self.assertEqual(self.book.db.execute(
            "SELECT count(*) FROM meta WHERE key GLOB 'chapter_retired:*'").fetchone()[0], 0)

    def test_unsafe_volume_and_title_components_are_rejected_without_state_changes(self):
        for field in ("title", "volume_dir"):
            for value in ("../外部", "卷/子目录", "卷\\子目录", "..", "卷\n标题", "卷\x00标题"):
                with self.subTest(field=field, value=value):
                    revision = self.book.meta("revision")
                    self.assert_error("invalid_input", lambda: self.save_plan(1, **{field: value}))
                    self.assertEqual(self.book.meta("revision"), revision)
                    self.assertIsNone(self.book.db.execute("SELECT data FROM plans WHERE chapter=1").fetchone())


if __name__ == "__main__":
    unittest.main()
