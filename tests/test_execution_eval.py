import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("execution_eval", ROOT / "scripts/execution_eval.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class ExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace, self.skills = self.root / "workspace", self.root / "skills"
        self.workspace.mkdir()
        self.skills.mkdir()
        (self.workspace / "original.md").write_text("原稿", encoding="utf-8")
        (self.skills / "SKILL.md").write_text("规则", encoding="utf-8")
        self.case = {"id": "trial", "prompt": "只保存候选", "invocation": "explicit",
                     "allowed_changes": ["candidate.md"], "required_outputs": ["candidate.md"],
                     "review_required": ["候选与正式稿分开"]}
        self.case_file, self.baseline = self.root / "case.json", self.root / "baseline.json"
        self.trace = self.root / "trace.jsonl"
        self.events([])

    def events(self, events):
        events = [{"type": "thread.started", "thread_id": "test-thread"}, {"type": "turn.started"},
                  *events, {"type": "turn.completed"}]
        self.trace.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    def start(self):
        self.case_file.write_text(json.dumps(self.case), encoding="utf-8")
        return evaluation.start(argparse.Namespace(case_file=self.case_file, workspace=self.workspace,
                               skills_root=self.skills, output=self.baseline))

    def check(self, trace=True):
        return evaluation.check(argparse.Namespace(baseline=self.baseline, trace=self.trace if trace else None,
                                                  output=self.root / "result.json"))

    def candidate(self):
        (self.workspace / "candidate.md").write_text("候选", encoding="utf-8")

    def test_allowed_artifact_never_certifies_review(self):
        self.case["passed"] = True
        self.start()
        self.candidate()
        report, code = self.check()
        self.assertEqual(code, 0)
        self.assertTrue(report["mechanical_ok"])
        self.assertEqual(report["execution_status"], "needs_review")
        self.assertNotIn("ok", report)
        self.assertEqual(report["review_required"], self.case["review_required"])

    def test_unauthorized_modify_add_delete_and_empty_directory(self):
        for kind in ("modify", "add", "delete", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as sub:
                original = self.workspace / "original.md"
                original.write_text("原稿", encoding="utf-8")
                self.baseline = Path(sub).resolve() / "baseline.json"
                self.start()
                self.candidate()
                if kind == "modify": original.write_text("偷改", encoding="utf-8")
                elif kind == "add": (self.workspace / "extra.md").touch()
                elif kind == "delete": original.unlink()
                else: (self.workspace / "extra").mkdir()
                report, code = self.check()
                self.assertEqual(code, 1)
                self.assertFalse(report["mechanical_ok"])
                self.assertTrue(report["checks"]["unauthorized_changes"])
                (self.root / "result.json").unlink()

    def test_missing_required_output(self):
        self.start()
        report, code = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"]["missing_outputs"], ["candidate.md"])

    def test_skill_drift(self):
        self.start()
        self.candidate()
        (self.skills / "SKILL.md").write_text("新规则", encoding="utf-8")
        report, code = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"]["skill_changes"][0]["path"], "SKILL.md")

    def test_empty_required_output(self):
        self.start()
        (self.workspace / "candidate.md").touch()
        report, code = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"]["empty_outputs"], ["candidate.md"])

    def test_failed_command_is_preserved_for_recovery_review(self):
        self.start()
        self.candidate()
        self.events([{"type": "item.completed", "item": {"id": "command-1", "type": "command_execution",
                     "command": "a real command", "exit_code": 1}}])
        report, code = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(report["execution_status"], "needs_review")
        self.assertEqual(report["trace"]["status"], "complete")
        self.assertEqual(report["trace"]["failed_commands"][0]["line"], 3)

    def test_missing_empty_invalid_and_failed_turn_traces(self):
        self.start()
        self.candidate()
        for content, expected_code, status in ((None, 2, "unverified"), ("", 1, "incomplete"),
                ("not json", 1, "incomplete"), ('{"type":"agent_message","passed":true}', 1, "incomplete"),
                ('{"type":"turn.completed","passed":true}', 1, "incomplete"),
                ('{"type":"turn.failed"}\n{"type":"turn.completed","passed":true}', 1, "failed"),
                ('{"type":"error","message":"failure"}\n{"type":"turn.completed"}', 1, "failed")):
            with self.subTest(content=content):
                if content is not None: self.trace.write_text(content, encoding="utf-8")
                report, code = self.check(trace=content is not None)
                self.assertEqual((code, report["trace"]["status"]), (expected_code, status))
                (self.root / "result.json").unlink()

    def test_started_followup_turn_and_command_require_completion(self):
        self.start()
        self.candidate()
        for event in ({"type": "turn.started"}, {"type": "item.started", "item": {
                "type": "command_execution", "id": "unfinished", "command": "still running"}}):
            with self.subTest(event=event):
                self.events([])
                with self.trace.open("a", encoding="utf-8") as stream:
                    stream.write("\n" + json.dumps(event))
                report, code = self.check()
                self.assertEqual((code, report["trace"]["status"]), (1, "incomplete"))
                (self.root / "result.json").unlink()

    def test_unfinished_mcp_and_invalid_completed_items_are_incomplete(self):
        self.start()
        self.candidate()
        for event in ({"type": "item.started", "item": {"id": "tool-1", "type": "mcp_tool_call",
                "server": "tools", "tool": "read", "arguments": {}, "result": None,
                "error": None, "status": "in_progress"}}, {"type": "item.completed", "item": None},
                {"type": "item.completed", "item": {"type": "agent_message"}}):
            with self.subTest(event=event):
                self.events([event])
                report, code = self.check()
                self.assertEqual((code, report["trace"]["status"]), (1, "incomplete"))
                (self.root / "result.json").unlink()

    def test_declined_and_failed_null_exit_codes_remain_reviewable(self):
        self.start()
        self.candidate()
        for status in ("declined", "failed"):
            with self.subTest(status=status):
                command = {"id": "cmd-1", "type": "command_execution", "command": "a requested command",
                           "aggregated_output": "", "exit_code": None, "status": "in_progress"}
                self.events([{"type": "item.started", "item": command}, {"type": "item.completed",
                    "item": {**command, "status": status}}, {"type": "item.completed", "item": {
                    "id": "cmd-2", "type": "command_execution", "command": "a permitted command",
                    "exit_code": 0, "status": "completed"}}])
                report, code = self.check()
                self.assertEqual((code, report["trace"]["status"]), (0, "complete"))
                self.assertEqual(report["execution_status"], "needs_review")
                self.assertEqual(report["trace"]["failed_commands"][0]["status"], status)
                (self.root / "result.json").unlink()

    def test_unknown_items_and_unpaired_native_message_completion_are_compatible(self):
        self.start()
        self.candidate()
        self.events([{"type": "item.started", "item": {"id": "new-1", "type": "future_item"}},
                     {"type": "item.completed", "item": {"id": "new-1", "type": "future_item"}},
                     {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message"}}])
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write('\n{"type":"item.completed","item":{"id":"warning-1","type":"error","message":"warning"}}')
        report, code = self.check()
        self.assertEqual((code, report["trace"]["status"]), (0, "complete"))

    def test_execution_items_after_turn_ended_are_incomplete(self):
        self.start()
        self.candidate()
        for kind in ("command_execution", "mcp_tool_call", "collab_tool_call", "web_search", "todo_list"):
            with self.subTest(kind=kind):
                self.events([])
                item = {"id": "late-1", "type": kind, "command": "late command", "exit_code": 0}
                with self.trace.open("a", encoding="utf-8") as stream:
                    stream.write("\n" + json.dumps({"type": "item.started", "item": item}))
                    stream.write("\n" + json.dumps({"type": "item.completed", "item": item}))
                report, code = self.check()
                self.assertEqual((code, report["trace"]["status"]), (1, "incomplete"))
                (self.root / "result.json").unlink()

    def test_evidence_overwrite_and_tree_output_rejected(self):
        self.start()
        with self.assertRaises(ValueError): self.start()
        with self.assertRaises(ValueError):
            evaluation.output_path(self.workspace / "result.json", (self.workspace, self.skills))
        with self.assertRaises(ValueError):
            evaluation.check(argparse.Namespace(baseline=self.baseline, trace=self.trace, output=self.trace))

    def test_traversal_overlap_and_links_rejected(self):
        for name in ("../outside.md", "/absolute", "folder/../file", "folder/*", "C:\\file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                evaluation.relative_path(name, pattern=True)
        with self.assertRaises(ValueError): evaluation.roots_for(self.root, self.skills)
        link = self.workspace / "linked.md"
        try: link.symlink_to(self.skills / "SKILL.md")
        except OSError: self.skipTest("Symbolic link creation unavailable")
        with self.assertRaises(ValueError): self.start()
        with self.assertRaises(ValueError): evaluation.safe_path(link)

    def test_directory_pattern_allows_children_and_ignores_bytecode(self):
        self.case["allowed_changes"] = ["drafts/**"]
        self.case["required_outputs"] = ["drafts/candidate.md"]
        self.start()
        (self.workspace / "drafts").mkdir()
        (self.workspace / "drafts/candidate.md").write_text("候选", encoding="utf-8")
        (self.skills / "__pycache__").mkdir()
        (self.skills / "__pycache__/test.pyc").touch()
        report, code = self.check()
        self.assertEqual(code, 0)
        self.assertTrue(report["mechanical_ok"])


if __name__ == "__main__":
    unittest.main()
