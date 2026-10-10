import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("execution_review", ROOT / "scripts/execution_review.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class ExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # The repository itself is called story-skill. Its directory name must
        # not be confused with the user's explicit invocation of a skill.
        self.root = Path(self.temp.name).resolve() / "story-skill" / "trial"
        self.root.mkdir(parents=True)
        self.workspace, self.skills, self.host = (self.root / name for name in ("story-skill", "skills", "host"))
        for path in (self.workspace, self.skills, self.host):
            path.mkdir()
        (self.workspace / "original.md").write_text("原稿必须保留。\n", encoding="utf-8")
        self.skill_name = "story-skill-review/SKILL.md"
        for root in (self.skills, self.host):
            (root / self.skill_name).parent.mkdir()
            (root / self.skill_name).write_text("# 审稿\n只审查候选，不采用。\n", encoding="utf-8")
        self.case = {"id": "review-case", "prompt": "请审查候选。", "invocation": "implicit",
                     "allowed_changes": ["candidate.md"], "required_outputs": ["candidate.md"],
                     "review_required": ["不改原稿"]}
        self.case_path = self.write("case.json", self.case)
        self.baseline = self.root / "baseline.json"
        review.evaluation.start(argparse.Namespace(case_file=self.case_path, workspace=self.workspace,
                                skills_root=self.skills, output=self.baseline))
        (self.workspace / "candidate.md").write_text("保留选择。\n结尾的回应缺少动作依据。\n", encoding="utf-8")
        self.target = self.host / self.skill_name
        self.task = self.root / "task.txt"
        self.task.write_text(f"请审查候选，意见保存为candidate.md，只写这个文件。\n工作目录：{self.workspace}\n", encoding="utf-8")
        self.launch = self.write("launch.json", {"host": "codex_cli", "argv": ["codex", "exec", "--cd", str(self.workspace), "--json", "--output-last-message", str(self.root / "final.txt"), "-"],
                           "task_sha256": self.digest(self.task)})
        self.host_receipt = self.write("host.json", {"root": str(self.host),
                                "before": {"files": {self.skill_name: self.digest(self.target)}},
                                "after": {"files": {self.skill_name: self.digest(self.target)}}})
        self.trace = self.root / "trace.jsonl"
        self.trace_events([self.command()])
        self.checked = self.root / "check.json"
        self.check_execution()
        self.record_path = self.root / "record.json"
        self.record = {"schema": 1, "case_id": self.case["id"], "baseline_sha256": self.digest(self.baseline),
                       "check_sha256": self.digest(self.checked), "task": self.ref(self.task),
                       "reviewer": {"kind": "model", "mode": "independent"},
                       "invocation": {"mode": "native_discovery", "host": "codex_cli", "launch": self.ref(self.launch),
                                      "host_skill_snapshot": self.ref(self.host_receipt)},
                       "claims": [{"id": "skill-read", "status": "observed", "kind": "skill_read", "reason": "成功命令返回了实际规则。",
                                   "evidence": [self.trace_ref(target=str(self.target), target_sha256=self.digest(self.target))]}]}

    def digest(self, path):
        return review.evaluation.sha(Path(path).read_bytes())

    def ref(self, path):
        return {"path": str(path), "sha256": self.digest(path)}

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def command(self, **updates):
        item = {"id": "read-1", "type": "command_execution", "command": f"/bin/zsh -lc 'cat {self.target}'",
                "aggregated_output": self.target.read_text(encoding="utf-8"), "exit_code": 0, "status": "completed"}
        item.update(updates)
        return {"type": "item.completed", "item": item}

    def trace_events(self, events):
        native = [{"type": "thread.started", "thread_id": "trial-thread"}, {"type": "turn.started"},
                  *events, {"type": "turn.completed"}]
        self.trace.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in native), encoding="utf-8")

    def check_execution(self):
        self.checked.unlink(missing_ok=True)
        review.evaluation.check(argparse.Namespace(baseline=self.baseline, trace=self.trace, output=self.checked))
        if hasattr(self, "record"):
            self.record["check_sha256"] = self.digest(self.checked)
            for claim in self.record["claims"]:
                for ref in claim["evidence"]:
                    if ref.get("source") == "trace":
                        ref["sha256"] = self.digest(self.trace)

    def trace_ref(self, **updates):
        value = {"source": "trace", "line": 3, "sha256": self.digest(self.trace), "quote": "只审查候选，不采用。"}
        value.update(updates)
        return value

    def artifact_claim(self, kind="content", **updates):
        ref = {"source": "artifact", "path": "candidate.md", "sha256": self.digest(self.workspace / "candidate.md"),
               "line": 2, "quote": "结尾的回应缺少动作依据。"}
        ref.update(updates)
        return {"id": "content-review", "status": "observed", "kind": kind,
                "reason": "复核者对这一原文作出判断；工具只核验引用。", "evidence": [ref]}

    def run_review(self, source=None):
        self.record_path.write_text(json.dumps(self.record, ensure_ascii=False), encoding="utf-8")
        return review.check(argparse.Namespace(baseline=self.baseline, check_file=self.checked, record=self.record_path,
                                               output=self.root / "review-check.json", source_skills_root=source))

    def update_launch_task(self):
        value = json.loads(self.launch.read_text())
        value["task_sha256"] = self.digest(self.task)
        self.launch.write_text(json.dumps(value), encoding="utf-8")
        self.record["task"] = self.ref(self.task)
        self.record["invocation"]["launch"] = self.ref(self.launch)

    def test_native_read_is_traceable_but_never_certifies_quality_or_desktop(self):
        self.record["claims"].append(self.artifact_claim())
        result, code = self.run_review()
        self.assertEqual((code, result["validation_status"]), (0, "traceable"))
        self.assertEqual(result["invocation"]["status"], "observed")
        self.assertEqual(result["claims"][1]["status"], "observed")
        self.assertIn("Semantic correctness of review judgements", result["unverified_scope"])
        self.assertIn("Desktop discovery from a CLI trial", result["unverified_scope"])
        self.assertNotIn("passed", result)

    def test_supplied_skill_read_does_not_become_native_discovery(self):
        self.task.write_text(f"请按 {self.skills}/story-skill-review/SKILL.md 审查。", encoding="utf-8")
        self.update_launch_task()
        self.record["invocation"]["mode"] = "supplied_skill"
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["mode"], result["invocation"]["status"]), (0, "supplied_skill", "observed"))

    def test_explicit_skill_name_or_path_cannot_pass_as_native(self):
        for task in ("请使用 story-skill-review 审查。", f"按 {self.skills} 中规则审查。", f"按 {self.host} 中规则审查。"):
            with self.subTest(task=task):
                self.task.write_text(task, encoding="utf-8")
                self.update_launch_task()
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))
                (self.root / "review-check.json").unlink()

    def test_launch_injection_cannot_pass_as_native(self):
        value = json.loads(self.launch.read_text())
        value["argv"] += ["--add-dir", str(self.skills)]
        self.launch.write_text(json.dumps(value), encoding="utf-8")
        self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_relative_frozen_path_in_task_cannot_pass_as_native(self):
        self.task.write_text("请读取 ../skills 中规则再审查。", encoding="utf-8")
        self.update_launch_task()
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_repo_name_in_legitimate_workspace_and_output_paths_is_not_injection(self):
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (0, "observed"))
        self.assertIn("/story-skill/", str(self.workspace))

    def test_short_or_equal_path_options_do_not_hide_skill_name_in_other_arguments(self):
        for argv in (["codex", "exec", f"--cd={self.workspace}", f"--output-last-message={self.root / 'final.txt'}"],
                     ["codex", "exec", "-C", str(self.workspace), "-o", str(self.root / "final.txt")]):
            with self.subTest(argv=argv):
                value = json.loads(self.launch.read_text()); value["argv"] = argv
                self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (0, "observed"))
                (self.root / "review-check.json").unlink()

    def test_relative_working_directory_is_resolved_from_recorded_startup_cwd(self):
        for args in (["--cd", "story-skill"], ["--cd=story-skill"], ["-C", "story-skill"]):
            with self.subTest(args=args):
                value = json.loads(self.launch.read_text())
                value.update(argv=["codex", "exec", *args, "--json"], cwd=str(self.workspace.parent))
                self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (0, "observed"))
                (self.root / "review-check.json").unlink()

    def test_relative_directory_without_absolute_startup_cwd_is_unverified(self):
        for args, cwd in ((["--cd", "story-skill"], None), (["--cd=story-skill"], None),
                          (["-C", "story-skill"], "some-relative-directory"), (["--cd", "../skills"], None)):
            with self.subTest(args=args, cwd=cwd):
                value = json.loads(self.launch.read_text())
                value.update(argv=["codex", "exec", *args, "--json"], cwd=cwd)
                self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (2, "unverified"))
                self.assertIn("ambiguous", result["invocation"]["issues"][0])
                (self.root / "review-check.json").unlink()

    def test_relative_directory_resolution_preserves_actual_frozen_skill_path(self):
        value = json.loads(self.launch.read_text())
        value.update(argv=["codex", "exec", "-C", "skills/story-skill-review"], cwd=str(self.workspace.parent))
        self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_real_task_skill_name_survives_confirmed_relative_working_directory(self):
        self.task.write_text("请用 $story-skill-plan 规划。", encoding="utf-8")
        self.update_launch_task()
        value = json.loads(self.launch.read_text())
        value.update(argv=["codex", "exec", "--cd=story-skill"], cwd=str(self.workspace.parent))
        self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_config_content_cannot_escape_via_relative_directory_or_option_like_value(self):
        for config in ('developer_instructions="use $story-skill-plan"',
                       f'--output-last-message={self.workspace}/$story-skill-plan'):
            with self.subTest(config=config):
                value = json.loads(self.launch.read_text())
                value.update(argv=["codex", "exec", "--cd", "story-skill", "--config", config], cwd=str(self.workspace.parent))
                self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))
                (self.root / "review-check.json").unlink()

    def test_config_injection_is_not_masked_as_a_path_option(self):
        value = json.loads(self.launch.read_text())
        value["argv"] += ["--config", f'developer_instructions="--output-last-message {self.workspace}; use $story-skill-plan"']
        self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_explicit_dollar_skill_after_legitimate_workspace_reference_is_preserved(self):
        self.task.write_text(f"工作目录：{self.workspace}\n请用 $story-skill-plan 完成规划。", encoding="utf-8")
        self.update_launch_task()
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_skill_path_in_output_slot_cannot_escape_injection_detection(self):
        value = json.loads(self.launch.read_text())
        value["argv"] += ["--output-last-message", str(self.skills / self.skill_name)]
        self.launch.write_text(json.dumps(value)); self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_workspace_child_skill_file_is_not_masked_as_working_directory(self):
        self.task.write_text(f"请按 {self.workspace}/story-skill-plan/SKILL.md 规划。", encoding="utf-8")
        self.update_launch_task()
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_unfamiliar_repo_named_path_is_ambiguous_instead_of_proven_injection(self):
        self.task.write_text(f"请自然审查。附件路径为 {self.root}/other/story-skill/notes.md。", encoding="utf-8")
        self.update_launch_task()
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (2, "unverified"))
        self.assertIn("ambiguous", result["invocation"]["issues"][0])

    def test_explicitly_provided_frozen_input_is_not_native(self):
        value = json.loads(self.launch.read_text())
        value["provided_skill_paths"] = [str(self.skills / self.skill_name)]
        self.launch.write_text(json.dumps(value), encoding="utf-8")
        self.record["invocation"]["launch"] = self.ref(self.launch)
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (1, "failed"))

    def test_native_requires_launch_and_actual_host_version(self):
        for field in ("launch", "host_skill_snapshot"):
            with self.subTest(field=field):
                removed = self.record["invocation"].pop(field)
                result, code = self.run_review()
                self.assertEqual((code, result["invocation"]["status"]), (2, "unverified"))
                self.record["invocation"][field] = removed
                (self.root / "review-check.json").unlink()

    def test_single_current_host_snapshot_is_not_runtime_version_proof(self):
        self.host_receipt.write_text(json.dumps({"root": str(self.host), "files": {self.skill_name: self.digest(self.target)}}))
        self.record["invocation"]["host_skill_snapshot"] = self.ref(self.host_receipt)
        result, code = self.run_review()
        self.assertEqual((code, result["host_skills"]["status"], result["invocation"]["status"]), (2, "current_only", "unverified"))

    def test_later_target_upgrade_does_not_invalidate_complete_native_cat_return(self):
        self.target.write_text("不同规则\n", encoding="utf-8")
        # Before/after were stable. The complete native output still hashes to that version.
        result, code = self.run_review()
        self.assertEqual((code, result["host_skills"]["status"], result["host_skills"]["current_status"]), (0, "stable", "drift"))
        self.assertEqual(result["claims"][0]["status"], "observed")
        self.assertEqual(result["claims"][0]["evidence"][0]["identity_source"], "complete_native_return")

    def test_later_upgrade_without_old_content_identity_is_unverified(self):
        self.trace_events([self.command(command=f"sed -n '2p' {self.target}", aggregated_output="只审查候选，不采用。\n")])
        self.check_execution()
        self.target.write_text("新规则\n", encoding="utf-8")
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))
        self.assertEqual(result["host_skills"]["current_status"], "drift")

    def test_archived_old_version_supports_a_partial_read_after_upgrade(self):
        archive = self.root / "old-SKILL.md"
        archive.write_bytes(self.target.read_bytes())
        self.trace_events([self.command(command=f"sed -n '2p' {self.target}", aggregated_output="只审查候选，不采用。\n")])
        self.check_execution()
        self.target.write_text("新规则\n", encoding="utf-8")
        self.record["claims"][0]["evidence"][0]["target_snapshot"] = self.ref(archive)
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (0, "observed"))
        self.assertEqual(result["claims"][0]["evidence"][0]["identity_source"], "archived_file")

    def test_host_version_change_during_execution_is_separate_from_later_update(self):
        value = json.loads(self.host_receipt.read_text())
        self.target.write_text("新规则\n", encoding="utf-8")
        value["after"]["files"][self.skill_name] = self.digest(self.target)
        self.host_receipt.write_text(json.dumps(value))
        self.record["invocation"]["host_skill_snapshot"] = self.ref(self.host_receipt)
        result, code = self.run_review()
        self.assertEqual((code, result["host_skills"]["status"], result["host_skills"]["current_status"]), (1, "drift", "same"))
        self.assertEqual(result["invocation"]["status"], "unverified")

    def test_later_unread_core_update_does_not_fail_recorded_review_skill_read(self):
        core = self.host / "story-skill/SKILL.md"
        core.parent.mkdir()
        core.write_text("旧core规则\n", encoding="utf-8")
        value = json.loads(self.host_receipt.read_text())
        for area in ("before", "after"):
            value[area]["files"]["story-skill/SKILL.md"] = self.digest(core)
        self.host_receipt.write_text(json.dumps(value))
        self.record["invocation"]["host_skill_snapshot"] = self.ref(self.host_receipt)
        core.write_text("之后安装的新core规则\n", encoding="utf-8")
        result, code = self.run_review()
        self.assertEqual((code, result["invocation"]["status"]), (0, "observed"))
        self.assertEqual(result["host_skills"]["current_vs_after"], ["story-skill/SKILL.md"])

    def test_agent_completion_claim_is_not_skill_read_evidence(self):
        self.trace_events([{"type": "item.completed", "item": {"id": "message", "type": "agent_message", "text": "只审查候选，不采用。已读取规则。"}}])
        self.check_execution()
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))
        self.assertFalse(result["claims"][0]["evidence"][0]["native_result"])

    def test_command_string_without_returned_text_is_insufficient(self):
        self.trace_events([self.command(aggregated_output="")])
        self.check_execution()
        self.record["claims"][0]["evidence"][0]["quote"] = str(self.target)
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))

    def test_echoed_rule_and_unsupported_compound_commands_are_unverified(self):
        for command in (f"echo '只审查候选，不采用。 cat {self.target}'", f"echo ready; cat {self.target}"):
            with self.subTest(command=command):
                self.trace_events([self.command(command=command)])
                self.check_execution()
                result, code = self.run_review()
                self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))
                (self.root / "review-check.json").unlink()

    def test_failed_command_cannot_prove_read(self):
        self.trace_events([self.command(exit_code=1, status="failed")])
        self.check_execution()
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))

    def test_successful_known_mcp_read_return_is_supported(self):
        self.trace_events([{"type": "item.completed", "item": {"id": "tool-1", "type": "mcp_tool_call", "tool": "read_file",
            "arguments": {"path": str(self.target)}, "result": {"content": [{"type": "text", "text": self.target.read_text()}]},
            "error": None, "status": "completed"}}])
        self.check_execution()
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (0, "observed"))

    def test_failed_tool_return_is_not_supported(self):
        self.trace_events([{"type": "item.completed", "item": {"id": "tool-1", "type": "mcp_tool_call", "tool": "read_file",
            "arguments": {"path": str(self.target)}, "result": {"isError": True, "content": [{"type": "text", "text": self.target.read_text()}]},
            "error": None, "status": "completed"}}])
        self.check_execution()
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["status"]), (2, "unverified"))

    def test_stale_artifact_or_forged_location_fails_reference_check(self):
        for updates in ({"sha256": "0" * 64}, {"line": 900}, {"quote": "原文没有这句话"}, {"path": "../outside.md"}):
            with self.subTest(updates=updates):
                self.record["claims"] = [self.artifact_claim(**updates)]
                result, code = self.run_review()
                self.assertEqual((code, result["claims"][0]["evidence_status"]), (1, "failed"))
                (self.root / "review-check.json").unlink()

    def test_changed_artifact_after_check_invalidates_old_review(self):
        self.record["claims"].append(self.artifact_claim())
        (self.workspace / "candidate.md").write_text("改稿\n", encoding="utf-8")
        result, code = self.run_review()
        self.assertEqual(code, 1)
        self.assertTrue(result["integrity"]["current_workspace"])
        self.assertEqual(result["claims"][1]["evidence_status"], "failed")

    def test_empty_artifact_is_not_observed(self):
        (self.workspace / "candidate.md").write_text(" \n", encoding="utf-8")
        self.check_execution()
        self.record["claims"] = [self.artifact_claim(quote=" ", line=1)]
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][0]["evidence_status"]), (1, "failed"))
        self.assertIn("Artifact is empty", result["claims"][0]["issues"])

    def test_blank_required_output_is_detected_without_an_artifact_claim(self):
        (self.workspace / "candidate.md").write_text(" \n", encoding="utf-8")
        self.check_execution()
        result, code = self.run_review()
        self.assertEqual(code, 1)
        self.assertEqual(result["integrity"]["blank_required_outputs"], ["candidate.md"])

    def test_stale_trace_reference_and_fake_line_fail(self):
        for updates in ({"sha256": "0" * 64}, {"line": 500}, {"quote": "未出现的回执"}):
            with self.subTest(updates=updates):
                self.record["claims"][0]["evidence"][0].update(updates)
                result, code = self.run_review()
                self.assertEqual((code, result["claims"][0]["evidence_status"]), (1, "failed"))
                (self.root / "review-check.json").unlink()
                self.record["claims"][0]["evidence"] = [self.trace_ref(target=str(self.target), target_sha256=self.digest(self.target))]

    def test_modified_raw_trace_is_rejected(self):
        with self.trace.open("a") as stream:
            stream.write('\n{"type":"item.completed","item":{"id":"new","type":"agent_message","text":"changed"}}')
        with self.assertRaisesRegex(ValueError, "Stale native trace"):
            self.run_review()

    def test_old_check_or_baseline_identity_is_rejected(self):
        for field in ("baseline_sha256", "check_sha256"):
            with self.subTest(field=field):
                saved = self.record[field]
                self.record[field] = "0" * 64
                with self.assertRaisesRegex(ValueError, "Stale review"):
                    self.run_review()
                self.record[field] = saved

    def test_tampered_mechanical_check_cannot_become_review_evidence(self):
        value = json.loads(self.checked.read_text())
        value["checks"]["missing_outputs"] = ["not-real.md"]
        self.checked.write_text(json.dumps(value))
        self.record["check_sha256"] = self.digest(self.checked)
        with self.assertRaisesRegex(ValueError, "do not agree"):
            self.run_review()

    def test_source_drift_does_not_rewrite_frozen_or_host_version(self):
        source = self.root / "source"
        source.mkdir()
        (source / "LICENSE").write_text("另一个并发任务改变的源码许可\n", encoding="utf-8")
        result, code = self.run_review(source)
        self.assertEqual((code, result["current_source_vs_frozen"]["status"]), (0, "drift"))
        self.assertEqual(result["invocation"]["status"], "observed")

    def test_changed_frozen_rules_after_check_are_reported(self):
        (self.skills / self.skill_name).write_text("并发修改\n", encoding="utf-8")
        result, code = self.run_review()
        self.assertEqual(code, 1)
        self.assertTrue(result["integrity"]["current_frozen_skills"])

    def test_passed_flag_alone_is_rejected(self):
        self.record["claims"] = [{"id": "success", "passed": True}]
        with self.assertRaisesRegex(ValueError, "passed=true"):
            self.run_review()

    def test_duplicate_claim_ids_are_rejected(self):
        self.record["claims"].append(dict(self.record["claims"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.run_review()

    def test_malformed_invocation_is_an_input_error(self):
        self.record["invocation"] = None
        with self.assertRaisesRegex(ValueError, "Invocation must"):
            self.run_review()

    def test_model_review_cannot_become_authenticated_human_response(self):
        self.record["claims"].append(self.artifact_claim(kind="reader_response"))
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][1]["status"]), (2, "unverified"))

    def test_independent_review_completion_statement_is_unverified(self):
        self.record["claims"].append(self.artifact_claim(kind="independent_review"))
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][1]["status"]), (2, "unverified"))

    def test_other_agent_return_is_distinguished_from_self_or_spawn(self):
        for other, tool, received in (("other-thread", "wait", True), ("trial-thread", "wait", False), ("other-thread", "spawn_agent", False)):
            with self.subTest(other=other, tool=tool):
                self.trace_events([self.command(), {"type": "item.completed", "item": {"id": "return-1", "type": "collab_tool_call",
                    "tool": tool, "status": "completed", "sender_thread_id": "trial-thread", "receiver_thread_ids": [other],
                    "agents_states": {other: {"status": "completed", "message": "独立意见：保留动作。"}}}}])
                self.record["claims"] = [self.record["claims"][0]]
                self.check_execution()
                self.record["claims"].append({"id": "independent", "kind": "independent_review", "status": "observed", "reason": "收到另一代理的具体意见。",
                    "evidence": [self.trace_ref(line=4, quote="独立意见：保留动作。")]})
                result, code = self.run_review()
                self.assertEqual(result["claims"][1]["status"], "unverified")
                self.assertEqual(result["claims"][1]["evidence"][0]["returned_other_agent"], received)
                self.assertEqual(code, 2)
                (self.root / "review-check.json").unlink()

    def test_legacy_return_can_claim_only_other_agent_text_receipt(self):
        self.trace_events([self.command(), {"type": "item.completed", "item": {"id": "return-1", "type": "collab_tool_call",
            "tool": "wait", "status": "completed", "sender_thread_id": "trial-thread", "receiver_thread_ids": ["other-thread"],
            "agents_states": {"other-thread": {"status": "completed", "message": "另一代理意见：保留动作。"}}}}])
        self.check_execution()
        self.record["claims"].append({"id": "received", "kind": "other_agent_return", "status": "observed", "reason": "仅确认另一代理返回文本。",
            "evidence": [self.trace_ref(line=4, quote="另一代理意见：保留动作。")]})
        result, code = self.run_review()
        self.assertEqual((code, result["claims"][1]["status"]), (0, "observed"))
        self.assertIn("reviewed draft", result["claims"][1]["evidence"][0]["scope"])

    def test_output_does_not_overwrite_evidence_or_workspace(self):
        self.run_review()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_review()


if __name__ == "__main__":
    unittest.main()
