"""Behavioral regressions for world context, title counts, and status receipts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("skill_audit_fixes_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


def plan(**changes):
    return {
        "volume_dir": "第一卷 交接", "title": "核对承诺",
        "goal": "核对承诺后决定下一步", "stop": "记入账本后停笔",
        "constraints": [], "requires": [], "tags": [], "length": [8, 200],
        "beats": [{"choice": "核对承诺", "change": "留下交接凭据"}],
        **changes,
    }


class TitleClosingMarkerTests(unittest.TestCase):
    def test_optional_closing_markers_do_not_count_as_title_text(self):
        cases = (
            ("# 标题 ###\n正文#号", 6),
            ("# 标题\t### \t\n正文#号", 6),
            ("\ufeff# 标题 ###\r\n正文#号", 6),
            ("# C# ###\n正文", 4),
            ("# 标题 # #\n正文", 5),
            ("# ###\n正文", 2),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                counts = story.manuscript_counts(text, include_title=True)
                self.assertEqual(counts["visible_nonspace_v1"], expected)

    def test_hashes_without_valid_closing_syntax_remain_content(self):
        cases = (
            ("# 标题#\n正文#号", 7),
            ("# 标题 ### 后缀\n正文", 9),
            ("# 标题 \\###\n正文", 8),
            ("# 标题\u00a0###\n正文", 7),
            ("# 标题 ###\u00a0\n正文", 7),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                counts = story.manuscript_counts(text, include_title=True)
                self.assertEqual(counts["visible_nonspace_v1"], expected)

    def test_title_count_option_controls_length_gate_without_marker_characters(self):
        text = "# 标题 ###\n正文#号"
        with_title = story.lint_text(text, plan(count_title=True, length=[6, 6]))
        without_title = story.lint_text(text, plan(count_title=False, length=[4, 4]))
        self.assertTrue(with_title["ok"], with_title)
        self.assertTrue(without_title["ok"], without_title)
        self.assertEqual(with_title["length_count"], 6)
        self.assertEqual(without_title["length_count"], 4)


class SkillAuditBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-skill-audit-fixes-")
        self.root = Path(self.temp.name) / "book"
        story.Book.create(self.root, "技能行为回归夹具", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def revision(self):
        return self.book.meta("revision")

    def delta(self, book, text):
        return {
            "book_id": book.meta("id"), "base_revision": book.meta("revision"),
            "summary": "核对承诺后留下交接凭据。", "changes": [],
            "review": {
                "draft_sha256": story.digest(text),
                "checks": {
                    name: {"note": "隔离夹具已核对动作与后果。", "quote": "留下交接凭据"}
                    for name in story.CHECKS
                },
                "issues": [],
            },
        }

    def save_global_rule(self):
        baseline = "# 第1章 立誓\n所有立誓者必须兑现承诺。\n"
        self.draft.write_bytes(baseline.encode("utf-8"))
        adopted = self.book.adopt(
            1, self.draft, "立誓规则已有正文证据。", self.revision(),
            volume_dir="第一卷 交接",
        )
        self.assertTrue(adopted["exports_complete"], adopted)
        story.world.save(self.book, {"rules": [{
            "id": "global-promise", "rule": "promise", "version": 1,
            "start": 0, "hard": True, "description": "所有立誓者必须兑现承诺。",
            "evidence": {
                "kind": "chapter", "chapter": 1, "sha256": story.digest(baseline),
                "quote": "所有立誓者必须兑现承诺。",
            },
        }]}, self.revision())
        self.book.save_plan(2, plan(), self.revision())
        self.assertTrue({"volume", "arc", "line", "entities", "time"}.isdisjoint(
            self.book.get_plan(2)
        ))

    def test_context_keeps_global_hard_rule_without_extra_plan_fields(self):
        self.save_global_rule()
        packet = self.book.context(2)
        self.assertIn("world", packet)
        rules = {record["id"]: record for record in packet["world"]["rules"]}
        self.assertIn("global-promise", rules)
        self.assertTrue(rules["global-promise"]["hard"])
        self.assertEqual(rules["global-promise"]["evidence"]["mode"], "chapter")

    def test_prepare_checks_global_rule_without_extra_plan_fields(self):
        self.save_global_rule()
        self.draft.write_text(
            "# 第2章 核对承诺\n沈禾核对誓言，决定先留下交接凭据。\n", encoding="utf-8"
        )
        prepared = self.book.prepare(2, self.draft)
        self.assertIn("world_check", prepared)
        self.assertIn("ok", prepared["world_check"])
        self.assertEqual(prepared["world_check"]["scope"]["entities"], [])
        self.assertIn("story_time_unknown", {
            warning["code"] for warning in prepared["world_check"]["warnings"]
        })
        self.assertFalse(prepared["ready_to_commit"])

    def test_unpublished_global_rule_stays_planned_and_is_checked(self):
        self.book.save_plan(1, plan(), self.revision())
        story.world.save(self.book, {"rules": [{
            "id": "planned-promise", "rule": "promise", "version": 1,
            "start": 0, "hard": True, "description": "立誓者须兑现承诺。",
            "evidence": {"kind": "author_plan", "note": "已采用的规则，尚无正文证据。"},
        }]}, self.revision())
        packet = self.book.context(1)
        self.assertIn("world", packet)
        self.assertEqual(packet["world"]["rules"], [])
        self.assertEqual(
            [record["id"] for record in packet["world"]["planned"]["rules"]],
            ["planned-promise"],
        )
        self.draft.write_text(
            "# 第1章 核对承诺\n沈禾核对誓言，决定先留下交接凭据。\n", encoding="utf-8"
        )
        self.assertIn("world_check", self.book.prepare(1, self.draft))

    def assert_status_snapshot(self, integrity):
        self.book.integrity = integrity
        first = "# 第1章 核对承诺\n沈禾核对承诺，然后留下交接凭据。\n"
        self.book.save_plan(1, plan(), self.revision())
        self.draft.write_bytes(first.encode("utf-8"))
        initial = self.book.commit(1, self.draft, self.delta(self.book, first))
        self.assertTrue(initial["scope_exports_complete"], initial)
        self.book.save_plan(2, plan(), self.revision())
        old_revision = self.revision()
        # Permit a real writer to commit while the repaired reader holds its snapshot.
        self.book.db.execute("PRAGMA journal_mode=WAL")
        writer = story.Book(self.root)
        try:
            second = first.replace("第1章", "第2章")
            second_draft = self.root / "concurrent-draft.md"
            second_draft.write_bytes(second.encode("utf-8"))
            raw = self.delta(writer, second)
            original_rows = self.book._artifact_rows
            writer_receipts = []

            def commit_after_reader_selects_rows():
                rows = original_rows()
                if not writer_receipts:
                    with patch.object(story, "atomic_write", side_effect=OSError(
                        "simulated second chapter export failure"
                    )):
                        writer_receipts.append(writer.commit(2, second_draft, raw))
                return rows

            with patch.object(self.book, "_artifact_rows", side_effect=commit_after_reader_selects_rows):
                observed = self.book.status()

            self.assertEqual(len(writer_receipts), 1)
            receipt = writer_receipts[0]
            self.assertTrue(receipt["committed"], receipt)
            self.assertFalse(receipt["exports_complete"], receipt)
            missing_path = writer.chapter_path(2)
            self.assertFalse((self.root / missing_path).exists())
            self.assertEqual(observed["next_chapter"], observed["last_chapter"] + 1, observed)
            # Either coherent snapshot is valid; combining old health with new progress is not.
            if observed["revision"] == old_revision:
                self.assertEqual(observed["last_chapter"], 1, observed)
                self.assertEqual(observed["pending_export_count"], 0, observed)
                self.assertEqual(observed["integrity"]["unverified_archive_count"], 0, observed)
                self.assertEqual(observed["integrity"]["full_book_verified"], integrity == "strict")
            else:
                self.assertEqual(observed["revision"], receipt["revision"], observed)
                self.assertEqual(observed["last_chapter"], 2, observed)
                self.assertIn(missing_path, observed["pending_exports"], observed)
                self.assertFalse(observed["integrity"]["full_book_verified"], observed)
            self.assertFalse(self.book.db.in_transaction)
            fresh = self.book.status()
            self.assertEqual(fresh["revision"], receipt["revision"])
            self.assertIn(missing_path, fresh["pending_exports"], fresh)
            self.assertFalse(fresh["integrity"]["full_book_verified"], fresh)
        finally:
            writer.close()

    def test_strict_status_does_not_mix_old_health_with_new_publication(self):
        self.assert_status_snapshot("strict")

    def test_local_status_does_not_mix_old_health_with_new_publication(self):
        self.assert_status_snapshot("local")

    def world_check_fixture(self):
        text = "# 第1章 开账\n沈禾开账时有一百枚铜钱，随后留下交接凭据。\n"
        self.draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(1, self.draft, "开账一百枚。", self.revision(), "第一卷 交接")
        story.world.save(self.book, {
            "entities": [
                {"id": "actor", "name": "沈禾", "kind": "character", "description": "持款人"},
                {"id": "coin", "name": "铜钱", "kind": "resource", "description": "支付用钱"},
            ],
            "transfers": [{
                "id": "opening", "resource": "coin", "receiver": "actor", "amount": "100",
                "quantity_text": "一百枚", "at": 0, "opening": True,
                "evidence": {"kind": "chapter", "chapter": 1, "sha256": story.digest(text),
                             "quote": "沈禾开账时有一百枚铜钱"},
            }],
        }, self.revision())
        self.book.save_plan(2, plan(entities=["actor"], time={"clock": "main", "start": 10, "end": 10}),
                            self.revision())
        return story.parser().parse_args(["world-check", "--book", str(self.root), "--chapter", "2"])

    def test_world_check_keeps_plan_and_evidence_in_one_snapshot(self):
        args = self.world_check_fixture()
        before = story.run(args)
        old_revision = self.revision()
        self.book.db.execute("PRAGMA journal_mode=WAL")
        writer = story.Book(self.root)
        original_get_plan = story.Book.get_plan
        updates = []

        def change_plan_and_add_earlier_proposal(reader, chapter):
            selected = original_get_plan(reader, chapter)
            if reader is not writer and not updates:
                updates.append(True)
                # Both complete states have no spending in their selected time window.
                writer.save_plan(2, plan(entities=["actor"], time={"clock": "main", "start": 20, "end": 20}),
                                 writer.meta("revision"))
                story.world.save(writer, {"transfers": [{
                    "id": "earlier-proposal", "resource": "coin", "sender": "actor", "amount": "150",
                    "quantity_text": "一百五十枚", "at": 10,
                    "evidence": {"kind": "author_plan", "note": "保留较早时刻的未采用付款方案。"},
                }]}, writer.meta("revision"))
            return selected

        try:
            with patch.object(story.Book, "get_plan", change_plan_and_add_earlier_proposal):
                observed = story.run(args)
            after = story.run(args)
            self.assertTrue(before["ok"], before)
            self.assertTrue(after["ok"], after)
            self.assertTrue(observed["ok"], observed)
            self.assertEqual(observed["scope"]["start"], 10)
            self.assertEqual(observed["revision"], old_revision)
            self.assertGreater(after["revision"], old_revision)
        finally:
            writer.close()

    def test_world_check_identifies_its_read_version_and_honors_budget(self):
        args = self.world_check_fixture()
        revision = self.revision()
        result = story.run(args)
        self.assertEqual(result["book_id"], self.book.meta("id"))
        self.assertEqual(result["revision"], revision)
        self.assertEqual(result["chapter"], 2)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))
        self.assertEqual(self.revision(), revision)
        args.budget_bytes = 256
        with self.assertRaises(story.StoryError) as error:
            story.run(args)
        self.assertEqual(error.exception.code, "budget_exceeded")
        self.assertEqual(self.revision(), revision)


if __name__ == "__main__":
    unittest.main()
