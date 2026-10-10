import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("execution_metrics", ROOT / "scripts/execution_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


class ExecutionMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def trace(self, events, name="trace.jsonl"):
        path = self.root / name
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        return path

    def cli(self, usages, session="main", name="trace.jsonl", items=()):
        events = [{"type": "thread.started", "thread_id": session}]
        for usage in usages:
            events.extend([{"type": "turn.started"}, *items,
                           {"type": "turn.completed", "usage": usage}])
        return self.trace(events, name)

    def cumulative(self, values, extra=(), name="session.jsonl"):
        return self.trace([{"type": "session_meta", "payload": {"id": "session"}},
                          *[{"type": "event_msg", "payload": {"type": "token_count", "info": {
                              "total_token_usage": value, "last_token_usage": {"input_tokens": 999999}}}}
                            for value in values], *extra], name)

    def source(self, path):
        return metrics.summarize([path])["sources"][0]

    def test_malformed_item_type_produces_invalid_report_without_a_crash(self):
        for item_type in ([], {}, None, True, 3):
            with self.subTest(item_type=item_type):
                path = self.cli([{"input_tokens": 10}], items=[
                    {"type": "item.completed", "item": {"id": "item-1", "type": item_type}}])
                report = metrics.summarize([path])
                self.assertEqual(report["status"], "invalid")
                self.assertIn({"line": 3, "code": "item_type_not_string"}, report["sources"][0]["issues"])
                self.assertIsNone(report["main"]["observed_per_turn_sum"]["input_tokens"])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(metrics.main(["--trace", str(path)]), 1)

    def test_real_fields_are_separate_and_missing_is_not_zero(self):
        path = self.cli([{"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 10,
                          "reasoning_output_tokens": 4, "cache_write_input_tokens": 0},
                         {"input_tokens": 200, "cached_input_tokens": 150, "output_tokens": 20}])
        report = metrics.summarize([path])
        total = report["main"]["observed_per_turn_sum"]
        self.assertEqual((total["input_tokens"], total["cached_input_tokens"], total["output_tokens"]), (300, 220, 30))
        self.assertEqual(total["reasoning_output_tokens"], 4)
        self.assertEqual(total["cache_write_input_tokens"], 0)
        self.assertIsNone(total["total_tokens"])
        self.assertIsNone(report["main"]["complete_per_turn_sum"]["reasoning_output_tokens"])
        self.assertEqual(report["sources"][0]["usage"]["field_coverage"]["reasoning_output_tokens"],
                         {"reported": 1, "records": 2})

    def test_unknown_item_event_is_not_counted_as_a_native_tool_lifecycle(self):
        path = self.cli([{"input_tokens": 10}], items=[
            {"type": "item.future", "item": {"id": "tool-1", "type": "command_execution"}}])
        report = metrics.summarize([path])
        source = report["sources"][0]
        self.assertEqual(source["counters"]["tool_lifecycles_observed"], 0)
        self.assertEqual(source["unknown_events"], [{"line": 3, "type": "item.future"}])
        self.assertEqual(report["status"], "partial")

    def test_concatenated_turns_without_event_ids_are_ambiguous_and_not_complete(self):
        segment = [{"type": "thread.started", "thread_id": "main"}, {"type": "turn.started"},
                   {"type": "turn.completed", "usage": {"input_tokens": 10}}]
        report = metrics.summarize([self.trace(segment + segment)])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["main"]["observed_per_turn_sum"]["input_tokens"], 20)
        self.assertIsNone(report["main"]["complete_per_turn_sum"]["input_tokens"])
        self.assertIn({"line": 4, "code": "repeated_thread_start"}, report["sources"][0]["issues"])

    def test_identical_usage_in_distinct_turns_is_not_a_duplicate(self):
        report = metrics.summarize([self.cli([{"input_tokens": 5}, {"input_tokens": 5}])])
        self.assertEqual(report["main"]["complete_per_turn_sum"]["input_tokens"], 10)
        self.assertEqual(len(report["sources"][0]["usage"]["records"]), 2)

    def test_missing_turn_usage_preserves_partial_observation(self):
        source = self.source(self.cli([{"input_tokens": 0}, None]))
        self.assertEqual(source["usage"]["observed_sum"]["input_tokens"], 0)
        self.assertIsNone(source["usage"]["complete_sum"]["input_tokens"])
        self.assertIsNone(source["usage"]["observed_sum"]["output_tokens"])
        self.assertEqual(source["usage"]["status"], "partial")
        source = self.source(self.cli([None]))
        self.assertEqual(source["usage"]["status"], "unreported")

    def test_duplicate_end_event_without_new_turn_is_excluded(self):
        end = {"type": "turn.completed", "usage": {"input_tokens": 7}}
        source = self.source(self.trace([{"type": "thread.started", "thread_id": "main"},
                                        {"type": "turn.started"}, end, end]))
        self.assertEqual(source["usage"]["observed_sum"]["input_tokens"], 7)
        self.assertEqual(source["duplicate_events"], [{"line": 4, "original_line": 3}])

    def test_event_id_duplicates_and_conflicts(self):
        end = {"type": "turn.completed", "event_id": "end-1", "usage": {"input_tokens": 7}}
        first = [{"type": "thread.started", "thread_id": "main"}, {"type": "turn.started"}, end]
        self.assertEqual(self.source(self.trace(first + [end]))["usage"]["observed_sum"]["input_tokens"], 7)
        report = metrics.summarize([self.trace(first + [{**end, "usage": {"input_tokens": 8}}])])
        self.assertEqual(report["status"], "invalid")
        self.assertIsNone(report["main"]["observed_per_turn_sum"]["input_tokens"])

    def test_identical_file_hash_excluded_but_keeps_both_paths(self):
        path = self.cli([{"input_tokens": 11}])
        copy = self.root / "copy.jsonl"
        copy.write_bytes(path.read_bytes())
        report = metrics.summarize([path, copy])
        self.assertEqual(report["main"]["complete_per_turn_sum"]["input_tokens"], 11)
        self.assertEqual(report["sources"][1]["duplicate_file_of"], str(path))
        self.assertEqual(report["sources"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_same_session_in_distinct_files_withholds_aggregate(self):
        a = self.cli([{"input_tokens": 10}], name="a.jsonl")
        b = self.cli([{"input_tokens": 20}], name="b.jsonl")
        report = metrics.summarize([a, b])
        self.assertEqual(report["main"]["overlap_status"], "unknown")
        self.assertIsNone(report["main"]["observed_per_turn_sum"]["input_tokens"])
        self.assertEqual([s["usage"]["observed_sum"]["input_tokens"] for s in report["sources"]], [10, 20])

    def test_distinct_sessions_and_child_totals_never_combined(self):
        a = self.cli([{"input_tokens": 10}], session="a", name="a.jsonl")
        b = self.cli([{"input_tokens": 20}], session="b", name="b.jsonl")
        c = self.cli([{"input_tokens": 30}], session="c", name="c.jsonl")
        report = metrics.summarize([a, b], [c])
        self.assertEqual(report["main"]["complete_per_turn_sum"]["input_tokens"], 30)
        self.assertEqual(report["child"]["complete_per_turn_sum"]["input_tokens"], 30)
        self.assertEqual(report["child_coverage"]["provided_distinct_files"], 1)
        self.assertIsNone(report["child_coverage"]["full_task_total"])
        self.assertEqual(report["child_coverage"]["main_includes_child_usage"], "unknown")

    def test_duplicate_file_cannot_claim_child_coverage(self):
        path = self.cli([{"input_tokens": 10}])
        report = metrics.summarize([path], [path])
        self.assertEqual(report["child_coverage"]["provided_distinct_files"], 0)
        self.assertIsNone(report["child"]["observed_per_turn_sum"]["input_tokens"])
        self.assertEqual(report["status"], "partial")

    def test_tool_lifecycle_count_not_event_count_and_explicit_failure_only(self):
        item = {"id": "tool", "type": "command_execution", "command": "private manuscript command"}
        complete = {"type": "item.completed", "item": {**item, "exit_code": 1}}
        source = self.source(self.cli([{"input_tokens": 10}], items=[
            {"type": "item.started", "item": item}, {"type": "item.updated", "item": item}, complete, complete]))
        self.assertEqual(source["counters"]["tool_lifecycles_observed"], 1)
        self.assertEqual(source["counters"]["tool_completions_observed"], 1)
        self.assertEqual(source["counters"]["tool_failures_explicit"], 1)
        self.assertEqual(source["counters"]["tool_lifecycles_unfinished"], 0)
        self.assertIsNone(source["counters"]["retry_events"])
        self.assertNotIn("private manuscript", json.dumps(source))
        self.assertEqual(source["tools"][0]["failure_lines"], [5])

    def test_duplicate_error_item_is_counted_once_and_false_error_is_not_failure(self):
        error = {"type": "item.completed", "item": {"id": "error-1", "type": "error", "message": "problem"}}
        source = self.source(self.cli([{"input_tokens": 1}], items=[error, error, {
            "type": "item.completed", "item": {"id": "tool", "type": "mcp_tool_call", "error": False}}]))
        self.assertEqual(source["counters"]["error_items"], 1)
        self.assertEqual(source["counters"]["tool_failures_explicit"], 0)

    def test_tool_id_reuse_in_another_turn_is_a_distinct_lifecycle(self):
        source = self.source(self.cli([{"input_tokens": 1}, {"input_tokens": 1}], items=[{
            "type": "item.completed", "item": {"id": "tool", "type": "mcp_tool_call", "status": "completed"}}]))
        self.assertEqual(source["counters"]["tool_lifecycles_observed"], 2)
        self.assertEqual(source["counters"]["tool_completions_without_start"], 2)

    def test_unfinished_turn_or_tool_withholds_complete_sum(self):
        path = self.cli([{"input_tokens": 3}], items=[{"type": "item.started", "item": {
            "id": "unfinished", "type": "web_search"}}])
        source = self.source(path)
        self.assertEqual(source["status"], "partial")
        self.assertEqual(source["usage"]["observed_sum"]["input_tokens"], 3)
        self.assertIsNone(source["usage"]["complete_sum"]["input_tokens"])
        self.assertEqual(source["counters"]["tool_failures_explicit"], 0)
        with path.open("a") as stream:
            stream.write('{"type":"turn.started"}\n')
        self.assertIn("unfinished_turn", [i["code"] for i in self.source(path)["issues"]])

    def test_failed_turn_does_not_imply_retry_or_unreported_zero(self):
        source = self.source(self.trace([{"type": "thread.started", "thread_id": "main"},
            {"type": "turn.started"}, {"type": "error", "message": "Reconnecting... 1/5", "retryable": True},
            {"type": "turn.failed"}, {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 10}}]))
        self.assertEqual((source["counters"]["turns_failed"], source["counters"]["error_events"]), (1, 1))
        self.assertIsNone(source["counters"]["retry_events"])
        self.assertEqual(source["usage"]["observed_sum"]["input_tokens"], 10)
        self.assertIsNone(source["usage"]["complete_sum"]["input_tokens"])
        self.assertEqual(source["failure_lines"], [3, 4])

    def test_cumulative_snapshots_not_summed_or_mixed_with_last_usage(self):
        path = self.cumulative([{"input_tokens": 100, "output_tokens": 3},
                                {"input_tokens": 150, "output_tokens": 5}])
        report = metrics.summarize([path])
        source = report["sources"][0]
        self.assertEqual(source["adapter"], "codex-session")
        self.assertEqual(source["usage"]["latest_cumulative"]["input_tokens"], 150)
        self.assertTrue(source["usage"]["cumulative_monotonic"])
        self.assertIsNone(source["usage"]["observed_sum"])
        self.assertIsNone(report["main"]["observed_per_turn_sum"]["input_tokens"])

    def test_cumulative_decrease_and_missing_latest_field_remain_visible(self):
        source = self.source(self.cumulative([{"input_tokens": 100, "output_tokens": 10},
                                             {"input_tokens": 20}]))
        self.assertFalse(source["usage"]["cumulative_monotonic"])
        self.assertEqual(source["usage"]["latest_cumulative"]["input_tokens"], 20)
        self.assertIsNone(source["usage"]["latest_cumulative"]["output_tokens"])
        self.assertEqual(source["status"], "partial")
        self.assertIn("cumulative_decreased:input_tokens", [i["code"] for i in source["issues"]])

    def test_cumulative_no_info_does_not_query_or_report_account_fields(self):
        path = self.cumulative([{"input_tokens": 100}], extra=[{"type": "event_msg", "payload": {
            "type": "token_count", "info": None, "rate_limits": {"private_account": "ignored"}}}])
        source = self.source(path)
        self.assertEqual(len(source["usage"]["records"]), 1)
        self.assertNotIn("private_account", json.dumps(source))

    def test_cumulative_duplicate_ids_and_conflicts_preserve_original_lines(self):
        event = {"type": "event_msg", "event_id": "snapshot", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 10}}}}
        meta = {"type": "session_meta", "payload": {"id": "session"}}
        source = self.source(self.trace([meta, event, event]))
        self.assertEqual(len(source["usage"]["records"]), 1)
        self.assertEqual(source["duplicate_events"], [{"line": 3, "original_line": 2}])
        changed = {**event, "timestamp": "different"}
        self.assertEqual(self.source(self.trace([meta, event, changed]))["status"], "invalid")

    def test_unknown_usage_keys_not_guessed_and_unsupported_event_not_counted(self):
        source = self.source(self.cli([{"prompt_tokens": 100, "output_tokens": 10}]))
        self.assertIsNone(source["usage"]["observed_sum"]["input_tokens"])
        self.assertEqual(source["unknown_usage_fields"][0]["fields"], ["prompt_tokens"])
        path = self.cli([None])
        with path.open("a") as stream:
            stream.write('{"type":"future.event","usage":{"input_tokens":1000}}\n')
        source = self.source(path)
        self.assertIsNone(source["usage"]["observed_sum"]["input_tokens"])
        self.assertEqual(source["unknown_events"][0]["line"], 4)

    def test_invalid_counts_and_json_are_not_partially_aggregated(self):
        for value in (-1, True, 1.5, "20", []):
            with self.subTest(value=value):
                report = metrics.summarize([self.cli([{"input_tokens": value}])])
                self.assertEqual(report["status"], "invalid")
                self.assertIsNone(report["main"]["observed_per_turn_sum"]["input_tokens"])
        for raw in (b'{"type":"turn.completed","type":"turn.started"}\n', b'not-json\n', b'\xff',
                    b'{"type":"turn.completed","usage":{"input_tokens":NaN}}\n'):
            with self.subTest(raw=raw):
                path = self.root / "bad.jsonl"
                path.write_bytes(raw)
                self.assertEqual(metrics.summarize([path])["status"], "invalid")

    def test_empty_foreign_and_mixed_formats_fail_clearly(self):
        for events in ([], [{"type": "result", "usage": {"input_tokens": 5}}],
                       [{"type": "turn.started"}, {"type": "session_meta", "payload": {"id": "session"}}]):
            with self.subTest(events=events):
                report = metrics.summarize([self.trace(events)])
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["sources"][0]["status"], "unsupported")

    def test_multiple_sessions_in_one_file_is_invalid(self):
        path = self.cumulative([{"input_tokens": 1}], extra=[{"type": "session_meta", "payload": {"id": "other"}}])
        self.assertEqual(metrics.summarize([path])["status"], "invalid")

    def test_cli_output_is_exclusive_and_inputs_unchanged(self):
        path = self.cli([{"input_tokens": 10}])
        before = path.read_bytes()
        output = self.root / "report.json"
        self.assertEqual(metrics.main(["--trace", str(path), "--output", str(output)]), 0)
        self.assertEqual(json.loads(output.read_text())["schema"], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(metrics.main(["--trace", str(path), "--output", str(output)]), 2)
            self.assertEqual(metrics.main(["--trace", str(path), "--output", str(path)]), 2)
        self.assertEqual(path.read_bytes(), before)
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(metrics.main(["--trace", str(path)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["main"]["observed_per_turn_sum"]["input_tokens"], 10)

    def test_cli_bad_format_exit_code_and_linked_input_rejected(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(metrics.main(["--trace", str(self.trace([]))]), 1)
        path = self.cli([{"input_tokens": 10}])
        link = self.root / "link.jsonl"
        link.symlink_to(path)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(metrics.main(["--trace", str(link)]), 2)

    def test_original_line_numbers_include_blank_lines(self):
        path = self.cli([{"input_tokens": 10}])
        path.write_text("\n" + path.read_text(), encoding="utf-8")
        self.assertEqual(self.source(path)["usage"]["records"][0]["line"], 4)


if __name__ == "__main__":
    unittest.main()
