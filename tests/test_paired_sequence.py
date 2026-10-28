"""Paired-sequence evidence invariants; synthetic traces are not model results."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("paired_sequence_tested", ROOT / "benchmarks/skill-execution/paired_sequence.py")
paired = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paired)


class PairedSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.sources = []
        for name, entry in (("one", "long/SKILL.md"), ("two", "write/SKILL.md")):
            source = self.root / name
            (source / entry).parent.mkdir(parents=True)
            (source / entry).write_text("Synthetic skill fixture, not a writing model.", encoding="utf-8")
            self.sources.append((source, entry))
        condition = {"model": "synthetic-test-model", "settings": {"reasoning_effort": "high"}, "repetitions": 1}
        self.protocol = {"schema": 1, "id": "synthetic", "provenance": {
            "origin": "original_synthetic", "classification": "development",
            "actual_reader_feedback": False, "independent_holdout": False},
            "configuration": {"conditions": {a: copy.deepcopy(condition) for a in paired.ARMS}, "assessment_mode": "blind"},
            "fixtures": {"input.md": "Both arms receive the same original material."},
            "phases": [{"prompt": f"Write candidate chapter {n}, do not change prior work.",
                        "output": f"chapters/{n}.md", "length": {"min": 2000, "max": 2800,
                        "count_method": "visible_nonspace_v2", "count_title": False},
                        "review_required": ["Observe actual prose, not a score"]} for n in (1, 2)]}
        self.path = self.root / "protocol.json"

    def prepare(self):
        self.path.write_text(json.dumps(self.protocol), encoding="utf-8")
        return paired.prepare(self.path, *self.sources[0], *self.sources[1], self.run)

    def complete(self, arm="candidate", number=1, count=2400, exit_code=0):
        folder = self.run / arm / f"phase-{number}"
        workspace = self.run / arm / "workspace"
        (workspace / f"chapters/{number}.md").write_text(f"第{number}章 合成夹具\n" + "文" * count, encoding="utf-8")
        launch = {"argv": ["/synthetic/codex", "exec", "--cd", str(workspace), "--model", "synthetic-test-model",
                           "-c", 'model_reasoning_effort="high"', "-"],
                  "task_sha256": paired.trials.file_hash(folder / "task.txt")}
        paired.write(folder / "launch.json", launch)
        paired.write(folder / "process-result.json", {"exit_code": exit_code, "timed_out": False})
        events = [{"type": "thread.started", "thread_id": f"synthetic-{arm}-{number}"},
                  {"type": "turn.started"}, {"type": "turn.completed"}]
        (folder / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def test_preparation_preserves_identical_inputs_and_never_claims_execution(self):
        result = self.prepare()
        self.assertEqual(result["execution"], "not_started")
        inputs = [(self.run / a / "workspace/input.md").read_bytes() for a in paired.ARMS]
        self.assertEqual(*inputs)
        task = (self.run / "candidate/phase-1/task.txt").read_text()
        self.assertNotIn("Observe actual prose", task)
        report = paired.inspect_phase(self.run, "candidate", 1)
        self.assertFalse(report["continuation_ready"])
        self.assertFalse(report["quality_assessed"])
        self.assertIsNone(report["usage"])

    def test_inspection_exit_code_does_not_report_missing_execution_as_success(self):
        self.prepare()
        argv = ["inspect", "--run", str(self.run), "--arm", "candidate", "--number", "1"]
        with mock.patch("builtins.print"):
            self.assertEqual(paired.main(argv), 1)
            self.complete()
            self.assertEqual(paired.main(argv), 0)

    def test_complete_synthetic_evidence_still_requires_content_review(self):
        self.prepare(); self.complete()
        report = paired.inspect_phase(self.run, "candidate", 1)
        self.assertTrue(report["continuation_ready"])
        self.assertEqual(report["count"], 2400)
        self.assertEqual(report["status"], "needs_content_review")
        self.assertFalse(report["quality_assessed"])

    def test_missing_trace_or_launch_cannot_advance(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Prior phase"):
            paired.begin_phase(self.run, "candidate", 2)
        self.assertFalse((self.run / "candidate/phase-2").exists())

    def test_short_original_can_continue_without_disguising_length_failure(self):
        self.prepare(); self.complete(count=100)
        self.assertFalse(paired.inspect_phase(self.run, "candidate", 1)["within_length"])
        paired.begin_phase(self.run, "candidate", 2)
        phase = paired.read(self.run / "candidate/phase-2/phase.json")
        self.assertFalse(phase["previous"]["within_length"])
        self.assertEqual(phase["previous"]["count"], 100)

    def test_previous_chapter_is_protected_in_next_phase(self):
        self.prepare(); self.complete()
        paired.begin_phase(self.run, "candidate", 2)
        self.complete(number=2)
        (self.run / "candidate/workspace/chapters/1.md").write_text("Changed", encoding="utf-8")
        report = paired.inspect_phase(self.run, "candidate", 2)
        self.assertFalse(report["continuation_ready"])
        self.assertTrue(report["mechanical"]["checks"]["unauthorized_changes"])
        self.assertEqual((self.run / "candidate/phase-1/prose.original.md").read_text(), "第1章 合成夹具\n" + "文" * 2400)

    def test_completion_preserves_original_and_never_replaces_a_report(self):
        self.prepare(); self.complete()
        result = paired.complete_phase(self.run, "candidate", 1)
        self.assertFalse(result["quality_assessed"])
        self.assertTrue((self.run / "candidate/phase-1/prose.original.md").is_file())
        with self.assertRaisesRegex(ValueError, "existing phase report"):
            paired.complete_phase(self.run, "candidate", 1)

    def test_archived_original_cannot_be_silently_replaced_before_continuation(self):
        self.prepare(); self.complete()
        (self.run / "candidate/phase-1/prose.original.md").write_text("different")
        with self.assertRaisesRegex(ValueError, "Archived original differs"):
            paired.begin_phase(self.run, "candidate", 2)
        self.assertFalse((self.run / "candidate/phase-2").exists())

    def test_input_or_unrequested_file_change_blocks_continuation(self):
        for name in ("input.md", "extra.md"):
            with self.subTest(name=name):
                if self.run.exists():
                    import shutil
                    shutil.rmtree(self.run)
                self.prepare(); self.complete()
                (self.run / "candidate/workspace" / name).write_text("unauthorized", encoding="utf-8")
                self.assertFalse(paired.inspect_phase(self.run, "candidate", 1)["continuation_ready"])

    def test_drifted_skill_and_protocol_are_rejected(self):
        self.prepare()
        path = self.run / "candidate/skills/write/SKILL.md"
        old = path.read_text(); path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "skill snapshot changed"):
            paired.inspect_phase(self.run, "candidate", 1)
        path.write_text(old)
        frozen = self.run / "protocol.json"
        changed = paired.read(frozen); changed["phases"][0]["prompt"] = "different"
        frozen.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "protocol changed"):
            paired.inspect_phase(self.run, "candidate", 1)

    def test_phase_task_drift_is_rejected(self):
        self.prepare()
        (self.run / "candidate/phase-1/task.txt").write_text("different")
        with self.assertRaisesRegex(ValueError, "phase file changed"):
            paired.inspect_phase(self.run, "candidate", 1)

    def test_model_mismatch_never_counts_as_paired_execution(self):
        self.prepare(); self.complete()
        path = self.run / "candidate/phase-1/launch.json"
        launch = paired.read(path); launch["argv"][5] = "different-model"
        path.write_text(json.dumps(launch))
        self.assertFalse(paired.inspect_phase(self.run, "candidate", 1)["continuation_ready"])

    def test_failed_process_cannot_advance(self):
        self.prepare(); self.complete(exit_code=1)
        self.assertFalse(paired.inspect_phase(self.run, "candidate", 1)["continuation_ready"])

    def test_boolean_is_not_a_process_exit_code(self):
        self.prepare(); self.complete(exit_code=False)
        self.assertFalse(paired.inspect_phase(self.run, "candidate", 1)["continuation_ready"])

    def test_contradictory_process_bindings_cannot_advance(self):
        self.prepare(); self.complete()
        path = self.run / "candidate/phase-1/process-result.json"
        original = paired.read(path)
        for field, value in (("arm", "baseline"), ("phase", 99), ("number", True),
                             ("case_id", "other"), ("task_sha256", "0" * 64)):
            with self.subTest(field=field):
                path.write_text(json.dumps({**original, field: value}))
                report = paired.inspect_phase(self.run, "candidate", 1)
                self.assertFalse(report["continuation_ready"])
                self.assertEqual(report["process_binding"]["status"], "mismatch")
        path.write_text(json.dumps(original))
        self.assertEqual(paired.inspect_phase(self.run, "candidate", 1)["process_binding"]["status"], "unverified")

    def test_matching_process_bindings_do_not_certify_capture_origin(self):
        self.prepare(); self.complete()
        path = self.run / "candidate/phase-1/process-result.json"
        process = paired.read(path)
        process.update(arm="candidate", phase=1, task_sha256=paired.trials.file_hash(path.parent / "task.txt"))
        path.write_text(json.dumps(process))
        report = paired.inspect_phase(self.run, "candidate", 1)
        self.assertTrue(report["continuation_ready"])
        self.assertEqual(report["process_binding"]["status"], "matches_recorded_fields")
        self.assertIn("unverified", report["process_binding"]["scope"])

    def test_existing_run_or_phase_is_never_overwritten(self):
        self.prepare()
        with self.assertRaises(ValueError): self.prepare()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            paired.begin_phase(self.run, "candidate", 1)

    def test_unequal_models_and_repeats_rejected_before_run_creation(self):
        self.protocol["configuration"]["conditions"]["candidate"]["model"] = "other"
        with self.assertRaisesRegex(ValueError, "same model"):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_short_chapter_protocol_and_path_overlap_rejected(self):
        self.protocol["phases"][0]["length"]["min"] = 800
        with self.assertRaisesRegex(ValueError, "normal-length"):
            self.prepare()
        self.protocol["phases"][0]["length"]["min"] = 2000
        self.protocol["phases"][1]["output"] = "input.md"
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.prepare()

    def test_linked_source_is_rejected(self):
        (self.sources[0][0] / "linked").symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "Linked or special"):
            self.prepare()

    def test_overlapping_fixture_files_rejected_before_copy(self):
        self.protocol["fixtures"]["input.md/child.md"] = "not a directory"
        with self.assertRaisesRegex(ValueError, "Fixture file paths"):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_source_edit_during_copy_preserves_partial_run_without_manifest(self):
        original = paired.shutil.copytree
        def changing_copy(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            if not args:
                (Path(source) / "changed.md").write_text("race")
            return result
        with mock.patch.object(paired.shutil, "copytree", side_effect=changing_copy):
            with self.assertRaisesRegex(ValueError, "changed during freeze"):
                self.prepare()
        self.assertTrue(self.run.exists())
        self.assertFalse((self.run / "sequence.json").exists())


if __name__ == "__main__":
    unittest.main()
