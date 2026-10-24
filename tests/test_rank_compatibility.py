"""Frozen pre-versioning evidence must remain readable without relaxing validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_rank
import test_research


rank = test_rank.rank
research = test_research.research
FIXTURES = Path(__file__).parent / "fixtures/rank-samples"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rehash(sample):
    sample["sample_id"] = hashlib.sha256(rank._json(
        {key: value for key, value in sample.items() if key != "sample_id"}).encode("utf-8")).hexdigest()
    return sample


class RankCompatibilityTests(unittest.TestCase):
    def test_frozen_samples_replay_their_original_selection_and_identity(self):
        cases = (("sample-v1-released.json", ["1", "2"],
                  "9bd44cf0c5da363a45d4ece9c967d7de00d27fea47dff393614d7b2d96642844"),
                 ("sample-v1-full-tags.json", ["1", "4"],
                  "ed5ed5997c5c85d55724f7a9b1131abc4b112cc28b815d801413368d72ef6ed6"))
        for name, selected, sample_id in cases:
            with self.subTest(name=name):
                saved = fixture(name)
                original = deepcopy(saved)
                result = rank.validate_sample(saved)
                self.assertEqual(saved, original)
                self.assertEqual(result, original)
                self.assertEqual(result["sample_id"], sample_id)
                self.assertEqual([book["identity"]["value"] for book in result["books"]], selected)

    def test_new_samples_version_the_full_tag_algorithm_without_upgrading_old_samples(self):
        old = fixture("sample-v1-released.json")
        previous_development = fixture("sample-v1-full-tags.json")
        current = rank.build_sample(old["snapshots"], limit=2)
        self.assertEqual((current["schema_version"], current["algorithm_version"]), (2, 2))
        self.assertEqual(current["books"], previous_development["books"])
        self.assertNotIn(current["sample_id"], (old["sample_id"], previous_development["sample_id"]))
        self.assertEqual(rank.validate_sample(current), current)

    def test_display_limits_cannot_change_either_historical_algorithm(self):
        for name in ("sample-v1-released.json", "sample-v1-full-tags.json"):
            saved = fixture(name)
            for limit in (0, 1, 51, 80):
                with self.subTest(name=name, limit=limit), patch.object(rank, "MAX_TAG_ROWS", limit):
                    self.assertEqual(rank.validate_sample(saved), saved)

    def test_rehashed_algorithm_switch_or_selection_tampering_is_still_rejected(self):
        first, second = fixture("sample-v1-released.json"), fixture("sample-v1-full-tags.json")
        for saved, other in ((first, second), (second, first)):
            changes = [lambda value: value.update(method=other["method"]),
                       lambda value: value.update(books=deepcopy(other["books"])),
                       lambda value: value["books"][0].update(reasons=["篡改理由"]),
                       lambda value: value["snapshots"][0]["entries"][0].update(title="篡改来源")]
            for change in changes:
                bad = deepcopy(saved)
                change(bad)
                rehash(bad)
                with self.subTest(sample=saved["sample_id"], change=change), self.assertRaises(rank.RankError):
                    rank.validate_sample(bad)
        current = rank.build_sample(first["snapshots"], limit=2)
        current.update(algorithm_version=1, method=first["method"])
        with self.assertRaises(rank.RankError):
            rank.validate_sample(rehash(current))

    def test_unknown_versions_methods_and_invalid_field_combinations_fail_closed(self):
        old = fixture("sample-v1-released.json")
        current = rank.build_sample(old["snapshots"], limit=2)
        invalid = []
        for schema in (True, 1.0, 0, 3, "2"):
            invalid.append({**old, "schema_version": schema})
        for algorithm in (None, True, 2.0, 0, 3, "2"):
            invalid.append({**current, "algorithm_version": algorithm})
        invalid += [{**old, "algorithm_version": 1}, {**old, "method": "未知抽样方法"},
                    {**old, "kind": "research_decision"},
                    {key: value for key, value in current.items() if key != "algorithm_version"}]
        for bad in invalid:
            with self.subTest(schema=bad["schema_version"], algorithm=bad.get("algorithm_version")), self.assertRaises(rank.RankError):
                rank.validate_sample(rehash(bad))


class ResearchCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-research-compat-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.decision = fixture("decision-v1-released.json")
        self.original = deepcopy(self.decision)
        self.record = self.decision["topics"][0]["hypotheses"][0]["verifications"][0]
        self.assertEqual(self.record["verification_id"],
                         "79afae2aef6f3eca2f16240363669c3103438047ee2b6cda0db4ac51e3bb3014")
        self.input = self.root / "decision.json"
        self.input.write_bytes((FIXTURES / "decision-v1-released.json").read_bytes())

    def cli(self, command, *args):
        process = subprocess.run([sys.executable, "-B", str(test_rank.ROOT / "skills/story-skill/scripts/story.py"),
                                  command, "--input", str(self.input), *map(str, args)],
                                 capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_real_cli_checks_renders_and_appends_without_rebinding_historical_evidence(self):
        original_bytes = self.input.read_bytes()
        checked = self.cli("research-check")
        self.assertEqual((checked["sample_id"], checked["verifications"]), (self.decision["sample"]["sample_id"], 1))
        rendered = self.root / "rendered"
        self.cli("research-render", "--output-dir", rendered)
        self.assertEqual(json.loads((rendered / "decision.json").read_text()), self.original)
        self.assertIn(self.decision["sample"]["method"], (rendered / "planning-candidate.md").read_text())
        evidence = {"schema_version": 1, "kind": "research_evidence", "sample_id": checked["sample_id"],
                    "items": [{key: self.record[key] for key in research.EVIDENCE_FIELDS}]}
        added = deepcopy(evidence["items"][0])
        added.update(book_key=self.decision["sample"]["books"][1]["book_key"],
                     conclusion="contradicts", finding="另一合成片段未表现相同的回应。")
        evidence["items"].append(added)
        evidence_path = self.root / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
        output = self.root / "verified"
        receipt = self.cli("research-verify", "--evidence", evidence_path, "--output-dir", output)
        self.assertEqual(receipt["added_verifications"], 1)
        saved = json.loads((output / "decision.json").read_text())
        self.assertEqual(saved["sample"], self.original["sample"])
        records = saved["topics"][0]["hypotheses"][0]["verifications"]
        self.assertEqual(records[0], self.record)
        self.assertEqual(len(records), 2)
        self.assertEqual(self.input.read_bytes(), original_bytes)

    def test_new_algorithm_sample_cannot_inherit_old_verifications(self):
        sample = rank.build_sample(self.decision["sample"]["snapshots"], limit=2)
        rebound = deepcopy(self.decision)
        rebound["sample"] = sample
        with self.assertRaisesRegex(ValueError, "sample_id|book_key"):
            research.validate_decision(rebound)
        new = research.initialize(sample)
        new["topics"][0]["hypotheses"][0]["claim"] = self.record["hypothesis_claim"]
        evidence = {"schema_version": 1, "kind": "research_evidence",
                    "sample_id": self.decision["sample"]["sample_id"],
                    "items": [{key: self.record[key] for key in research.EVIDENCE_FIELDS}]}
        with self.assertRaisesRegex(ValueError, "sample_id"):
            research.verify(new, evidence)

    def test_unversioned_full_tag_research_also_keeps_its_sample_id(self):
        sample = fixture("sample-v1-full-tags.json")
        decision = research.initialize(sample)
        self.assertEqual(research.validate_decision(decision)["sample"], sample)


if __name__ == "__main__":
    unittest.main()
