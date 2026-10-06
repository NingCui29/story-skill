import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("test_benchmark", ROOT / "scripts/benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkReadingContractTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "benchmarks/profiles.json").read_text(encoding="utf-8"))

    def test_each_real_scenario_has_an_audited_reading_contract(self):
        self.assertEqual({profile["id"] for profile in self.config["profiles"]},
                         set(benchmark.REQUIRED_CANDIDATE_FILES))
        self.assertEqual(benchmark.profile_contract_problems(self.config), [])

    def test_writing_and_content_review_cannot_omit_delivery_review(self):
        for profile in self.config["profiles"]:
            if benchmark.CONTENT_REVIEW not in benchmark.REQUIRED_CANDIDATE_FILES[profile["id"]]:
                continue
            with self.subTest(profile=profile["id"]):
                config = copy.deepcopy(self.config)
                changed = next(item for item in config["profiles"] if item["id"] == profile["id"])
                changed["candidate"].remove(benchmark.CONTENT_REVIEW)
                self.assertEqual(benchmark.profile_contract_problems(config),
                                 [f"Missing required candidate file: {profile['id']}: {benchmark.CONTENT_REVIEW}"])

    def test_complete_chapter_review_includes_triggered_scene_and_length_rules(self):
        for profile_id in ("review_conservative", "review_conflict"):
            for required in (benchmark.DRAMA_GUIDE, benchmark.CHAPTER_GUIDE):
                with self.subTest(profile=profile_id, required=required):
                    config = copy.deepcopy(self.config)
                    profile = next(item for item in config["profiles"] if item["id"] == profile_id)
                    profile["candidate"].remove(required)
                    self.assertEqual(benchmark.profile_contract_problems(config),
                                     [f"Missing required candidate file: {profile_id}: {required}"])

    def test_untriggered_platform_and_history_guides_are_not_added(self):
        for profile in self.config["profiles"]:
            self.assertFalse(any(path.endswith(("history.md", "fanqie-content-review.md",
                                                "qimao-content-review.md", "suspense-evidence.md"))
                                 for path in profile["candidate"]))

    def test_benchmark_rejects_omissions_before_loading_tokenizer_or_upstream(self):
        config = copy.deepcopy(self.config)
        config["profiles"][0]["candidate"].remove(benchmark.CONTENT_REVIEW)
        with tempfile.TemporaryDirectory(prefix="story-benchmark-invalid-") as directory:
            root = Path(directory)
            (root / "benchmarks").mkdir()
            (root / "benchmarks/profiles.json").write_text(json.dumps(config), encoding="utf-8")
            with patch.object(benchmark, "ROOT", root), self.assertRaisesRegex(SystemExit, "content-review.md"):
                benchmark.benchmark(root / "missing-upstream", None)

    def test_inventory_fingerprints_raw_bytes_and_counts_normalized_text(self):
        class Encoder:
            def encode(self, text, *, disallowed_special):
                self.assertions = (text, disallowed_special)
                return list(text)

        with tempfile.TemporaryDirectory(prefix="story-benchmark-inventory-") as directory:
            root, encoder = Path(directory), Encoder()
            raw = b"\xef\xbb\xbfhello\r\nworld\r\n"
            (root / "reference.md").write_bytes(raw)
            entry, = benchmark.inventory(root, ["reference.md"], encoder)
        normalized = "hello\nworld\n"
        self.assertEqual(encoder.assertions, (normalized, ()))
        self.assertEqual(entry["bytes"], len(raw))
        self.assertEqual(entry["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry["normalized_text_sha256"], hashlib.sha256(normalized.encode()).hexdigest())
        self.assertEqual(entry["tokens"], len(normalized))


if __name__ == "__main__":
    unittest.main()
