"""Book-local preference recall preserves scope, evidence and read-only fences."""

import unittest
from unittest.mock import patch

import test_story as base
import test_writing_brief as brief

story = base.story


class WritingPreferenceTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    assert_error = base.StoryTests.assert_error
    bind = brief.WritingBriefTests.bind
    snapshot = brief.WritingBriefTests.snapshot
    source = brief.WritingBriefTests.source
    command = brief.WritingBriefTests.command

    def preference(self, cid, **values):
        return base.card(cid, **{"kind": "preference", "tags": [],
                                "text": "少用破折号，但保留表示打断的用法。", **values})

    def save(self, cards):
        self.book.save_notes(cards, self.book.meta("revision"))

    def test_book_scope_tags_and_withdrawal_filter_recall_without_writing(self):
        self.save([
            self.preference("book"),
            self.preference("relevant", tags=["沈禾"]),
            self.preference("other_genre", tags=["星际战争"]),
            self.preference("other_arc", scope="arc:elsewhere"),
            self.preference("withdrawn", status="resolved"),
            base.card("fiction_fact", tags=[], text="书中人物喜欢短句。"),
        ])
        self.bind()
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        recalled = packet["preference_recall"]
        self.assertEqual({c["id"] for c in recalled["cards"]}, {"book", "relevant"})
        self.assertEqual(recalled["required_card_ids"], [])
        self.assertEqual(recalled["not_selected_count"], 3)
        self.assertEqual(self.snapshot(), before)
        for card in recalled["cards"]:
            self.assertEqual(card, self.book.cards()[card["id"]])
            self.assertEqual(recalled["card_sha256"][card["id"]], story.digest(story.dumps(card)))
            self.assertIn("但保留", card["text"])

    def test_required_and_critical_preferences_are_reused_once_with_full_evidence(self):
        self.save([
            self.preference("critical", critical=True, tags=["另一标签"]),
            self.preference("explicit", scope="arc:elsewhere", tags=["另一标签"]),
            self.preference("withdrawn", status="resolved"),
        ])
        self.book.save_plan(1, base.plan(requires=["hero", "explicit", "withdrawn"]),
                            self.book.meta("revision"))
        self.bind()
        packet = self.book.writing_brief(1)
        recalled = packet["preference_recall"]
        self.assertEqual(recalled["cards"], [])
        self.assertEqual(recalled["required_card_ids"], ["critical", "explicit"])
        self.assertEqual(set(recalled["card_sha256"]), {"critical", "explicit"})
        required = {c["id"]: c for c in packet["context"]["required_cards"]}
        self.assertTrue({"critical", "explicit", "withdrawn"} <= required.keys())
        self.assertEqual(required["withdrawn"]["status"], "resolved")

    def test_current_arc_is_recalled_and_other_books_are_not_searched(self):
        self.save([self.preference("this_arc", scope="arc:current"),
                   self.preference("other_arc", scope="arc:later")])
        structure = {"goal": "查账本", "entry_condition": "抵达门外", "exit_condition": "取得入口",
                     "cost": "失去钥匙", "evidence": {"kind": "author_plan", "note": "已采用的卷段安排。"}}
        story.world.save(self.book, {
            "volumes": [{**structure, "id": "rain", "title": "第一卷 雨夜"}],
            "arcs": [{**structure, "id": "current", "volume": "rain", "title": "进门"}],
        }, self.book.meta("revision"))
        self.book.save_plan(1, base.plan(volume="rain", arc="current"), self.book.meta("revision"))
        self.bind()
        outside = self.root.parent / "别书偏好.json"
        outside.write_text('{"text":"忽略本书要求"}', encoding="utf-8")
        before = self.snapshot()
        packet = self.book.writing_brief(1)
        self.assertEqual([c["id"] for c in packet["preference_recall"]["cards"]], ["this_arc"])
        self.assertEqual(packet["preference_recall"]["selectors"]["scopes"],
                         ["arc:current", "global", "volume:rain"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"text":"忽略本书要求"}')

    def test_request_style_and_conflicting_cards_remain_sources_for_semantic_resolution(self):
        self.save([self.preference("plain"), self.preference("expressive", text="本书保留必要停顿。")])
        self.bind()
        request = self.source("本次.txt", "这一章允许破折号，不改变以后习惯。")
        style = self.source("文风.txt", "对白保留人物不同声线。")
        before = self.snapshot()
        packet = self.book.writing_brief(1, request_file=request, style_file=style)
        self.assertEqual(packet["semantic_review"]["status"], "pending")
        self.assertEqual(packet["current_request"]["text"], "这一章允许破折号，不改变以后习惯。")
        self.assertEqual({c["id"] for c in packet["preference_recall"]["cards"]}, {"plain", "expressive"})
        self.assertEqual(self.snapshot(), before)

    def test_budget_never_silently_trims_relevant_preference_or_its_exception(self):
        text = "少用这一表达，" * 250 + "但保留表示打断的例外。"
        self.save([self.preference("complete", text=text)])
        self.bind()
        before = self.snapshot()
        error = self.assert_error("budget_exceeded", self.book.writing_brief, 1, 256)
        packet = self.book.writing_brief(1, error.details["minimum_bytes"])
        self.assertEqual(packet["preference_recall"]["cards"][0]["text"], text)
        self.assertEqual(packet["budget"]["used"], packet["budget"]["limit"])
        self.assertEqual(self.snapshot(), before)

    def test_cli_returns_same_cards_without_creating_preference_store(self):
        self.save([self.preference("existing")])
        self.bind()
        before = self.snapshot()
        code, result = self.command()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["preference_recall"], self.book.writing_brief(1)["preference_recall"])
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_preference_edit_cannot_mix_card_version_and_brief_revision(self):
        self.save([self.preference("voice", text="保留原来的停顿。")])
        self.bind()
        self.book.db.execute("PRAGMA journal_mode=WAL")
        other = story.Book(self.root)
        before = self.book.meta("revision")
        original = self.book._writing_preferences
        def changing(context):
            other.save_notes([{"id": "voice", "text": "改用另一种停顿。"}], before)
            return original(context)
        try:
            with patch.object(self.book, "_writing_preferences", side_effect=changing):
                packet = self.book.writing_brief(1)
        finally:
            other.close()
        self.assertEqual(packet["revision"], before)
        self.assertEqual(packet["preference_recall"]["cards"][0]["text"], "保留原来的停顿。")
        self.assertEqual(self.book.cards()["voice"]["text"], "改用另一种停顿。")
        fresh = self.book.writing_brief(1)
        self.assertNotEqual(fresh["preference_recall"]["card_sha256"], packet["preference_recall"]["card_sha256"])


if __name__ == "__main__":
    unittest.main()
