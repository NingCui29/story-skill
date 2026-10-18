"""Synthetic evidence-shape tests; no model calls or literary quality claims."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prose_trial_inspection", ROOT / "benchmarks/skill-execution/prose_trials.py")
trials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trials)


class ProseTrialInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.skills = self.root / "source-skills"
        (self.skills / "story-skill-review").mkdir(parents=True)
        (self.skills / "story-skill-review/SKILL.md").write_text("Synthetic review instructions.\n", encoding="utf-8")
        self.configuration = {"conditions": {arm: {
            "model": "test-model", "settings": {"reasoning_effort": "high", "host_version": "test-host", "seed": None},
            "repetitions": 1} for arm in ("baseline", "candidate")}, "assessment_mode": "independent"}
        self.run = self.root / "run"
        trials.prepare("prose-telegraph", self.skills, self.skills, self.configuration, self.run)
        self.sample = self.run / "candidate/sample-1"
        self.workspace = self.sample / "workspace"
        self.case = trials.read_json(self.run / "case.json")

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def write_trace(self, middle=(), ended=True):
        events = [{"type": "thread.started", "thread_id": "trial-thread"}, {"type": "turn.started"}, *middle]
        if ended:
            events.append({"type": "turn.completed", "usage": {
                "input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 25}})
        (self.sample / "trace.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")

    def complete(self):
        for name, rule in self.case["output_length"].items():
            (self.workspace / name).write_text("文" * rule["min"], encoding="utf-8")
        self.write_trace([{"type": "item.completed", "item": {
            "id": "answer", "type": "agent_message", "text": "本次任务已完成，独立审稿全部通过。"}}])
        self.write(self.sample / "launch.json", {
            "host": "Codex CLI", "argv": ["codex", "exec", "--cd", str(self.workspace),
                "--json", "--model", "test-model", "-c", 'model_reasoning_effort="high"', "-"],
            "cwd": str(self.root), "task_sha256": trials.file_hash(self.sample / "task.txt"),
            "model_requested": "test-model", "reasoning_effort_requested": "high"})
        self.write(self.sample / "process-result.json", {"exit_code": 0, "timed_out": False,
            "case": self.case["id"], "arm": "candidate"})
        (self.sample / "stderr.log").write_text("", encoding="utf-8")
        (self.sample / "final.txt").write_text("本次任务已完成，独立审稿全部通过。", encoding="utf-8")

    def inspect(self):
        result = trials.inspect(self.run, ["candidate/1"])
        self.assertFalse(result["quality_assessed"])
        self.assertEqual(result["actual_conditions_status"], "unverified")
        return result["samples"][0]

    def record_review(self, with_dispatch):
        quote = "独立意见：此处仍须由作者确认人物动机。"
        input_ref = {"path": "候选.md", "sha256": trials.file_hash(self.workspace / "候选.md")}
        dispatch_packet = {"schema": 1, "assignment_id": "review-1", "task": "review", "input": input_ref}
        return_packet = {"schema": 1, "assignment_id": "review-1", "input_sha256": input_ref["sha256"]}
        dispatch = {"type": "item.completed", "item": {
            "id": "dispatch", "type": "collab_tool_call", "tool": "spawn_agent", "status": "completed",
            "sender_thread_id": "trial-thread", "receiver_thread_ids": ["reviewer-thread"],
            "prompt": trials.review.REVIEW_ASSIGNMENT + json.dumps(dispatch_packet, ensure_ascii=False),
            "agents_states": {"reviewer-thread": {"status": "running", "message": None}}}}
        returned = {"type": "item.completed", "item": {
            "id": "returned", "type": "collab_tool_call", "tool": "wait", "status": "completed",
            "sender_thread_id": "trial-thread", "receiver_thread_ids": ["reviewer-thread"],
            "agents_states": {"reviewer-thread": {"status": "completed", "message":
                trials.review.REVIEW_RETURN + json.dumps(return_packet) + "\n" + quote}}}}
        self.write_trace([dispatch, returned] if with_dispatch else [returned])
        checked = self.root / "review-input-check.json"
        trials.evaluation.check(argparse.Namespace(baseline=self.sample / "baseline.json",
            trace=self.sample / "trace.jsonl", output=checked))
        trace_ref = {"source": "trace", "sha256": trials.file_hash(self.sample / "trace.jsonl"),
                     "line": 4 if with_dispatch else 3, "quote": quote}
        claim = {"id": "review-current", "kind": "independent_review", "status": "observed",
                 "reason": "Synthetic reference linkage test, not a real review.", "evidence": [trace_ref]}
        if with_dispatch:
            claim["review_assignment"] = {"schema": 1, "receiver_thread_id": "reviewer-thread", "input": input_ref,
                "dispatch": {**trace_ref, "line": 3, "quote": trials.review.REVIEW_ASSIGNMENT}}
        record = {"schema": 1, "baseline_sha256": trials.file_hash(self.sample / "baseline.json"),
            "check_sha256": trials.file_hash(checked),
            "task": {"path": str(self.sample / "task.txt"), "sha256": trials.file_hash(self.sample / "task.txt")},
            "reviewer": {"kind": "model", "mode": "independent"}, "invocation": {
                "mode": "supplied_skill", "host": "Codex CLI", "launch": {
                    "path": str(self.sample / "launch.json"), "sha256": trials.file_hash(self.sample / "launch.json")}},
            "claims": [claim]}
        self.write(self.sample / "review-record.json", record)

    def test_prepared_only_is_not_started_and_inspection_does_not_mutate_run(self):
        before = trials.evaluation.snapshot(self.run)
        result = trials.inspect(self.run)
        self.assertEqual(result["status"], "not_started")
        self.assertEqual(result["sample_counts"]["not_started"], 2)
        self.assertEqual(result["samples"][0]["process"]["status"], "unverified")
        self.assertIsNone(result["samples"][0]["usage"])
        self.assertEqual(before, trials.evaluation.snapshot(self.run))

    def test_observable_completion_still_requires_content_review(self):
        self.complete()
        before = trials.evaluation.snapshot(self.run)
        result = self.inspect()
        self.assertEqual(result["status"], "needs_content_review")
        self.assertEqual(result["execution_check"]["trace"]["status"], "complete")
        self.assertEqual(result["usage"]["main"]["observed_per_turn_sum"]["input_tokens"], 50)
        self.assertIsNone(result["all_task_usage"])
        self.assertIsNone(result["cost"])
        fields = result["launch_conditions"]["fields"]
        self.assertEqual(fields["model"]["status"], "matches_launch")
        self.assertEqual(fields["reasoning_effort"]["status"], "matches_launch")
        self.assertEqual(fields["host_version"]["status"], "unverified")
        self.assertEqual(result["review"]["independent_assignment_status"], "unverified")
        self.assertEqual(before, trials.evaluation.snapshot(self.run))
        all_samples = trials.inspect(self.run)
        self.assertEqual(all_samples["status"], "incomplete")
        self.assertEqual(all_samples["sample_counts"]["not_started"], 1)

    def test_timeout_retains_failed_process_incomplete_trace_and_missing_outputs(self):
        self.write_trace(ended=False)
        self.write(self.sample / "process-result.json", {"exit_code": None, "timed_out": True})
        result = self.inspect()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["process"]["receipt"]["timed_out"])
        self.assertEqual(result["execution_check"]["trace"]["status"], "incomplete")
        self.assertEqual(len(result["execution_check"]["checks"]["missing_outputs"]), 2)

    def test_completion_statement_and_zero_exit_do_not_substitute_for_trace(self):
        self.complete()
        (self.sample / "trace.jsonl").unlink()
        result = self.inspect()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["execution_check"]["trace"]["status"], "unverified")
        self.assertEqual(result["review"]["independent_assignment_status"], "unverified")

    def test_only_self_report_is_incomplete(self):
        (self.sample / "final.txt").write_text("已完成并通过独立审稿。", encoding="utf-8")
        self.assertEqual(self.inspect()["status"], "incomplete")

    def test_required_outputs_alone_do_not_substitute_for_execution(self):
        for name, rule in self.case["output_length"].items():
            (self.workspace / name).write_text("文" * rule["min"], encoding="utf-8")
        result = self.inspect()
        self.assertTrue(result["execution_check"]["mechanical_ok"])
        self.assertEqual(result["status"], "incomplete")

    def test_length_uses_runtime_visible_count_and_rejects_out_of_range(self):
        self.complete()
        rule = self.case["output_length"]["候选.md"]
        # Blank-looking filler and combining marks must not pad the visible count.
        (self.workspace / "候选.md").write_text("文" * (rule["min"] - 1) + "\u200b\u3164\u0301" * 50, encoding="utf-8")
        result = self.inspect()
        self.assertTrue(result["execution_check"]["mechanical_ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["output_length"]["候选.md"]["actual"], rule["min"] - 1)
        self.assertEqual(result["output_length"]["候选.md"]["status"], "out_of_range")

    def test_nonzero_exit_and_missing_or_blank_terminal_outputs_fail(self):
        self.complete()
        process = self.read(self.sample / "process-result.json")
        process["exit_code"] = 1
        self.write(self.sample / "process-result.json", process)
        self.assertEqual(self.inspect()["status"], "failed")
        process["exit_code"] = 0
        self.write(self.sample / "process-result.json", process)
        (self.workspace / "候选.md").unlink()
        result = self.inspect()
        self.assertEqual(result["status"], "failed")
        self.assertIn("候选.md", result["execution_check"]["checks"]["missing_outputs"])
        (self.workspace / "候选.md").write_text(" \n\t", encoding="utf-8")
        self.assertIn("候选.md", self.inspect()["blank_required_outputs"])

    def test_launch_model_effort_task_and_workspace_mismatch_are_not_passed(self):
        self.complete()
        original = self.read(self.sample / "launch.json")
        variants = []
        for index, value in ((6, "other-model"), (8, 'model_reasoning_effort="low"'), (3, str(self.root))):
            altered = deepcopy(original)
            altered["argv"][index] = value
            variants.append(altered)
        variants.append({**original, "task_sha256": "0" * 64})
        for launch in variants:
            with self.subTest(launch=launch):
                self.write(self.sample / "launch.json", launch)
                result = self.inspect()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["launch_conditions"]["status"], "mismatch")

    def test_requested_model_field_cannot_supply_missing_argv_or_override_conflicts(self):
        self.complete()
        launch = self.read(self.sample / "launch.json")
        launch["argv"][5:7] = []
        self.write(self.sample / "launch.json", launch)
        result = self.inspect()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["launch_conditions"]["fields"]["model"]["status"], "unverified")
        launch["argv"][2:2] = ["--model=test-model", "-c", 'model="other-model"']
        self.write(self.sample / "launch.json", launch)
        self.assertEqual(self.inspect()["status"], "failed")

    def test_missing_process_or_declared_effort_does_not_complete(self):
        self.complete()
        (self.sample / "process-result.json").unlink()
        self.assertEqual(self.inspect()["status"], "incomplete")
        self.complete()
        launch = self.read(self.sample / "launch.json")
        launch["argv"][7:9] = []
        self.write(self.sample / "launch.json", launch)
        self.assertEqual(self.inspect()["status"], "incomplete")

    def test_toml_key_whitespace_cannot_hide_model_or_effort_overrides(self):
        self.complete()
        original = self.read(self.sample / "launch.json")
        for override in ('model = "other-model"', ' model_reasoning_effort = "low"'):
            with self.subTest(override=override):
                launch = deepcopy(original)
                launch["argv"][-1:-1] = ["-c", override]
                self.write(self.sample / "launch.json", launch)
                result = self.inspect()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["launch_conditions"]["status"], "mismatch")

    def test_unsupported_profile_config_and_toml_values_remain_incomplete(self):
        self.complete()
        original = self.read(self.sample / "launch.json")
        for extra in (["--profile", "another-profile"], ["-c", 'profiles.another.model="different"'],
                      ["-c", "model_reasoning_effort='high'"], ["--local-provider=ollama"]):
            with self.subTest(extra=extra):
                launch = deepcopy(original)
                launch["argv"][-1:-1] = extra
                self.write(self.sample / "launch.json", launch)
                result = self.inspect()
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(result["launch_conditions"]["unverified_overrides"])

    def test_malformed_trace_and_receipt_errors_are_preserved(self):
        self.complete()
        (self.sample / "trace.jsonl").write_text('{"type":"thread.started"}\nnot-json\n', encoding="utf-8")
        result = self.inspect()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["usage"]["status"], "invalid")
        (self.sample / "process-result.json").write_text('{"exit_code":0,"timed_out":false,"exit_code":1}', encoding="utf-8")
        result = self.inspect()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Duplicate JSON key", result["issues"][0]["message"])

    def test_wait_without_dispatch_cannot_certify_independent_review(self):
        self.complete()
        self.record_review(with_dispatch=False)
        result = self.inspect()
        self.assertEqual(result["status"], "needs_content_review")
        self.assertEqual(result["review"]["independent_assignment_status"], "unverified")
        self.assertEqual(result["review"]["status"], "unverified")

    def test_existing_review_checker_binds_assignment_and_current_artifact(self):
        self.complete()
        self.record_review(with_dispatch=True)
        result = self.inspect()
        self.assertEqual(result["status"], "needs_content_review")
        self.assertEqual(result["review"]["independent_assignment_status"], "observed")
        self.assertEqual(result["review"]["status"], "traceable")
        with (self.workspace / "候选.md").open("a", encoding="utf-8") as stream:
            stream.write("新")
        stale = self.inspect()
        self.assertEqual(stale["status"], "failed")
        self.assertIn("Stale review check_sha256", stale["issues"][0]["message"])

    def test_selector_scope_and_evidence_link_protection(self):
        for selection in (["candidate/../1"], ["candidate/01"], ["candidate/2"], ["candidate/1", "candidate/1"], []):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                trials.inspect(self.run, selection)
        external = self.root / "external.jsonl"
        external.write_text("", encoding="utf-8")
        try:
            (self.sample / "trace.jsonl").symlink_to(external)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        result = self.inspect()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Symbolic links", result["issues"][0]["message"])

    def test_cli_output_is_exclusive_outside_protected_trees_and_keeps_inputs(self):
        self.complete()
        before = trials.evaluation.snapshot(self.run)
        output = self.root / "inspection.json"
        arguments = ["inspect", "--run", str(self.run), "--sample", "candidate/1", "--output"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(trials.main([*arguments, str(output)]), 0)
            self.assertEqual(trials.main([*arguments, str(output)]), 2)
            self.assertEqual(trials.main([*arguments, str(self.workspace / "inspection.json")]), 2)
            self.assertEqual(trials.main([*arguments, str(self.skills / "inspection.json")]), 2)
        self.assertEqual(self.read(output)["status"], "needs_content_review")
        self.assertEqual(before, trials.evaluation.snapshot(self.run))
        self.assertFalse((self.skills / "inspection.json").exists())

    def test_absent_evidence_slots_are_reserved_and_cannot_be_filled_by_inspection(self):
        before = trials.evaluation.snapshot(self.run)
        for arm in ("baseline", "candidate"):
            sample = self.run / arm / "sample-1"
            for name in (*trials.SAMPLE_EVIDENCE.values(), "review-record.json"):
                with self.subTest(arm=arm, name=name), redirect_stdout(io.StringIO()):
                    self.assertEqual(trials.main(["inspect", "--run", str(self.run),
                        "--sample", "candidate/1", "--output", str(sample / name)]), 2)
                    self.assertFalse((sample / name).exists())
        self.assertEqual(trials.inspect(self.run)["status"], "not_started")
        self.assertEqual(before, trials.evaluation.snapshot(self.run))
        # A report beside the reserved evidence slots is allowed.
        output = self.sample / "inspection.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(trials.main(["inspect", "--run", str(self.run), "--output", str(output)]), 2)
        self.assertEqual(self.read(output)["status"], "not_started")


if __name__ == "__main__":
    unittest.main()
