"""Behavioral contract for the read-only local author workbench v1."""
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("workbench_contract_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-workbench-")
        self.root = Path(self.temp.name).resolve() / "中文 书#册%目录"
        self.title = '雨夜 <script>alert("workbench-title")</script> & “账本”'
        story.Book.create(self.root, self.title, "long")
        self.book = story.Book(self.root)
        self.body_tokens = []
        for chapter in (1, 2):
            self.commit(chapter)
        self.account_id = "ACCOUNT-SECRET-DO-NOT-DISPLAY"
        self.remote_book_id = "REMOTE-SECRET-DO-NOT-DISPLAY"
        self.publish_plan = story.publish.prepare(self.book, {
            "platform": "fanqie", "account_id": self.account_id,
            "remote_book_id": self.remote_book_id, "chapters": [1, 2], "mode": "draft",
        }, self.book.meta("revision"), summary=True)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def commit(self, chapter):
        title = f"雨夜 & 账本#{chapter}%"
        token = f"FULL-CHAPTER-{chapter}-MUST-NOT-BE-EMBEDDED"
        text = (f"# 第{chapter}章 {title}\n"
                "沈禾把唯一的钥匙交给守门人。\n"
                "她答应在天亮之前带回账本。\n"
                f"{token}，这句只用于证明工作台没有嵌入整章。\n")
        plan = {
            "title": title, "volume_dir": "第一卷 雨#夜%", "goal": "用钥匙换取入口",
            "stop": "进入门内，不拿到账本", "constraints": ["天亮前返回"],
            "requires": [], "tags": ["沈禾"], "length": [10, 1000],
            "beats": [{"choice": "沈禾交出钥匙", "change": "得到入口并失去退路"}],
        }
        self.book.save_plan(chapter, plan, self.book.meta("revision"))
        draft = self.root / ".story/drafts" / f"第{chapter}章.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(text, encoding="utf-8")
        review = {name: {
            "note": "已核对人物选择、代价、连续性和停笔位置。",
            "quote": "沈禾把唯一的钥匙交给守门人。",
        } for name in story.CHECKS}
        delta = {
            "book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
            "summary": f"第{chapter}章完成钥匙交接，并保留下一章要处理的账本。",
            "changes": [],
            "review": {"draft_sha256": story.digest(text), "checks": review, "issues": []},
        }
        result = self.book.commit(chapter, draft, delta)
        self.assertTrue(result["exports_complete"], result)
        self.body_tokens.append(token)

    def cli(self, command, *arguments):
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), command, "--book", str(self.root), *arguments],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        raw = process.stdout if process.stdout.strip() else process.stderr
        try:
            packet = json.loads(raw)
        except json.JSONDecodeError:
            self.fail(f"CLI did not return JSON: stdout={process.stdout!r} stderr={process.stderr!r}")
        return process, packet

    def test_workbench_links_use_existing_registered_volume_paths(self):
        packet = story.workbench.snapshot(self.book)
        page = story.workbench.render_html(packet)
        for row in packet["chapters"]["results"]:
            expected = self.book.chapter_path(row["chapter"])
            self.assertEqual(row["path"], expected)
            target = self.root / expected
            self.assertTrue(target.is_file())
            self.assertIn(target.as_uri(), page)
        process, cli_packet = self.cli("workbench-snapshot")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(cli_packet["chapters"]["results"], packet["chapters"]["results"])

    def test_unregistered_legacy_chapter_path_keeps_original_fallback(self):
        metadata = {"__root": str(self.root)}
        self.assertEqual(story.workbench._chapter_path(metadata, 1), "chapters/0001.md")

    def authoritative_state(self):
        state_path = self.root / ".story/state.sqlite3"
        ledger_path = self.root / ".story/publishing.sqlite3"
        chapter_paths = [self.root / self.book.chapter_path(chapter) for chapter in (1, 2)]
        return {
            "revision": self.book.meta("revision"),
            "core_bytes": state_path.read_bytes(),
            "core_mtime": state_path.stat().st_mtime_ns,
            "core_dump": tuple(self.book.db.iterdump()),
            "publishing_bytes": ledger_path.read_bytes(),
            "publishing_mtime": ledger_path.stat().st_mtime_ns,
            "chapters": {str(path): path.read_bytes() for path in chapter_paths},
        }

    def assert_story_error(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def test_snapshot_and_export_do_not_change_authoritative_state(self):
        before = self.authoritative_state()
        first = story.workbench.snapshot(self.book)
        second = story.workbench.snapshot(self.book)
        exported = story.workbench.export(self.book)

        self.assertEqual(first["snapshot"]["id"], second["snapshot"]["id"])
        self.assertEqual(exported["snapshot_id"], first["snapshot"]["id"])
        self.assertEqual(self.authoritative_state(), before)

    def test_snapshot_contract_is_bounded_paged_and_composite(self):
        packet = story.workbench.snapshot(self.book, limit=1, budget=64000)
        required = {
            "contract", "captured_at", "snapshot", "book", "progress", "chapters",
            "artifacts", "cards", "world", "history", "analysis", "publish",
            "attention", "completeness",
        }
        self.assertTrue(required.issubset(packet), set(packet))
        self.assertEqual(packet["contract"], "story.workbench.v1")
        self.assertRegex(packet["captured_at"], r"^\d{4}-\d\d-\d\dT.*Z$")
        self.assertEqual(packet["book"], {
            "id": self.book.meta("id"), "root": str(self.root), "title": self.title,
            "kind": "long", "revision": self.book.meta("revision"),
        })

        composite = packet["snapshot"]
        self.assertEqual(composite["core_revision"], self.book.meta("revision"))
        for field in ("id", "publishing_fingerprint", "filesystem_manifest_sha256"):
            self.assertRegex(composite[field], r"^[0-9a-f]{64}$")

        chapters = packet["chapters"]
        self.assertEqual(chapters["total"], 2)
        self.assertEqual(chapters["offset"], 0)
        self.assertEqual(chapters["limit"], 1)
        self.assertEqual(chapters["returned"], 1)
        self.assertEqual(len(chapters["results"]), 1)
        self.assertIsNotNone(chapters["next_cursor"])
        self.assertTrue(chapters["has_more"])
        self.assertFalse(chapters["complete"])

        next_page = story.workbench.snapshot(
            self.book, limit=1, budget=64000, cursor=chapters["next_cursor"])
        self.assertEqual(next_page["snapshot"]["id"], composite["id"])
        self.assertEqual(next_page["chapters"]["offset"], 1)
        self.assertEqual(next_page["chapters"]["returned"], 1)
        self.assertFalse(next_page["chapters"]["has_more"])
        self.assertFalse(next_page["chapters"]["complete"])
        self.assertIsNone(next_page["chapters"]["next_cursor"])

        wider_page = story.workbench.snapshot(self.book, limit=2, budget=64000)
        self.assertEqual(wider_page["snapshot"]["id"], composite["id"])
        self.assertTrue(wider_page["chapters"]["complete"])

        serialized = story.dumps(packet)
        for token in (*self.body_tokens, self.account_id, self.remote_book_id):
            self.assertNotIn(token, serialized)
        self.assertNotRegex(serialized, r'"(?:body|text|prose)":')

        process, cli_packet = self.cli("workbench-snapshot", "--limit", "1", "--budget-bytes", "64000")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(cli_packet["contract"], "story.workbench.v1")
        self.assertEqual(cli_packet["chapters"]["returned"], 1)
        self.assertEqual(cli_packet["snapshot"]["id"], composite["id"])

        self.book.save_notes([{
            "id": "cursor-change", "kind": "hook", "text": "第二页之后新增的待办。",
            "source": "测试", "tags": [], "critical": False, "status": "active", "due": 3,
        }], self.book.meta("revision"))
        self.assert_story_error(
            "stale_snapshot", story.workbench.snapshot, self.book,
            limit=1, budget=64000, cursor=chapters["next_cursor"])

    def test_composite_identity_tracks_publishing_and_displayed_file_facts(self):
        initial = story.workbench.snapshot(self.book)
        revision = initial["snapshot"]["core_revision"]

        story.publish.cancel(self.book, self.publish_plan["id"])
        publishing_changed = story.workbench.snapshot(self.book)
        self.assertEqual(publishing_changed["snapshot"]["core_revision"], revision)
        self.assertNotEqual(publishing_changed["snapshot"]["publishing_fingerprint"],
                            initial["snapshot"]["publishing_fingerprint"])
        self.assertNotEqual(publishing_changed["snapshot"]["id"], initial["snapshot"]["id"])

        chapter = self.root / self.book.chapter_path(2)
        chapter.write_text(chapter.read_text(encoding="utf-8") + "作者在工具外修改。\n", encoding="utf-8")
        file_changed = story.workbench.snapshot(self.book)
        self.assertEqual(file_changed["snapshot"]["core_revision"], revision)
        self.assertNotEqual(file_changed["snapshot"]["filesystem_manifest_sha256"],
                            publishing_changed["snapshot"]["filesystem_manifest_sha256"])
        self.assertNotEqual(file_changed["snapshot"]["id"], publishing_changed["snapshot"]["id"])
        self.assertEqual(file_changed["progress"]["exports"]["changed_count"], 1)

    def test_cursor_rejects_an_off_page_chapter_change_without_revision_bump(self):
        first = story.workbench.snapshot(self.book, limit=1, budget=64000)
        cursor = first["chapters"]["next_cursor"]
        self.book.db.execute("UPDATE chapter_state SET summary=? WHERE chapter=1",
                             ("工具外改变了尚未读取页的摘要。",))
        self.book.db.commit()

        self.assert_story_error(
            "stale_snapshot", story.workbench.snapshot, self.book,
            limit=1, budget=64000, cursor=cursor)

    def test_composite_identity_tracks_publishing_export_files(self):
        before = story.workbench.snapshot(self.book)
        exported = story.publish.export_material(self.book, self.publish_plan["id"])
        self.assertTrue(exported["export_created"], exported)
        after_export = story.workbench.snapshot(self.book)
        self.assertNotEqual(after_export["snapshot"]["filesystem_manifest_sha256"],
                            before["snapshot"]["filesystem_manifest_sha256"])

        archive = Path(exported["export"]["path"])
        archive.write_bytes(archive.read_bytes() + b"changed outside the tool")
        after_change = story.workbench.snapshot(self.book)
        self.assertNotEqual(after_change["snapshot"]["filesystem_manifest_sha256"],
                            after_export["snapshot"]["filesystem_manifest_sha256"])
        self.assertNotEqual(after_change["snapshot"]["id"], after_export["snapshot"]["id"])

    def test_invalid_publishing_receipt_changes_the_composite_identity(self):
        before = story.workbench.snapshot(self.book)
        directory = self.root / ".story/publishing-exports"
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / ("material-" + "f" * 32 + ".receipt.json")
        receipt.write_text("not valid receipt json", encoding="utf-8")
        after = story.workbench.snapshot(self.book)
        self.assertEqual(after["publish"]["invalid_exports"], 1)
        self.assertNotEqual(after["snapshot"]["publishing_fingerprint"],
                            before["snapshot"]["publishing_fingerprint"])
        self.assertNotEqual(after["snapshot"]["id"], before["snapshot"]["id"])

    def test_noncanonical_publishing_manifest_is_rejected(self):
        ledger = self.root / ".story/publishing.sqlite3"
        db = sqlite3.connect(ledger)
        try:
            trigger = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='plans_immutable'"
            ).fetchone()[0]
            manifest = db.execute(
                "SELECT manifest FROM plans WHERE id=?", (self.publish_plan["id"],)
            ).fetchone()[0]
            db.execute("DROP TRIGGER plans_immutable")
            db.execute("UPDATE plans SET manifest=? WHERE id=?",
                       (manifest + " ", self.publish_plan["id"]))
            db.execute(trigger)
            db.commit()
        finally:
            db.close()

        self.assert_story_error("publishing_corrupt", story.workbench.snapshot, self.book)

    def test_publishing_manifest_limit_is_checked_before_manifest_validation(self):
        with (patch.object(story.workbench, "MAX_PUBLISH_MANIFEST_BYTES", 1),
              patch.object(story.publish, "_validated_manifest",
                           wraps=story.publish._validated_manifest) as validated):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)
        validated.assert_not_called()

    def test_missing_or_wrong_size_publish_zip_is_required_attention(self):
        exported = story.publish.export_material(self.book, self.publish_plan["id"])
        archive = Path(exported["export"]["path"])
        archive.unlink()

        missing = story.workbench.snapshot(self.book)
        self.assertEqual(missing["publish"]["export_receipts_total"], 1)
        self.assertEqual(missing["publish"]["archives_missing"], 1)
        self.assertEqual(missing["publish"]["archives_size_mismatch"], 0)
        self.assertFalse(missing["publish"]["archive_hash_checked"])
        self.assertTrue(any(item["code"] == "publish_archives_missing" and
                            item["level"] == "required" for item in missing["attention"]))
        page = story.workbench.render_html(missing)
        self.assertIn("1 份导出回执", page)
        self.assertNotIn("份材料导出", page)
        self.assertIn("尚未逐字节核验 ZIP", page)

        archive.write_bytes(b"wrong-size")
        mismatched = story.workbench.snapshot(self.book)
        self.assertEqual(mismatched["publish"]["archives_missing"], 0)
        self.assertEqual(mismatched["publish"]["archives_size_mismatch"], 1)
        self.assertTrue(any(item["code"] == "publish_archives_size_mismatch" and
                            item["level"] == "required" for item in mismatched["attention"]))

    def test_budget_and_page_limits_are_enforced(self):
        self.assert_story_error("budget_exceeded", story.workbench.snapshot, self.book, budget=256)
        self.assert_story_error("invalid_input", story.workbench.snapshot, self.book, limit=101)

    def test_sparse_future_plan_does_not_claim_the_next_chapter_is_ready(self):
        plan = {
            "title": "更远的一章", "volume_dir": "第一卷 雨夜", "goal": "处理后续选择",
            "stop": "留下新的问题", "constraints": ["不越过当前章"], "requires": [],
            "tags": ["沈禾"], "length": [10, 1000],
            "beats": [{"choice": "沈禾先等待", "change": "远期计划已存在"}],
        }
        self.book.save_plan(4, plan, self.book.meta("revision"))
        packet = story.workbench.snapshot(self.book)
        self.assertGreater(packet["chapters"]["planned_total"], packet["progress"]["last_chapter"])
        self.assertFalse(packet["progress"]["next_plan_exists"])
        self.assertIn("先补第3章计划", story.workbench.render_html(packet))

    def test_due_card_total_does_not_underreport_the_bounded_list(self):
        notes = [{
            "id": f"due-{index:02d}", "kind": "hook", "text": f"第 {index} 项待处理事项。",
            "source": "测试", "tags": [], "critical": False, "status": "active", "due": 3,
        } for index in range(12)]
        self.book.save_notes(notes, self.book.meta("revision"))
        packet = story.workbench.snapshot(self.book)
        self.assertEqual(packet["cards"]["due_or_overdue_total"], 12)
        self.assertEqual(packet["cards"]["due_or_overdue_returned"], 10)
        self.assertEqual(packet["cards"]["due_or_overdue_omitted"], 2)
        self.assertEqual(len(packet["cards"]["due_or_overdue"]), 10)
        self.assertTrue(any("有 12 项" in item["message"] for item in packet["attention"]))

    def test_unregistered_files_are_reported_honestly_and_never_guessed(self):
        misleading = {
            "最新草稿.md": "这不是已登记草稿。",
            "全书总纲_最终采用版.md": "这不是已登记大纲。",
            "封面_最终版.png": "not really a png",
        }
        for name, content in misleading.items():
            (self.root / name).write_text(content, encoding="utf-8")

        packet = story.workbench.snapshot(self.book)
        self.assertFalse(packet["artifacts"]["registry_exists"])
        self.assertEqual(packet["artifacts"]["recent"], [])
        for field in ("drafts", "candidates", "readable_outlines", "covers_and_other_artifacts"):
            self.assertEqual(packet["completeness"][field], "unregistered")
        serialized = story.dumps(packet)
        for name in misleading:
            self.assertNotIn(name, serialized)

        result = story.workbench.export(self.book)
        page = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("尚未登记", page)
        for name in misleading:
            self.assertNotIn(name, page)

    def test_reading_export_embeds_verified_text_and_navigation(self):
        result = story.workbench.export(self.book, include_text=True)
        page = Path(result["path"]).read_text(encoding="utf-8")
        for token in self.body_tokens:
            self.assertIn(token, page)
        self.assertIn('href="#chapter-1"', page)
        self.assertIn('id="chapter-2"', page)
        self.assertIn("第一卷 雨#夜%", page)
        self.assertNotIn('<script>alert(', page)

    def test_three_column_reader_script_is_hash_bound(self):
        import base64
        result = story.workbench.export(self.book, include_text=True)
        page = Path(result["path"]).read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + expected + "'", page)
        self.assertIn('aria-label="作品目录"', page)
        self.assertIn('class="manuscript"', page)
        self.assertIn('aria-label="章节信息"', page)
        self.assertIn('data-note="chapter-1"', page)
        self.assertIn('id="overview"', page)

    def test_reader_empty_and_partial_directory_are_explicit(self):
        packet = story.workbench.snapshot(self.book, limit=1)
        reading = story.workbench._reading_text(self.book, packet)
        page = story.workbench.render_html(packet, reading)
        self.assertIn('本页 1 章', page)
        self.assertNotIn('href="#chapter-1"', page)
        self.assertIn('id="chapter-search"', page)
        self.assertIn('id="previous" disabled', page)
        self.assertIn('id="next" disabled', page)
        packet["chapters"]["results"] = []
        page = story.workbench.render_html(packet, {})
        self.assertIn("尚无正式章节", page)
        self.assertIn('id="overview"', page)
        self.assertNotIn('class="chapter-link"', page)

    def test_reader_overview_preserves_delivery_and_coverage(self):
        packet = story.workbench.snapshot(self.book, limit=1)
        page = story.workbench.render_html(packet, story.workbench._reading_text(self.book, packet))
        self.assertIn("已载入 1 / 2 章，本页并非全书", page)
        self.assertIn("本地准备记录 1 份", page)
        self.assertIn("不代表平台已保存", page)
        self.assertIn('id="missing-chapter" hidden', page)
        self.assertNotIn(self.account_id, page)
        packet = story.workbench.snapshot(self.book)
        page = story.workbench.render_html(packet, story.workbench._reading_text(self.book, packet))
        self.assertIn("已载入全部 2 章", page)

    def test_reading_export_rejects_external_changes(self):
        packet = story.workbench.snapshot(self.book)
        path = self.root / packet["chapters"]["results"][0]["path"]
        path.write_text("外部改稿", encoding="utf-8")
        self.assert_story_error("workbench_changed", story.workbench.export,
                                self.book, include_text=True)
        self.assertFalse((self.root / ".story/workbench/index.html").exists())

    def test_html_is_escaped_self_contained_and_omits_complete_prose(self):
        process, result = self.cli("workbench-export")
        self.assertEqual(process.returncode, 0, process.stderr)
        path = Path(result["path"])
        self.assertEqual(path, self.root / ".story/workbench/index.html")
        self.assertTrue(path.is_file())
        raw = path.read_bytes()
        page = raw.decode("utf-8")

        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn(html.escape(self.title, quote=True), page)
        self.assertNotIn('<script>alert("workbench-title")</script>', page)
        self.assertIn("<style", page.lower())
        self.assertNotRegex(page, r"(?i)\b(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//")
        self.assertNotRegex(page, r"(?i)@import\s+url|url\(\s*['\"]?(?:https?:)?//")
        self.assertNotIn("http://", page.lower())
        self.assertNotIn("https://", page.lower())
        for token in (*self.body_tokens, self.account_id, self.remote_book_id):
            self.assertNotIn(token, page)

    def test_output_rejects_escape_and_linked_parent(self):
        outside = self.root.parent / "outside.html"
        self.assert_story_error("path_escape", story.workbench.export, self.book, "../outside.html")
        self.assert_story_error("path_escape", story.workbench.export, self.book, outside)
        self.assertFalse(outside.exists())

        external = self.root.parent / "external-workbench"
        external.mkdir()
        linked = self.root / ".story/workbench"
        try:
            linked.symlink_to(external, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Directory link creation is unavailable")
            environment = dict(os.environ, STORY_WORKBENCH_LINK=str(linked), STORY_WORKBENCH_TARGET=str(external))
            made = subprocess.run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:STORY_WORKBENCH_LINK -Target "
                "$env:STORY_WORKBENCH_TARGET -ErrorAction Stop | Out-Null",
            ], env=environment, capture_output=True,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if made.returncode:
                self.skipTest("Neither symlink nor Windows junction creation is available")
        with self.assertRaises(story.StoryError) as caught:
            story.workbench.export(self.book)
        self.assertIn(caught.exception.code, ("linked_path", "path_escape", "safe_export_unavailable"))
        self.assertFalse((external / "index.html").exists())

    def test_invalid_metadata_is_rejected_instead_of_rendered(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='revision'",
                             ('\"<img src=x onerror=alert(1)>\"',))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_malformed_schema_json_and_missing_table_are_state_corrupt(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='schema'", ("not-json",))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)
        process, packet = self.cli("workbench-snapshot")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(packet["error"], "state_corrupt", packet)

        self.book.db.execute("UPDATE meta SET value=? WHERE key='schema'",
                             (story.dumps(story.SCHEMA_VERSION),))
        self.book.db.execute("ALTER TABLE world_entities RENAME TO broken_world_entities")
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_oversized_integer_metadata_is_rejected_without_a_traceback(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='last_chapter'",
                             (str(10 ** 100),))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_export_rejects_a_git_tracked_derived_page(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        target = self.root / ".story/workbench/index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old tracked workbench", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", "--",
                        ".story/workbench/index.html"], check=True)
        self.assert_story_error("workbench_tracked_output", story.workbench.export, self.book)
        self.assertEqual(target.read_text(encoding="utf-8"), "old tracked workbench")

    def test_git_book_requires_ignore_rule_before_export(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/\n")
        result = story.workbench.export(self.book)
        self.assertEqual(result["git_ignore_status"], "ignored")
        self.assertTrue(Path(result["path"]).is_file())

    def test_git_rule_for_only_index_is_insufficient_for_backups(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/index.html\n")
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)

    def test_git_probe_only_rules_do_not_masquerade_as_output_ignores(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/.story-ignore-probe\n")
            stream.write("/.story/workbench/.backups/.story-ignore-probe\n")
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)

    def test_git_verification_failure_in_a_repository_fails_closed(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        with patch.object(story.workbench.subprocess, "run", side_effect=FileNotFoundError("no git")):
            self.assert_story_error("workbench_git_check_failed", story.workbench.export, self.book)

    def test_open_browser_receipt_does_not_overstate_failure(self):
        with patch.object(story.workbench.webbrowser, "open", return_value=True):
            opened = story.workbench.export(self.book, open_browser=True)
        self.assertTrue(opened["opened"])
        self.assertIsNone(opened["open_error"])

        with patch.object(story.workbench.webbrowser, "open", side_effect=OSError("no browser")):
            failed = story.workbench.export(self.book, open_browser=True)
        self.assertFalse(failed["opened"])
        self.assertIn("no browser", failed["open_error"])
        self.assertTrue(Path(failed["path"]).is_file())

    def test_cli_snapshot_without_publishing_record_is_read_only(self):
        other = self.root.parent / "无发布记录"
        story.Book.create(other, "无发布记录", "long")
        state = other / ".story/state.sqlite3"
        before = (state.read_bytes(), state.stat().st_mtime_ns)
        ledger = other / ".story/publishing.sqlite3"
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), "workbench-snapshot", "--book", str(other)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        packet = json.loads(process.stdout)
        self.assertFalse(packet["publish"]["record_exists"])
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)
        self.assertFalse(ledger.exists())

    def test_core_change_during_final_publishing_capture_is_retried(self):
        original = story.workbench._publishing_capture
        captures = 0
        changed_revision = None

        def capture(book, limit):
            nonlocal captures, changed_revision
            captures += 1
            if captures == 2:
                concurrent = story.Book(self.root)
                try:
                    concurrent.save_notes([{
                        "id": "concurrent-change", "kind": "hook",
                        "text": "发布摘要读取期间写入的有效状态变化。", "source": "测试",
                        "tags": [], "critical": False, "status": "active", "due": 3,
                    }], concurrent.meta("revision"))
                    changed_revision = concurrent.meta("revision")
                finally:
                    concurrent.close()
            return original(book, limit)

        with patch.object(story.workbench, "_publishing_capture", side_effect=capture):
            packet = story.workbench.snapshot(self.book)

        self.assertGreaterEqual(captures, 4)
        self.assertIsNotNone(changed_revision)
        self.assertEqual(packet["book"]["revision"], changed_revision)
        self.assertEqual(packet["snapshot"]["core_revision"], changed_revision)

    def test_internal_scans_have_hard_limits(self):
        with patch.object(story.workbench, "MAX_MANAGED_ARTIFACTS", 1):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)
        with patch.object(story.workbench, "MAX_PUBLISH_PLANS", 0):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)

        story.publish.export_material(self.book, self.publish_plan["id"])
        with patch.object(story.workbench, "MAX_PUBLISH_RECEIPTS", 0):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)

    @unittest.skipIf(os.name == "nt", "Windows holds the open database path against replacement")
    def test_read_only_book_detects_state_path_replacement(self):
        self.book.close()
        readonly = story.workbench._ReadOnlyBook(self.root)
        state = self.root / ".story/state.sqlite3"
        displaced = self.root / ".story/state-before-replacement.sqlite3"
        raw = state.read_bytes()
        state.replace(displaced)
        state.write_bytes(raw)
        try:
            self.assert_story_error("workbench_changed", story.workbench.snapshot, readonly)
        finally:
            readonly.close()

    def test_successful_refresh_preserves_previous_complete_page(self):
        first = story.workbench.export(self.book)
        previous = Path(first["path"]).read_bytes()
        self.book.save_notes([{
            "id": "refresh-change", "kind": "hook", "text": "刷新后显示的新事项。",
            "source": "测试", "tags": [], "critical": True, "status": "active", "due": 3,
        }], self.book.meta("revision"))
        second = story.workbench.export(self.book)
        self.assertIsNotNone(second["backup"])
        self.assertEqual(Path(second["backup"]).read_bytes(), previous)
        self.assertNotEqual(Path(second["path"]).read_bytes(), previous)

    def test_atomic_failure_preserves_previous_complete_page(self):
        first = story.workbench.export(self.book)
        target = Path(first["path"])
        previous = target.read_bytes()
        self.book.save_notes([{
            "id": "new-attention", "kind": "hook", "text": "等待作者决定账本交给谁。",
            "source": "作者明确设定", "tags": ["账本"], "critical": True,
            "status": "active", "due": 2,
        }], self.book.meta("revision"))

        original_publish = story._publish_no_replace

        def interrupt_new_page(source, destination):
            source_name = getattr(source, "name", Path(str(source)).name)
            if str(source_name).startswith(".story-tmp-") and Path(str(destination)).name == "index.html":
                raise OSError("simulated interrupted workbench publication")
            return original_publish(source, destination)

        with patch.object(story, "_publish_no_replace", side_effect=interrupt_new_page):
            error = self.assert_story_error("export_io", story.workbench.export, self.book)
        self.assertEqual(Path(error.details["path"]), target)
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(list(target.parent.glob(".story-tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
