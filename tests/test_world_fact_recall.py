"""A change of owner must not leave the previous owner's context stale."""
from pathlib import Path
import tempfile
import unittest

import test_context_dependencies as integration
from test_long_world import FixtureBook, WorldError, entity, plan, planned, world


class FactRecallTests(unittest.TestCase):
    def setUp(self):
        self.book = FixtureBook()
        self.ev1 = self.book.chapter(1, "甲收起钥匙和账册。")
        self.ev2 = self.book.chapter(2, "甲把钥匙交给乙，账册仍由甲保管。")
        self.save(entities=[entity("a", "甲"), entity("b", "乙"),
                            entity("key", "钥匙", "item"), entity("ledger", "账册", "item")])
        self.save(facts=[self.fact("key-a", "甲", ["a"], 0)])

    def tearDown(self):
        self.book.db.close()

    def save(self, **payload):
        world.save(self.book, payload, self.book.meta("revision"))

    def fact(self, rid, value, entities, start, **overrides):
        return {"id": rid, "subject": "key", "predicate": "持有人", "value": value,
                "entities": entities, "start": start, "evidence": self.ev1, **overrides}

    def context(self, at=20, chapter=3):
        return world.context(self.book, plan(["a"], at=at), chapter)

    def test_handover_updates_previous_holder_context_and_preserves_flashback(self):
        self.save(facts=[self.fact("key-b", "乙", ["b"], 10, evidence=self.ev2)])
        self.assertEqual([r["value"] for r in self.context()["facts"]], ["乙"])
        self.assertEqual([r["value"] for r in self.context(at=5)["facts"]], ["甲"])
        self.assertEqual([r["value"] for r in self.context(chapter=2)["facts"]], ["甲"])

    def assert_ambiguous_handover(self, at, warning):
        self.save(facts=[self.fact("key-b", "乙", ["b"], at, evidence=self.ev2)])
        packet = self.context()
        self.assertEqual({r["id"] for r in packet["facts"]}, {"key-a", "key-b"})
        self.assertIn(warning, {r["code"] for r in packet["warnings"]})

    def test_unknown_handover_keeps_both_candidates(self):
        self.assert_ambiguous_handover(None, "world_time_unknown")

    def test_tied_handover_keeps_both_candidates(self):
        self.assert_ambiguous_handover(0, "world_order_ambiguous")

    def test_proposed_handover_does_not_change_observed_ownership(self):
        self.save(facts=[self.fact("future-owner", "乙", ["b"], 10, evidence=planned())])
        packet = self.context()
        self.assertEqual([r["id"] for r in packet["facts"]], ["key-a"])
        self.assertEqual([r["id"] for r in packet["planned"]["facts"]], ["future-owner"])

    def test_related_slot_does_not_pull_in_other_items_predicates_or_clocks(self):
        self.save(facts=[self.fact("key-b", "乙", ["b"], 10, evidence=self.ev2),
                         self.fact("other-clock", "甲", ["b"], 15, clock="dream"),
                         self.fact("other-item", "乙", ["b"], 15, subject="ledger"),
                         self.fact("other-property", "旧铜", ["b"], 15, predicate="材质")])
        self.assertEqual([r["id"] for r in self.context()["facts"]], ["key-b"])

    def test_new_owner_evidence_must_be_current_even_without_old_owner_tag(self):
        self.save(facts=[self.fact("key-b", "乙", ["b"], 10, evidence=self.ev2)])
        world.invalidate_chapters(self.book, [2])
        with self.assertRaises(WorldError) as caught:
            self.context()
        self.assertEqual(caught.exception.code, "stale_world_evidence")
        self.assertEqual(caught.exception.details["id"], "key-b")

    def test_retired_association_does_not_recall_an_unrelated_owner(self):
        self.save(facts=[self.fact("key-b", "乙", ["b"], 10, evidence=self.ev2)])
        correction = self.book.chapter(3, "先前误记，钥匙从未由甲持有。")
        with self.book.transaction(self.book.meta("revision")):
            world.invalidate_chapters(self.book, [1])
            world.apply_in_transaction(self.book, {"retirements": [{"kind": "facts", "id": "key-a",
                "reason": "纠正此前记错的持有人", "evidence": correction}]}, repair=True)
        self.assertEqual(self.context(chapter=4)["facts"], [])


class FactDependencyIntegrationTests(unittest.TestCase):
    def test_dependencies_use_recalled_owner_evidence_without_mutating_book(self):
        story = integration.story
        with tempfile.TemporaryDirectory(prefix="story-fact-recall-") as directory:
            root = Path(directory) / "book"
            story.Book.create(root, "钥匙交接", "long")
            book = story.Book(root)
            try:
                text = "# 第1章 交接\n甲先收起钥匙，十刻后把钥匙交给乙。\n"
                draft = root / "source.md"
                draft.write_bytes(text.encode("utf-8"))
                book.adopt(1, draft, "甲把钥匙交给乙。", book.meta("revision"), "第一卷 夜门")
                evidence = {"kind": "chapter", "chapter": 1, "sha256": story.digest(text),
                            "quote": text.splitlines()[1]}
                story.world.save(book, {"entities": [entity("a", "甲"), entity("b", "乙"), entity("key", "钥匙", "item")],
                    "facts": [{"id": rid, "subject": "key", "predicate": "持有人", "value": value,
                               "entities": [holder], "start": at, "evidence": evidence}
                              for rid, value, holder, at in (("key-a", "甲", "a", 0), ("key-b", "乙", "b", 10))]}, book.meta("revision"))
                book.save_plan(2, {"volume_dir": "第一卷 夜门", "title": "索钥", "goal": "甲取回钥匙",
                    "stop": "向乙提出请求", "beats": [{"choice": "向乙索钥", "change": "等待回复"}],
                    "length": [1, 100], "requires": [], "tags": [], "entities": ["a"],
                    "time": {"clock": "main", "start": 20, "end": 20}}, book.meta("revision"))
                revision = book.meta("revision")
                result = book.dependency_candidates(2)
                found = {(r["kind"], r["ref"]): r["sha"] for r in result["candidates"]}
                self.assertIn(("world.facts", "key-b"), found)
                self.assertNotIn(("world.facts", "key-a"), found)
                self.assertEqual(found[("world.facts", "key-b")], book.world_read("facts", "key-b")["record_sha256"])
                self.assertEqual(book.meta("revision"), revision)
            finally:
                book.close()


if __name__ == "__main__":
    unittest.main()
