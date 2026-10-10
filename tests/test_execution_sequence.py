"""Real Book transactions with synthetic traces; these are not model trials."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "execution_sequence", ROOT / "benchmarks/skill-execution/sequence_prepare.py")
sequence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sequence)


class ExecutionSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / "run"
        sequence.start("six-chapter-continuity", self.run)
        self.workspace = self.run / "workspace"
        self.skills = self.run / "skills"
        self.manifest = sequence.load(self.run / "sequence.json")
        self.story, _ = sequence.preparer.load_runtime(self.skills)

    def initialize(self):
        agreement = "后续每章正文800—1000字，不计首行章名。"
        (self.workspace / "创作约定.md").write_text(agreement, encoding="utf-8")
        self.story.Book.create(self.workspace, self.manifest["configuration"]["title"], "long")

    def commit(self, first, last, wrong_volume=False):
        book = self.story.Book(self.workspace)
        try:
            for chapter in range(first, last + 1):
                volume = self.manifest["configuration"]["volumes"][0 if chapter <= 3 else 1]
                if wrong_volume:
                    volume = "第三卷 错误卷"
                title = f"事务验收{chapter}"
                plan = {
                    "title": title, "volume_dir": volume, "goal": "明确今天实际能完成的检修事项",
                    "stop": "记录实际完成事项后停止", "beats": [
                        {"choice": "按约定时间分别完成当下能做的事项", "change": "检修表新增一条实测结果"}],
                    "constraints": ["不把未来计划登记为已经发生的事实"], "requires": [], "tags": ["沈禾"],
                    "length": [800, 1000], "count_method": "visible_nonspace_v2", "count_title": False,
                    "length_exception": {"source": "book_agreement", "path": "创作约定.md",
                                         "quote": "后续每章正文800—1000字，不计首行章名。"},
                }
                book.save_plan(chapter, plan, book.meta("revision"))
                relative = f"01_大纲细纲/第{chapter}章细纲.md"
                target = self.workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# 第{chapter}章细纲\n状态：已采用\n\n" + json.dumps(plan, ensure_ascii=False), encoding="utf-8")
                self.story.outline.bind(book, chapter, relative, book.meta("revision"), sequence.preparer.sha256(target))
                quote = "沈禾记下今天实际完成的一项。"
                # Intentionally synthetic prose, used only to exercise real state,
                # length-exception, outline binding, commit and export operations.
                sentence = "沈禾把检修表放到桌边，小周先确认下一步能做什么。他们照着商量过的时间分开行动，各自把实际完成的部分告诉对方。"
                text = f"第{chapter}章 {title}\n\n" + (sentence * 20)[:850] + "\n\n" + quote + "\n"
                draft = self.workspace / f".story/drafts/第{chapter}章.md"
                draft.parent.mkdir(parents=True, exist_ok=True)
                draft.write_text(text, encoding="utf-8")
                packet = book.prepare(chapter, draft)
                self.assertTrue(packet["lint"]["ok"], packet["lint"])
                delta = packet["delta"]
                delta["summary"] = "合成事务夹具：沈禾记录今天实际完成的检修事项。"
                delta["changes"] = []
                delta["review"]["checks"] = {
                    key: {"note": "合成事务夹具核对，原句存在于此版本，只验收本地状态与导出，不宣称文学评阅。", "quote": quote}
                    for key in self.story.CHECKS}
                delta["review"]["issues"] = []
                receipt = book.commit(chapter, draft, delta)
                self.assertTrue(receipt["committed"])
                self.assertTrue(receipt["exports_complete"])
        finally:
            book.close()

    def report(self, number, thread_id=None):
        folder = self.run / f"round-{number}"
        events = [{"type": "thread.started", "thread_id": thread_id or f"synthetic-test-{number}"},
                  {"type": "turn.started"}, {"type": "turn.completed"}]
        (folder / "trace.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        report, code = sequence.evaluation.check(argparse.Namespace(
            baseline=folder / "baseline.json", trace=folder / "trace.jsonl", output=folder / "result.json"))
        self.assertEqual(code, 0, report)
        return report

    def advance_one(self):
        self.initialize()
        self.commit(1, 2)
        self.report(1)
        return sequence.complete_round(self.run, 1)

    def prepare_finish_inputs(self):
        self.advance_one()
        self.commit(3, 4)
        self.report(2)
        sequence.complete_round(self.run, 2)
        self.commit(5, 6)
        book = self.story.Book(self.workspace, read_only=True)
        try:
            assembled = "\n\n".join((self.workspace / book.chapter_path(n)).read_text(encoding="utf-8").strip()
                                       for n in range(1, 7))
        finally:
            book.close()
        (self.workspace / "全书正文.md").write_text(assembled, encoding="utf-8")
        (self.workspace / "完本回读.md").write_text("合成测试占位回读，仍需独立内容审查。", encoding="utf-8")
        self.report(3)

    def test_start_freezes_skills_without_preparing_formal_book_or_answers(self):
        self.assertFalse((self.workspace / ".story").exists())
        self.assertFalse((self.workspace / "chapters").exists())
        self.assertEqual([p.name for p in self.workspace.iterdir()], ["创作输入.md"])
        task = (self.run / "round-1/task.txt").read_text(encoding="utf-8")
        self.assertNotIn("review_required", task)
        self.assertNotIn("机械", task)
        self.assertEqual(sequence.evaluation.snapshot(self.skills), self.manifest["skills_snapshot"])
        self.assertFalse(self.manifest["native_skill_discovery_tested"])

    def test_real_six_chapter_three_round_chain_never_self_certifies_quality(self):
        next_round = self.advance_one()
        self.assertEqual(next_round["round"], 2)
        baseline = sequence.load(self.run / "round-2/baseline.json")
        self.assertEqual(baseline["workspace"], str(self.workspace))
        self.assertEqual(baseline["before"]["skills"], self.manifest["skills_snapshot"])
        self.assertIn(".story/state.sqlite3", baseline["before"]["workspace"]["files"])
        second_prompt = sequence.load(self.run / "round-2/case.json")["prompt"]
        self.assertEqual(second_prompt, "继续。")
        self.commit(3, 4)
        self.report(2)
        sequence.complete_round(self.run, 2)
        self.commit(5, 6)
        book = self.story.Book(self.workspace, read_only=True)
        try:
            assembled = "\n\n".join((self.workspace / book.chapter_path(n)).read_text(encoding="utf-8").strip()
                                       for n in range(1, 7))
        finally:
            book.close()
        (self.workspace / "全书正文.md").write_text(assembled, encoding="utf-8")
        (self.workspace / "完本回读.md").write_text("这是合成测试占位回读，仍需真实模型与独立内容审查。", encoding="utf-8")
        self.report(3)
        result = sequence.complete_round(self.run, 3, final=True)
        self.assertTrue(result["mechanical_sequence_complete"])
        self.assertEqual(result["execution_status"], "needs_review")
        self.assertFalse(result["quality_assessed"])
        self.assertFalse(result["native_skill_discovery_tested"])
        self.assertTrue((self.run / "sequence-result.json").is_file())
        self.assertEqual(sequence.load(self.run / "round-3/verified.json")["book"]["status"]["last_chapter"], 6)

    def test_refuses_existing_output(self):
        with self.assertRaisesRegex(ValueError, "new directory"):
            sequence.start("six-chapter-continuity", self.run)

    def test_refuses_skipping_round(self):
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            sequence.complete_round(self.run, 2)
        self.assertFalse((self.run / "round-3").exists())

    def test_refuses_missing_execution_evidence(self):
        self.initialize()
        self.commit(1, 2)
        with self.assertRaisesRegex(ValueError, "input file"):
            sequence.complete_round(self.run, 1)
        self.assertFalse((self.run / "round-2").exists())

    def test_refuses_frozen_skill_drift(self):
        (self.skills / "story-skill/SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "drifted"):
            sequence.complete_round(self.run, 1)

    def test_refuses_changed_prepared_input(self):
        self.initialize()
        self.commit(1, 2)
        self.report(1)
        (self.run / "round-1/task.txt").write_text("替换请求", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "input changed"):
            sequence.complete_round(self.run, 1)

    def test_refuses_changed_trace_or_wrong_report_binding(self):
        self.initialize()
        self.commit(1, 2)
        self.report(1)
        trace = self.run / "round-1/trace.jsonl"
        trace.write_text(trace.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "trace"):
            sequence.complete_round(self.run, 1)

    def test_refuses_report_rebound_to_another_baseline(self):
        self.initialize()
        self.commit(1, 2)
        self.report(1)
        path = self.run / "round-1/result.json"
        altered = sequence.load(path)
        altered["baseline"]["sha256"] = "0" * 64
        path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "another round or baseline"):
            sequence.complete_round(self.run, 1)

    def test_refuses_changed_workspace_after_check(self):
        self.initialize()
        self.commit(1, 2)
        self.report(1)
        (self.workspace / "创作约定.md").write_text("检查后变化", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "after the round check"):
            sequence.complete_round(self.run, 1)

    def test_files_without_real_committed_progress_cannot_advance(self):
        self.initialize()
        fake = self.workspace / "chapters/第一卷 重新开门/第2章 假正文.md"
        fake.parent.mkdir(parents=True)
        fake.write_text("假正文" * 300, encoding="utf-8")
        self.report(1)
        with self.assertRaisesRegex(ValueError, "Actual book status"):
            sequence.complete_round(self.run, 1)

    def test_extra_actual_chapter_is_not_treated_as_round_completion(self):
        self.initialize()
        self.commit(1, 3)
        self.report(1)
        with self.assertRaisesRegex(ValueError, "Actual book status"):
            sequence.complete_round(self.run, 1)

    def test_changed_registered_export_is_not_accepted_as_formal_progress(self):
        self.initialize()
        self.commit(1, 2)
        book = self.story.Book(self.workspace, read_only=True)
        try:
            path = self.workspace / book.chapter_path(2)
        finally:
            book.close()
        path.write_text("替换导出，状态库中的正文并未改变。" * 50, encoding="utf-8")
        self.report(1)
        with self.assertRaisesRegex(ValueError, "exports are incomplete or changed"):
            sequence.complete_round(self.run, 1)

    def test_refuses_wrong_actual_volume(self):
        self.initialize()
        self.commit(1, 2, wrong_volume=True)
        self.report(1)
        with self.assertRaisesRegex(ValueError, "Wrong volume"):
            sequence.complete_round(self.run, 1)

    def test_refuses_unregistered_formal_export(self):
        self.initialize()
        self.commit(1, 2)
        (self.workspace / "chapters/未登记第3章.md").write_text("不是实际正式正文", encoding="utf-8")
        self.report(1)
        with self.assertRaisesRegex(ValueError, "unregistered"):
            sequence.complete_round(self.run, 1)

    def test_refuses_reusing_same_session(self):
        self.advance_one()
        self.commit(3, 4)
        self.report(2, "synthetic-test-1")
        with self.assertRaisesRegex(ValueError, "new session"):
            sequence.complete_round(self.run, 2)

    def test_refuses_repeated_advance_or_overwrite(self):
        self.advance_one()
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            sequence.complete_round(self.run, 1)
        self.assertTrue((self.run / "round-1/verified.json").is_file())

    def test_refuses_rebinding_book_identity(self):
        self.advance_one()
        self.commit(3, 4)
        # A swapped ID can be made through the runtime's metadata API; SQL files
        # are not hand-edited, and no historical formal text is rewritten.
        book = self.story.Book(self.workspace)
        try:
            with book.db:
                book.set_meta("id", "swapped-book-identity")
        finally:
            book.close()
        self.report(2)
        with self.assertRaisesRegex(ValueError, "identity"):
            sequence.complete_round(self.run, 2)

    def test_refuses_previous_evidence_change(self):
        self.advance_one()
        self.commit(3, 4)
        self.report(2)
        path = self.run / "round-1/verified.json"
        altered = sequence.load(path)
        altered["book"]["status"]["id"] = "altered"
        path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            sequence.complete_round(self.run, 2)

    def test_finish_refuses_changed_or_removed_raw_evidence_from_every_earlier_round(self):
        self.prepare_finish_inputs()
        for earlier in (1, 2):
            for filename in ("trace.jsonl", "result.json"):
                path = self.run / f"round-{earlier}" / filename
                original = path.read_bytes()
                for removed in (False, True):
                    with self.subTest(earlier=earlier, filename=filename, removed=removed):
                        if removed:
                            path.unlink()
                        else:
                            path.write_bytes(original + b"\n")
                        try:
                            with self.assertRaisesRegex(ValueError, "execution evidence changed or missing"):
                                sequence.complete_round(self.run, 3, final=True)
                            self.assertFalse((self.run / "round-3/verified.json").exists())
                            self.assertFalse((self.run / "sequence-result.json").exists())
                        finally:
                            path.write_bytes(original)

    def test_finish_refuses_whitespace_only_required_readback_after_complete_chapters(self):
        self.prepare_finish_inputs()
        for blank in (" \n\t", "\u3000\n"):
            with self.subTest(blank=repr(blank)):
                (self.workspace / "完本回读.md").write_text(blank, encoding="utf-8")
                (self.run / "round-3/result.json").unlink()
                self.report(3)
                with self.assertRaisesRegex(ValueError, "outputs are missing or empty"):
                    sequence.complete_round(self.run, 3, final=True)
                self.assertFalse((self.run / "round-3/verified.json").exists())
                self.assertFalse((self.run / "sequence-result.json").exists())

    def test_reading_copy_allows_only_actual_chapters_and_exact_title(self):
        chapters = ["第1章 开门\n\n实际正文甲。\n", "第2章 留下\n\n实际正文乙。\n"]
        actual = "\n\n".join(chapters)
        for prefix in ("", "书名\n\n", "# 书名\n\n"):
            with self.subTest(prefix=prefix):
                sequence.verify_reading_copy(prefix + actual, "书名", chapters)

    def test_reading_copy_refuses_duplicates_candidates_reordering_and_comments(self):
        first, second = "第1章 开门\n\n实际正文甲。\n", "第2章 留下\n\n实际正文乙。\n"
        for text in (first + first + second, first + second + second,
                     first + "未采用候选正文\n" + second, second + first,
                     "书名后记：下面是正文。\n" + first + second,
                     first + second + "\n作者附言：已经完本。"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "Final reading copy"):
                    sequence.verify_reading_copy(text, "书名", [first, second])

    def test_configuration_cannot_smuggle_prewritten_formal_fixtures(self):
        config_folder = Path(self.temp.name).resolve() / "config"
        config_folder.mkdir()
        config = self.manifest["configuration"]
        config["fixtures"]["chapters/第1章.md"] = "预写正式章"
        (config_folder / "six-chapter-continuity.json").write_text(json.dumps(config), encoding="utf-8")
        with mock.patch.object(sequence, "SEQUENCES", config_folder):
            with self.assertRaisesRegex(ValueError, "prewritten formal"):
                sequence.start("six-chapter-continuity", Path(self.temp.name).resolve() / "smuggled")
        self.assertFalse((Path(self.temp.name) / "smuggled").exists())


if __name__ == "__main__":
    unittest.main()
