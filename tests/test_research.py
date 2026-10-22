"""Offline research handoff tests with real temporary text/report references."""
import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/story-skill/scripts/story_research.py"
SPEC = importlib.util.spec_from_file_location("research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def sample():
    snapshot = research.rank.template()
    snapshot["platform"] = "fixture-platform"
    snapshot["source"].update(url="https://example.com/library", board="更新时间书库", sample_method="读取第一页",
                               limitations=["仅第一页，非人气榜"], cohort="new")
    first = snapshot["entries"][0]
    first.update(book_id="book-a", book_url="https://example.com/book/a", title="雨后借灯", author="样例作者",
                 category="奇幻", tags=["成长"], word_count=12000)
    second = deepcopy(first)
    second.update(book_id="book-b", book_url="https://example.com/book/b", title="长街拾光", rank=2)
    snapshot["entries"].append(second)
    return research.rank.build_sample([snapshot], limit=2)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.text = self.root / "原文.md"
        self.report = self.root / "分析.md"
        # These literal-quote fixtures intentionally contain LF bytes on every OS.
        self.text.write_bytes("第一章\n她用最后一盏灯换来邻居的信任。\n第二章\n承诺的代价仍未兑现。\n".encode("utf-8"))
        self.report.write_bytes("## 场景证据\n第一章行动带来关系变化；仅读两章，后续兑现未知。\n".encode("utf-8"))
        self.decision = research.initialize(sample())
        topic = self.decision["topics"][0]
        topic.update(title="关系回报的奇幻候选", target_reader="偏好人物关系推进的读者", payoff="行动换来可见信任",
                     author_strength="擅长生活场景", differentiation="使用另一套人物与冲突", risk="可能提前耗尽悬念",
                     next_check="继续读第三章，核实承诺代价")
        topic["hypotheses"][0]["claim"] = "主人公的主动付出能产生具体关系回报"
        self.parser = argparse.ArgumentParser()
        research.register_parser(self.parser.add_subparsers(dest="command", required=True))

    def evidence(self, conclusion="supports", book_index=0):
        result = research.evidence_template(self.decision)
        result["items"] = [result["items"][book_index]]
        result["items"][0].update(text_ref=str(self.text), report_ref=str(self.report),
                                  read_scope={"coverage": "partial", "description": "第一至二章，未读后续章节"},
                                  locators={"text": ["第一章第 2 行"], "report": ["场景证据，第 2 行"]},
                                  quote="她用最后一盏灯换来邻居的信任。",
                                  conclusion=conclusion, finding="付出灯具后邻居改变了态度，形成局部关系回报。",
                                  limitations=["只验证两章中的机制，未证明市场需求"])
        return result

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def cli(self, *args):
        return research.run(self.parser.parse_args(args))

    def test_initialization_keeps_original_hypotheses_pending(self):
        decision = research.initialize(sample())
        topic = decision["topics"][0]
        self.assertIsNone(topic["title"])
        self.assertIsNone(topic["hypotheses"][0]["claim"])
        self.assertEqual(topic["hypotheses"][0]["verifications"], [])
        self.assertEqual({book["book_key"] for book in topic["books"]}, {book["book_key"] for book in decision["sample"]["books"]})
        self.assertTrue(all(book["reason"] for book in topic["books"]))
        self.assertIn("仅第一页", research.render(decision))

    def test_cli_end_to_end_preserves_inputs_and_does_not_initialize_novel(self):
        source = self.write("sample.json", sample())
        before = source.read_bytes()
        init_dir = self.root / "initial"
        self.cli("research-init", "--sample", str(source), "--output-dir", str(init_dir))
        self.assertEqual({path.name for path in init_dir.iterdir()},
                         {"decision.json", "analysis-brief.md", "planning-candidate.md", "evidence-template.json"})
        decision_path = self.write("edited-decision.json", self.decision)
        evidence_path = self.write("evidence.json", self.evidence())
        decision_before, evidence_before = decision_path.read_bytes(), evidence_path.read_bytes()
        verified_dir = self.root / "verified"
        result = self.cli("research-verify", "--input", str(decision_path), "--evidence", str(evidence_path),
                          "--output-dir", str(verified_dir))
        self.assertEqual(result["added_verifications"], 1)
        checked = self.cli("research-check", "--input", str(verified_dir / "decision.json"))
        self.assertEqual((checked["verifications"], checked["status"]), (1, "candidate"))
        final_dir = self.root / "rendered"
        self.cli("research-render", "--input", str(verified_dir / "decision.json"), "--output-dir", str(final_dir))
        rendered = (final_dir / "planning-candidate.md").read_text(encoding="utf-8")
        self.assertIn("局部", rendered)
        self.assertIn("未读后续章节", rendered)
        self.assertIn(str(self.text), rendered)
        self.assertIn("尚未采用", rendered)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(decision_path.read_bytes(), decision_before)
        self.assertEqual(evidence_path.read_bytes(), evidence_before)
        self.assertFalse(list(self.root.rglob(".story")))
        self.assertFalse(list(self.root.rglob("*.sqlite*")))

    def test_conflicting_findings_are_retained_without_majority_vote(self):
        supported, _ = research.verify(self.decision, self.evidence())
        contradicted, added = research.verify(supported, self.evidence("contradicts", 1))
        records = contradicted["topics"][0]["hypotheses"][0]["verifications"]
        self.assertEqual(added, 1)
        self.assertEqual({record["conclusion"] for record in records}, {"supports", "contradicts"})
        self.assertEqual(records[0], supported["topics"][0]["hypotheses"][0]["verifications"][0])
        self.assertIn("相互冲突", research.render(contradicted))
        self.assertEqual(self.decision["topics"][0]["hypotheses"][0]["verifications"], [])

    def test_repeating_same_evidence_is_idempotent(self):
        first, added = research.verify(self.decision, self.evidence())
        second, repeated = research.verify(first, self.evidence())
        self.assertEqual((added, repeated), (1, 0))
        self.assertEqual(first, second)

    def test_all_conclusions_remain_explicit_and_reading_scope_does_not_upgrade(self):
        for conclusion in research.VERDICTS:
            with self.subTest(conclusion=conclusion):
                decision, _ = research.verify(self.decision, self.evidence(conclusion))
                record = decision["topics"][0]["hypotheses"][0]["verifications"][0]
                self.assertEqual(record["conclusion"], conclusion)
                self.assertEqual(record["read_scope"]["coverage"], "partial")
                self.assertIn(research.VERDICT_LABELS[conclusion], research.render(decision))

    def test_sample_tampering_and_stale_source_binding_are_rejected(self):
        bad = deepcopy(self.decision)
        bad["sample"]["books"][0]["observation"]["title"] = "冒名作品"
        with self.assertRaises(ValueError):
            research.validate_decision(bad)
        evidence = self.evidence()
        evidence["sample_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "sample_id"):
            research.verify(self.decision, evidence)

    def test_unknown_topic_hypothesis_or_book_is_rejected(self):
        for field in ("topic_id", "hypothesis_id", "book_key"):
            bad = self.evidence()
            bad["items"][0][field] = "unknown"
            with self.subTest(field=field), self.assertRaises(ValueError):
                research.verify(self.decision, bad)

    def test_verified_claim_cannot_be_rewritten_and_pending_claim_does_not_accept_evidence(self):
        verified, _ = research.verify(self.decision, self.evidence())
        verified["topics"][0]["hypotheses"][0]["claim"] = "完全不同的假设"
        with self.assertRaisesRegex(ValueError, "另建 hypothesis_id"):
            research.validate_decision(verified)
        pending = research.initialize(sample())
        with self.assertRaises(ValueError):
            research.verify(pending, self.evidence())

    def test_new_hypothesis_keeps_previous_verified_history(self):
        verified, _ = research.verify(self.decision, self.evidence())
        first = deepcopy(verified["topics"][0]["hypotheses"][0])
        verified["topics"][0]["hypotheses"].append({"hypothesis_id": "hypothesis-2", "claim": "新假设", "verifications": []})
        self.assertEqual(research.validate_decision(verified)["topics"][0]["hypotheses"][0], first)

    def test_local_reference_hash_change_is_detected_without_overwriting_history(self):
        verified, _ = research.verify(self.decision, self.evidence())
        self.text.write_text("已经替换成新版本", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "文件已变化"):
            research.validate_decision(verified)
        path = self.write("decision.json", verified)
        destination = self.root / "rejected"
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.cli("research-render", "--input", str(path), "--output-dir", str(destination))
        self.assertFalse(destination.exists())
        self.assertEqual(path.read_bytes(), original)

    def test_url_references_remain_unfetched_declarations_and_keep_fragment(self):
        evidence = self.evidence()
        evidence["items"][0].update(text_ref="https://example.com/chapter#paragraph-3", report_ref="https://example.com/report#finding")
        verified, _ = research.verify(self.decision, evidence)
        record = verified["topics"][0]["hypotheses"][0]["verifications"][0]
        self.assertEqual(record["reference_hashes"], {"text_ref": None, "report_ref": None})
        self.assertIn("#paragraph-3", record["text_ref"])
        self.assertIn("未联网核验", research.render(verified))
        self.assertFalse(record["quote_checked"])

    def test_local_quote_is_checked_literally_and_insufficient_can_remain_without_quote(self):
        verified, _ = research.verify(self.decision, self.evidence())
        self.assertTrue(verified["topics"][0]["hypotheses"][0]["verifications"][0]["quote_checked"])
        for quote in ("原文没有发生的事", None):
            evidence = self.evidence()
            evidence["items"][0]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                research.verify(self.decision, evidence)
        evidence = self.evidence("insufficient")
        evidence["items"][0]["quote"] = None
        verified, _ = research.verify(self.decision, evidence)
        record = verified["topics"][0]["hypotheses"][0]["verifications"][0]
        self.assertFalse(record["quote_checked"])
        self.assertEqual(record["conclusion"], "insufficient")

    def test_non_utf8_text_cannot_claim_verified_literal_quote(self):
        self.text.write_bytes(b"\xff\xfe\x00\x01")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            research.verify(self.decision, self.evidence())

    def test_multiline_quote_preserves_exact_text_including_line_break(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                quote = "第一章" + newline + "她用最后一盏灯换来邻居的信任。"
                self.text.write_bytes((quote + newline + "第二章" + newline).encode("utf-8"))
                evidence = self.evidence()
                evidence["items"][0]["quote"] = quote
                verified, _ = research.verify(self.decision, evidence)
                record = verified["topics"][0]["hypotheses"][0]["verifications"][0]
                self.assertTrue(record["quote_checked"])
                self.assertEqual(record["quote"], quote)
                mismatched = deepcopy(evidence)
                other_newline = "\r\n" if newline == "\n" else "\n"
                mismatched["items"][0]["quote"] = quote.replace(newline, other_newline)
                with self.assertRaisesRegex(ValueError, "精确匹配"):
                    research.verify(self.decision, mismatched)

    def test_missing_bad_credentialed_and_directory_references_are_rejected(self):
        for ref in (str(self.root / "absent.md"), str(self.root), "relative.md", "file:///etc/passwd",
                    "https://user:secret@example.com/read", "javascript:alert(1)"):
            bad = self.evidence()
            bad["items"][0]["text_ref"] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                research.verify(self.decision, bad)

    def test_incomplete_template_or_missing_scope_locator_cannot_be_verified(self):
        cases = [research.evidence_template(self.decision)]
        for field, value in (("read_scope", {"coverage": "full", "description": None}),
                             ("locators", {"text": [], "report": ["结论"]}), ("conclusion", "proven_hit"),
                             ("finding", None)):
            bad = self.evidence()
            bad["items"][0][field] = value
            cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                research.verify(self.decision, bad)

    def test_history_tampering_duplicate_ids_and_unselected_books_fail(self):
        verified, _ = research.verify(self.decision, self.evidence())
        tampered = deepcopy(verified)
        tampered["topics"][0]["hypotheses"][0]["verifications"][0]["finding"] = "改写旧结论"
        duplicate_topic = deepcopy(self.decision)
        duplicate_topic["topics"].append(deepcopy(duplicate_topic["topics"][0]))
        duplicate_hypothesis = deepcopy(self.decision)
        duplicate_hypothesis["topics"][0]["hypotheses"].append(deepcopy(duplicate_hypothesis["topics"][0]["hypotheses"][0]))
        missing_book = deepcopy(verified)
        missing_book["topics"][0]["books"] = []
        for bad in (tampered, duplicate_topic, duplicate_hypothesis, missing_book):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                research.validate_decision(bad)

    def test_existing_directory_or_file_cannot_be_overwritten(self):
        path = self.write("decision.json", self.decision)
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        for destination in (existing, path):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.cli("research-render", "--input", str(path), "--output-dir", str(destination))
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_existing_and_broken_output_symlinks_cannot_be_overwritten(self):
        path = self.write("decision.json", self.decision)
        existing = self.root / "existing"
        existing.mkdir()
        alias = self.root / "alias"
        broken = self.root / "broken"
        try:
            alias.symlink_to(existing, target_is_directory=True)
            broken.symlink_to(self.root / "absent", target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows did not grant symbolic-link creation privilege")
            raise
        for destination in (alias, broken):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.cli("research-render", "--input", str(path), "--output-dir", str(destination))
        self.assertTrue(alias.is_symlink())
        self.assertTrue(broken.is_symlink())
        self.assertEqual(list(existing.iterdir()), [])
        self.assertFalse((self.root / "absent").exists())

    def test_invalid_input_and_output_limits_fail_before_writing(self):
        path = self.root / "bad.json"
        destination = self.root / "result"
        for raw in ('{"schema_version":1,"schema_version":2}', '{"value":NaN}', '{broken}'):
            path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.cli("research-render", "--input", str(path), "--output-dir", str(destination))
            self.assertFalse(destination.exists())
        path = self.write("decision.json", self.decision)
        with patch.object(research, "MAX_INPUT_BYTES", 1), self.assertRaises(ValueError):
            research.load_json(path)
        with patch.object(research.rank, "MAX_OUTPUT_BYTES", 1), self.assertRaises(ValueError):
            self.cli("research-render", "--input", str(path), "--output-dir", str(destination))
        self.assertFalse(destination.exists())

    def test_import_and_standalone_cli_work_outside_repository(self):
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=self.root,
                                env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("research-verify", result.stdout)
        self.assertIn("拆文", result.stdout)
        error = subprocess.run([sys.executable, str(SCRIPT), "research-check", "--input", str(self.root / "missing.json")],
                               cwd=self.root, env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(error.returncode, 2, error.stderr)
        self.assertFalse(json.loads(error.stderr)["ok"])
        self.assertIn("JSON", json.loads(error.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
