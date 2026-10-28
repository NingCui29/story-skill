"""Synthetic native-event tests; these are not measured model executions."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/execution_activity.py"
SPEC = importlib.util.spec_from_file_location("execution_activity", SCRIPT)
activity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activity)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "写作规则.md"
        self.source.write_text("首行约定。\n沿用人物已知，不提前揭底。\n保留有效安静。\n", encoding="utf-8")
        self.trace = self.root / "main.jsonl"

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def command(self, item_id="read-1", command=None, output=None, **updates):
        item = {"id": item_id, "type": "command_execution", "status": "completed", "exit_code": 0,
                "command": command or f"cat '{self.source}'", "aggregated_output": output if output is not None else self.source.read_text()}
        item.update(updates)
        return item

    def write_trace(self, items, path=None, session="parent", duplicate=False):
        path = path or self.trace
        events = [{"type": "thread.started", "thread_id": session}, {"type": "turn.started"}]
        for item in items:
            events += [{"type": "item.started", "item": {"id": item["id"], "type": item["type"]}},
                       {"type": "item.completed", "item": item}]
            if duplicate:
                events.append(deepcopy(events[-1]))
        events += [{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30}}]
        path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
        return path

    def annotation(self, line=4, phase="preparation", trace=None, source=None, quote="沿用人物已知，不提前揭底。"):
        trace, source = trace or self.trace, source or self.source
        return {"trace": {"path": str(trace), "sha256": self.digest(trace)}, "line": line, "phase": phase,
                "source": {"path": str(source), "sha256": self.digest(source)}, "quote": quote}

    def analyze(self, *annotations, children=(), traces=None):
        return activity.analyze(traces or [str(self.trace)], list(map(str, children)), {"schema": 1, "annotations": list(annotations)})

    def test_successful_excerpt_does_not_claim_full_read(self):
        self.write_trace([self.command(output="沿用人物已知，不提前揭底。")])
        result = self.analyze(self.annotation())
        self.assertEqual(result["status"], "selected_evidence_checked")
        item = result["activities"][0]
        self.assertEqual(item["status"], "excerpt_observed")
        self.assertEqual(item["coverage"], "cited_excerpt_only")
        self.assertEqual(item["phase_origin"], "developer_annotation")
        self.assertNotIn("file_bytes_read", item)
        self.assertIsNone(result["full_task_total"])

    def test_command_mention_and_agent_claim_are_not_read_evidence(self):
        for item in (self.command(command=f"echo '{self.source}'"),
                     {"id": "claim-1", "type": "agent_message", "text": "沿用人物已知，不提前揭底。"}):
            with self.subTest(item=item):
                self.write_trace([item])
                result = self.analyze(self.annotation())
                self.assertEqual(result["activities"][0]["status"], "unverified")
                self.assertEqual(result["phases"], {})

    def test_failed_or_unfinished_command_cannot_certify_read(self):
        for updates in ({"exit_code": 1}, {"status": "failed"}, {"exit_code": None}, {"exit_code": True}):
            with self.subTest(updates=updates):
                self.write_trace([self.command(**updates)])
                self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "unverified")

    def test_quote_must_be_in_output_and_current_source(self):
        self.write_trace([self.command(output="只输出首行约定。")])
        self.assertIn("returned output", self.analyze(self.annotation())["activities"][0]["reason"])
        self.write_trace([self.command(output="源码中不存在的句子")])
        self.assertIn("source version", self.analyze(self.annotation(quote="源码中不存在的句子"))["activities"][0]["reason"])

    def test_changed_source_and_trace_are_rejected(self):
        self.write_trace([self.command()])
        annotation = self.annotation()
        self.source.write_text("改变了规则。", encoding="utf-8")
        self.assertIn("source version", self.analyze(annotation)["activities"][0]["reason"])
        self.write_trace([self.command(item_id="changed")])
        self.assertIn("changed trace", self.analyze(annotation)["activities"][0]["reason"])

    def test_head_sed_and_read_file_return_supported_excerpts(self):
        for command in (f"head -n 2 '{self.source}'", f"sed -n '2,3p' '{self.source}'"):
            with self.subTest(command=command):
                self.write_trace([self.command(command=command)])
                self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "excerpt_observed")
        item = {"id": "mcp-1", "type": "mcp_tool_call", "tool": "read_file", "status": "completed",
                "arguments": {"path": str(self.source)}, "result": {"content": [{"type": "text", "text": self.source.read_text()}]}}
        self.write_trace([item])
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "excerpt_observed")
        item["result"]["isError"] = True
        self.write_trace([item])
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "unverified")

    def test_relative_path_requires_native_cwd_and_shell_composition_is_not_assumed(self):
        self.write_trace([self.command(command=f"cat '{self.source.name}'")])
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "unverified")
        self.write_trace([self.command(command=f"cat '{self.source.name}'", cwd=str(self.root))])
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "excerpt_observed")
        self.write_trace([self.command(command=f"cat '{self.source}'; echo done")])
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "unverified")

    def test_distinct_reads_with_same_excerpt_are_reported_as_repeated_evidence(self):
        self.write_trace([self.command(), self.command(item_id="read-2")])
        result = self.analyze(self.annotation(), self.annotation(line=6, phase="review"))
        self.assertEqual(result["phases"]["review"]["repeated_excerpts"], 1)
        self.assertEqual(result["activities"][1]["repeated_excerpt_of"], 1)
        self.assertIsNone(result["full_task_total"])

    def test_duplicate_annotation_and_replayed_native_event_do_not_count_twice(self):
        self.write_trace([self.command()], duplicate=True)
        first = self.annotation()
        result = self.analyze(first, deepcopy(first), self.annotation(line=5))
        self.assertEqual(result["phases"]["preparation"]["observed_excerpts"], 1)
        self.assertEqual([r["status"] for r in result["activities"]], ["excerpt_observed", "duplicate_annotation", "duplicate_annotation"])

    def test_conflicting_phase_does_not_double_count(self):
        self.write_trace([self.command()])
        result = self.analyze(self.annotation(), self.annotation(phase="review"))
        self.assertEqual(result["status"], "partial")
        self.assertIn("conflicting phase", result["activities"][1]["reason"])
        self.assertNotIn("review", result["phases"])

    def test_different_excerpts_from_same_event_cannot_create_two_phases(self):
        self.write_trace([self.command()])
        result = self.analyze(self.annotation(), self.annotation(phase="review", quote="保留有效安静。"))
        self.assertEqual(result["activities"][1]["status"], "unverified")
        self.assertNotIn("review", result["phases"])

    def dispatch(self, receiver="child", sender="parent", tool="spawn_agent", **updates):
        item = {"id": "dispatch-1", "type": "collab_tool_call", "tool": tool, "status": "completed",
                "sender_thread_id": sender, "receiver_thread_ids": [receiver], "prompt": "核对文稿"}
        item.update(updates)
        return item

    def test_native_dispatch_links_supplied_child_session_without_adding_usage(self):
        child = self.write_trace([], self.root / "child.jsonl", session="child")
        self.write_trace([self.dispatch()])
        result = self.analyze(children=[child])
        link = result["native_task_links"][0]
        self.assertEqual(link["status"], "native_session_link")
        self.assertEqual(link["receiver_session_id"], "child")
        self.assertEqual(link["usage_inclusion"], "unknown")
        self.assertIsNone(result["full_task_total"])
        self.assertIsNone(result["usage"]["child_coverage"]["full_task_total"])

    def test_caller_child_label_does_not_prove_parentage(self):
        child = self.write_trace([], self.root / "child.jsonl", session="child")
        self.write_trace([self.command()])
        self.assertEqual(self.analyze(children=[child])["native_task_links"], [])
        self.write_trace([self.dispatch(receiver="other-child")])
        result = self.analyze(children=[child])
        self.assertEqual(result["native_task_links"][0]["status"], "child_trace_missing")

    def test_bad_sender_failed_dispatch_and_wait_cannot_create_links(self):
        for item in (self.dispatch(sender="somebody-else"), self.dispatch(status="failed"),
                     self.dispatch(tool="wait"), self.dispatch(receiver="parent")):
            with self.subTest(item=item):
                self.write_trace([item])
                self.assertEqual(self.analyze()["native_task_links"], [])

    def test_invalid_or_mixed_trace_cannot_certify_evidence(self):
        self.write_trace([self.command()])
        with self.trace.open("a") as handle:
            handle.write('{"type":"session_meta","payload":{"id":"another"}}\n')
        self.assertEqual(self.analyze(self.annotation())["activities"][0]["status"], "unverified")

    def test_no_mapping_leaves_reads_unclaimed_and_duplicate_trace_deduplicated(self):
        self.write_trace([self.command()])
        result = activity.analyze([str(self.trace), str(self.trace)])
        self.assertEqual(result["activities"], [])
        self.assertEqual(result["phases"], {})
        self.assertEqual(result["usage"]["main"]["files"], 1)

    def test_map_rejects_duplicate_keys_and_invalid_types(self):
        path = self.root / "map.json"
        path.write_text('{"schema":1,"schema":1,"annotations":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            activity.load_mapping(path)
        self.write_trace([self.command()])
        for change in ({"line": True}, {"phase": "automatically-guessed"}, {"source": {"path": "relative", "sha256": "0" * 64}}):
            bad = self.annotation(); bad.update(change)
            self.assertEqual(self.analyze(bad)["activities"][0]["status"], "unverified")

    def test_cli_output_is_exclusive_and_does_not_modify_inputs(self):
        self.write_trace([self.command()])
        mapping = self.root / "map.json"
        mapping.write_text(json.dumps({"schema": 1, "annotations": [self.annotation()]}, ensure_ascii=False), encoding="utf-8")
        output = self.root / "result.json"
        before = self.trace.read_bytes(), self.source.read_bytes()
        args = [sys.executable, "-B", str(SCRIPT), "--trace", str(self.trace), "--map", str(mapping), "--output", str(output)]
        first = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = output.read_bytes()
        second = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(output.read_bytes(), saved)
        self.assertEqual((self.trace.read_bytes(), self.source.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
