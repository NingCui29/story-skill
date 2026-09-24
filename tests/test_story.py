import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 第1章 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n门开了，雨水冲进屋里。\n"


def card(cid="hero", **extra):
    return {"id": cid, "kind": "character", "text": "沈禾还持有钥匙。", "source": "用户设定",
            "tags": ["沈禾"], **extra}


def plan(**extra):
    return {"volume_dir": "第一卷 雨夜", "goal": "用钥匙换取入口", "stop": "进入门内，不拿到账本",
            "beats": [{"choice": "沈禾交出钥匙", "change": "得到入口并失去退路"}],
            "constraints": ["天亮前返回"], "requires": ["hero"], "tags": ["沈禾"], "length": [20, 120], **extra}


class StoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-test-")
        self.root = Path(self.temp.name) / "中文书目录"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.book.save_notes([card()], 0)
        self.book.save_plan(1, plan(), 1)
        self.draft = self.root / ".story/drafts/第一章.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def delta(self, text=DRAFT, **extra):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾交出钥匙进入门内，承诺天亮前返回。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    name: {"note": "交出钥匙的选择与代价在场，未提前取得账本。", "quote": "沈禾把唯一的钥匙交给守门人。"}
                    for name in story.CHECKS}, "issues": []}, **extra}

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as result:
            function(*args, **kwargs)
        self.assertEqual(result.exception.code, code)
        return result.exception

    def test_reinitialization_never_overwrites(self):
        identity = self.book.meta("id")
        self.assert_error("book_exists", story.Book.create, self.root, "新名字", "long")
        self.assertEqual(self.book.meta("id"), identity)

    def test_missing_book_read_does_not_create_files(self):
        missing = self.root / "不存在"
        self.assert_error("book_missing", story.Book, missing)
        self.assertFalse(missing.exists())

    def test_unicode_length_contract(self):
        self.assertEqual(story.visible_count("# 标题\r\n甲 乙\t，\u200b\ufeff\nA"), 4)
        self.assertEqual(story.visible_count("第一章\n甲。"), 5)

    def test_input_placeholders_and_invalid_range(self):
        self.assert_error("placeholder", story.valid_plan, plan(goal="待补充"))
        self.assert_error("invalid_input", story.valid_plan, plan(length=[120, 20]))
        self.assert_error("invalid_input", story.valid_plan, plan(length=[True, 120]))
        self.assert_error("invalid_input", story.valid_card, card(critical="yes"))

    def test_context_pins_critical_explicit_and_due_cards(self):
        notes = [card("limit", critical=True, tags=[], text="不能隔空开门"),
                 card("debt", kind="hook", due=1, tags=[], text="天亮前带回账本"),
                 card("later", kind="hook", due=10, tags=[]),
                 card("closed", status="resolved", tags=[])]
        self.book.save_notes(notes, 2)
        self.book.save_plan(1, plan(requires=["hero", "closed"]), 3)
        result = self.book.context(1)
        self.assertEqual({c["id"] for c in result["required_cards"]}, {"hero", "closed", "limit", "debt"})
        self.assertEqual(result["omitted_optional_count"], 1)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))

    def test_context_fails_instead_of_truncating_required_constraints(self):
        error = self.assert_error("budget_exceeded", self.book.context, 1, 300)
        self.assertGreater(error.details["minimum_bytes"], 300)
        self.assertEqual(self.book.meta("revision"), 2)

    def test_optional_context_stays_bounded_and_deterministic(self):
        self.book.save_notes([card(f"extra{i}", text="遥远的旧日。" * 40) for i in range(60)], 2)
        a = self.book.context(1, 3000)
        self.assertLessEqual(len(story.dumps(a).encode("utf-8")), 3000)
        self.assertGreater(a["omitted_optional_count"], 50)
        self.assertEqual(a, self.book.context(1, 3000))

    def test_missing_required_card_is_not_silently_skipped(self):
        self.book.save_plan(1, plan(requires=["missing"]), 2)
        self.assert_error("missing_required_cards", self.book.context, 1)
        self.assert_error("missing_required_cards", self.book.commit, 1, self.draft, self.delta())
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_commit_retry_is_idempotent_and_exported(self):
        delta = self.delta()
        first = self.book.commit(1, self.draft, delta)
        second = self.book.commit(1, self.draft, delta)
        self.assertTrue(first["exports_complete"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(encoding="utf-8"), DRAFT)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM chapters").fetchone()[0], 1)

    def test_cross_book_delta_rejected(self):
        other_root = self.root.parent / "另一书"
        story.Book.create(other_root, "另一书", "long")
        other = story.Book(other_root)
        try:
            other.save_notes([card()], 0)
            other.save_plan(1, plan(), 1)
            self.assert_error("wrong_book", other.commit, 1, self.draft, self.delta())
            self.assertEqual(other.meta("last_chapter"), 0)
        finally:
            other.close()

    def test_stale_state_requires_rechecking(self):
        delta = self.delta()
        other = story.Book(self.root)
        try:
            other.save_notes([card(text="钥匙已经折断")], 2)
        finally:
            other.close()
        self.assert_error("stale_revision", self.book.commit, 1, self.draft, delta)
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_stale_review_and_fabricated_evidence_rejected(self):
        old = self.delta()
        old["review"]["draft_sha256"] = "wrong"
        self.assert_error("stale_review", self.book.commit, 1, self.draft, old)
        bad = self.delta(changes=[card(text="拿到账本", quote="她已经拿到了账本。")])
        self.assert_error("invalid_evidence", self.book.commit, 1, self.draft, bad)
        bad = self.delta()
        bad["review"]["checks"]["causality"]["quote"] = "不存在的句子"
        self.assert_error("invalid_evidence", self.book.commit, 1, self.draft, bad)
        self.assertEqual(self.book.meta("revision"), 2)

    def test_blockers_and_length_fail_before_state_mutation(self):
        bad = self.delta()
        bad["review"]["issues"] = [{"severity": "blocker", "issue": "提前兑现终局"}]
        self.assert_error("review_blocker", self.book.commit, 1, self.draft, bad)
        self.book.save_plan(1, plan(length=[1000, 2000]), 2)
        self.assert_error("lint_failed", self.book.commit, 1, self.draft, self.delta())
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_card_change_and_chapter_commit_are_atomic(self):
        delta = self.delta(changes=[{"id": "hero", "text": "沈禾已交出钥匙", "quote": "沈禾把唯一的钥匙交给守门人。"}])
        original = self.book.cards()
        with patch.object(self.book, "queue_artifact", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.book.commit(1, self.draft, delta)
        self.assertEqual(self.book.cards(), original)
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_export_failure_preserves_commit_and_can_recover(self):
        delta = self.delta()
        with patch.object(story, "atomic_write", side_effect=OSError("disk temporarily unavailable")):
            result = self.book.commit(1, self.draft, delta)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(self.book.meta("last_chapter"), 1)
        self.assertEqual(self.book.status()["pending_export_count"], 1)
        self.assert_error("exports_unresolved", self.book.context, 1)
        self.book.close()
        self.book = story.Book(self.root)
        recovered = self.book.commit(1, self.draft, delta)
        self.assertTrue(recovered["idempotent"])
        self.assertTrue(recovered["exports_complete"])

    def test_outside_edits_are_never_overwritten(self):
        target = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        target.parent.mkdir(parents=True)
        target.write_text("用户原稿", encoding="utf-8")
        self.assert_error("export_conflict", self.book.commit, 1, self.draft, self.delta())
        self.assertEqual(target.read_text(encoding="utf-8"), "用户原稿")
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_outside_edits_after_commit_are_visible_and_block_continuation(self):
        self.book.commit(1, self.draft, self.delta())
        target = self.root / self.book.chapter_path(1)
        target.write_text("用户后改稿", encoding="utf-8")
        self.assertEqual(self.book.status()["changed_export_count"], 1)
        self.assert_error("exports_unresolved", self.book.context, 1)
        self.assert_error("export_conflict", self.book.export)
        self.assertEqual(target.read_text(encoding="utf-8"), "用户后改稿")

    def test_replace_last_restores_removed_changes_and_keeps_history(self):
        delta = self.delta(changes=[{"id": "hero", "text": "已经交出钥匙", "quote": "沈禾把唯一的钥匙交给守门人。"},
                                    card("debt", kind="hook", text="答应带回账本", due=2, quote="她答应在天亮之前带回账本。")])
        self.book.commit(1, self.draft, delta)
        baseline = self.book.context(1)
        self.assertEqual(baseline["required_cards"][0]["text"], "沈禾还持有钥匙。")
        revised = DRAFT + "她决定先确认守门人的身份。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        self.book.commit(1, self.draft, self.delta(revised), replace_last=True)
        self.assertNotIn("debt", self.book.cards())
        self.assertEqual(self.book.cards()["hero"]["text"], "沈禾还持有钥匙。")
        event = self.book.db.execute("SELECT data FROM events WHERE kind='replace_chapter'").fetchone()[0]
        previous_sha = json.loads(event)["previous"]["body_sha256"]
        self.assertEqual(self.book.db.execute("SELECT text FROM core_objects WHERE sha=?", (previous_sha,)).fetchone()[0], DRAFT)

    def test_replace_detects_intervening_card_edit(self):
        self.book.commit(1, self.draft, self.delta(changes=[{"id": "hero", "text": "已交出钥匙", "quote": "沈禾把唯一的钥匙交给守门人。"}]))
        self.book.save_notes([card(text="用户重新指定钥匙状态")], self.book.meta("revision"))
        self.assert_error("revised_state_conflict", self.book.commit, 1, self.draft, self.delta(), True)

    def test_cannot_skip_chapter_or_replace_earlier_history(self):
        self.assert_error("chapter_order", self.book.commit, 2, self.draft, self.delta())
        self.book.commit(1, self.draft, self.delta())
        self.book.save_plan(2, plan(), self.book.meta("revision"))
        self.book.commit(2, self.draft, self.delta())
        self.assert_error("chapter_order", self.book.commit, 1, self.draft, self.delta(), True)

    def test_adopt_preserves_source_and_does_not_fabricate_history(self):
        original = self.draft.read_bytes()
        result = self.book.adopt(108, self.draft, "末章有明确交接，钥匙已经交出", 2, volume_dir="第一卷 雨夜")
        self.assertEqual(result["adopted_through"], 108)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM chapters").fetchone()[0], 1)
        self.assertEqual(self.book.status()["next_chapter"], 109)
        self.assertEqual(self.draft.read_bytes(), original)
        self.assert_error("adopt_nonempty", self.book.adopt, 109, self.draft, "重复导入", 3)

    def test_atomic_export_failure_after_replace_can_retry(self):
        self.book.commit(1, self.draft, self.delta())
        revised = DRAFT + "她没有回头。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        delta = self.delta(revised)
        with patch.object(story, "atomic_write", side_effect=OSError("temporary failure")):
            result = self.book.commit(1, self.draft, delta, True)
        self.assertFalse(result["exports_complete"])
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(encoding="utf-8"), DRAFT)
        self.assertTrue(self.book.commit(1, self.draft, delta, True)["exports_complete"])

    def test_parallel_identical_submissions_only_commit_once(self):
        delta = self.delta()
        barrier = threading.Barrier(2)
        def attempt():
            book = story.Book(self.root)
            try:
                barrier.wait(timeout=10)
                return book.commit(1, self.draft, delta)
            finally:
                book.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(result["idempotent"] for result in results), 1)
        self.assertEqual(self.book.meta("last_chapter"), 1)
        self.assertEqual(self.book.meta("revision"), 3)

    def test_recall_output_is_bounded(self):
        self.book.save_notes([card(f"r{i}", text="线索" * 500) for i in range(20)], 2)
        result = self.book.recall("线索", 1000)
        self.assertEqual(result["omitted"], 20)
        self.assertLessEqual(len(story.dumps(result).encode("utf-8")), 1000)

    def test_path_escape_and_linked_managed_directory(self):
        self.assert_error("path_escape", story.safe_path, self.root, "../other")
        external = self.root.parent / "外部"
        external.mkdir()
        target = self.root / "chapters"
        try:
            target.symlink_to(external, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Link creation is unavailable")
            environment = dict(os.environ, STORY_SKILL_TEST_LINK=str(target), STORY_SKILL_TEST_TARGET=str(external))
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                     "New-Item -ItemType Junction -Path $env:STORY_SKILL_TEST_LINK -Target $env:STORY_SKILL_TEST_TARGET -ErrorAction Stop | Out-Null"],
                                    env=environment, capture_output=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                self.skipTest("Neither symlink nor Windows junction creation is available")
        with self.assertRaises(story.StoryError) as result:
            self.book.commit(1, self.draft, self.delta())
        self.assertIn(result.exception.code, ("linked_path", "path_escape"))
        self.assertEqual(list(external.iterdir()), [])

    def source_fixture(self):
        text = "前言：雨一直下。\r\n第1章 入门\r\n" + "沈禾在雨中等候。\r\n" * 35 + "番外：守门人的信\r\n那把钥匙已被收起。\r\n第2章 天亮\r\n门重新关上。"
        path = self.root / "原文.txt"
        path.write_bytes(text.encode("utf-8"))
        result = self.book.ingest(path, "partial", chunk_chars=256)
        return result["source"], path, text

    def analysis_for(self, chunk):
        quote = chunk["text"].strip().splitlines()[-1]
        return {"chunk_sha256": chunk["sha"], "summary": "可见片段展示门与钥匙的行动线索。",
                "findings": [{"kind": "线索", "claim": "本块保留了可定位的文本线索。", "quote": quote}]}

    def test_chunking_preserves_every_character_and_bonus_titles(self):
        sid, path, original = self.source_fixture()
        rows = self.book.db.execute("SELECT * FROM chunks WHERE source=? ORDER BY ordinal", (sid,)).fetchall()
        rebuilt = "".join(original[row["start"]:row["end"]] for row in rows)
        self.assertEqual(rebuilt, original)
        self.assertTrue(any("番外" in row["title"] for row in rows))
        self.assertTrue(all(row["end"] - row["start"] <= 256 for row in rows))
        self.assertEqual(path.read_bytes().decode("utf-8"), original)
        self.assertTrue(self.book.coverage(sid)["text_coverage_contiguous"])

    def test_ingest_and_record_resume_idempotently(self):
        sid, path, _ = self.source_fixture()
        first = self.book.next_chunks(sid)["chunks"][0]
        analysis = self.analysis_for(first)
        self.book.record(sid, first["ordinal"], analysis)
        rev = self.book.meta("revision")
        self.assertTrue(self.book.record(sid, first["ordinal"], analysis)["idempotent"])
        self.assertTrue(self.book.ingest(path, "partial")["idempotent"])
        self.assertEqual(self.book.meta("revision"), rev)
        self.assertEqual(self.book.next_chunks(sid)["chunks"][0]["ordinal"], 2)
        changed = copy.deepcopy(analysis)
        changed["summary"] = "更精确的摘要"
        self.assert_error("analysis_exists", self.book.record, sid, 1, changed)

    def test_new_session_can_discover_source_id_and_resume_without_chat_history(self):
        sid, _, _ = self.source_fixture()
        first = self.book.next_chunks(sid)["chunks"][0]
        self.book.record(sid, first["ordinal"], self.analysis_for(first))
        self.book.close()
        self.book = story.Book(self.root)
        saved = self.book.status()["recent_sources"][0]
        self.assertEqual(saved["source"], sid)
        self.assertEqual(saved["next_chunk"], 2)
        self.assertIsNone(saved["report_path"])
        self.assertEqual(self.book.list_sources()["results"][0]["analyzed"], 1)

    def test_wrong_analysis_hash_quote_and_incomplete_report(self):
        sid, _, _ = self.source_fixture()
        first = self.book.next_chunks(sid)["chunks"][0]
        bad = self.analysis_for(first)
        bad["chunk_sha256"] = "bad"
        self.assert_error("source_hash_mismatch", self.book.record, sid, 1, bad)
        bad = self.analysis_for(first)
        bad["findings"][0]["quote"] = "未发生的完整大结局"
        self.assert_error("invalid_evidence", self.book.record, sid, 1, bad)
        report = self.root / "report.md"
        report.write_text("报告" * 30, encoding="utf-8")
        self.assert_error("analysis_incomplete", self.book.report, sid, report)
        self.assertEqual(self.book.coverage(sid)["analyzed"], 0)

    def test_analysis_budget_fails_without_skipping_pending_chunk(self):
        sid, _, _ = self.source_fixture()
        self.assert_error("budget_exceeded", self.book.next_chunks, sid, 2, 256)
        self.assertEqual(self.book.coverage(sid)["next_chunk"], 1)

    def test_completed_partial_source_is_not_reported_as_whole_book(self):
        sid, _, _ = self.source_fixture()
        while self.book.coverage(sid)["pending"]:
            for chunk in self.book.next_chunks(sid)["chunks"]:
                self.book.record(sid, chunk["ordinal"], self.analysis_for(chunk))
        analysis_baseline = self.book.findings(sid)["analysis_sha256"]
        report = self.root / "reviewed-report.md"
        report.write_text("这份报告只讨论导入的片段。门、钥匙与天亮构成可见的限制，角色以交出资源换取行动机会。番外保留了守门人的另一侧信息。", encoding="utf-8")
        result = self.book.report(sid, report, analysis_baseline)
        content = Path(result["report"]).read_text(encoding="utf-8")
        self.assertIn("source_coverage: partial", content)
        self.assertTrue(result["complete_for_imported_text"])
        self.assertTrue(result["exports_complete"])
        findings = self.book.findings(sid, 0, 1)
        self.assertEqual(findings["next_offset"], 1)
        next_page = self.book.findings(sid, findings["next_offset"], 1)
        self.assertEqual(next_page["results"][0]["chunk"], 2)

    def test_source_read_uses_exact_offsets_and_budget(self):
        sid, _, text = self.source_fixture()
        self.assertEqual(self.book.source_read(sid, 2, 10, 1000)["text"], text[2:10])
        self.assert_error("invalid_range", self.book.source_read, sid, 10, 2, 1000)

    def test_no_heading_and_long_single_line_are_lossless(self):
        text = "雨" * 900
        chunks = story.split_source(text, 256)
        self.assertEqual("".join(text[a:b] for a, b, _ in chunks), text)
        self.assertEqual(len(chunks), 4)

    def test_cli_unicode_input_and_template_array(self):
        result = subprocess.run([sys.executable, str(TOOL), "status", "--book", str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["title"], "门后的雨")
        result = subprocess.run([sys.executable, str(TOOL), "template", "notes"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(json.loads(result.stdout), list)

    def test_crlf_draft_hash_matches_lint_and_export_bytes(self):
        text = DRAFT.replace("\n", "\r\n")
        self.draft.write_bytes(text.encode("utf-8"))
        delta = self.delta(text)
        self.assertEqual(self.book.lint(1, self.draft)["draft_sha256"], delta["review"]["draft_sha256"])
        self.book.commit(1, self.draft, delta)
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), self.draft.read_bytes())


if __name__ == "__main__":
    unittest.main()
