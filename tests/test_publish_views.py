"""Read frozen publishing material in bounded views at normal chapter sizes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("publishing_views_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)
publishing = story.publish

QUOTE = "沈禾把盖过印章的收据放在桌上，请守门人逐字核对交接的日期。"
PARAGRAPH = (
    "码头上的风还没有停。她沿着潮湿的石阶走到仓门前，把昨夜整理的名单摊开。"
    "守门人把灯移近纸面，先看模糊的印记，再检查背面的折痕。"
    "两名搬运工分别讲清到场的时辰，她把先后顺序记在另一张纸上。"
    "她留下抄件，把原件交给掌柜，约好在下一班船靠岸前核对登记簿。"
)


class PublishViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-publish-views-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "长篇视图测试 𠮷"
        story.Book.create(self.root, "雾港长篇视图样例", "long")
        self.book = story.Book(self.root)
        self.addCleanup(self.book.close)
        self.bodies = {}
        self.titles = {}

    def revision(self):
        return self.book.meta("revision")

    def delta(self, chapter, text):
        return {"book_id": self.book.meta("id"), "base_revision": self.revision(),
                "summary": f"容量样例第{chapter}章：核对交接记录，保留抄件。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "issues": [], "checks": {
                    name: {"note": "隔离容量样例：已核对所引交接原句与停笔位置。", "quote": QUOTE}
                    for name in story.CHECKS}},
                "dependencies": [], "dependency_review": {
                    "complete": True, "note": "隔离样例没有引用其他作品或章节资料。"}}

    def commit_body(self, chapter, body, replace_last=False):
        text = f"第{chapter}章 {self.titles[chapter]}\n" + body
        draft = self.root / ".story" / f"fixture-{chapter}.md"
        draft.write_bytes(text.encode("utf-8"))
        result = self.book.commit(chapter, draft, self.delta(chapter, text), replace_last=replace_last)
        self.assertTrue(result["exports_complete"], result)
        return result

    def make_formal_chapters(self, count=12):
        for chapter in range(1, count + 1):
            self.titles[chapter] = f"雨夜第{chapter}封来信"
            paragraphs = [QUOTE, f"这是第{chapter}份交接记录，纸角留着一枚𠮷字印记。"]
            while story.visible_count("\n".join(paragraphs)) < 2400:
                paragraphs.append(PARAGRAPH)
            newline = "\r\n" if chapter % 2 == 0 else "\n"
            body = (newline * 2).join(paragraphs) + newline
            self.assertGreaterEqual(story.visible_count(body), 2000)
            self.assertLessEqual(story.visible_count(body), 3000)
            plan = {"title": self.titles[chapter], "volume_dir": "第一卷 雾港来信",
                    "goal": "核对仓库交接记录", "stop": "留下抄件，约定下一次核对",
                    "requires": [], "length": [2000, 3000],
                    "beats": [{"choice": "逐项核对记录", "change": "保留可查证的抄件"}]}
            self.book.save_plan(chapter, plan, self.revision())
            self.commit_body(chapter, body)
            self.bodies[chapter] = body

    def payload(self, chapters=None, platform="fanqie"):
        return {"platform": platform, "account_id": "view-author-fixture",
                "remote_book_id": "view-book-fixture",
                "chapters": list(chapters if chapters is not None else self.bodies), "mode": "draft"}

    def prepare(self, summary=False, chapters=None, platform="fanqie"):
        return publishing.prepare(self.book, self.payload(chapters, platform), self.revision(), summary=summary)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def assert_view_identity(self, view, plan, status="prepared"):
        self.assertEqual(view["id"], plan["id"])
        self.assertEqual(view["manifest_sha256"], plan["manifest_sha256"])
        self.assertEqual(view["status"], status)
        self.assertEqual(view["source_revision"], plan["source_revision"])
        self.assertFalse(view["source_check_performed"])
        self.assertIsNone(view["source_matches_current"])
        self.assertFalse(view["platform_verified"])
        self.assertFalse(view["ready_to_upload"])
        self.assertEqual(view["content_verification"], "unread")

    def cli(self, name, *args):
        result = subprocess.run([sys.executable, "-B", str(TOOL), name, "--book", str(self.root), *args],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        try:
            packet = json.loads(result.stdout or result.stderr)
        except json.JSONDecodeError:
            self.fail(f"CLI did not return a JSON packet: {result.stdout!r} {result.stderr!r}")
        return result, packet

    def test_summary_prepare_and_legacy_full_prepare_share_one_complete_snapshot(self):
        self.make_formal_chapters()
        before = self.revision(), tuple(self.book.db.iterdump())
        compact = self.prepare(summary=True)
        self.assertEqual(compact["content_scope"], "metadata")
        self.assertEqual(compact["total_chapters"], 12)
        self.assertNotIn("manifest", compact)
        self.assertNotIn("chapter_snapshot", compact)
        self.assertNotIn("chapter_page", compact)
        self.assertLess(len(story.dumps(compact).encode("utf-8")), 16000)
        self.assertFalse(compact["idempotent"])
        full = self.prepare()
        again = self.prepare(summary=True)
        for repeated in (full, again):
            self.assertEqual(repeated["id"], compact["id"])
            self.assertEqual(repeated["manifest_sha256"], compact["manifest_sha256"])
            self.assertTrue(repeated["idempotent"])
        self.assertEqual(full["content_scope"], "full_manifest")
        self.assertEqual(len(full["manifest"]["chapters"]), 12)
        for chapter in full["manifest"]["chapters"]:
            self.assertEqual(chapter["body"], self.bodies[chapter["chapter"]])
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual((self.revision(), tuple(self.book.db.iterdump())), before)

    def test_twelve_normal_size_chapters_page_once_each_and_single_chapter_fits_default_budget(self):
        self.make_formal_chapters()
        plan = self.prepare(summary=True)
        ledger = self.root / ".story/publishing.sqlite3"
        before = ledger.read_bytes(), self.revision()
        rows, offsets, offset = [], [], 0
        while offset is not None:
            page = publishing.inspect(self.book, plan["id"], summary=True, offset=offset, limit=5)
            self.assert_view_identity(page, plan)
            self.assertEqual(page["content_scope"], "chapter_summaries")
            self.assertNotIn("manifest", page)
            self.assertNotIn("chapter_snapshot", page)
            self.assertLessEqual(page["budget"]["used"], 16000)
            section = page["chapter_page"]
            self.assertEqual((section["offset"], section["limit"], section["total"]), (offset, 5, 12))
            rows.extend(section["results"])
            offsets.append(offset)
            offset = section["next_offset"]
        self.assertEqual(offsets, [0, 5, 10])
        self.assertEqual([row["chapter"] for row in rows], list(range(1, 13)))
        for row in rows:
            number = row["chapter"]
            self.assertEqual(set(row), {"chapter", "title", "chapter_path", "body_characters",
                                        "local_visible_nonspace_v1", "upload_sha256"})
            self.assertEqual(row["title"], self.titles[number])
            self.assertEqual(row["chapter_path"], self.book.chapter_path(number))
            self.assertEqual(row["body_characters"], len(self.bodies[number]))
            self.assertEqual(row["local_visible_nonspace_v1"], story.visible_count(self.bodies[number]))
        empty = publishing.inspect(self.book, plan["id"], summary=True, offset=12, limit=5)
        self.assertEqual(empty["chapter_page"]["results"], [])
        self.assertIsNone(empty["chapter_page"]["next_offset"])
        one = publishing.inspect(self.book, plan["id"], chapter=6)
        self.assert_view_identity(one, plan)
        self.assertEqual(one["content_scope"], "single_chapter")
        self.assertNotIn("manifest", one)
        self.assertNotIn("chapter_page", one)
        self.assertEqual(one["chapter_snapshot"]["body"], self.bodies[6])
        self.assertEqual(one["chapter_snapshot"]["upload_sha256"], rows[5]["upload_sha256"])
        self.assertLessEqual(one["budget"]["used"], 16000)
        self.assertEqual((ledger.read_bytes(), self.revision()), before)

    def test_low_budgets_reject_every_view_without_returning_truncated_prose(self):
        self.make_formal_chapters()
        plan = self.prepare(summary=True)
        error = self.assert_code("budget_exceeded", publishing.inspect, self.book, plan["id"])
        self.assertGreater(error.details["minimum_bytes"], 16000)
        for view in ({}, {"summary": True, "limit": 5}, {"chapter": 6}):
            with self.subTest(view=view):
                error = self.assert_code("budget_exceeded", publishing.inspect,
                                         self.book, plan["id"], budget=256, **view)
                self.assertGreater(error.details["minimum_bytes"], 256)
                self.assertNotIn("manifest", error.details)
                self.assertNotIn("chapter_snapshot", error.details)
                self.assertNotIn("chapter_page", error.details)
                packet = publishing.inspect(self.book, plan["id"],
                                             budget=error.details["minimum_bytes"] + 64, **view)
                self.assert_view_identity(packet, plan)
                if "chapter" in view:
                    self.assertEqual(packet["chapter_snapshot"]["body"], self.bodies[6])
                elif view.get("summary"):
                    self.assertEqual(len(packet["chapter_page"]["results"]), 5)
                else:
                    self.assertEqual(len(packet["manifest"]["chapters"]), 12)
                    self.assertEqual(packet["manifest"]["chapters"][5]["body"], self.bodies[6])

    def test_invalid_views_and_chapters_outside_the_frozen_selection_are_rejected(self):
        self.make_formal_chapters(3)
        plan = self.prepare(summary=True, chapters=(1, 2))
        invalid = [{"summary": True, "chapter": 1}, {"offset": 1}, {"limit": 5},
                   {"chapter": 1, "offset": 1}, {"chapter": 1, "limit": 5},
                   {"summary": True, "offset": -1}, {"summary": True, "offset": True},
                   {"summary": True, "limit": 0}, {"summary": True, "limit": 101},
                   {"summary": True, "limit": "5"}, {"chapter": 0}, {"chapter": True},
                   {"chapter": "1"}]
        for view in invalid:
            with self.subTest(view=view):
                self.assert_code("invalid_input", publishing.inspect, self.book, plan["id"], **view)
        for chapter in (3, 99):
            with self.subTest(chapter=chapter):
                error = self.assert_code("publish_chapter_missing", publishing.inspect,
                                         self.book, plan["id"], chapter=chapter)
                self.assertEqual(error.details["chapter"], chapter)
        self.assertEqual(publishing.inspect(self.book, plan["id"], chapter=2)["chapter_snapshot"]["body"],
                         self.bodies[2])

    def test_stale_and_cancelled_views_keep_old_bodies_without_claiming_a_fresh_source_check(self):
        self.make_formal_chapters(2)
        active = self.prepare(summary=True)
        cancelled = self.prepare(summary=True, platform="qimao")
        publishing.cancel(self.book, cancelled["id"])
        old_body = self.bodies[2]
        self.commit_body(2, old_body + "她在下一次核对前补记了新的送货时辰。\n", replace_last=True)
        # A view must not claim current validity just because the stored status is prepared.
        unchecked = publishing.inspect(self.book, active["id"], chapter=2)
        self.assert_view_identity(unchecked, active)
        self.assertEqual(unchecked["chapter_snapshot"]["body"], old_body)
        self.assertEqual(publishing.check(self.book, active["id"])["status"], "stale")
        before = (self.root / ".story/publishing.sqlite3").read_bytes(), self.revision()
        for plan, status in ((active, "stale"), (cancelled, "cancelled")):
            for view in ({"summary": True}, {"chapter": 2}, {}):
                with self.subTest(status=status, view=view):
                    packet = publishing.inspect(self.book, plan["id"], budget=40000, **view)
                    self.assert_view_identity(packet, plan, status=status)
                    self.assertFalse(packet["ok"])
                    if "chapter" in view:
                        self.assertEqual(packet["chapter_snapshot"]["body"], old_body)
                    elif not view:
                        self.assertEqual(packet["manifest"]["chapters"][1]["body"], old_body)
        self.assertEqual(((self.root / ".story/publishing.sqlite3").read_bytes(), self.revision()), before)

    def test_cli_summary_prepare_pages_and_one_complete_chapter_use_default_budgets(self):
        self.make_formal_chapters()
        path = self.root / ".story/publish-input.json"
        path.write_text(json.dumps(self.payload(), ensure_ascii=False), encoding="utf-8")
        result, prepared = self.cli("publish-prepare", "--input", str(path),
                                    "--expect", str(self.revision()), "--summary")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(prepared["content_scope"], "metadata")
        self.assertNotIn("manifest", prepared)
        self.assertLess(len(result.stdout.encode("utf-8")), 16000)
        result, page = self.cli("publish-inspect", "--id", prepared["id"],
                               "--summary", "--offset", "5", "--limit", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_view_identity(page, prepared)
        self.assertEqual(page["content_scope"], "chapter_summaries")
        self.assertEqual([row["chapter"] for row in page["chapter_page"]["results"]], [6, 7, 8, 9, 10])
        self.assertEqual(page["chapter_page"]["next_offset"], 10)
        result, one = self.cli("publish-inspect", "--id", prepared["id"], "--chapter", "6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_view_identity(one, prepared)
        self.assertEqual(one["content_scope"], "single_chapter")
        self.assertEqual(one["chapter_snapshot"]["body"], self.bodies[6])
        self.assertLessEqual(one["budget"]["used"], 16000)
        result, refused = self.cli("publish-inspect", "--id", prepared["id"],
                                  "--chapter", "6", "--budget-bytes", "256")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(refused["error"], "budget_exceeded")
        self.assertNotIn("chapter_snapshot", refused)
        result, missing = self.cli("publish-inspect", "--id", prepared["id"], "--chapter", "99")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(missing["error"], "publish_chapter_missing")


if __name__ == "__main__":
    unittest.main()
