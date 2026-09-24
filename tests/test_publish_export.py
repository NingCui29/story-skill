"""Portable offline chapter material must match its frozen, checked snapshot."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

from tests import test_publish as fixtures


publishing, story = fixtures.publishing, fixtures.story


class PublishExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book
        self.output = self.fixture.root / ".story/publishing-exports"

    def files(self):
        return {p for p in self.output.rglob("*.zip") if p.is_file()}

    def seed(self):
        body_one = fixtures.BODY.replace("账。", "账：𠮷字旁画着一枚🌧️印记。").replace("\n", "\r\n")
        self.fixture.commit(1, text="第1章 雨夜𠮷字\r\n" + body_one, title="雨夜𠮷字")
        body_two = fixtures.BODY.rstrip("\n") + "街边灯笼🌧️还亮着。"
        self.fixture.commit(2, text=body_two, title="未写章头的稿件")
        prepared = self.fixture.prepare((1, 2))
        return prepared, {1: body_one, 2: body_two}

    def assert_blocked(self, result, status):
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], status)
        self.assertFalse(result["export_created"])
        self.assertNotIn("export", result)
        self.assertFalse(result["platform_verified"])
        self.assertFalse(result["ready_to_upload"])

    def test_multichapter_zip_has_exact_frozen_utf8_fields_and_check_receipt(self):
        prepared, bodies = self.seed()
        before = self.fixture.writing_snapshot()
        result = publishing.export_material(self.book, prepared["id"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["export_created"])
        self.assertTrue(result["source_check_performed"])
        self.assertTrue(result["source_matches_current"])
        self.assertFalse(result["platform_verified"])
        self.assertFalse(result["ready_to_upload"])
        self.assertEqual(result["remote_state"], "unknown")
        exported = result["export"]
        target = Path(exported["path"])
        self.assertTrue(target.is_absolute())
        self.assertEqual(target.parent, self.output)
        self.assertEqual(target.suffix, ".zip")
        data = target.read_bytes()
        self.assertEqual(exported["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(exported["bytes"], len(data))
        self.assertEqual(exported["format"], "zip")
        self.assertEqual(exported["encoding"], "utf-8")
        self.assertEqual(exported["chapters"], 2)
        expected_names = {"manifest.json", "receipt.json", "使用说明.txt", "目录.txt"}
        for chapter in (1, 2):
            expected_names.update(f"章节/第{chapter}章/{name}.txt" for name in ("标题", "正文", "作者的话"))
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), expected_names)
            self.assertEqual(len(archive.namelist()), len(expected_names))
            self.assertEqual(exported["files"], len(expected_names))
            self.assertEqual(json.loads(archive.read("manifest.json").decode("utf-8")), prepared["manifest"])
            receipt = json.loads(archive.read("receipt.json").decode("utf-8"))
            for field in ("id", "status", "checked_at", "manifest_sha256", "source_check_performed",
                          "source_matches_current", "platform_verified", "ready_to_upload", "remote_state", "chapter_checks"):
                self.assertEqual(receipt[field], result[field], field)
            self.assertIsInstance(receipt["exported_at"], str)
            self.assertTrue(receipt["exported_at"])
            for entry in prepared["manifest"]["chapters"]:
                chapter = entry["chapter"]
                self.assertEqual(entry["body"], bodies[chapter])
                for filename, field in (("标题", "title"), ("正文", "body"), ("作者的话", "author_note")):
                    content = archive.read(f"章节/第{chapter}章/{filename}.txt")
                    self.assertEqual(content, entry[field].encode("utf-8"))
                    self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
            self.assertTrue(archive.read("使用说明.txt").decode("utf-8").strip())
            self.assertTrue(archive.read("目录.txt").decode("utf-8").strip())
        self.assertEqual(self.files(), {target})
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertEqual(self.fixture.inspect(prepared["id"])["manifest"], prepared["manifest"])

    def test_repeated_export_creates_new_path_and_never_changes_previous_package(self):
        prepared, _ = self.seed()
        first = publishing.export_material(self.book, prepared["id"])
        first_path = Path(first["export"]["path"])
        original = first_path.read_bytes()
        second = publishing.export_material(self.book, prepared["id"])
        second_path = Path(second["export"]["path"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(first_path.read_bytes(), original)
        self.assertEqual(self.files(), {first_path, second_path})
        for path in (first_path, second_path):
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json")), prepared["manifest"])

    def test_valid_zip_with_wrong_body_is_rejected_and_own_stage_is_removed(self):
        prepared, _ = self.seed()
        before = self.fixture.writing_snapshot()
        original_write = zipfile.ZipFile.writestr
        damaged = []

        def write_changed_body(archive, name, content, *args, **kwargs):
            if name == "章节/第1章/正文.txt":
                content = "损坏但压缩包CRC仍然正确的正文。".encode("utf-8")
                damaged.append(name)
            return original_write(archive, name, content, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "writestr", new=write_changed_body):
            with self.assertRaises(story.StoryError) as caught:
                publishing.export_material(self.book, prepared["id"])
        self.assertEqual(damaged, ["章节/第1章/正文.txt"])
        self.assertEqual(caught.exception.code, "publish_export_failed")
        self.assertEqual(caught.exception.details.get("member"), "章节/第1章/正文.txt")
        self.assertEqual(self.files(), set())
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertEqual(self.fixture.inspect(prepared["id"])["manifest"], prepared["manifest"])
        retried = publishing.export_material(self.book, prepared["id"])
        self.assertTrue(retried["export_created"])
        with zipfile.ZipFile(retried["export"]["path"]) as archive:
            self.assertEqual(archive.read("章节/第1章/正文.txt"),
                             prepared["manifest"]["chapters"][0]["body"].encode("utf-8"))

    def test_unchecked_formal_revision_blocks_export_and_persists_stale(self):
        prepared, _ = self.seed()
        self.fixture.commit(2, text=self.fixture.texts[2] + "她发现门外还有人。", title="未写章头的稿件", replace_last=True)
        before = self.fixture.writing_snapshot()
        result = publishing.export_material(self.book, prepared["id"])
        self.assert_blocked(result, "stale")
        self.assertTrue(result["source_check_performed"])
        self.assertFalse(result["source_matches_current"])
        self.assertEqual({row["chapter"] for row in result["chapter_checks"] if not row["matches_current"]}, {2})
        self.assertEqual(self.fixture.inspect(prepared["id"])["status"], "stale")
        self.assertEqual(self.files(), set())
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_external_edit_and_missing_chapter_export_block_material(self):
        prepared, _ = self.seed()
        chapter_path = self.fixture.root / self.book.chapter_path(2)
        original = chapter_path.read_bytes()
        for changed in ("外部编辑稿，不能当作已核对正文。".encode("utf-8"), None):
            with self.subTest(missing=changed is None):
                if changed is None:
                    chapter_path.unlink()
                else:
                    chapter_path.write_bytes(changed)
                result = publishing.export_material(self.book, prepared["id"])
                self.assert_blocked(result, "stale")
                self.assertTrue(result["source_check_performed"])
                self.assertFalse(result["source_matches_current"])
                self.assertIn("exports_unresolved", {reason["code"] for reason in result["reasons"]})
                self.assertEqual(self.files(), set())
                if changed is None:
                    self.assertFalse(chapter_path.exists())
                else:
                    self.assertEqual(chapter_path.read_bytes(), changed)
                chapter_path.write_bytes(original)

    def test_stale_plan_is_rechecked_but_not_revived_by_export(self):
        prepared, _ = self.seed()
        path = self.fixture.root / self.book.chapter_path(2)
        path.unlink()
        first = publishing.check(self.book, prepared["id"])
        self.assertEqual(first["status"], "stale")
        self.assertTrue(self.book.export()["exports_complete"])
        result = publishing.export_material(self.book, prepared["id"])
        self.assert_blocked(result, "stale")
        self.assertTrue(result["source_check_performed"])
        self.assertTrue(result["source_matches_current"])
        self.assertNotEqual(result["checked_at"], first["checked_at"])
        self.assertEqual(self.files(), set())

    def test_cancelled_plan_does_not_run_a_source_check_or_create_material(self):
        prepared, _ = self.seed()
        cancelled = publishing.cancel(self.book, prepared["id"])
        (self.fixture.root / self.book.chapter_path(2)).unlink()
        result = publishing.export_material(self.book, prepared["id"])
        self.assert_blocked(result, "cancelled")
        self.assertFalse(result["source_check_performed"])
        self.assertIsNone(result["source_matches_current"])
        self.assertEqual(result["checked_at"], cancelled["checked_at"])
        self.assertEqual(self.files(), set())

    def test_cli_returns_real_package_path_and_keeps_unicode_content(self):
        prepared, _ = self.seed()
        result = subprocess.run([sys.executable, "-B", str(fixtures.TOOL), "publish-export", "--book", str(self.fixture.root),
                                 "--id", prepared["id"]], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertTrue(packet["export_created"])
        target = Path(packet["export"]["path"])
        self.assertEqual(target.parent, self.output)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("章节/第1章/标题.txt").decode("utf-8"), "雨夜𠮷字")
            self.assertEqual(json.loads(archive.read("receipt.json"))["id"], prepared["id"])

    def test_linked_output_directory_cannot_write_outside_the_book(self):
        prepared, _ = self.seed()
        outside = self.fixture.root.parent / "outside-export"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_bytes(b"preserve")
        try:
            self.output.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create directory symlink: {error}")
        with self.assertRaises(story.StoryError) as caught:
            publishing.export_material(self.book, prepared["id"])
        self.assertIn(caught.exception.code, ("linked_path", "unsafe_publish_path", "export_conflict"))
        self.assertEqual(list(outside.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_concurrent_destination_conflict_preserves_existing_file(self):
        prepared, _ = self.seed()
        first = publishing.export_material(self.book, prepared["id"])
        previous_path = Path(first["export"]["path"])
        previous_bytes = previous_path.read_bytes()
        original_publish = story._publish_no_replace
        collided = []
        existing = b"concurrent user's existing material"

        def create_conflict(source, target):
            path = Path(target)
            with path.open("xb") as handle:
                handle.write(existing)
            collided.append(path)
            return original_publish(source, target)

        with patch.object(story, "_publish_no_replace", side_effect=create_conflict):
            with self.assertRaises(story.StoryError) as caught:
                publishing.export_material(self.book, prepared["id"])
        self.assertEqual(caught.exception.code, "publish_export_failed")
        self.assertEqual(len(collided), 1)
        self.assertEqual(collided[0].read_bytes(), existing)
        self.assertEqual(previous_path.read_bytes(), previous_bytes)
        self.assertEqual(self.files(), {previous_path, collided[0]})


if __name__ == "__main__":
    unittest.main()
