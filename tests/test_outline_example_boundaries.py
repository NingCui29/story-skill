"""Examples cannot authorize manuscript saves or satisfy outline audit fields."""

import unittest

import test_plan_outline_sync as binding
import test_outline_position_audit as positioning


class OutlineExampleBoundaryTests(unittest.TestCase):
    def binding_fixture(self):
        fixture = binding.OutlineSyncTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def positioning_fixture(self):
        fixture = positioning.OutlinePositionAuditTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_quoted_adoption_examples_cannot_bind_or_commit(self):
        for example in ("> 状态：已采用\n", "> 字段示例：\n状态：已采用\n",
                        "> > 字段示例：\n状态：已采用\n"):
            with self.subTest(example=example):
                f = self.binding_fixture()
                path, sha = f.readable(content="# 章细纲\n" + example + "\n## 本章目标\n")
                before = (tuple(f.book.db.iterdump()), f.file_snapshot())
                f.assert_error("invalid_input", f.bind, path, sha)
                f.assert_error("outline_binding_required", f.book.commit, 1, f.draft, f.delta())
                self.assertEqual((tuple(f.book.db.iterdump()), f.file_snapshot()), before)
                self.assertIsNone(binding.outline.binding_for(f.book, 1))
                self.assertEqual(f.book.meta("last_chapter"), 0)

    def test_quoted_markers_cannot_hide_a_real_candidate(self):
        for example in ("> <!--\n", "> <!--\n> 状态：已采用\n> -->\n",
                        "> 示例\n<!-- 顶层注释 -->\n", ">     <!--\n",
                        "> ```\n> 示例\n> ```\n",
                        "> # 字段示例\n", "> 字段示例：\n状态：已采用\n\n"):
            with self.subTest(example=example):
                f = self.binding_fixture()
                path, sha = f.readable(content="# 章细纲\n状态：已采用\n" + example +
                                      "状态：候选\n## 本章目标\n")
                before = (tuple(f.book.db.iterdump()), f.file_snapshot())
                error = f.assert_error("invalid_input", f.bind, path, sha)
                self.assertEqual(error.details["declared_statuses"], ["已采用", "候选"])
                self.assertEqual((tuple(f.book.db.iterdump()), f.file_snapshot()), before)

    def test_real_declaration_after_quoted_blocks_can_commit(self):
        for example in ("> 示例\n\n", "> # 示例标题\n",
                        "> ```\n> 示例\n> ```\n", "> 旧模板\n> ---\n",
                        ">     示例代码\n", "> \t示例代码\n"):
            with self.subTest(example=example):
                f = self.binding_fixture()
                path, sha = f.readable(content="# 章细纲\n" + example +
                                      "状态：已采用\n## 本章目标\n")
                f.bind(path, sha)
                result = f.book.commit(1, f.draft, f.delta())
                self.assertTrue(result["committed"])
                self.assertTrue(result["exports_complete"])

    def test_replacing_a_bound_declaration_with_a_quote_blocks_writing(self):
        f = self.binding_fixture()
        path, sha = f.readable(content="# 章细纲\n状态：已采用\n## 本章目标\n")
        f.bind(path, sha)
        f.readable(path, "# 章细纲\n> 示例\n状态：已采用\n\n## 本章目标\n")
        before = (tuple(f.book.db.iterdump()), f.file_snapshot())
        for operation in (lambda: f.book.context(1), lambda: f.book.prepare(1, f.draft),
                          lambda: f.book.commit(1, f.draft, f.delta())):
            f.assert_error("outline_plan_drift", operation)
            self.assertEqual((tuple(f.book.db.iterdump()), f.file_snapshot()), before)

    def test_frontmatter_templates_cannot_satisfy_platform_coverage(self):
        f = self.positioning_fixture()
        templates = f.platform_block("fanqie") + "\n" + f.platform_block("qimao")
        text = "---\ndescription: |\n" + "".join("  " + line + "\n" for line in templates.splitlines())
        text += "---\n# 全书总纲\n定位尚未填写。\n"
        before = tuple(f.book.db.iterdump())
        result = f.audit(text, require_platforms=("fanqie", "qimao"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["covered_platforms"], [])
        self.assertEqual(result["section_lines"], [])
        self.assertEqual(tuple(f.book.db.iterdump()), before)

    def test_readonly_audit_keeps_real_sections_after_complex_metadata(self):
        f = self.positioning_fixture()
        prefix = '---\ntitle: "候选规划"\nreviewers: [甲, 乙]\n---\n\n'
        result = f.audit(prefix + f.platform_block("fanqie"), require_platforms=("fanqie",))
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["section_lines"], [6])
        result = f.audit('---\ndescription: |\n' + f.complete_outline())
        self.assertFalse(result["ok"])
        self.assertEqual(result["section_lines"], [])

    def test_inline_and_quoted_field_examples_do_not_fill_missing_fields(self):
        f = self.positioning_fixture()
        original = f.complete_outline()
        for replacement in ('`目标平台：起点。`\n- 作品阶段：拟投。',
                            '> 填写示例：\n目标平台：起点。\n\n- 作品阶段：拟投。'):
            with self.subTest(replacement=replacement):
                result = f.audit(original.replace('- 目标平台：起点；作品阶段：拟投。', replacement))
                self.assertFalse(result["ok"])
                self.assertIn({"code": "position_field_missing", "field": "目标平台"}, result["issues"])
        result = f.audit(original.replace('- 来源与核对日期：后台参考；2026-10-01。',
                                         '- 备注：`示例；来源与核对日期：2026-10-01`'))
        self.assertFalse(result["ok"])
        self.assertIn({"code": "source_date_missing"}, result["issues"])
        result = f.audit(original.replace('- 来源与核对日期：后台参考；2026-10-01。',
                                         '- 来源与核对日期：待核对；`作者依据与日期：用户约定 2026-10-04`'))
        self.assertFalse(result["ok"])
        self.assertIn({"code": "source_date_missing"}, result["issues"])

    def test_code_formatted_values_remain_valid(self):
        f = self.positioning_fixture()
        text = f.complete_outline().replace('来源与核对日期：后台参考；2026-10-01。',
                                            '来源与核对日期：`2026-10-01`。')
        result = f.audit(text)
        self.assertTrue(result["ok"], result["issues"])


if __name__ == "__main__":
    unittest.main()
