"""Explicit adopted-outline binding without guessing Markdown intent."""

import hashlib
import importlib.util
import json
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

    def file_snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

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

    def test_related_chapters_are_limited_to_the_same_recorded_file(self):
        self.book.save_plan(2, base.plan(goal="核对收据"), self.book.meta("revision"))
        first_path, first_sha = self.readable()
        second_path, second_sha = self.readable("01_大纲细纲/第2章 领取资格.md")
        self.bind(first_path, first_sha)
        outline.bind(self.book, 2, second_path, self.book.meta("revision"), second_sha)
        with self.book.transaction():
            self.book.set_meta("outline_binding:unrelated", "broken legacy record")
        revision = self.book.meta("revision")
        self.assertEqual(self.bind(first_path, first_sha)["related_chapters"], [1])
        self.assertEqual(self.book.meta("revision"), revision)
        self.root.joinpath(first_path).unlink()
        error = self.assert_error("outline_plan_drift", self.book.context, 1)
        self.assertEqual(error.details["related_chapters"], [1])
        self.assertEqual(self.book.meta("revision"), revision)

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

    def test_shared_outline_chapter_progress_does_not_override_document_status(self):
        path, sha = self.readable("01_大纲细纲/全书规划.md", """# 全书规划
状态：已采用

## 第1章 门后的雨
状态：已提交
沈禾交出钥匙。

## 第2章 领取资格
状态：待写
沈禾核对收据。
""")
        self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
        self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_statuses_before_and_after_document_title_must_agree(self):
        revision = self.book.meta("revision")
        for prefix, title in (("status: adopted\n", "# 章细纲"),
                              ("status: adopted\n", "# 雨夜的交接"),
                              ("status: adopted\n", "## 章细纲"),
                              ("---\nstatus: adopted\n---\n", "# 章细纲"),
                              ("---\n# metadata comment\nstatus: adopted\n...\n", "# 雨夜的交接")):
            with self.subTest(prefix=prefix, title=title):
                path, sha = self.readable("README.md", prefix + title + "\n状态：候选\n\n## 本章目标\n沈禾交出钥匙。\n")
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["adopted", "候选"])
                self.assertEqual(self.book.meta("revision"), revision)
                self.assertIsNone(outline.binding_for(self.book, 1))

    def test_consistent_frontmatter_and_title_status_are_both_accepted(self):
        path, sha = self.readable("README.md", "---\n# metadata comment\nstatus: adopted\n---\n"
                                  "# 雨夜的交接\n状态：已采用\n\n## 第1章\n状态：待写\n")
        self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
        self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_frontmatter_values_cannot_supply_document_adoption(self):
        revision = self.book.meta("revision")
        for metadata in ("description: |\n  status: adopted\n",
                         "description: >-\n  状态：已采用\n",
                         "metadata:\n  status: adopted\n",
                         "metadata: {status: adopted}\n",
                         "description: \"说明\nstatus: adopted\n\"\n",
                         "description: '说明\nstatus: adopted\n'\n"):
            with self.subTest(metadata=metadata):
                content = "---\n" + metadata + "---\n# 全书总纲\n尚未确认采用。\n"
                path, sha = self.readable("README.md", content)
                args = base.story.parser().parse_args([
                    "outline-bind", "--book", str(self.root), "--chapter", "1", "--file", path,
                    "--sha256", sha, "--expect", str(revision)])
                self.assert_error("invalid_input", base.story.run, args)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)
                self.assert_error("outline_binding_required", self.book.commit, 1, self.draft, self.delta())
                self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_frontmatter_scalar_markers_cannot_hide_candidate_header(self):
        revision = self.book.meta("revision")
        for marker, example in (("```", "```\n状态：已采用\n```"),
                                ("~~~", "~~~\n状态：已采用\n~~~"),
                                ("<!--", "<!--\n状态：已采用\n-->")):
            with self.subTest(marker=marker):
                content = (f"---\ndescription: |\n  {marker}\nstatus: candidate\n---\n"
                           f"# 全书总纲\n{example}\n## 第1章\n沈禾交出钥匙。\n")
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["candidate"])
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)

    def test_frontmatter_metadata_and_markdown_have_separate_parser_state(self):
        content = ("---\ndescription: |-\n  status: candidate\n  ---\n  ```\n  <!--\n"
                   "  # not a heading\nstatus: adopted\n  # metadata comment\n---\n# 全书规划\n状态：已采用\n"
                   "\n## 第1章\n状态：待写\n沈禾交出钥匙。\n")
        path, sha = self.readable("README.md", content)
        args = base.story.parser().parse_args([
            "outline-bind", "--book", str(self.root), "--chapter", "1", "--file", path,
            "--sha256", sha, "--expect", str(self.book.meta("revision"))])
        self.assertEqual(base.story.run(args)["outline"]["status"], "adopted")
        self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")
        committed = self.book.commit(1, self.draft, self.delta())
        self.assertTrue(committed["exports_complete"])
        self.assertEqual(self.book.meta("last_chapter"), 1)

    def test_unclosed_or_unsupported_frontmatter_cannot_borrow_markdown_adoption(self):
        revision = self.book.meta("revision")
        for prefix in ("---\nstatus: adopted\n",
                       "---\nstatus: adopted\nmetadata: [\nstatus: candidate\n]\n---\n",
                       "---\nstatus: adopted\ndescription: \"说明\nstatus: candidate\n\"\n---\n",
                       "---\nstatus: adopted\ndescription:\n  status: candidate\n---\n",
                       "---\nstatus: |\n  adopted\n---\n",
                       "---\nstatus: \"adopted\"\n---\n",
                       "---\nstatus: adopted\n  but still awaiting approval\n---\n"):
            with self.subTest(prefix=prefix):
                path, sha = self.readable("README.md", prefix + "# 全书规划\n状态：已采用\n")
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)

    def test_indented_opening_delimiters_are_code_not_frontmatter(self):
        for indent in ("    ", "\t", " \t"):
            with self.subTest(indent=indent):
                content = (f"{indent}---\n{indent}状态：候选\n{indent}---\n\n"
                           "# 全书总纲\n状态：已采用\n\n## 第1章\n状态：待写\n")
                path, sha = self.readable("README.md", content)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_plain_frontmatter_keys_allow_spacing_and_comments(self):
        for declaration in ("status : adopted", "状态 : 已采用", "status\t: adopted # 已确认"):
            with self.subTest(declaration=declaration):
                content = ("---\n" + declaration + "\n  # metadata note\n---\n"
                           "# 全书总纲\n\n## 第1章\n状态：待写\n")
                path, sha = self.readable("README.md", content)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_setext_body_status_cannot_supply_document_adoption(self):
        revision = self.book.meta("revision")
        for prefix in ("", "# 全书规划\n\n", "---\nupdated: 2026-10-03\n---\n"):
            for heading in ("第1章", "本章目标", "场景一", "第1章\n门后的雨"):
                for underline in ("===", "---"):
                    with self.subTest(prefix=prefix, heading=heading, underline=underline):
                        content = prefix + heading + "\n" + underline + "\n状态：已采用\n"
                        path, sha = self.readable("README.md", content)
                        self.assert_error("invalid_input", self.bind, path, sha)
                        self.assertIsNone(outline.binding_for(self.book, 1))
                        self.assertEqual(self.book.meta("revision"), revision)
        self.assert_error("outline_binding_required", self.book.commit, 1, self.draft, self.delta())
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_adopted_header_is_not_overridden_by_setext_chapter_progress(self):
        for prefix in ("状态：已采用\n\n", "# 全书规划\n状态：已采用\n\n",
                       "---\nstatus: adopted\n---\n"):
            for underline in ("===", "---"):
                with self.subTest(prefix=prefix, underline=underline):
                    content = prefix + "第1章\n门后的雨\n" + underline + "\n状态：待写\n"
                    path, sha = self.readable("README.md", content)
                    self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                    self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")
        committed = self.book.commit(1, self.draft, self.delta())
        self.assertTrue(committed["exports_complete"])
        self.assertEqual(self.book.meta("last_chapter"), 1)

    def test_setext_document_titles_preserve_adoption_and_header_conflicts(self):
        for title, underline in (("全书总纲", "---"), ("第1章 雨夜细纲", "---"),
                                 ("雨夜的交接", "===")):
            with self.subTest(title=title, underline=underline):
                document = title + "\n" + underline + "\n状态：已采用\n\n## 本章目标\n状态：待写\n"
                path, sha = self.readable("README.md", document)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                previous = outline.binding_for(self.book, 1)
                revision = self.book.meta("revision")
                path, sha = self.readable("README.md", "status: candidate\n\n" + document)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["candidate", "已采用"])
                self.assertEqual(outline.binding_for(self.book, 1), previous)
                self.assertEqual(self.book.meta("revision"), revision)

    def test_multiline_setext_body_title_cannot_supply_status(self):
        for underline in ("===", "---"):
            with self.subTest(underline=underline):
                path, sha = self.readable("README.md", "第1章\n状态：已采用\n" + underline + "\n")
                revision = self.book.meta("revision")
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)

    def test_setext_examples_do_not_hide_later_header_conflicts(self):
        for example in ("```\n第1章\n---\n```\n", "<!--\n第1章\n===\n-->\n",
                        "    第1章\n    ---\n"):
            with self.subTest(example=example):
                content = "# 全书总纲\n状态：已采用\n" + example + "status: candidate\n## 第1章\n"
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "candidate"])

    def test_titleless_status_does_not_consume_a_chapter_heading_as_title(self):
        for prefix in ("状态：已采用\n\n", "---\nstatus: adopted\n---\n"):
            for heading in ("# 第1章", "## 第1章", "# 本章目标", "## 场景一"):
                with self.subTest(prefix=prefix, heading=heading):
                    path, sha = self.readable("README.md", prefix + heading + "\n状态：待写\n")
                    self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                    self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_body_adoption_declaration_cannot_supply_document_header(self):
        revision = self.book.meta("revision")
        for title in ("", "# 全书规划\n", "---\nupdated: 2026-10-03\n---\n"):
            for heading in ("# 第1章", "## 第1章", "# 场景一", "## 场景一"):
                with self.subTest(title=title, heading=heading):
                    path, sha = self.readable("README.md", title + heading + "\n状态：已采用\n")
                    self.assert_error("invalid_input", self.bind, path, sha)
                    self.assertIsNone(outline.binding_for(self.book, 1))
                    self.assertEqual(self.book.meta("revision"), revision)

    def test_contradictory_document_header_is_rejected_before_body_sections(self):
        for first in ("状态：已采用", "## 状态：已采用", "- **状态**：已采用",
                      "| 状态 | 已采用 |"):
            with self.subTest(first=first):
                content = f"# 全书规划\n{first}\nstatus: candidate\n## 第1章\n状态：已采用\n"
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "candidate"])

    def test_document_status_heading_is_allowed_before_chapter_sections(self):
        for first in ("## 状态：已采用", "- **状态**：已采用", "| 状态 | 已采用 |"):
            with self.subTest(first=first):
                path, sha = self.readable("README.md", f"# 全书规划\n{first}\n## 第1章\n状态：待写\n")
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")

    def test_explicit_outline_document_titles_keep_legacy_heading_levels(self):
        for title in ("章细纲", "全书规划", "全书总纲", "本卷大纲", "近期细纲（第1至3章）"):
            for level in ("##", "###"):
                with self.subTest(title=title, level=level):
                    content = f"{level} {title} ###\n状态：已采用\n\n#### 第1章\n状态：待写\n"
                    path, sha = self.readable("README.md", content)
                    self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                    self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_custom_chapter_outline_titles_keep_document_status(self):
        for title in ("第1章 门后的雨 · 细纲", "第1章 雨夜细纲", "第一章 雨夜细纲",
                      "第１章 雨夜细纲（已审阅）"):
            for level in ("#", "##", "###"):
                with self.subTest(title=title, level=level):
                    content = f"{level} {title}\n状态：已采用\n\n#### 本章目标\n状态：待写\n"
                    path, sha = self.readable("README.md", content)
                    self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                    self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_custom_outline_titles_cannot_hide_conflicts_or_adopt_body_status(self):
        revision = self.book.meta("revision")
        for content, declared in (
            ("## 第1章 雨夜细纲\n状态：候选\n", ["候选"]),
            ("## 第1章 雨夜细纲\n### 场景一\n状态：已采用\n", []),
            ("状态：已采用\n## 第1章 雨夜细纲\n状态：候选\n### 场景一\n", ["已采用", "候选"]),
            ("状态：候选\n## 第1章 雨夜细纲\n状态：已采用\n", ["候选", "已采用"]),
        ):
            with self.subTest(content=content):
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details.get("declared_statuses", []), declared)
                self.assertEqual(self.book.meta("revision"), revision)
                self.assertIsNone(outline.binding_for(self.book, 1))

    def test_later_chapter_outline_heading_ends_document_header(self):
        path, sha = self.readable("README.md", "# 全书规划\n状态：已采用\n"
                                  "## 第1章 雨夜细纲\n状态：待写\n")
        self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
        self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_indented_examples_cannot_supply_the_document_status(self):
        revision = self.book.meta("revision")
        for indent in ("    ", "\t", " \t"):
            for example in ("状态：已采用", "| 状态 | 已采用 |"):
                with self.subTest(indent=indent, example=example):
                    content = f"# 章细纲\n\n字段格式示例：\n\n{indent}{example}\n"
                    path, sha = self.readable("README.md", content)
                    self.assert_error("invalid_input", self.bind, path, sha)
                    self.assertIsNone(outline.binding_for(self.book, 1))
                    self.assertEqual(self.book.meta("revision"), revision)

    def test_indented_candidate_examples_do_not_override_real_document_status(self):
        for declaration in ("状态：已采用", "| 状态 | 已采用 |"):
            for indent in ("    ", "\t", " \t"):
                with self.subTest(declaration=declaration, indent=indent):
                    content = (f"# 章细纲\n   {declaration}\n\n字段格式示例：\n\n"
                               f"{indent}状态：候选\n{indent}| 状态 | 草稿 |\n")
                    path, sha = self.readable("README.md", content)
                    self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                    self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_shared_outline_progress_requires_rebinding_every_associated_chapter(self):
        self.book.save_plan(2, base.plan(goal="核对收据"), self.book.meta("revision"))
        content = ("# 全书规划\n状态：已采用\n\n## 第1章 门后的雨\n状态：待写\n"
                   "沈禾交出钥匙。\n\n## 第2章 领取资格\n状态：待写\n沈禾核对收据。\n")
        path, sha = self.readable("01_大纲细纲/全书规划.md", content)
        self.assertEqual(self.bind(path, sha)["related_chapters"], [1])
        bound = outline.bind(self.book, 2, path, self.book.meta("revision"), sha)
        self.assertEqual(bound["related_chapters"], [1, 2])
        self.assertEqual(outline.verify(self.book, 2, self.book.get_plan(2))["status"], "adopted")
        self.assertTrue(self.book.commit(1, self.draft, self.delta())["committed"])
        self.assertEqual(self.book.context(2)["outline"]["status"], "adopted")

        _, new_sha = self.readable(path, content.replace("状态：待写", "状态：已提交", 1))
        revision = self.book.meta("revision")
        error = self.assert_error("outline_plan_drift", self.book.context, 2)
        self.assertEqual(error.details["changed"], ["outline"])
        self.assertEqual(error.details["related_chapters"], [1, 2])
        self.assertEqual(self.book.meta("revision"), revision)

        self.assertEqual(self.bind(path, new_sha)["related_chapters"], [1, 2])
        self.assert_error("outline_plan_drift", self.book.context, 2)
        outline.bind(self.book, 2, path, self.book.meta("revision"), new_sha)
        self.assertEqual(self.book.context(2)["outline"]["status"], "adopted")

    def bound_history_sequence(self, rebind_between_chapters=False):
        original = self.book.cards(["hero"])["hero"]
        self.book.save_plan(2, base.plan(title="核对收据", goal="核对收据换回账本",
                                       stop="拿到账本，不取回钥匙"), self.book.meta("revision"))
        content = ("# 全书规划\n状态：已采用\n\n## 第1章 门后的雨\n状态：待写\n"
                   "沈禾交出钥匙。\n\n## 第2章 核对收据\n状态：待写\n沈禾拿到账本。\n")
        path, sha = self.readable("01_大纲细纲/全书规划.md", content)
        for chapter in (1, 2):
            outline.bind(self.book, chapter, path, self.book.meta("revision"), sha)
        self.assertEqual(self.book.cards(["hero"])["hero"], original)
        first = self.book.commit(1, self.draft, self.delta(changes=[{
            "id": "hero", "text": "沈禾已交出钥匙，仍未取得账本。", "quote": "沈禾把唯一的钥匙交给守门人。"}]))
        self.assertTrue(first["exports_complete"])
        after_first = self.book.cards(["hero"])["hero"]

        if rebind_between_chapters:
            _, sha = self.readable(path, content.replace("状态：待写", "状态：已提交", 1))
            for chapter in (1, 2):
                outline.bind(self.book, chapter, path, self.book.meta("revision"), sha)
            self.assertEqual(self.book.cards(["hero"])["hero"], after_first)

        second_text = "第2章 核对收据\n沈禾拿到账本，却没有拿回钥匙。\n她把收据交给守门人，约定天亮前还书。\n"
        self.draft.write_bytes(second_text.encode("utf-8"))
        raw = self.delta(second_text, summary="沈禾取得账本，钥匙仍在守门人手中。", changes=[{
            "id": "hero", "text": "沈禾已取得账本，钥匙仍在守门人手中。", "quote": "沈禾拿到账本，却没有拿回钥匙。"}])
        for check in raw["review"]["checks"].values():
            check.update(note="核对取得账本与未取回钥匙的结果。", quote="沈禾拿到账本，却没有拿回钥匙。")
        second = self.book.commit(2, self.draft, raw)
        self.assertTrue(second["exports_complete"])
        return original, after_first, self.book.cards(["hero"])["hero"], first, second

    def history_cli(self, name, chapter, *extra):
        return base.story.run(base.story.parser().parse_args([
            name, "--book", str(self.root), "--chapter", str(chapter), *extra]))

    def test_history_cli_reconstructs_before_and_after_write_time_outline_bindings(self):
        original, after_first, after_second, first, _ = self.bound_history_sequence()
        revision = self.book.meta("revision")
        for chapter, before, after in ((1, original, after_first), (2, after_first, after_second)):
            with self.subTest(chapter=chapter):
                self.assertEqual(self.history_cli("history-state", chapter, "--before")["cards"], [before])
                self.assertEqual(self.history_cli("history-state", chapter)["cards"], [after])
        self.assertEqual(self.book.meta("revision"), revision)
        branch = self.history_cli("history-start", 1, "--expect", str(revision))
        self.assertEqual([item["chapter"] for item in branch["affected"]], [1, 2])
        self.assertEqual(branch["baseline_publication_revision"], first["revision"] - 1)
        pointers = json.loads(base.story.history._body(self.book, branch["baseline_state_sha256"]))
        self.assertEqual(json.loads(base.story.history._body(self.book, pointers["hero"])), original)
        self.assertEqual(self.book.cards(["hero"])["hero"], after_second)
        self.assertEqual(self.book.meta("last_chapter"), 2)
        self.assertTrue(all(item["candidate_sha256"] is None for item in branch["affected"]))

    def test_shared_outline_rebindings_are_neutral_during_history_reconstruction(self):
        _, after_first, after_second, _, second = self.bound_history_sequence(rebind_between_chapters=True)
        self.assertEqual(self.history_cli("history-state", 2, "--before")["cards"], [after_first])
        self.assertEqual(self.history_cli("history-state", 2)["cards"], [after_second])
        branch = self.history_cli("history-start", 2, "--expect", str(self.book.meta("revision")))
        self.assertEqual(branch["baseline_publication_revision"], second["revision"] - 1)
        pointers = json.loads(base.story.history._body(self.book, branch["baseline_state_sha256"]))
        self.assertEqual(json.loads(base.story.history._body(self.book, pointers["hero"])), after_first)
        self.assertEqual(self.book.cards(["hero"])["hero"], after_second)
        self.assertEqual(self.book.meta("last_chapter"), 2)

    def test_fenced_examples_do_not_reject_adopted_header_or_context(self):
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = (f"# 章细纲\n状态：已采用\n{marker}markdown\n"
                           f"状态：候选\n| 状态 | 草稿 |\n{marker}\n沈禾交出钥匙。\n")
                path, sha = self.readable("README.md", content)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_fenced_adopted_example_cannot_supply_required_declaration(self):
        revision = self.book.meta("revision")
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = f"# 章细纲\n{marker}markdown\n状态：已采用\n{marker}\n尚未确认。\n"
                path, sha = self.readable("README.md", content)
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.book.meta("revision"), revision)

    def test_candidate_header_after_fenced_adoption_example_is_rejected(self):
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = f"{marker}markdown\n状态：已采用\n{marker}\n状态：候选\n"
                path, sha = self.readable("README.md", content)
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))

    def test_bound_context_rejects_candidate_header_after_fenced_example(self):
        revision = self.book.meta("revision")
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = f"{marker}markdown\n状态：已采用\n{marker}\n状态：候选\n"
                path, sha = self.readable("README.md", content)
                record = {"status": "adopted", "chapter": 1, "path": path,
                          "sha256": sha, "plan_sha256": outline._plan_sha256(self.book.get_plan(1))}
                with self.book.transaction():
                    self.book.set_meta("outline_binding:1", record)
                error = self.assert_error("outline_plan_drift", self.book.context, 1)
                self.assertEqual(error.details["declared_statuses"], ["候选"])
                self.assertEqual(self.book.meta("revision"), revision)

    def test_status_fence_requires_matching_marker_length_and_plain_closer(self):
        for marker, other in (("`", "~"), ("~", "`")):
            with self.subTest(marker=marker):
                content = (f"status: adopted\n   {marker * 4}markdown\n"
                           f"状态：候选\n{marker * 3}\n状态：草稿\n"
                           f"{other * 4}\n| 状态 | 待定 |\n"
                           f"{marker * 4}markdown\n状态：候选\n"
                           f"  {marker * 5} \t\nstatus: candidate\n")
                self.assertEqual(outline._declared_statuses(content.encode("utf-8")),
                                 ["adopted", "candidate"])

    def test_adopted_header_after_long_notes_or_code_examples_can_be_committed(self):
        for notes in ("作者说明。\n" * 40, "作者说明：" + "甲" * 24000 + "\n",
                      "```markdown\n" + "状态：候选\n" * 40 + "```\n"):
            with self.subTest(notes=notes[:40]):
                content = "# 章细纲\n" + notes + "状态：已采用\n## 本章目标\n状态：待写\n"
                path, sha = self.readable("README.md", content)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")
                self.assertEqual(self.book.lint(1, self.draft)["outline"]["status"], "adopted")
        committed = self.book.commit(1, self.draft, self.delta())
        self.assertTrue(committed["committed"])
        self.assertTrue(committed["exports_complete"])
        self.assertEqual(self.root.joinpath(self.book.chapter_path(1)).read_bytes(), base.DRAFT.encode("utf-8"))

    def test_long_header_conflicts_block_binding_and_writing_without_file_changes(self):
        for notes in ("作者说明。\n" * 38, "作者说明：" + "甲" * 24000 + "\n"):
            with self.subTest(notes=notes[:40]):
                content = "# 章细纲\n状态：已采用\n" + notes + "状态：候选\n## 本章目标\n"
                path, sha = self.readable("README.md", content)
                before = self.file_snapshot()
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])
                self.assertEqual(self.file_snapshot(), before)

                # Simulate a record accepted by the former truncated scan.
                record = {"status": "adopted", "chapter": 1, "path": path,
                          "sha256": sha, "plan_sha256": outline._plan_sha256(self.book.get_plan(1))}
                with self.book.transaction():
                    self.book.set_meta("outline_binding:1", record)
                before = self.file_snapshot()
                for operation in (lambda: self.book.context(1),
                                  lambda: self.book.lint(1, self.draft),
                                  lambda: self.book.prepare(1, self.draft),
                                  lambda: self.book.commit(1, self.draft, self.delta())):
                    error = self.assert_error("outline_plan_drift", operation)
                    self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])
                    self.assertEqual(self.file_snapshot(), before)
                self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_commented_adoption_cannot_supply_a_new_binding(self):
        for comment in ("<!--\n状态：已采用\n-->\n",
                        "<!--\n```markdown\n状态：已采用\n## 本章目标\n-->\n",
                        "<!-- 状态：已采用 -->\n", "<!--\n状态：已采用\n"):
            with self.subTest(comment=comment):
                path, sha = self.readable("README.md", "# 章细纲\n" + comment + "## 本章目标\n")
                before = self.file_snapshot()
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.file_snapshot(), before)

    def test_commented_candidate_and_headings_do_not_override_real_adoption(self):
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = (f"# 章细纲\n<!--\n{marker}markdown\n## 第1章\n状态：候选\n-->\n"
                           "状态：已采用 <!-- 保留审阅说明 -->\n## 本章目标\n状态：待写\n")
                path, sha = self.readable("README.md", content)
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")
        committed = self.book.commit(1, self.draft, self.delta())
        self.assertTrue(committed["committed"])
        self.assertTrue(committed["exports_complete"])

    def test_comment_openers_in_code_do_not_hide_a_real_candidate_declaration(self):
        examples = ("```markdown\n<!--\n```\n", "~~~markdown <!--\n状态：已采用\n~~~\n",
                    "    <!--\n", "\t<!--\n", " \t<!--\n",
                    "字段示例：`<!--`\n", "字段示例：``字面 ` <!--``\n",
                    "注释格式：\\<!--\n")
        for example in examples:
            with self.subTest(example=example):
                content = "# 章细纲\n状态：已采用\n" + example + "状态：候选\n## 本章目标\n"
                path, sha = self.readable("README.md", content)
                before = self.file_snapshot()
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])
                self.assertEqual(self.file_snapshot(), before)

    def test_comment_fences_cannot_hide_a_later_candidate_declaration(self):
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                content = (f"# 章细纲\n状态：已采用\n<!--\n{marker}markdown\n"
                           "## 本章目标\n-->\n状态：候选\n## 本章目标\n")
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])

    def test_multiline_code_spans_cannot_supply_a_document_declaration(self):
        for marker in ("`", "``"):
            with self.subTest(marker=marker):
                content = f"# 章细纲\n{marker}字段示例\n状态：已采用\n{marker}\n## 本章目标\n"
                path, sha = self.readable("README.md", content)
                before = self.file_snapshot()
                self.assert_error("invalid_input", self.bind, path, sha)
                self.assertIsNone(outline.binding_for(self.book, 1))
                self.assertEqual(self.file_snapshot(), before)

    def test_multiline_code_comment_openers_cannot_hide_real_candidate_status(self):
        for marker in ("`", "``"):
            with self.subTest(marker=marker):
                content = (f"# 章细纲\n状态：已采用\n{marker}字段示例\n<!--\n{marker}\n"
                           "状态：候选\n## 本章目标\n")
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])

    def test_escaped_comment_and_backtick_openers_keep_visible_statuses(self):
        for example in ("注释格式：\\<!--\n状态：候选\n",
                        "反引号格式：\\`\n状态：候选\n`\n",
                        "注释格式：\\\\<!--\n状态：草稿\n-->\n状态：候选\n"):
            with self.subTest(example=example):
                content = "# 章细纲\n状态：已采用\n" + example + "## 本章目标\n"
                path, sha = self.readable("README.md", content)
                error = self.assert_error("invalid_input", self.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])

    def test_unpaired_inline_code_cannot_cross_blank_heading_or_fence_boundaries(self):
        for middle, ending, expected in (("未闭合 `示例\n", "", ["已采用", "候选"]),
                                         ("未闭合 `示例\n\n", "`\n", ["已采用", "候选"]),
                                         ("未闭合 `示例\n```markdown\n状态：草稿\n```\n", "`\n", ["已采用", "候选"]),
                                         ("未闭合 `示例\n## 本章目标\n", "`\n", ["已采用"])):
            with self.subTest(middle=middle):
                content = "# 章细纲\n状态：已采用\n" + middle + "状态：候选\n" + ending
                self.assertEqual(outline._declared_statuses(content.encode("utf-8")), expected)

    def test_hiding_a_newly_bound_declaration_still_blocks_all_writing_gates(self):
        path, sha = self.readable("README.md", "# 章细纲\n状态：已采用\n## 本章目标\n")
        self.bind(path, sha)
        self.readable(path, "# 章细纲\n<!--\n状态：已采用\n-->\n## 本章目标\n")
        before = self.file_snapshot()
        for operation in (lambda: self.book.context(1), lambda: self.book.lint(1, self.draft),
                          lambda: self.book.prepare(1, self.draft),
                          lambda: self.book.commit(1, self.draft, self.delta())):
            error = self.assert_error("outline_plan_drift", operation)
            self.assertEqual(error.details["changed"], ["outline"])
            self.assertEqual(self.file_snapshot(), before)
        self.assertEqual(self.book.meta("last_chapter"), 0)

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

    def test_natural_title_words_are_not_draft_or_candidate_path_markers(self):
        for name in ("候选人", "草稿纸上的秘密", "draftsmanship", "candidature"):
            with self.subTest(name=name):
                path, sha = self.readable(f"01_大纲细纲/候选人的旅途/第1章 {name}.md")
                self.assertEqual(self.bind(path, sha)["outline"]["status"], "adopted")
                self.assertEqual(self.book.context(1)["outline"]["status"], "adopted")

    def test_explicit_draft_and_candidate_role_markers_remain_rejected(self):
        for marker in ("候选", "候选稿", "候选细纲", "草稿", "草稿版", "草稿细纲", "draft", "drafts",
                       "candidate", "candidates", "ＤＲＡＦＴ"):
            for pattern in ("01_大纲细纲/{marker}/第1章.md", "01_大纲细纲/第1章_{marker}_v2.md",
                            "01_大纲细纲/第1章.{marker}.md", "01_大纲细纲/第1章（{marker}）.md"):
                relative = pattern.format(marker=marker)
                with self.subTest(relative=relative):
                    self.assert_error("invalid_input", self.bind, relative, "0" * 64)

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
