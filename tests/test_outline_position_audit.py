"""Current-book positioning audit remains structural and read-only."""

import hashlib
import os
import unittest

import test_story as base


class OutlinePositionAuditTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown

    def audit(self, text):
        relative = "01_大纲细纲/全书总纲.md"
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        revision = self.book.meta("revision")
        args = base.story.parser().parse_args([
            "outline-audit", "--book", str(self.root), "--file", relative])
        result = base.story.run(args)
        self.assertEqual(result["sha256"], before)
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), before)
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["scope"], "outline_structure_only")
        return result

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

    def test_fanqie_novel_accepts_labelled_subsections(self):
        result = self.audit("""## 作品定位与平台分类
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
""")
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("阅读标签", result["fields_found"])
        self.assertIn("内容标签", result["fields_found"])

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

    def test_linked_outline_path_is_rejected(self):
        file = self.root / "01_大纲细纲/全书总纲.md"
        file.parent.mkdir(parents=True, exist_ok=True)
        source = self.root / "private.md"
        source.write_text("private", encoding="utf-8")
        os.link(source, file)
        args = base.story.parser().parse_args([
            "outline-audit", "--book", str(self.root), "--file", "01_大纲细纲/全书总纲.md"])
        with self.assertRaises(base.story.StoryError) as raised:
            base.story.run(args)
        self.assertEqual(raised.exception.code, "invalid_input")


if __name__ == "__main__":
    unittest.main()
