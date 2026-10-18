import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prose_trials", ROOT / "benchmarks/skill-execution/prose_trials.py")
trials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trials)


class PairedProseTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.baseline_skills = self.root / "baseline-skills"
        self.candidate_skills = self.root / "candidate-skills"
        for source, text in ((self.baseline_skills, "old rules"), (self.candidate_skills, "new rules")):
            (source / "story-skill-review").mkdir(parents=True)
            (source / "story-skill-review/SKILL.md").write_text(text, encoding="utf-8")
            (source / "story-skill-review/reference.md").write_text("shared reference", encoding="utf-8")
        self.configuration = {
            "conditions": {
                name: {"model": "test-only-placeholder", "settings": {"reasoning_effort": None}, "repetitions": 1}
                for name in ("baseline", "candidate")
            },
            "assessment_mode": "blind",
        }
        self.run = self.root / "run"

    def prepare(self, case_id="prose-telegraph"):
        return trials.prepare(case_id, self.baseline_skills, self.candidate_skills, self.configuration, self.run)

    def load(self, relative):
        return json.loads((self.run / relative).read_text(encoding="utf-8"))

    def write(self, relative, value):
        (self.run / relative).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_pair_freezes_equal_inputs_separate_rules_and_no_quality_claim(self):
        result = self.prepare()
        record = self.load("trial.json")
        self.assertEqual(result["task_execution"], "not_started")
        self.assertFalse(result["quality_assessed"])
        self.assertEqual(record["declared_conditions"]["baseline"], record["declared_conditions"]["candidate"])
        self.assertNotEqual(record["arms"]["baseline"]["skills_snapshot_sha256"],
                            record["arms"]["candidate"]["skills_snapshot_sha256"])
        baseline, candidate = [record["arms"][arm]["samples"][0] for arm in ("baseline", "candidate")]
        self.assertEqual(baseline["input_snapshot"], candidate["input_snapshot"])
        self.assertIsNone(baseline["evidence"]["cost"])
        self.assertIsNone(candidate["evidence"]["all_task_usage"])
        self.assertEqual(record["review_plan"]["requested_mode"], "blind")
        self.assertFalse(record["review_plan"]["blinding_performed"])
        self.assertIsNone(record["review_plan"]["performed_mode"])
        self.assertTrue(trials.verify(self.run)["frozen_inputs_intact"])
        self.assertEqual(trials.verify(self.run)["actual_conditions_status"], "unverified")

    def test_each_case_has_original_development_provenance_and_frozen_baselines(self):
        ids = ("prose-telegraph", "prose-swallowed-confrontation", "prose-causal-claim", "prose-quiet-scene", "prose-detect-only",
               "prose-relationship-ambiguity", "prose-relationship-contradiction", "prose-folk-style-contract",
               "prose-cooperative-payoff-drift", "prose-voluntary-payoff-handoff")
        for case_id in ids:
            with self.subTest(case_id=case_id):
                self.run = self.root / case_id
                self.prepare(case_id)
                case = self.load("case.json")
                self.assertEqual(case["provenance"]["origin"], "original_synthetic")
                self.assertEqual(case["provenance"]["classification"], "development")
                self.assertFalse(case["provenance"]["actual_author_complaint"])
                self.assertFalse(case["provenance"]["independent_holdout"])
                for arm in ("baseline", "candidate"):
                    baseline = self.load(f"{arm}/sample-1/baseline.json")
                    self.assertEqual(baseline["case"], case)
                    self.assertEqual(set(baseline["before"]["workspace"]["files"]), set(case["fixtures"]))
                self.assertTrue(trials.verify(self.run)["frozen_inputs_intact"])

    def test_generator_task_does_not_include_review_rubric_or_other_arm(self):
        case_ids = ("prose-telegraph", "prose-folk-style-contract", "prose-cooperative-payoff-drift",
                    "prose-voluntary-payoff-handoff")
        for case_id in case_ids:
            with self.subTest(case_id=case_id):
                self.run = self.root / case_id
                self.prepare(case_id)
                case = self.load("case.json")
                for arm, other in (("baseline", "candidate"), ("candidate", "baseline")):
                    task = (self.run / arm / "sample-1/task.txt").read_text(encoding="utf-8")
                    self.assertIn(case["prompt"], task)
                    for criterion in case["review_required"]:
                        self.assertNotIn(criterion, task)
                    self.assertNotIn(str(self.run / other), task)
                    self.assertIn(str(self.run / arm / "skills/story-skill-review/SKILL.md"), task)

    def test_same_repetition_count_uses_fresh_workspaces(self):
        for arm in self.configuration["conditions"]:
            self.configuration["conditions"][arm]["repetitions"] = 2
        self.prepare()
        record = self.load("trial.json")
        for arm in ("baseline", "candidate"):
            samples = record["arms"][arm]["samples"]
            self.assertEqual(len(samples), 2)
            self.assertEqual(samples[0]["input_snapshot"], samples[1]["input_snapshot"])
            self.assertNotEqual(samples[0]["relative_root"], samples[1]["relative_root"])
        self.assertTrue(trials.verify(self.run)["frozen_inputs_intact"])

    def test_different_models_settings_and_repetition_counts_refused_before_output(self):
        for key, value in (("model", "different-model"), ("settings", {"reasoning_effort": "high"}), ("repetitions", 2)):
            with self.subTest(key=key):
                config = deepcopy(self.configuration)
                config["conditions"]["candidate"][key] = value
                with self.assertRaisesRegex(ValueError, "same model"):
                    trials.prepare("prose-telegraph", self.baseline_skills, self.candidate_skills, config, self.run)
                self.assertFalse(self.run.exists())

    def test_unknown_model_and_non_json_settings_refused(self):
        for invalid in (None, "", " "):
            config = deepcopy(self.configuration)
            config["conditions"]["baseline"]["model"] = invalid
            with self.subTest(model=invalid), self.assertRaises(ValueError):
                trials.validate_configuration(config)
        config = deepcopy(self.configuration)
        for arm in config["conditions"]:
            config["conditions"][arm]["settings"] = {"temperature": float("nan")}
        with self.assertRaises(ValueError):
            trials.validate_configuration(config)

    def test_allowed_outputs_are_not_mistaken_for_completed_execution_or_quality(self):
        self.prepare()
        case = self.load("case.json")
        for arm in ("baseline", "candidate"):
            workspace = self.run / arm / "sample-1/workspace"
            for output in case["required_outputs"]:
                (workspace / output).write_text("synthetic test output", encoding="utf-8")
        result = trials.verify(self.run)
        self.assertTrue(result["frozen_inputs_intact"])
        self.assertEqual(result["task_execution"], "unverified")
        self.assertFalse(result["quality_assessed"])

    def test_input_edit_and_extra_candidate_in_detect_only_are_rejected(self):
        self.prepare("prose-detect-only")
        workspace = self.run / "baseline/sample-1/workspace"
        case = self.load("case.json")
        original = next(iter(case["fixtures"]))
        (workspace / original).write_text("unauthorized edit", encoding="utf-8")
        (workspace / "candidate.md").write_text("unauthorized candidate", encoding="utf-8")
        result = trials.verify(self.run)
        self.assertFalse(result["frozen_inputs_intact"])
        self.assertTrue(any("unauthorized" in issue for issue in result["issues"]))
        # The existing range checker independently sees the same violation.
        report, code = trials.evaluation.check(argparse.Namespace(
            baseline=self.run / "baseline/sample-1/baseline.json", trace=None,
            output=self.run / "range-check.json"))
        self.assertEqual(code, 1)
        self.assertEqual(len(report["checks"]["unauthorized_changes"]), 2)

    def test_task_skill_and_baseline_drift_detected(self):
        self.prepare()
        for relative in ("baseline/sample-1/task.txt", "candidate/skills/story-skill-review/SKILL.md", "candidate/sample-1/baseline.json"):
            with (self.run / relative).open("a", encoding="utf-8") as stream:
                stream.write("\nchanged")
        result = trials.verify(self.run)
        self.assertFalse(result["frozen_inputs_intact"])
        for word in ("task", "skill", "baseline"):
            self.assertTrue(any(word in issue for issue in result["issues"]))

    def test_length_and_authorization_drift_detected(self):
        self.prepare()
        case = self.load("case.json")
        output = next(iter(case["output_length"]))
        case["output_length"][output]["min"] = 1
        case["allowed_changes"].append("extra.md")
        self.write("case.json", case)
        result = trials.verify(self.run)
        self.assertFalse(result["frozen_inputs_intact"])
        self.assertTrue(any("Length or authorization" in issue for issue in result["issues"]))

    def test_condition_drift_is_rejected_even_when_saved_configuration_is_changed(self):
        self.prepare()
        config = self.load("configuration.json")
        config["conditions"]["candidate"]["model"] = "other-model"
        self.write("configuration.json", config)
        with self.assertRaisesRegex(ValueError, "same model"):
            trials.verify(self.run)

    def test_existing_outputs_and_sources_are_never_overwritten(self):
        before = trials.evaluation.snapshot(self.baseline_skills)
        self.prepare()
        frozen_before = trials.evaluation.snapshot(self.run)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(frozen_before, trials.evaluation.snapshot(self.run))
        self.assertEqual(before, trials.evaluation.snapshot(self.baseline_skills))
        with self.assertRaises(ValueError):
            trials.prepare("prose-telegraph", self.baseline_skills, self.candidate_skills,
                           self.configuration, self.baseline_skills / "nested-run")

    def test_skill_links_and_traversal_cases_refused(self):
        with self.assertRaises(ValueError):
            trials.load_case("../prose-telegraph")
        linked = self.candidate_skills / "linked.md"
        try:
            linked.symlink_to(self.baseline_skills / "story-skill-review/SKILL.md")
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_feedback_template_has_no_invented_feedback_or_scores(self):
        template = json.loads((trials.CASES / "feedback-record.template.json").read_text(encoding="utf-8"))
        self.assertEqual(template["record_status"], "not_recorded")
        self.assertIsNone(template["classification"])
        self.assertEqual(template["classification_choices"], ["development", "known_feedback", "holdout"])
        for field in ("original_author_feedback", "original_model_assessment"):
            self.assertIsNone(template[field]["quote"])
            self.assertIsNone(template[field]["path"])
        self.assertIsNone(template["review"]["mode"])
        self.assertNotIn("score", template)

    def test_hard_linked_skill_source_refused_before_output_creation(self):
        linked = self.candidate_skills / "hard-linked.md"
        try:
            os.link(self.candidate_skills / "story-skill-review/SKILL.md", linked)
        except OSError:
            self.skipTest("Hard links unavailable")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_frozen_roots_and_inputs_cannot_be_replaced_by_identical_links(self):
        paths = ("baseline/skills", "candidate/sample-1/workspace", "baseline/sample-1",
                 "candidate/sample-1/task.txt", "baseline/sample-1/baseline.json")
        for index, relative in enumerate(paths):
            with self.subTest(relative=relative):
                self.run = self.root / f"linked-run-{index}"
                self.prepare()
                target = self.run / relative
                moved = self.root / f"moved-input-{index}"
                target.rename(moved)
                try:
                    target.symlink_to(moved, target_is_directory=moved.is_dir())
                except OSError:
                    self.skipTest("Symbolic links unavailable")
                with self.assertRaisesRegex(ValueError, "Symbolic links"):
                    trials.verify(self.run)


if __name__ == "__main__":
    unittest.main()
