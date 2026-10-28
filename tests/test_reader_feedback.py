"""Reader observations keep actual opportunities, provenance and corrections separate."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/story-skill/scripts/story_reader_feedback.py"
SPEC = importlib.util.spec_from_file_location("reader_feedback", SCRIPT)
feedback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedback)


class ReaderFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "第一轮.md"
        self.second = self.root / "第二轮.md"
        self.first.write_text("灯终于亮了。\n她把钥匙放进自己口袋。\n", encoding="utf-8")
        self.second.write_text("她用昨天留下的钥匙打开门。\n", encoding="utf-8")
        self.model = self.root / "模型原始反馈.txt"
        self.model.write_text("这里只是模型观察，不是真人反应。", encoding="utf-8")
        self.plan = {"study_id": "trial-1", "question": "已有回报能否产生下一次阅读期待？",
            "participants": [{"participant_id": "p01", "source_kind": "human", "cohort": "目标读者"},
                             {"participant_id": "p02", "source_kind": "human", "cohort": "目标读者"},
                             {"participant_id": "m01", "source_kind": "model", "cohort": "模型观察"}],
            "rounds": [{"round_id": "r1", "manuscript": str(self.first), "reading_scope": "第一章全文",
                        "available_at": "2026-10-01T10:00:00+08:00", "closes_at": "2026-10-02T10:00:00+08:00",
                        "delivery": "本地文件", "reminder_policy": "不主动提醒", "follows_round_id": None},
                       {"round_id": "r2", "manuscript": str(self.second), "reading_scope": "第二章全文",
                        "available_at": "2026-10-03T10:00:00+08:00", "closes_at": "2026-10-04T10:00:00+08:00",
                        "delivery": "本地文件", "reminder_policy": "不主动提醒", "follows_round_id": "r1"}]}
        self.study = feedback.initialize(self.plan)
        self.parser = argparse.ArgumentParser()
        feedback.register_parser(self.parser.add_subparsers(dest="command", required=True))

    def observation(self, participant="p01", round_id="r1", **updates):
        record = deepcopy(feedback.observation_template(self.study)["items"][0])
        record.update(participant_id=participant, round_id=round_id,
                      observed_at="2026-10-02T12:00:00+08:00" if round_id == "r1" else "2026-10-04T12:00:00+08:00",
                      opportunity="provided", provided_at="2026-10-01T11:00:00+08:00" if round_id == "r1" else "2026-10-03T11:00:00+08:00",
                      reminder="none", reading="completed", evidence_kind="participant_report", reader_words="读完了。")
        if participant == "m01":
            record.update(evidence_kind="model_output", evidence_file=str(self.model), reader_words="模型说明。")
        record.update(updates)
        return record

    def add(self, study, *items):
        return feedback.record_observations(study, {"plan_sha256": study["plan_sha256"], "items": list(items)})

    def summary(self, study, as_of="2026-10-05T00:00:00Z"):
        return feedback.summarize(study, as_of)

    def total(self, summary, name, **selector):
        return sum(r["counts"].get(name, 0) for r in summary["groups"]
                   if all(r.get(k) == v for k, v in selector.items()))

    def write(self, filename, data):
        path = self.root / filename
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_plan_is_frozen_without_claiming_participation(self):
        summary = self.summary(self.study)
        self.assertEqual(self.total(summary, "unobserved"), 6)
        self.assertEqual(self.total(summary, "started"), 0)
        self.assertNotEqual(self.study["plan"]["rounds"][0]["manuscript"], str(self.first))
        self.assertEqual(self.study["records"], [])

    def test_missing_opportunity_does_not_count_as_dropout(self):
        study, _ = self.add(self.study, self.observation(opportunity="not_provided", provided_at=None, reading="not_started"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "no_opportunity"), 1)
        self.assertEqual(self.total(summary, "not_started_at_observation"), 0)
        self.assertEqual(self.total(summary, "provided"), 0)

    def test_no_return_without_prior_reading(self):
        study, _ = self.add(self.study, self.observation(round_id="r2"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "prior_reading_unverified"), 1)
        self.assertEqual(self.total(summary, "reported_return"), 0)
        study, _ = self.add(study, self.observation())
        self.assertEqual(self.total(self.summary(study), "reported_return"), 1)

    def test_start_is_distinct_from_completion(self):
        study, _ = self.add(self.study, self.observation(reading="started"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "started"), 1)
        self.assertEqual(self.total(summary, "completed"), 0)

    def test_open_window_and_expired_unobserved_status_are_distinct(self):
        study, _ = self.add(self.study, self.observation(reading="not_started", observed_at="2026-10-01T12:00:00+08:00"))
        self.assertEqual(self.total(self.summary(study, "2026-10-01T14:00:00+08:00"), "window_open"), 1)
        self.assertEqual(self.total(self.summary(study), "reading_unknown_at_cutoff"), 1)
        self.assertEqual(self.total(self.summary(study), "not_started_at_observation"), 0)

    def test_after_cutoff_report_does_not_infer_cause(self):
        study, _ = self.add(self.study, self.observation(reading="not_started", reader_words="这两天没有时间。"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "not_started_at_observation"), 1)
        self.assertNotIn("retention_rate", summary)
        self.assertNotIn("dropout", feedback.render(study, summary))

    def test_late_opportunity_has_separate_group_even_if_read(self):
        study, _ = self.add(self.study, self.observation(), self.observation("p02", provided_at="2026-10-02T11:00:00+08:00"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "late_opportunity"), 1)
        self.assertEqual(self.total(summary, "started", opportunity_timing="late"), 1)
        self.assertEqual(self.total(summary, "started", opportunity_timing="in_window"), 1)

    def test_human_model_unknown_and_reminders_stay_separate(self):
        plan = deepcopy(self.plan)
        plan["participants"].append({"participant_id": "u01", "source_kind": "unknown", "cohort": "未知"})
        study = feedback.initialize(plan)
        study, _ = self.add(study, self.observation(), self.observation("p02", reminder="sent"), self.observation("m01"),
                           self.observation("u01", opportunity="unknown", provided_at=None, reading="unknown", evidence_kind="unknown"))
        summary = self.summary(study)
        self.assertEqual(self.total(summary, "started", source_kind="human", reminder="none"), 1)
        self.assertEqual(self.total(summary, "started", source_kind="human", reminder="sent"), 1)
        self.assertEqual(self.total(summary, "started", source_kind="model"), 1)
        self.assertEqual(self.total(summary, "started", source_kind="unknown"), 0)

    def test_source_kind_cannot_be_forged_by_observation_fields(self):
        for record in (self.observation(evidence_kind="model_output", evidence_file=str(self.model)),
                       self.observation("m01", evidence_kind="participant_report"),
                       self.observation(reading="completed", evidence_kind="unknown")):
            with self.subTest(record=record), self.assertRaises(feedback.FeedbackError):
                self.add(self.study, record)

    def test_reports_keep_verbatim_text_and_hypothesis_separate(self):
        words = "我想看看她怎么用钥匙。\n但第二段有些拖。"
        study, _ = self.add(self.study, self.observation(reader_words=words, text_quote="她把钥匙放进自己口袋。",
                           text_locator="第一章第 2 行", revision_hypothesis="可能需要压缩重复说明。"))
        self.assertEqual(study["records"][0]["reader_words"], words)
        rendered = feedback.render(study, self.summary(study))
        self.assertIn(words, rendered)
        self.assertIn("修改假设（尚未证明）", rendered)

    def test_false_quote_and_quote_without_locator_are_rejected(self):
        for record in (self.observation(text_quote="不存在的原句", text_locator="第一章"),
                       self.observation(text_quote="灯终于亮了。")):
            with self.assertRaises(feedback.FeedbackError):
                self.add(self.study, record)

    def test_corrections_are_append_only_and_repeated_import_is_idempotent(self):
        first = self.observation(reading="started")
        study, added = self.add(self.study, first)
        repeated, added_again = self.add(study, first)
        self.assertEqual((added, added_again), (1, 0))
        self.assertEqual(study, repeated)
        correction = self.observation(observed_at="2026-10-02T13:00:00+08:00", supersedes=study["records"][0]["record_id"])
        revised, _ = self.add(study, correction)
        self.assertEqual(revised["records"][0], study["records"][0])
        self.assertEqual(len(revised["records"]), 2)
        self.assertEqual(self.total(self.summary(revised), "completed"), 1)
        self.assertEqual(self.total(self.summary(revised, "2026-10-02T12:30:00+08:00"), "completed"), 0)

    def test_correction_cannot_replace_other_person_or_old_branch(self):
        study, _ = self.add(self.study, self.observation())
        prior = study["records"][0]["record_id"]
        with self.assertRaises(feedback.FeedbackError):
            self.add(study, self.observation("p02", supersedes=prior))
        with self.assertRaises(feedback.FeedbackError):
            self.add(study, self.observation(reader_words="不同反馈", supersedes=None))
        revised, _ = self.add(study, self.observation(reader_words="修正", supersedes=prior))
        with self.assertRaises(feedback.FeedbackError):
            self.add(revised, self.observation(reader_words="过期修正", supersedes=prior))

    def test_source_change_warns_without_destroying_old_feedback(self):
        study, _ = self.add(self.study, self.observation())
        before = deepcopy(study)
        self.first.write_text("完全不同的新稿", encoding="utf-8")
        summary = self.summary(study)
        self.assertEqual(summary["status"], "source_changed")
        self.assertEqual(study, before)
        self.assertIn("changed", feedback.render(study, summary))
        with self.assertRaisesRegex(feedback.FeedbackError, "稿件文件已变化"):
            self.add(study, self.observation("p02"))

    def test_missing_source_and_changed_evidence_remain_visible(self):
        study, _ = self.add(self.study, self.observation("m01"))
        self.first.unlink()
        self.model.write_text("后来改写的模型意见", encoding="utf-8")
        warnings = self.summary(study)["source_warnings"]
        self.assertEqual({item["status"] for item in warnings}, {"changed", "unavailable"})

    def test_plan_version_and_record_tampering_are_rejected(self):
        study, _ = self.add(self.study, self.observation())
        altered = deepcopy(study)
        altered["plan"]["rounds"][0]["reading_scope"] = "整书全文"
        with self.assertRaises(feedback.FeedbackError):
            feedback.validate_study(altered)
        altered = deepcopy(study)
        altered["records"][0]["reader_words"] = "我非常满意"
        with self.assertRaises(feedback.FeedbackError):
            feedback.validate_study(altered)
        with self.assertRaises(feedback.FeedbackError):
            feedback.record_observations(study, {"plan_sha256": "0" * 64, "items": [self.observation("p02")]})

    def test_invalid_times_ids_and_opportunities_are_rejected(self):
        mutations = ({"participant_id": []}, {"round_id": "unknown"}, {"observed_at": "2026-10-02"},
                     {"provided_at": "2026-09-01T00:00:00Z"}, {"provided_at": "2026-12-01T00:00:00Z"},
                     {"opportunity": "not_provided"}, {"evidence_file": None, "evidence_kind": "local_record"})
        for update in mutations:
            with self.subTest(update=update), self.assertRaises(feedback.FeedbackError):
                self.add(self.study, self.observation(**update))

    def test_invalid_plan_cycles_duplicate_ids_and_binary_text_are_rejected(self):
        for mutate in (lambda p: p["rounds"][0].update(follows_round_id="r2"),
                       lambda p: p["rounds"][1].update(round_id="r1"),
                       lambda p: p["participants"].append(deepcopy(p["participants"][0])),
                       lambda p: p["rounds"][0].update(closes_at=p["rounds"][0]["available_at"])):
            plan = deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(feedback.FeedbackError):
                feedback.initialize(plan)
        self.first.write_bytes(b"\xff\xfe")
        with self.assertRaises(feedback.FeedbackError):
            feedback.initialize(self.plan)

    def test_timezone_conversion_overflow_is_an_input_error_without_partial_output(self):
        for instant in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            with self.subTest(instant=instant), self.assertRaisesRegex(feedback.FeedbackError, "UTC"):
                self.add(self.study, self.observation(observed_at=instant))
        study_file = self.write("study.json", self.study)
        output = self.root / "out-of-range-report"
        result = subprocess.run([sys.executable, str(SCRIPT), "reader-render", "--input", str(study_file),
                                 "--as-of", "0001-01-01T00:00:00+01:00", "--output-dir", str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stderr)["ok"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(output.exists())

    def test_future_records_are_not_counted_and_versions_do_not_mix(self):
        study, _ = self.add(self.study, self.observation(), self.observation(round_id="r2"))
        early = self.summary(study, "2026-10-02T13:00:00+08:00")
        self.assertEqual(self.total(early, "started"), 1)
        rows = self.summary(study)["groups"]
        self.assertEqual(len({r["manuscript_sha256"] for r in rows}), 2)

    def test_standalone_cli_creates_new_versions_without_novel_state(self):
        plan = self.write("plan.json", self.plan)
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        output = self.root / "round-zero"
        proc = subprocess.run([sys.executable, str(SCRIPT), "reader-init", "--plan", str(plan), "--output-dir", str(output)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        study = json.loads((output / "study.json").read_text(encoding="utf-8"))
        observed = self.write("observations.json", {"plan_sha256": study["plan_sha256"], "items": [self.observation()]})
        next_output = self.root / "round-one"
        result = feedback.run(self.parser.parse_args(["reader-record", "--input", str(output / "study.json"),
             "--observations", str(observed), "--output-dir", str(next_output), "--as-of", "2026-10-05T00:00:00Z"]))
        self.assertEqual(result["added_records"], 1)
        self.assertEqual(json.loads((output / "study.json").read_text())["records"], [])
        self.assertEqual({p.name for p in next_output.iterdir()}, {"study.json", "summary.json", "summary.md", "observation-template.json"})
        for name, raw in before.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        self.assertFalse(list(self.root.rglob("*.sqlite*")))
        self.assertFalse(list(self.root.rglob(".story")))
        with self.assertRaises(feedback.FeedbackError):
            feedback.run(self.parser.parse_args(["reader-render", "--input", str(output / "study.json"), "--output-dir", str(next_output)]))

    def test_duplicate_json_keys_are_rejected(self):
        source = self.root / "bad.json"
        source.write_text('{"study_id":"one","study_id":"two"}', encoding="utf-8")
        with self.assertRaises(feedback.FeedbackError):
            feedback.research.load_json(source)


if __name__ == "__main__":
    unittest.main()
