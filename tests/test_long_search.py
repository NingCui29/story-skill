import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "story_search_test", ROOT / "skills/story-skill/scripts/story_search.py")
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(search.SCHEMA)

    def tearDown(self):
        self.db.close()

    def put(self, key, text, kind="card", metadata=None):
        return search.upsert(self.db, kind, key, text, metadata)

    def test_two_character_and_long_names_and_single_character_title(self):
        text = "# 北库\n江棠让杜承安核对借条。顾主任随后打来电话。"
        self.put("chapter:1", text, "chapter", {"path": "chapters/0001.md"})
        self.put("unrelated", "江边有人，棠树无声。杜家承接平安渡运。")
        for term in ("江棠", "杜承安", "顾"):
            packet = search.query(self.db, term)
            self.assertTrue(packet["complete"])
            self.assertEqual(packet["total"], 1)
            hit = packet["matches"][0]
            self.assertEqual(text[hit["start"]:hit["end"]], term)
            self.assertEqual(hit["quote"], term)
            self.assertEqual(hit["source_sha256"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(hit["line"], 2)
            self.assertEqual(hit["metadata"]["path"], "chapters/0001.md")

    def test_literal_punctuation_english_spaces_and_unicode_offsets(self):
        text = "灯😀亮。\n编号 A-19 / pump_v2。江棠：别签！"
        self.put("evidence", text)
        for term in ("A-19", "pump_v2", " / ", "：别签！", "😀", "\n编号"):
            hit = search.query(self.db, term)["matches"][0]
            self.assertEqual(text[hit["start"]:hit["end"]], term)
            self.assertIn(term, hit["snippet"])
        absent = search.query(self.db, "a-19")
        self.assertTrue(absent["no_match_confirmed"])
        self.assertTrue(absent["capabilities"]["case_sensitive"])

    def test_bigram_presence_is_not_an_exact_match(self):
        self.put("false-positive", "江棠在门口。棠把借条收好。")
        packet = search.query(self.db, "江棠把")
        self.assertEqual(packet["metrics"]["documents_verified"], 1)
        self.assertEqual(packet["matches"], [])
        self.assertTrue(packet["no_match_confirmed"])
        self.assertFalse(packet["candidates"][0]["exact_match"])

    def test_twenty_thousand_unrelated_cards_do_not_scan_source_texts(self):
        for number in range(20_000):
            self.put(f"unrelated:{number}", "北仓盘点完毕，库门已经关好。")
        self.put("jiang", "江棠收起了借条。")
        self.put("du", "杜承安把泵送回北库。")
        for term, key in (("江棠", "jiang"), ("杜承安", "du")):
            packet = search.query(self.db, term)
            self.assertEqual(packet["scope"]["indexed_documents"], 20_002)
            self.assertEqual([hit["id"] for hit in packet["matches"]], [key])
            self.assertEqual(packet["metrics"]["seed_postings_read"], 1)
            self.assertEqual(packet["metrics"]["unique_sources_read"], 1)
            self.assertEqual(packet["metrics"]["posting_candidates_examined"], 1)
        plan = self.db.execute("EXPLAIN QUERY PLAN SELECT document_id FROM search_postings "
                               "WHERE gram=? ORDER BY kind,document_id LIMIT ?", ("江棠", 31)).fetchall()
        self.assertTrue(any("SEARCH" in row[3] and "PRIMARY KEY" in row[3] for row in plan))
        self.assertFalse(any("TEMP B-TREE" in row[3] for row in plan))

    def test_identical_hash_keeps_distinct_sources_and_reads_object_once(self):
        text = "江棠检查借条。" * 1000
        self.put("hero", text, metadata={"path": "cards/hero.json"})
        self.put("1", text, "chapter", {"path": "chapters/0001.md"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM core_objects").fetchone()[0], 1)
        packet = search.query(self.db, "江棠")
        self.assertEqual({(hit["kind"], hit["key"]) for hit in packet["matches"]},
                         {("card", "hero"), ("chapter", "1")})
        self.assertEqual(packet["metrics"]["unique_sources_read"], 1)
        self.assertEqual(packet["metrics"]["documents_verified"], 2)
        grams = len(set(text) | {text[i:i + 2] for i in range(len(text) - 1)})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM search_postings").fetchone()[0], 2 * grams)

    def test_removing_one_shared_source_preserves_other_identity_and_object(self):
        self.put("hero", "江棠保管借条。")
        self.put("1", "江棠保管借条。", "chapter")
        search.remove(self.db, "card", "hero")
        packet = search.query(self.db, "江棠")
        self.assertEqual([(hit["kind"], hit["id"]) for hit in packet["matches"]], [("chapter", "1")])
        self.assertEqual(packet["metrics"]["seed_document_frequency"], 1)
        search.remove(self.db, "chapter", "1")
        self.assertTrue(search.query(self.db, "江棠")["no_match_confirmed"])
        # Search cannot know whether chapter/artifact pointers still own this object.
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM core_objects").fetchone()[0], 1)

    def test_update_remove_and_metadata_only_updates_preserve_identity(self):
        self.put("hero", "江棠保管原件。")
        original = search.query(self.db, "江棠")["matches"][0]["document_id"]
        self.put("hero", "杜承安保管副本。")
        self.assertTrue(search.query(self.db, "江棠")["no_match_confirmed"])
        self.assertEqual(search.query(self.db, "杜承安")["matches"][0]["document_id"], original)
        postings = self.db.execute("SELECT * FROM search_postings ORDER BY gram,kind,document_id").fetchall()
        self.put("hero", "杜承安保管副本。", metadata={"path": "new.json"})
        self.assertEqual(postings, self.db.execute(
            "SELECT * FROM search_postings ORDER BY gram,kind,document_id").fetchall())
        self.assertEqual(search.query(self.db, "杜承安")["matches"][0]["metadata"], {"path": "new.json"})
        self.assertTrue(search.remove(self.db, "card", "hero"))
        self.assertFalse(search.remove(self.db, "card", "hero"))
        self.assertTrue(search.query(self.db, "杜承安")["no_match_confirmed"])
        for table in ("search_documents", "search_postings", "search_grams", "search_kinds"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_candidate_cutoff_never_claims_no_match_and_kind_filter_uses_index(self):
        for number in range(5):
            self.put(str(number), "甲乙丙乙甲")
        self.put("last", "甲乙甲", "chapter")
        with patch.object(search, "MAX_CANDIDATES", 3):
            packet = search.query(self.db, "甲乙甲")
            self.assertEqual(packet["matches"], [])
            self.assertFalse(packet["complete"])
            self.assertFalse(packet["no_match_confirmed"])
            self.assertIsNone(packet["total"])
            self.assertEqual(packet["truncated_reasons"], ["candidate_limit"])
            filtered = search.query(self.db, "甲乙甲", kinds=["chapter"])
            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["metrics"]["seed_postings_read"], 1)
            self.assertTrue(filtered["complete"])
        self.assertEqual(search.query(self.db, "甲乙甲", kinds=[])["scope"]["indexed_documents"], 0)

    def test_result_and_character_limits_are_explicit_and_results_stable(self):
        for number in range(4):
            self.put(str(number), "江棠" + "旁" * number)
        first = search.query(self.db, "江棠", limit=1)
        self.assertEqual(first["matches"], search.query(self.db, "江棠", limit=1)["matches"])
        self.assertIn("result_limit", first["truncated_reasons"])
        self.assertIsNone(first["total"])
        with patch.object(search, "MAX_VERIFICATION_CHARS", 1):
            packet = search.query(self.db, "江棠")
            self.assertEqual(len(packet["matches"]), 1)
            self.assertIn("verification_char_limit", packet["truncated_reasons"])
            self.assertFalse(packet["search_complete"])

    def test_injected_helpers_and_caller_transaction_rollback(self):
        originals = (search.fail, search.dumps, search.digest)
        def failure(code, message, **details):
            raise RuntimeError(code)
        try:
            search.inject(SimpleNamespace(fail=failure, dumps=lambda v: json.dumps(v, ensure_ascii=False),
                                          digest=lambda text: hashlib.sha256(text.encode()).hexdigest()))
            with self.assertRaisesRegex(RuntimeError, "invalid_input"):
                search.query(self.db, "")
            self.put("hero", "江棠")
            self.assertTrue(self.db.in_transaction)
            search.query(self.db, "江棠")
            self.assertTrue(self.db.in_transaction)
            self.db.rollback()
            self.assertEqual(search.query(self.db, "江棠")["total"], 0)
            self.assertFalse(self.db.in_transaction)
        finally:
            search.fail, search.dumps, search.digest = originals

    def test_failed_update_restores_old_postings_source_and_metadata(self):
        self.put("hero", "江棠持有原件。", metadata={"revision": 1})
        self.db.commit()
        self.db.execute("CREATE TEMP TRIGGER search_test_failure BEFORE INSERT ON search_postings "
                        "WHEN NEW.gram='杜承' BEGIN SELECT RAISE(ABORT,'simulated index write failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated index write failure"):
            self.put("hero", "杜承安持有副本。", metadata={"revision": 2})
        self.assertEqual(search.query(self.db, "江棠")["matches"][0]["metadata"], {"revision": 1})
        self.assertTrue(search.query(self.db, "杜承安")["no_match_confirmed"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM core_objects").fetchone()[0], 1)
        # A failed index update also leaves caller transaction ownership intact.
        self.assertTrue(self.db.in_transaction)


if __name__ == "__main__":
    unittest.main()
