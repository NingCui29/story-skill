"""A writing brief assembles evidence without adopting, trimming or changing it."""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from outline_fixture import bind_adopted_outline
import test_story as base


story = base.story


class WritingBriefTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    assert_error = base.StoryTests.assert_error
    delta = base.StoryTests.delta

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def source(self, name, data):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return name

    def bind(self, chapter=1):
        return bind_adopted_outline(story, self.book, chapter)

    def command(self, *args, chapter=1, root=None):
        result = subprocess.run([sys.executable, "-B", str(base.TOOL), "writing-brief",
                                 "--book", str(root or self.root), "--chapter", str(chapter), *args],
                                capture_output=True, text=True, encoding="utf-8")
        return result.returncode, json.loads(result.stdout or result.stderr)

    def test_legacy_unsubmitted_default_is_read_only_blocked_until_plan_sync_and_rebind(self):
        # Install a pre-upgrade plan directly: current save_plan correctly
        # rejects this unexcepted finite range before it could enter the book.
        legacy = self.book.get_plan(1)
        legacy.update(length=[2400, 2800], count_method="visible_nonspace_v2", count_title=False)
        legacy.pop("length_exception", None)
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(legacy),))
        self.bind()
        before = self.snapshot()
        error = self.assert_error("length_exception_required", self.book.writing_brief, 1)
        self.assertEqual(error.details["saved_length"], [2400, 2800])
        self.assertEqual(error.details["default_length"], [2000, None])
        self.assertEqual(error.details["recovery_commands"], ["plan", "outline-bind"])
        self.assertEqual(self.book.get_plan(1), legacy)
        self.assertEqual(self.snapshot(), before)

        binding = story.outline.binding_for(self.book, 1)
        self.book.save_plan(1, {**legacy, "length": [2000, None]}, self.book.meta("revision"))
        before_rebind = self.snapshot()
        self.assert_error("outline_plan_drift", self.book.writing_brief, 1)
        self.assertEqual(self.snapshot(), before_rebind)
        story.outline.bind(self.book, 1, binding["path"], self.book.meta("revision"), binding["sha256"])
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        self.assertEqual(packet["context"]["plan"]["length"], [2000, None])
        self.assertNotIn("length_exception", packet["context"]["plan"])
        self.assertEqual(self.snapshot(), before)

    def test_explicit_user_finite_agreement_remains_available_in_the_brief(self):
        current = self.book.get_plan(1)
        current.update(length=[2400, 2800], count_method="visible_nonspace_v2", count_title=False,
                       length_exception={"source": "user_request", "quote": "本章明确按2400到2800字写作。"})
        self.book.save_plan(1, current, self.book.meta("revision"))
        self.bind()
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        self.assertEqual(packet["context"]["plan"]["length"], [2400, 2800])
        self.assertEqual(packet["context"]["plan"]["length_exception"], current["length_exception"])
        self.assertEqual(self.snapshot(), before)

    def test_brief_rechecks_current_book_agreement_without_repairing_changed_sources(self):
        quote = "本章采用2400到2800字的明确篇幅约定。"
        agreement = self.root / "创作约定.md"
        agreement.write_text(quote + "\n", encoding="utf-8")
        current = self.book.get_plan(1)
        current.update(length=[2400, 2800], count_method="visible_nonspace_v2", count_title=False,
                       length_exception={"source": "book_agreement", "path": "创作约定.md", "quote": quote})
        self.book.save_plan(1, current, self.book.meta("revision"))
        self.bind()
        for replacement in (None, "原有篇幅原句已被移除。\n"):
            with self.subTest(replacement=replacement):
                agreement.write_text(quote + "\n", encoding="utf-8")
                self.assertEqual(self.book.writing_brief(1)["context"]["plan"]["length"], [2400, 2800])
                if replacement is None:
                    agreement.unlink()
                else:
                    agreement.write_text(replacement, encoding="utf-8")
                before, revision = self.snapshot(), self.book.meta("revision")
                self.assert_error("length_exception_source_missing", self.book.writing_brief, 1)
                self.assertEqual(self.book.meta("revision"), revision)
                self.assertEqual(self.book.get_plan(1), current)
                self.assertEqual(self.snapshot(), before)

    def test_default_minimum_only_plan_survives_api_and_cli_brief_readback(self):
        current = self.book.get_plan(1)
        current.update(length=[2000, None], count_method="visible_nonspace_v2", count_title=False)
        current.pop("length_exception", None)
        self.book.save_plan(1, current, self.book.meta("revision"))
        self.bind()
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        self.assertEqual(packet["context"]["plan"]["length"], [2000, None])
        code, actual = self.command()
        self.assertEqual(code, 0, actual)
        self.assertEqual(actual["context"]["plan"]["length"], [2000, None])
        self.assertEqual(self.snapshot(), before)

    def test_complete_sources_are_identified_and_not_adopted_as_style_decisions(self):
        self.bind()
        request_raw = b"\xef\xbb\xbf" + "只写到进门；这不是采用新剧情。\r\n".encode("utf-8")
        request = self.source("00_项目策划/本次请求.txt", request_raw)
        style = self.source("00_项目策划/本书文风.md", "保留人物说话差异。候选：全改成短句。\n")
        before = self.snapshot()
        packet = self.book.writing_brief(1, request_file=request, style_file=style)
        self.assertEqual(packet["context"]["plan"], self.book.get_plan(1))
        self.assertEqual(packet["context"]["required_cards"], [self.book.cards()["hero"]])
        self.assertEqual(packet["current_request"]["sha256"], hashlib.sha256(request_raw).hexdigest())
        self.assertEqual(packet["current_request"]["text"], request_raw.decode("utf-8-sig"))
        outline = packet["adopted_outline"]
        self.assertEqual(outline["text"], (self.root / outline["path"]).read_bytes().decode("utf-8-sig"))
        self.assertEqual(outline["sha256"], packet["context"]["outline"]["sha256"])
        self.assertEqual(packet["semantic_review"]["status"], "pending")
        self.assertEqual(packet["book_style_source"]["status"], "provided")
        self.assertEqual(packet["semantic_review"]["missing_sources"], [])
        self.assertEqual(packet["delivery_scope"]["stop"], self.book.get_plan(1)["stop"])
        self.assertEqual(packet["budget"]["used"], len(story.dumps(packet).encode("utf-8")))
        self.assertEqual(self.snapshot(), before)

    def test_no_request_or_style_reports_missing_sources_without_requiring_new_metadata(self):
        self.bind()
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        for role in ("current_request", "book_style_source"):
            self.assertEqual(packet[role], {"status": "not_provided"})
        self.assertEqual(packet["semantic_review"]["missing_sources"], ["current_request", "book_style_source"])
        self.assertEqual(self.snapshot(), before)

    def test_unbound_or_generated_candidate_does_not_become_an_adopted_outline(self):
        candidate = self.source("01_大纲细纲/第1章 候选.md", self.book.plan_render(1)["text"])
        before = self.snapshot()
        self.assert_error("outline_unbound", self.book.writing_brief, 1)
        self.assertIsNone(story.outline.binding_for(self.book, 1))
        self.assertTrue((self.root / candidate).is_file())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.book.context(1)["outline"]["status"], "unbound")

    def test_plan_drift_and_outline_drift_still_fail(self):
        self.bind()
        self.book.save_plan(1, base.plan(stop="停在门外"), self.book.meta("revision"))
        before = self.snapshot()
        self.assert_error("outline_plan_drift", self.book.writing_brief, 1)
        self.assertEqual(self.snapshot(), before)
        # Rebinding is explicit fixture preparation, never performed by the brief.
        record = story.outline.binding_for(self.book, 1)
        story.outline.bind(self.book, 1, record["path"], self.book.meta("revision"), record["sha256"])
        self.source(record["path"], "# 第1章\n状态：已采用\n现在越过停笔点。\n")
        before = self.snapshot()
        self.assert_error("outline_plan_drift", self.book.writing_brief, 1)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_source_errors_preserve_original_files(self):
        self.bind()
        bad = (("不存在.txt", None, "brief_source_missing"),
               ("空白.txt", b" \n", "brief_source_empty"),
               ("错误编码.txt", b"\xff", "brief_source_encoding"))
        for relative, raw, code in bad:
            with self.subTest(code=code):
                if raw is not None:
                    self.source(relative, raw)
                before = self.snapshot()
                self.assert_error(code, self.book.writing_brief, 1, request_file=relative)
                self.assertEqual(self.snapshot(), before)

    def test_path_escape_symlink_parent_symlink_and_hardlink_are_rejected(self):
        self.bind()
        original = self.source("00_项目策划/请求.txt", "本书请求\n")
        for relative in ("../别书.txt", str(self.root / original)):
            self.assert_error("path_escape", self.book.writing_brief, 1, request_file=relative)
        outside = self.root.parent / "外部.txt"
        outside.write_text("外部材料", encoding="utf-8")
        (self.root / "软链接.txt").symlink_to(outside)
        (self.root / "目录链接").symlink_to(self.root / "00_项目策划", target_is_directory=True)
        os.link(self.root / original, self.root / "硬链接.txt")
        before = self.snapshot()
        for relative in ("软链接.txt", "目录链接/请求.txt", "硬链接.txt"):
            with self.subTest(relative=relative):
                self.assert_error("linked_path", self.book.writing_brief, 1, request_file=relative)
        self.assertEqual(self.snapshot(), before)

    def test_large_required_context_and_complete_sources_are_never_trimmed_to_fit(self):
        self.book.save_notes([base.card("must", critical=True, text="关键世界约束。" * 200)], self.book.meta("revision"))
        self.bind()
        request = self.source("请求.txt", "完整请求。" * 400)
        before = self.snapshot()
        error = self.assert_error("budget_exceeded", self.book.writing_brief, 1, 2000, request_file=request)
        packet = self.book.writing_brief(1, error.details["minimum_bytes"], request_file=request)
        self.assertEqual(packet["current_request"]["text"], (self.root / request).read_text(encoding="utf-8"))
        self.assertEqual({card["id"] for card in packet["context"]["required_cards"]}, {"hero", "must"})
        self.assertEqual(self.snapshot(), before)

    def test_reported_minimum_budget_fits_the_whole_brief_in_one_retry(self):
        self.bind()
        request = self.source("请求.txt", "完整写作请求。" * 100)
        style = self.source("文风.txt", "保留本书缓慢节奏。" * 50)
        before = self.snapshot()
        error = self.assert_error("budget_exceeded", self.book.writing_brief, 1, 256,
                                  request_file=request, style_file=style)
        packet = self.book.writing_brief(1, error.details["minimum_bytes"], request_file=request, style_file=style)
        self.assertEqual(packet["budget"]["used"], packet["budget"]["limit"])
        self.assertIn("完整写作请求", packet["current_request"]["text"])
        self.assertEqual(self.snapshot(), before)

    def test_optional_cards_are_explicitly_omitted_instead_of_crowding_out_sources(self):
        self.book.save_notes([base.card("extra", text="很长的可选关联。" * 200)], self.book.meta("revision"))
        self.bind()
        packet = self.book.writing_brief(1, 10000)
        self.assertEqual(packet["context"]["optional_cards"], [])
        self.assertEqual(packet["context"]["omitted_optional_count"], 1)

    def test_next_chapter_includes_previous_transition_and_submitted_chapter_is_out_of_scope(self):
        self.bind()
        self.book.commit(1, self.draft, self.delta())
        self.book.save_plan(2, base.plan(), self.book.meta("revision"))
        self.bind(2)
        before = self.snapshot()
        packet = self.book.writing_brief(2)
        self.assertEqual(packet["context"]["previous"]["chapter"], 1)
        self.assertEqual(packet["context"]["previous"]["tail"], base.DRAFT)
        self.assert_error("writing_brief_scope", self.book.writing_brief, 1)
        self.assert_error("writing_brief_scope", self.book.writing_brief, 3)
        self.assertEqual(self.snapshot(), before)

    def test_export_drift_and_missing_required_card_remain_blockers(self):
        self.bind()
        self.book.save_plan(1, base.plan(requires=["missing"]), self.book.meta("revision"))
        record = story.outline.binding_for(self.book, 1)
        story.outline.bind(self.book, 1, record["path"], self.book.meta("revision"), record["sha256"])
        self.assert_error("missing_required_cards", self.book.writing_brief, 1)
        self.book.save_plan(1, base.plan(), self.book.meta("revision"))
        story.outline.bind(self.book, 1, record["path"], self.book.meta("revision"), record["sha256"])
        self.book.commit(1, self.draft, self.delta())
        self.book.save_plan(2, base.plan(), self.book.meta("revision"))
        self.bind(2)
        self.source(self.book.chapter_path(1), "作者尚未复核的外部修改\n")
        before = self.snapshot()
        self.assert_error("exports_unresolved", self.book.writing_brief, 2)
        self.assertEqual(self.snapshot(), before)

    def test_cli_uses_read_only_connection_and_does_not_create_missing_book(self):
        self.bind()
        before = self.snapshot()
        code, packet = self.command()
        self.assertEqual(code, 0, packet)
        self.assertTrue(packet["read_only"])
        self.assertEqual(self.snapshot(), before)
        missing = self.root / "未建书库"
        code, packet = self.command(root=missing)
        self.assertEqual(code, 2, packet)
        self.assertEqual(packet["error"], "book_missing")
        self.assertFalse(missing.exists())
        args = story.parser().parse_args(["writing-brief", "--book", str(self.root), "--chapter", "1"])
        original = story.Book
        captured = []
        def opening(*values, **kwargs):
            book = original(*values, **kwargs)
            captured.append(book.db.execute("PRAGMA query_only").fetchone()[0])
            return book
        with patch.object(story, "Book", side_effect=opening):
            story.run(args)
        self.assertEqual(captured, [1])

    def test_context_composition_keeps_owning_snapshot_and_does_not_write(self):
        self.bind()
        before = self.snapshot()
        forbidden = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
        self.book.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK)
        try:
            with self.book.read_snapshot():
                self.book.writing_brief(1)
                self.assertTrue(self.book.db.in_transaction)
            self.assertFalse(self.book.db.in_transaction)
            self.assert_error("budget_exceeded", self.book.writing_brief, 1, 256)
            self.assertFalse(self.book.db.in_transaction)
        finally:
            self.book.db.set_authorizer(None)
        self.assertEqual(self.snapshot(), before)

    def test_windows_read_directory_pins_parents_without_creating_a_guard(self):
        # Execute the Windows branch with fake handles; this is not a native Windows run.
        target = self.root.resolve() / "00_项目策划"
        opened, closed = [], []
        @contextmanager
        def handle(path, directory=False, write_shared=False):
            self.assertTrue(directory)
            self.assertFalse(write_shared)
            opened.append(str(path))
            try:
                yield object()
            finally:
                closed.append(str(path))
        with patch.object(story.os, "name", "nt"), patch.object(story, "_windows_path_handle", handle), \
                patch.object(story, "_windows_directory_guard", side_effect=AssertionError("No temporary guard")):
            with story._pinned_read_directory(target) as directory:
                self.assertEqual(directory.path, target)
                self.assertEqual(closed, [])
        self.assertEqual(closed, list(reversed(opened)))
        self.assertEqual(len(opened), len(target.parts))

    def test_reading_a_replaced_source_does_not_certify_the_old_path(self):
        self.bind()
        relative = self.source("请求.txt", "原始请求\n")
        replacement = self.root / "待换.txt"
        replacement.write_text("变更请求\n", encoding="utf-8")
        original = story._bound_reader
        blocked = []
        @contextmanager
        def replacing(file):
            with original(file) as stream:
                if str(file.path) == str((self.root / relative).resolve()):
                    original_read = stream.read
                    def after_read(*args):
                        result = original_read(*args)
                        if os.name == "nt":
                            # Native strict directory handles prevent this replacement.
                            with self.assertRaises(PermissionError):
                                replacement.replace(self.root / relative)
                            blocked.append(True)
                        else:
                            replacement.replace(self.root / relative)
                        return result
                    with patch.object(stream, "read", side_effect=after_read):
                        yield stream
                else:
                    yield stream
        with patch.object(story, "_bound_reader", replacing):
            if os.name == "nt":
                packet = self.book.writing_brief(1, request_file=relative)
                self.assertEqual(packet["current_request"]["text"], "原始请求\n")
                self.assertEqual(packet["current_request"]["sha256"],
                                 hashlib.sha256("原始请求\n".encode()).hexdigest())
                self.assertTrue(blocked)
                self.assertTrue(replacement.is_file())
            else:
                self.assert_error("brief_source_changed", self.book.writing_brief, 1, request_file=relative)

    @unittest.skipUnless(os.name == "nt", "Native Windows path/handle timestamp semantics")
    def test_path_ctime_semantics_do_not_reject_a_stable_source(self):
        self.bind()
        relative = self.source("请求.txt", "完整请求。\r\n")
        source = self.root / relative
        original = Path.lstat
        def creation_time(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == source:
                values = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
                values["st_ctime_ns"] += 1
                return SimpleNamespace(**values)
            return info
        before = self.snapshot()
        with patch.object(Path, "lstat", creation_time):
            packet = self.book.writing_brief(1, request_file=relative)
        self.assertEqual(packet["current_request"]["text"], "完整请求。\r\n")
        self.assertEqual(packet["current_request"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(self.snapshot(), before)

    def test_state_hardlink_is_rejected(self):
        self.bind()
        os.link(self.book.path, self.root.parent / "state-alias.sqlite3")
        self.assert_error("linked_path", self.book.writing_brief, 1)

    @unittest.skipIf(os.name == "nt", "POSIX directory replacement; Windows strict handles prevent renaming")
    def test_replaced_parent_alias_does_not_rebind_the_displayed_source_path(self):
        self.bind()
        relative = self.source("materials/请求.txt", "本书原始请求\n")
        parent = self.book.root / "materials"
        external = self.root.parent / "外部目录"
        external.mkdir()
        external.joinpath("请求.txt").write_text("外部文本\n", encoding="utf-8")
        original = story._bound_reader
        @contextmanager
        def replacing(file):
            with original(file) as stream:
                if file.path.parent == parent:
                    original_read = stream.read
                    def after_read(*args):
                        result = original_read(*args)
                        parent.rename(self.book.root / "saved-materials")
                        parent.symlink_to(external, target_is_directory=True)
                        return result
                    with patch.object(stream, "read", side_effect=after_read):
                        yield stream
                else:
                    yield stream
        with patch.object(story, "_bound_reader", replacing):
            self.assert_error("brief_source_changed", self.book.writing_brief, 1, request_file=relative)
        self.assertEqual(external.joinpath("请求.txt").read_text(encoding="utf-8"), "外部文本\n")

    def test_book_root_alias_retains_existing_canonicalization_and_explicit_identity(self):
        self.bind()
        linked = self.root.parent / "linked-book"
        linked.symlink_to(self.root.resolve(), target_is_directory=True)
        before = self.snapshot()
        code, result = self.command(root=linked)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["canonical_book_root"], str(self.root.resolve()))
        self.assertEqual(result["book_id"], self.book.meta("id"))
        self.assertEqual(self.snapshot(), before)

    def test_wal_writer_cannot_mix_new_cards_into_the_brief_read_snapshot(self):
        self.bind()
        self.book.db.execute("PRAGMA journal_mode=WAL")
        other = story.Book(self.root)
        previous_revision = self.book.meta("revision")
        original = story.world.context
        fired = []
        def concurrent_write(book, plan, chapter, budget=None):
            if not fired:
                other.save_notes([base.card("new_optional", text="下一版本新添的关联。")], previous_revision)
                fired.append(True)
            return original(book, plan, chapter, budget)
        try:
            with patch.object(story.world, "context", concurrent_write):
                packet = self.book.writing_brief(1)
            self.assertEqual(packet["revision"], previous_revision)
            self.assertEqual(packet["context"]["revision"], previous_revision)
            self.assertEqual(packet["context"]["omitted_optional_count"], 0)
            self.assertEqual(self.book.meta("revision"), previous_revision + 1)
            self.assertIn("new_optional", self.book.cards())
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
