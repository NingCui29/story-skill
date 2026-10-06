"""Current-book positioning audit remains structural and read-only."""

import hashlib
import os
import unittest

import test_story as base


class OutlinePositionAuditTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown

    def audit(self, text, relative="01_大纲细纲/全书总纲.md", require_platforms=()):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        revision = self.book.meta("revision")
        argv = ["outline-audit", "--book", str(self.root), "--file", relative]
        for platform in require_platforms:
            argv.extend(["--require-platform", platform])
        args = base.story.parser().parse_args(argv)
        result = base.story.run(args)
        self.assertEqual(result["sha256"], before)
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), before)
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["scope"], "outline_structure_only")
        return result

    def complete_outline(self, heading="## 作品定位与平台分类"):
        return ("# 全书总纲\n状态：候选\n\n" + heading + "\n"
                "- 篇幅类型：长篇。\n- 目标平台：起点；作品阶段：拟投。\n"
                "- 平台入口：小说。\n- 来源与核对日期：后台参考；2026-10-01。\n"
                "- 平台分类：玄幻（拟选）。\n- 选择依据：主线规划中。\n- 待核对：后台选项。\n")

    def platform_block(self, platform):
        if platform == "fanqie":
            return """## 作品定位与平台分类（番茄小说）
- 篇幅类型：短篇。
- 目标平台：番茄小说；作品阶段：拟投。
- 平台入口：短故事。
- 来源与核对日期：番茄后台参考；2026-10-01。
- 主分类：现实故事（拟选）。
- 情节／角色／情绪／背景：各栏待核对。
- 选择依据：人物和主线。
- 待核对：现行选项。
"""
        return """## 作品定位与平台分类（七猫）
- 篇幅类型：短篇。
- 目标平台：七猫；作品阶段：拟投。
- 页面适用性：短故事入口待核对。
- 来源与核对日期：七猫后台参考；2026-10-02。
- 分类频道：女频（拟选）。
- 一级分类：现代言情（拟选）。
- 二级分类：婚姻家庭（拟选）。
- 风格／角色／情节／背景：各栏待核对。
- 选择依据：人物和主线。
- 待核对：现行选项。
"""

    def test_two_complete_platform_blocks_are_checked_independently_and_read_only(self):
        before = (self.root / ".story/state.sqlite3").read_bytes()
        result = self.audit(self.platform_block("fanqie") + "\n" + self.platform_block("qimao"),
                            require_platforms=("fanqie", "qimao"))
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["required_platforms"], ["fanqie", "qimao"])
        self.assertEqual(result["covered_platforms"], ["fanqie", "qimao"])
        self.assertEqual(result["checked_platform_rules"], "multiple")
        self.assertEqual(result["fields_found"], [])
        self.assertEqual([item["platform"] for item in result["platform_results"]], ["fanqie", "qimao"])
        self.assertEqual([item["checked_platform_rules"] for item in result["platform_results"]],
                         ["fanqie_short", "qimao"])
        self.assertTrue(all(item["ok"] for item in result["platform_results"]))
        self.assertEqual((self.root / ".story/state.sqlite3").read_bytes(), before)
        self.assertIsNone(base.story.outline.binding_for(self.book, 1))

    def test_optional_requirements_keep_old_single_platform_cli_compatible(self):
        text = self.platform_block("qimao")
        legacy = self.audit(text)
        self.assertTrue(legacy["ok"])
        self.assertEqual(legacy["required_platforms"], [])
        self.assertEqual(legacy["checked_platform_rules"], "qimao")
        self.assertEqual(legacy["fields_found"], legacy["platform_results"][0]["fields_found"])
        scoped = self.audit(text, require_platforms=("qimao", "qimao"))
        self.assertTrue(scoped["ok"])
        self.assertEqual(scoped["required_platforms"], ["qimao"])
        required = self.audit(text, require_platforms=("fanqie", "qimao"))
        self.assertFalse(required["ok"])
        self.assertEqual(required["issues"], [{"code": "required_platform_missing", "platform": "fanqie"}])

    def test_extra_specified_platform_is_audited_alongside_both_defaults(self):
        text = (self.platform_block("fanqie") + "\n" + self.platform_block("qimao") + "\n" +
                self.complete_outline("## 作品定位与平台分类（起点）"))
        result = self.audit(text, require_platforms=("fanqie", "qimao"))
        self.assertTrue(result["ok"], result["issues"])
        other = result["platform_results"][2]
        self.assertEqual(other["platform"], "起点")
        self.assertEqual(other["checked_platform_rules"], "generic")
        self.assertIn("平台分类", other["fields_found"])
        result = self.audit(text.replace("- 平台分类：玄幻（拟选）。\n", ""),
                            require_platforms=("fanqie", "qimao"))
        self.assertFalse(result["ok"])
        self.assertIn({"code": "classification_column_missing"}, result["platform_results"][2]["issues"])

    def test_platform_fields_and_source_dates_cannot_be_borrowed_from_other_blocks(self):
        fanqie = self.platform_block("fanqie").replace(
            "- 情节／角色／情绪／背景：各栏待核对。\n", "- 情节／情绪／背景：各栏待核对。\n")
        qimao = self.platform_block("qimao").replace(
            "- 来源与核对日期：七猫后台参考；2026-10-02。\n", "")
        result = self.audit(fanqie + "\n" + qimao, require_platforms=("fanqie", "qimao"))
        self.assertFalse(result["ok"])
        first, second = result["platform_results"]
        self.assertIn({"code": "classification_column_missing", "field": "角色"}, first["issues"])
        self.assertNotIn("角色", first["fields_found"])
        self.assertIn({"code": "source_date_missing"}, second["issues"])
        self.assertIn({"code": "position_field_missing", "field": "来源与核对日期"}, second["issues"])
        self.assertTrue(all("section_line" in issue and "platform" in issue for issue in result["issues"]))

    def test_mixed_target_platforms_never_fall_back_to_generic_success(self):
        for target in ("番茄、七猫", "番茄／七猫", "番茄；七猫", "番茄（七猫）", "番茄（兼投七猫）", "番茄与起点",
                       "fanqie / qimao"):
            with self.subTest(target=target):
                text = self.complete_outline().replace("目标平台：起点", "目标平台：" + target)
                result = self.audit(text, require_platforms=("fanqie", "qimao"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["checked_platform_rules"], "none")
                self.assertEqual(result["covered_platforms"], [])
                self.assertIsNone(result["platform_results"][0]["platform"])
                self.assertIn("position_platforms_mixed", [issue["code"] for issue in result["issues"]])
                self.assertIn({"code": "required_platform_missing", "platform": "fanqie"}, result["issues"])
                self.assertIn({"code": "required_platform_missing", "platform": "qimao"}, result["issues"])

    def test_separate_target_declarations_in_one_block_are_still_mixed(self):
        text = self.platform_block("fanqie") + "- 目标平台：七猫。\n"
        result = self.audit(text)
        self.assertFalse(result["ok"])
        self.assertIn({"code": "position_platforms_mixed", "platforms": ["fanqie", "qimao"]},
                      result["issues"])

    def test_status_annotations_do_not_become_additional_platforms(self):
        for suffix in ("，拟投", ", PLANNED", "; draft", "；待核对", " (under review)",
                       "（拟投，尚未创建）", " (Proposed; not created)", "（status: Pending）",
                       "（说明：按短故事入口规划）", "（说明：与七猫字段分开填写）"):
            with self.subTest(suffix=suffix):
                text = self.platform_block("fanqie").replace(
                    "目标平台：番茄小说", "目标平台：番茄小说" + suffix)
                result = self.audit(text, require_platforms=("fanqie",))
                self.assertTrue(result["ok"], result["issues"])
                self.assertEqual(result["covered_platforms"], ["fanqie"])
                self.assertEqual(result["platform_results"][0]["platforms_declared"], ["fanqie"])
        # The same syntax works for names without built-in platform rules.
        result = self.audit(self.complete_outline().replace("目标平台：起点", "目标平台：起点，拟投"))
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["covered_platforms"], ["起点"])

    def test_explicit_parenthetical_extra_targets_cannot_hide_behind_required_coverage(self):
        for annotation, target in (("（兼投起点）", "起点"), ("（另投：起点）", "起点"),
                                   ("（拟投；兼投：起点）", "起点"),
                                   (" (also submit to Qidian)", "qidian"),
                                   (" (Planned; ALSO SUBMIT TO: Qidian)", "qidian"),
                                   ("（拟投（兼投起点））", "起点")):
            with self.subTest(annotation=annotation):
                text = self.platform_block("fanqie").replace(
                    "目标平台：番茄小说", "目标平台：番茄小说" + annotation)
                result = self.audit(text + "\n" + self.platform_block("qimao"),
                                    require_platforms=("fanqie", "qimao"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["covered_platforms"], ["qimao"])
                self.assertIn({"code": "position_platforms_mixed", "platforms": ["fanqie", target]},
                              result["platform_results"][0]["issues"])
                self.assertIn({"code": "required_platform_missing", "platform": "fanqie"}, result["issues"])

    def test_status_words_are_only_ignored_as_complete_annotations(self):
        for other in ("待核对书城", "Pending Press", "Proposed Fiction"):
            with self.subTest(other=other):
                text = self.platform_block("fanqie").replace(
                    "目标平台：番茄小说", "目标平台：番茄小说，" + other)
                result = self.audit(text)
                self.assertFalse(result["ok"])
                self.assertIn({"code": "position_platforms_mixed", "platforms": ["fanqie", other.casefold()]},
                              result["issues"])

    def test_duplicate_platform_blocks_are_not_combined_or_counted_as_coverage(self):
        text = self.platform_block("fanqie") + "\n" + self.platform_block("fanqie")
        result = self.audit(text, require_platforms=("fanqie", "qimao"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["covered_platforms"], [])
        for item in result["platform_results"]:
            self.assertFalse(item["ok"])
            self.assertEqual(item["issues"][0]["code"], "position_platform_duplicate")
        self.assertIn({"code": "required_platform_missing", "platform": "fanqie"}, result["issues"])
        self.assertIn({"code": "required_platform_missing", "platform": "qimao"}, result["issues"])

    def test_heading_cannot_mislabel_or_replace_a_target_platform(self):
        text = self.platform_block("fanqie")
        for altered in (text.replace("（番茄小说）", "（七猫）"),
                        text.replace("（番茄小说）", "（番茄／七猫）"),
                        text.replace("- 目标平台：番茄小说；作品阶段：拟投。\n", "")):
            with self.subTest(altered=altered):
                result = self.audit(altered, require_platforms=("fanqie",))
                self.assertFalse(result["ok"])
                self.assertEqual(result["covered_platforms"], [])
                self.assertIn("position_platform_heading_mismatch", [issue["code"] for issue in result["issues"]])
                self.assertIn({"code": "required_platform_missing", "platform": "fanqie"}, result["issues"])

    def test_commented_or_fenced_second_platform_does_not_satisfy_coverage(self):
        qimao = self.platform_block("qimao")
        for hidden in ("<!--\n" + qimao + "-->\n", "```markdown\n" + qimao + "```\n"):
            with self.subTest(hidden=hidden):
                result = self.audit(self.platform_block("fanqie") + "\n" + hidden,
                                    require_platforms=("fanqie", "qimao"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["covered_platforms"], ["fanqie"])
                self.assertEqual(result["issues"], [{"code": "required_platform_missing", "platform": "qimao"}])

    def test_direct_audit_rejects_unknown_required_platforms(self):
        self.audit(self.complete_outline())
        for required in (("unknown",), "fanqie", [None]):
            with self.subTest(required=required):
                with self.assertRaises(base.story.StoryError) as raised:
                    base.story.outline.audit_position(self.book, "01_大纲细纲/全书总纲.md", required)
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_explicit_candidate_and_archived_paths_are_read_only_not_adoption(self):
        before = (self.root / ".story/state.sqlite3").read_bytes()
        text = self.complete_outline()
        baseline = self.audit(text)
        for relative in ("01_大纲细纲/全书总纲_候选.md", "01_大纲细纲/草稿/全书总纲.md",
                         "99_历史版本/全书总纲.md"):
            with self.subTest(relative=relative):
                result = self.audit(text, relative)
                self.assertTrue(result["ok"], result["issues"])
                self.assertEqual(result["sha256"], baseline["sha256"])
                self.assertEqual(result["fields_found"], baseline["fields_found"])
                self.assertIsNone(base.story.outline.binding_for(self.book, 1))
                with self.assertRaises(base.story.StoryError) as raised:
                    base.story.outline.bind(self.book, 1, relative, self.book.meta("revision"),
                                            result["sha256"])
                self.assertEqual(raised.exception.code, "invalid_input")
                self.assertIn("cannot use a draft, candidate, history, or manuscript path",
                              str(raised.exception))
                self.assertEqual((self.root / ".story/state.sqlite3").read_bytes(), before)

    def test_position_heading_accepts_atx_indentation_and_closing_hashes(self):
        for indent in ("", " ", "  ", "   "):
            for suffix in ("", " ##", "\t### \t"):
                with self.subTest(indent=indent, suffix=suffix):
                    result = self.audit(self.complete_outline(
                        indent + "## 作品定位与平台分类（拟投）" + suffix))
                    self.assertTrue(result["ok"], result["issues"])
                    self.assertEqual(result["section_lines"], [4])

    def test_non_atx_headings_and_nonclosing_hashes_do_not_create_sections(self):
        for heading in ("##作品定位与平台分类", "####### 作品定位与平台分类",
                        "## 作品定位与平台分类##", "## 作品定位与平台分类 ## 说明",
                        "## 作品定位与平台分类 \\##", "    ## 作品定位与平台分类 ##"):
            with self.subTest(heading=heading):
                result = self.audit(self.complete_outline(heading))
                self.assertEqual(result["issues"], [{"code": "position_section_missing"}])

    def test_position_section_ends_at_indented_closed_peer_heading(self):
        result = self.audit("  ## 作品定位与平台分类 ##\n- 篇幅类型：长篇。\n"
                            "   ## 场景材料 ###\n- 目标平台：七猫。\n")
        self.assertEqual(result["section_lines"], [1])
        self.assertNotIn("目标平台", result["fields_found"])
        self.assertIn({"code": "position_field_missing", "field": "目标平台"}, result["issues"])

    def test_complete_qimao_template_is_structural_only(self):
        result = self.audit("""# 全书总纲

## 作品定位与平台分类

- 篇幅类型：长篇；依据：计划连载。
- 目标平台：七猫；作品阶段：尚未创建（拟投）。
- 页面适用性：新建小说。
- 分类频道：女频。
- 来源与核对日期：本书后台截图；2026-10-01。
- 一级分类：现代言情（拟选）。
- 二级分类：职场情缘（拟选）。
- 风格／角色／情节／背景：各栏待核对。
- 选择依据：人物与主线规划中。
- 待核对：标签最终可选性。

## 第一卷
场面设计。
""")
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["section_lines"], [3])
        self.assertIn("目标平台", result["fields_found"])

    def test_missing_classification_and_invalid_date_are_reported(self):
        result = self.audit("""## 作品定位与平台分类

- 篇幅类型：长篇。
- 目标平台：番茄小说；作品阶段：待核对。
- 平台入口：短故事。
- 来源与核对日期：用户截图；2026-02-30。
- 选择依据：主线规划中。
- 待核对：栏目仍未选。
""")
        codes = {(issue["code"], issue.get("field")) for issue in result["issues"]}
        self.assertFalse(result["ok"])
        self.assertIn(("source_date_missing", None), codes)
        self.assertIn(("classification_column_missing", "主分类"), codes)
        self.assertIn(("classification_column_missing", "情绪"), codes)

    def test_classification_source_date_cannot_come_from_author_fields_on_same_line(self):
        original = "- 来源与核对日期：番茄后台参考；2026-10-01。"
        for separator in ("；", ";"):
            for source in ("待核对", "番茄后台参考；2026-02-30"):
                for author_first in (False, True):
                    with self.subTest(separator=separator, source=source, author_first=author_first):
                        author = "作者名称／笔名：北岸；作者依据与日期：用户约定 2026-10-04"
                        source_field = "来源与核对日期：" + source
                        fields = (author, source_field) if author_first else (source_field, author)
                        line = "- " + separator.join(fields).replace("；", separator) + "。"
                        result = self.audit(self.platform_block("fanqie").replace(original, line),
                                            require_platforms=("fanqie",))
                        self.assertFalse(result["ok"])
                        self.assertEqual(result["issues"], [{"code": "source_date_missing"}])

    def test_mentions_of_source_field_in_notes_cannot_supply_its_date(self):
        original = "- 来源与核对日期：番茄后台参考；2026-10-01。"
        for has_source in (False, True):
            with self.subTest(has_source=has_source):
                source = "- 来源与核对日期：待核对。\n" if has_source else ""
                note = "- 备注：来源与核对日期尚未取得；作者指定日期 2026-10-04。"
                result = self.audit(self.platform_block("fanqie").replace(original, source + note))
                self.assertFalse(result["ok"])
                self.assertIn({"code": "source_date_missing"}, result["issues"])
                missing = {"code": "position_field_missing", "field": "来源与核对日期"}
                if has_source:
                    self.assertNotIn(missing, result["issues"])
                else:
                    self.assertIn(missing, result["issues"])

    def test_source_date_accepts_its_own_semicolon_continuations(self):
        original = "- 来源与核对日期：番茄后台参考；2026-10-01。"
        for value in ("番茄后台参考 2026-10-01", "番茄后台参考；2026-10-01",
                      "番茄后台参考; 页面已存档; 2026-10-01",
                      "番茄后台参考（用户截图；2026-10-01）",
                      "番茄后台参考；2026-10-01；作者名称／笔名：待确认"):
            with self.subTest(value=value):
                result = self.audit(self.platform_block("fanqie").replace(
                    original, "- 来源与核对日期：" + value + "。"))
                self.assertTrue(result["ok"], result["issues"])

    def test_source_date_does_not_consume_later_field_continuations(self):
        original = "- 来源与核对日期：番茄后台参考；2026-10-01。"
        source = ("- 来源与核对日期：番茄后台参考；日期待核对；"
                  "作者名称／笔名：北岸；用户约定；2026-10-04。")
        result = self.audit(self.platform_block("fanqie").replace(original, source))
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], [{"code": "source_date_missing"}])

    def test_parenthetical_source_fields_keep_their_date_and_stop_before_author_fields(self):
        original = "- 来源与核对日期：番茄后台参考；2026-10-01。"
        for opening, closing, separator, colon in (("（", "）", "；", "："),
                                                   ("(", ")", ";", ":")):
            for nested in (False, True):
                for source_date in ("2026-10-01", "2026-02-30", "待核对"):
                    with self.subTest(parentheses=opening + closing, nested=nested, source_date=source_date):
                        annotation = (f"{opening}获取方式{colon}截图{separator}"
                                      f"核对日期{colon}{source_date}{closing}")
                        if nested:
                            annotation = opening + "用户留存" + annotation + closing
                        line = ("- 来源与核对日期：番茄后台参考" + annotation + separator +
                                "作者名称／笔名：北岸；作者依据与日期：用户约定 2026-10-04。")
                        result = self.audit(self.platform_block("fanqie").replace(original, line))
                        if source_date == "2026-10-01":
                            self.assertTrue(result["ok"], result["issues"])
                        else:
                            self.assertFalse(result["ok"])
                            self.assertEqual(result["issues"], [{"code": "source_date_missing"}])

    def test_fenced_template_cannot_disguise_missing_section(self):
        result = self.audit("""# 只存了模板

```markdown
## 作品定位与平台分类
- 篇幅类型：长篇。
```
""")
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], [{"code": "position_section_missing"}])

    def test_fenced_example_inside_section_cannot_supply_fields(self):
        result = self.audit("""## 作品定位与平台分类

```markdown
- 篇幅类型：长篇。
- 目标平台：七猫；作品阶段：尚未创建。
- 页面适用性：新建小说。
- 来源与核对日期：示例；2026-10-01。
- 一级分类：现代言情（拟选）；二级分类：职场情缘。
- 分类频道：女频；风格：热血；角色：学生；情节：逆袭；背景：都市。
- 选择依据：示例；待核对：无。
```
""")
        self.assertFalse(result["ok"])
        self.assertIn({"code": "position_field_missing", "field": "目标平台"}, result["issues"])

    def test_commented_template_cannot_supply_a_position_section(self):
        result = self.audit("# 全书总纲\n<!--\n## 作品定位与平台分类\n- 目标平台：七猫。\n-->\n")
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], [{"code": "position_section_missing"}])

    def test_comments_code_and_indented_examples_cannot_supply_position_fields(self):
        for example in ("<!--\n- 目标平台：七猫。\n-->\n",
                        "    - 目标平台：七猫。\n", "\t- 目标平台：七猫。\n",
                        "`字段示例\n目标平台：七猫。\n`\n"):
            with self.subTest(example=example):
                result = self.audit("## 作品定位与平台分类\n" + example)
                self.assertNotIn("目标平台", result["fields_found"])
                self.assertIn({"code": "position_field_missing", "field": "目标平台"}, result["issues"])

    def test_comment_fences_and_headings_do_not_hide_the_real_section(self):
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                text = (f"<!--\n{marker}markdown\n## 作品定位与平台分类\n-->\n"
                        "## 作品定位与平台分类\n- 目标平台：七猫。\n")
                result = self.audit(text)
                self.assertEqual(result["section_lines"], [5])
                self.assertIn("目标平台", result["fields_found"])

    def test_code_comment_openers_do_not_hide_the_real_section(self):
        for example in ("```markdown\n<!--\n```\n", "~~~markdown <!--\n~~~\n",
                        "    <!--\n", "\t<!--\n", "示例：`<!--`\n",
                        "`字段示例\n<!--\n`\n"):
            with self.subTest(example=example):
                result = self.audit(example + "## 作品定位与平台分类\n- 目标平台：七猫。\n")
                self.assertEqual(result["section_lines"], [len(example.splitlines()) + 1])
                self.assertIn("目标平台", result["fields_found"])

    def test_fence_info_string_or_other_marker_cannot_end_an_example(self):
        template = """## 作品定位与平台分类
- 篇幅类型：长篇。
- 目标平台：七猫；作品阶段：尚未创建。
- 页面适用性：新建小说。
- 来源与核对日期：示例；2026-10-01。
- 一级分类：现代言情（拟选）；二级分类：职场情缘。
- 分类频道：女频；风格：热血；角色：学生；情节：逆袭；背景：都市。
- 选择依据：示例；待核对：无。
"""
        for marker, other in (("`", "~"), ("~", "`")):
            for false_close in (marker * 4 + "markdown", marker * 3, other * 4):
                with self.subTest(marker=marker, false_close=false_close):
                    result = self.audit(f"   {marker * 4}markdown\n{false_close}\n{template}{marker * 4}\n")
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["section_lines"], [])
                    self.assertEqual(result["issues"], [{"code": "position_section_missing"}])

    def test_real_section_after_plain_matching_closer_is_visible(self):
        for marker in ("`", "~"):
            with self.subTest(marker=marker):
                result = self.audit(f"{marker * 4}markdown\n## 作品定位与平台分类\n"
                                    f"  {marker * 5} \t\n## 作品定位与平台分类\n")
                self.assertEqual(result["section_lines"], [4])
                self.assertNotIn({"code": "position_section_missing"}, result["issues"])

    def test_other_platform_requires_real_classification_field(self):
        result = self.audit("""## 作品定位与平台分类

- 篇幅类型：长篇。
- 目标平台：起点；作品阶段：拟投。
- 平台入口：小说。
- 来源与核对日期：待实测；2026-10-01。
- 选择依据：规划中。
- 待核对：分类字段。
""")
        self.assertFalse(result["ok"])
        self.assertNotIn({"code": "length_kind_unresolved"}, result["issues"])
        self.assertIn({"code": "classification_column_missing"}, result["issues"])

    def test_classification_terms_in_rationale_do_not_supply_qimao_columns(self):
        result = self.audit("""## 作品定位与平台分类
- 篇幅类型：长篇。
- 目标平台：七猫；作品阶段：拟投。
- 页面适用性：新建小说。
- 来源与核对日期：后台截图；2026-10-01。
- 一级分类：现代言情（拟选）。
- 选择依据：尚缺分类频道、二级分类、风格、角色、情节、背景。
- 待核对：上述选项。
""")
        self.assertFalse(result["ok"])
        self.assertIn({"code": "classification_column_missing", "field": "二级分类"}, result["issues"])
        self.assertIn({"code": "classification_column_missing", "field": "风格"}, result["issues"])

    def test_status_in_general_to_check_field_does_not_satisfy_classification_status(self):
        result = self.audit("""## 作品定位与平台分类
- 篇幅类型：长篇。
- 目标平台：起点；作品阶段：拟投。
- 平台入口：小说。
- 来源与核对日期：后台截图；2026-10-01。
- 平台分类：玄幻。
- 选择依据：人物规划中。
- 待核对：无。
""")
        self.assertIn({"code": "classification_status_missing"}, result["issues"])

    def test_fanqie_short_entrance_containing_novel_name_still_checks_five_columns(self):
        result = self.audit("""## 作品定位与平台分类
- 篇幅类型：短篇。
- 目标平台：番茄小说；作品阶段：拟投。
- 平台入口：番茄小说短故事。
- 来源与核对日期：后台截图；2026-10-01。
- 主分类：现言甜宠（拟选）。
- 选择依据：人物规划中。
- 待核对：其余栏目。
""")
        self.assertEqual(result["checked_platform_rules"], "fanqie_short")
        self.assertIn({"code": "classification_column_missing", "field": "情绪"}, result["issues"])

    def fanqie_entrance_outline(self, entrance_lines):
        # Supply both sets of labelled columns so the entrance decision alone
        # determines these synthetic structure-check results.
        return (self.platform_block("fanqie")
                .replace("- 平台入口：短故事。", entrance_lines)
                .replace("- 选择依据：", "- 目标读者：女频（拟选）。\n"
                         "- 阅读标签：主分类待核对。\n- 内容标签：人设待核对。\n- 选择依据："))

    def test_fanqie_uncertain_entrance_does_not_select_rules(self):
        for entrance in ("短故事入口待核对", "短故事（入口待确认）", "小说（入口待核对）",
                         "短故事／小说（尚未决定）", "短故事或小说", "番茄小说（入口待确认）",
                         "短故事（标签待核对且入口待定）"):
            with self.subTest(entrance=entrance):
                result = self.audit(self.fanqie_entrance_outline("- 平台入口：" + entrance + "。"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["checked_platform_rules"], "none")
                self.assertEqual(result["issues"], [{"code": "platform_entrance_unresolved"}])

    def test_fanqie_conflicting_entrances_do_not_select_rules(self):
        before = (self.root / ".story/state.sqlite3").read_bytes()
        for lines in ("- 平台入口：小说。\n- 页面适用性：短故事。",
                      "- 平台入口：短故事。\n- 平台入口：小说。",
                      "- 平台入口：短故事；页面适用性：小说。",
                      "- 平台入口：短故事；小说。"):
            with self.subTest(lines=lines):
                result = self.audit(self.fanqie_entrance_outline(lines))
                self.assertFalse(result["ok"])
                self.assertEqual(result["checked_platform_rules"], "none")
                self.assertEqual(result["issues"], [{"code": "platform_entrance_unresolved"}])
        self.assertEqual((self.root / ".story/state.sqlite3").read_bytes(), before)

    def test_fanqie_clear_entrance_with_pending_tag_notes_still_selects_rules(self):
        for entrance, rule in (("短故事（入口已选，标签待核对）", "fanqie_short"),
                               ("小说（入口已选；标签：待核对）", "fanqie_novel"),
                               ("短故事；入口已确认；分类待核对", "fanqie_short"),
                               ("小说（内容标签尚未核对）", "fanqie_novel")):
            with self.subTest(entrance=entrance):
                result = self.audit(self.fanqie_entrance_outline("- 平台入口：" + entrance + "。"))
                self.assertTrue(result["ok"], result["issues"])
                self.assertEqual(result["checked_platform_rules"], rule)

    def test_fanqie_canonical_entrances_and_consistent_aliases_remain_compatible(self):
        for lines, rule in (("- 平台入口：短故事。", "fanqie_short"),
                            ("- 平台入口：番茄小说短故事。", "fanqie_short"),
                            ("- 平台入口：小说。", "fanqie_novel"),
                            ("- 平台入口：短故事；依据：本书拟选择的创建入口。", "fanqie_short"),
                            ("- 平台入口：小说；依据：本书拟选择的创建入口。", "fanqie_novel"),
                            ("- 平台入口：短故事。\n- 页面适用性：短故事入口。", "fanqie_short")):
            with self.subTest(lines=lines):
                result = self.audit(self.fanqie_entrance_outline(lines))
                self.assertTrue(result["ok"], result["issues"])
                self.assertEqual(result["checked_platform_rules"], rule)

    def test_fanqie_novel_accepts_labelled_subsections(self):
        text = """## 作品定位与平台分类
- 篇幅类型：长篇。
- 目标平台：番茄小说；作品阶段：尚未创建（拟投）。
- 平台入口：小说。
- 目标读者：男频（拟选）。
- 来源与核对日期：后台参考；2026-10-01。

### 番茄小说入口阅读标签
- 主分类：悬疑脑洞（拟选）。
- 主题：悬疑（拟选）。

### 番茄小说入口内容标签
- 情节：群像（拟选）。
- 情感：无CP（拟选）。

- 选择依据：当前主线规划中。
- 待核对：后台现行选项。
"""
        for indent, suffix in (("", ""), (" ", " ##"), ("  ", " ###"), ("   ", "\t# \t")):
            with self.subTest(indent=indent, suffix=suffix):
                formatted = "\n".join(indent + line + suffix if line.startswith("### ") else line
                                      for line in text.splitlines())
                result = self.audit(formatted)
                self.assertTrue(result["ok"], result["issues"])
                self.assertIn("阅读标签", result["fields_found"])
                self.assertIn("内容标签", result["fields_found"])

    def test_tag_groups_do_not_take_fields_from_indented_peer_sections(self):
        for boundary in ("  ### 作者备注 ###", "   ## 正文材料 ##"):
            with self.subTest(boundary=boundary):
                result = self.audit("## 作品定位与平台分类\n"
                                    "  ### 番茄小说入口阅读标签 ###\n\n" + boundary + "\n"
                                    "- 主分类：悬疑脑洞（拟选）。\n")
                self.assertNotIn("阅读标签", result["fields_found"])

    def test_closed_indented_example_headings_and_fields_stay_inert(self):
        example = "  ### 番茄小说入口阅读标签 ###\n- 主分类：悬疑脑洞（拟选）。\n"
        for hidden in ("```markdown\n" + example + "```\n",
                       "<!--\n" + example + "-->\n",
                       "\n".join("    " + line for line in example.splitlines())):
            with self.subTest(hidden=hidden):
                result = self.audit("  ## 作品定位与平台分类 ##\n" + hidden)
                self.assertNotIn("主分类", result["fields_found"])
                self.assertNotIn("阅读标签", result["fields_found"])

        section = "   ## 作品定位与平台分类 ##\n- 目标平台：七猫。\n"
        for hidden in ("```markdown\n" + section + "```\n", "<!--\n" + section + "-->\n"):
            with self.subTest(hidden=hidden):
                result = self.audit(hidden)
                self.assertEqual(result["issues"], [{"code": "position_section_missing"}])

    def test_empty_tag_subsection_does_not_count_as_column(self):
        result = self.audit("""## 作品定位与平台分类
- 篇幅类型：长篇。
- 目标平台：番茄小说；作品阶段：尚未创建（拟投）。
- 平台入口：小说。
- 目标读者：男频（拟选）。
- 来源与核对日期：后台参考；2026-10-01。
- 主分类：悬疑脑洞（拟选）。
- 选择依据：当前主线规划中。
- 待核对：后台现行选项。

### 番茄小说入口阅读标签

### 番茄小说入口内容标签
""")
        self.assertIn({"code": "classification_column_missing", "field": "阅读标签"}, result["issues"])
        self.assertIn({"code": "classification_column_missing", "field": "内容标签"}, result["issues"])

    def test_non_utf8_outline_is_reported_as_input_error(self):
        file = self.root / "01_大纲细纲/全书总纲.md"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"\xff\xfe")
        args = base.story.parser().parse_args([
            "outline-audit", "--book", str(self.root), "--file", "01_大纲细纲/全书总纲.md"])
        with self.assertRaises(base.story.StoryError) as raised:
            base.story.run(args)
        self.assertEqual(raised.exception.code, "invalid_encoding")

    def test_audit_still_rejects_unsafe_or_non_markdown_paths(self):
        for relative in ("../全书总纲_候选.md", str(self.root / "全书总纲_候选.md"),
                         "01_大纲细纲/草稿/总纲.txt", "C:/候选.md", "草稿\\总纲.md"):
            with self.subTest(relative=relative):
                args = base.story.parser().parse_args([
                    "outline-audit", "--book", str(self.root), "--file", relative])
                with self.assertRaises(base.story.StoryError) as raised:
                    base.story.run(args)
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_candidate_file_and_directory_symlinks_are_rejected(self):
        source = self.root / "source"
        source.mkdir()
        (source / "全书总纲.md").write_text(self.complete_outline(), encoding="utf-8")
        (self.root / "全书总纲_候选.md").symlink_to(source / "全书总纲.md")
        (self.root / "草稿").symlink_to(source, target_is_directory=True)
        for relative in ("全书总纲_候选.md", "草稿/全书总纲.md"):
            with self.subTest(relative=relative):
                args = base.story.parser().parse_args([
                    "outline-audit", "--book", str(self.root), "--file", relative])
                with self.assertRaises(base.story.StoryError) as raised:
                    base.story.run(args)
                self.assertEqual(raised.exception.code, "linked_path")

    def test_linked_outline_path_is_rejected(self):
        source = self.root / "private.md"
        source.write_text("private", encoding="utf-8")
        for relative in ("01_大纲细纲/全书总纲.md", "01_大纲细纲/草稿/全书总纲_候选.md"):
            with self.subTest(relative=relative):
                file = self.root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, file)
                args = base.story.parser().parse_args([
                    "outline-audit", "--book", str(self.root), "--file", relative])
                with self.assertRaises(base.story.StoryError) as raised:
                    base.story.run(args)
                self.assertEqual(raised.exception.code, "invalid_input")


if __name__ == "__main__":
    unittest.main()
