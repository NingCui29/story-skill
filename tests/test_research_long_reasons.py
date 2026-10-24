"""Long sampling explanations remain usable without rewriting source evidence."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_research


research = test_research.research
rank = research.rank
ROOT = test_research.ROOT
FIXTURES = ROOT / "tests/fixtures/rank-samples"


def snapshot(tag_count=100, width=180):
    value = rank.template()
    first = value["entries"][0]
    first.update(book_id="first", book_url="https://example.invalid/book/first",
                 tags=[f"标签{i:03}-" + "界" * width + "🧭" for i in range(tag_count)])
    second = deepcopy(first)
    second.update(book_id="second", book_url="https://example.invalid/book/second", rank=2)
    value["entries"].append(second)
    return value


def sample_at_reason_length(length):
    value = snapshot(tag_count=9, width=880)
    initial = rank.build_sample([value], limit=2)
    delta = length - len("；".join(initial["books"][0]["reasons"]))
    for entry in value["entries"]:
        entry["tags"][-1] = "标签008-" + "界" * (880 + delta) + "🧭"
    result = rank.build_sample([value], limit=2)
    assert len("；".join(result["books"][0]["reasons"])) == length
    return result


class LongSamplingReasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-long-reasons-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def cli(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "skills/story-skill/scripts/story.py"),
                                 *map(str, arguments)], cwd=self.root,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_retained_source(self, decision, original):
        self.assertEqual(decision["sample"], original)
        self.assertEqual(decision["sample"]["sample_id"], original["sample_id"])
        for selected, source in zip(decision["topics"][0]["books"], original["books"]):
            reason = selected["reason"]
            self.assertTrue(reason)
            self.assertLessEqual(len(reason), 8000)
            complete = "；".join(source["reasons"])
            if len(complete) <= 8000:
                self.assertEqual(reason, complete)
            else:
                self.assertNotEqual(reason, complete)
                self.assertIn("省略", reason)
                self.assertIn("sample.books", reason)
                self.assertIn(source["book_key"], reason)
                self.assertIn("reasons", reason)
                # The oversized tag explanation must not discard other complete reasons.
                for item in source["reasons"]:
                    if not item.startswith("补充本组重复标签："):
                        self.assertIn(item, reason)

    def test_short_and_exact_8000_character_reasons_are_preserved(self):
        for source in (test_research.sample(), sample_at_reason_length(8000)):
            with self.subTest(length=len("；".join(source["books"][0]["reasons"]))):
                original = deepcopy(source)
                decision = research.initialize(source)
                self.assert_retained_source(decision, original)
                self.assertEqual(source, original)
        # The limit counts Unicode characters, not encoded bytes.
        self.assertGreater(len("；".join(source["books"][0]["reasons"]).encode("utf-8")), 8000)

    def test_8001_and_single_oversized_reason_keep_other_reasons_and_source(self):
        for source in (sample_at_reason_length(8001), rank.build_sample([snapshot()], limit=2)):
            with self.subTest(length=len("；".join(source["books"][0]["reasons"]))):
                original = deepcopy(source)
                decision = research.initialize(source)
                self.assert_retained_source(decision, original)
                self.assertEqual(source, original)
                self.assertEqual(research.validate_decision(decision), decision)

    def test_current_and_both_legacy_formats_complete_real_cli_handoff_and_verification(self):
        source_path = self.write_json(self.root / "snapshot.json", snapshot())
        source_bytes = source_path.read_bytes()
        sample_dir = self.root / "sample"
        self.cli("rank-sample", "--input", source_path, "--limit", "2", "--output-dir", sample_dir)
        current_path = sample_dir / "sample.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual((current["schema_version"], current["algorithm_version"]), (2, 2))
        samples = [("current", current_path)]
        # Historical algorithm equivalence is separately covered by frozen fixtures;
        # these synthetic long samples exercise each supported handoff format.
        for name, algorithm in (("released", 1), ("unversioned-full-tags", 2)):
            old = rank._build_sample([snapshot()], limit=2, algorithm_version=algorithm, legacy=True)
            self.assertEqual(old["schema_version"], 1)
            self.assertNotIn("algorithm_version", old)
            samples.append((name, self.write_json(self.root / (name + ".json"), old)))
        text_path, report_path = self.root / "原文.txt", self.root / "分析.md"
        text_path.write_text("她把灯递过去，对方终于点了头。\n", encoding="utf-8")
        report_path.write_text("递灯之后出现回应；仅分析这一个片段。\n", encoding="utf-8")
        for name, path in samples:
            with self.subTest(format=name):
                sample_bytes = path.read_bytes()
                original = json.loads(sample_bytes)
                self.assertGreater(len(original["books"][0]["reasons"][1]), 8000)
                initialized = self.root / (name + "-initialized")
                self.cli("research-init", "--sample", path, "--output-dir", initialized)
                decision_path = initialized / "decision.json"
                decision = json.loads(decision_path.read_text(encoding="utf-8"))
                self.assert_retained_source(decision, original)
                self.cli("research-check", "--input", decision_path)
                decision["topics"][0]["hypotheses"][0]["claim"] = "人物主动行动之后获得具体回应"
                edited = self.write_json(self.root / (name + "-edited.json"), decision)
                evidence = research.evidence_template(decision)
                evidence["items"] = evidence["items"][:1]
                evidence["items"][0].update(
                    text_ref=str(text_path), report_ref=str(report_path),
                    read_scope={"coverage": "partial", "description": "只读递灯片段"},
                    locators={"text": ["第一行"], "report": ["第一行"]},
                    quote="她把灯递过去，对方终于点了头。", conclusion="supports",
                    finding="原文先展示递灯，随后展示对方的回应。", limitations=["未读后文"])
                evidence_path = self.write_json(self.root / (name + "-evidence.json"), evidence)
                verified = self.root / (name + "-verified")
                receipt = self.cli("research-verify", "--input", edited, "--evidence", evidence_path,
                                   "--output-dir", verified)
                self.assertEqual(receipt["added_verifications"], 1)
                self.cli("research-check", "--input", verified / "decision.json")
                rendered = self.root / (name + "-rendered")
                self.cli("research-render", "--input", verified / "decision.json", "--output-dir", rendered)
                final = json.loads((rendered / "decision.json").read_text(encoding="utf-8"))
                self.assert_retained_source(final, original)
                record = final["topics"][0]["hypotheses"][0]["verifications"][0]
                self.assertEqual(record["sample_id"], original["sample_id"])
                self.assertTrue(record["quote_checked"])
                self.assertEqual(path.read_bytes(), sample_bytes)
        self.assertEqual(source_path.read_bytes(), source_bytes)

    def test_hand_authored_oversized_reasons_are_still_rejected(self):
        decision = research.initialize(test_research.sample())
        decision["topics"][0]["books"][0]["reason"] = "界" * 8001
        with self.assertRaisesRegex(research.ResearchError, "reason.*8000"):
            research.validate_decision(decision)

    def test_existing_verified_decision_is_preserved_by_check_and_render(self):
        original = (FIXTURES / "decision-v1-released.json").read_bytes()
        path = self.root / "existing-decision.json"
        path.write_bytes(original)
        before = json.loads(original)
        checked = self.cli("research-check", "--input", path)
        self.assertEqual(checked["verifications"], 1)
        output = self.root / "existing-rendered"
        self.cli("research-render", "--input", path, "--output-dir", output)
        self.assertEqual(json.loads((output / "decision.json").read_text(encoding="utf-8")), before)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
