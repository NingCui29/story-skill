"""New historical card corrections must include their declared consumers."""
import unittest

import test_long_history as fixture

history, story = fixture.history, fixture.story


class HistoryStateScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.LongHistoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book

    def revision(self):
        return self.book.meta("revision")

    def snapshot(self):
        return self.revision(), tuple(self.book.db.iterdump())

    def assert_code(self, code, call):
        with self.assertRaises(story.StoryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def seed(self, imported_consumer=False):
        f = self.fixture
        original = self.book.cards(["key"])["key"]
        self.original = original
        self.old_sha = story.digest(story.dumps(original))
        independent = {"dependencies": [], "dependency_review": {
            "complete": True, "note": "本章独立，已核对全部依赖。"}}
        if imported_consumer:
            text = "# 第1章 借钥匙\n她交出钥匙，灯还亮着。\n"
            f.draft.write_bytes(text.encode("utf-8"))
            self.book.adopt(1, f.draft, "核对钥匙。", self.revision(), "第一卷 雨夜")
            f.texts[1] = text
            f.dep(1, extra=[{"kind": "card", "ref": "key", "sha": self.old_sha}])
            f.add(2, dependency_fields=independent)
            self.target = 2
        else:
            f.add(1, dependency_fields=independent)
            f.add(2, dependency_fields={"dependencies": [{"kind": "card", "ref": "key", "sha": self.old_sha}],
                "dependency_review": {"complete": True, "note": "第二章依赖已记录的钥匙状态。"}})
            self.target = 1
        started = f.start(self.target)
        self.assertEqual([item["chapter"] for item in started["affected"]], [self.target])
        return f.stage(started)

    def decision(self, keep=False):
        return {"id": "key", "before_sha": self.old_sha,
                "after": self.original if keep else {**self.original, "text": "钥匙已经交出。"},
                "chapter": self.target, "quote": "她交出钥匙", "note": "核对候选章后保留或补正钥匙终局。"}

    def test_changed_card_expands_transitive_scope_and_resets_all_reviews(self):
        staged = self.seed()
        f = self.fixture
        f.add(3, dependency_fields={"dependencies": [{"kind": "chapter", "ref": 2, "sha": story.digest(f.texts[2])}],
            "dependency_review": {"complete": True, "note": "第三章沿用第二章的交接。"}})
        f.add(4, dependency_fields={"dependencies": [], "dependency_review": {
            "complete": True, "note": "第四章独立，已核对全部依赖。"}})
        story.world.save(self.book, {"entities": [{"id": "shen", "name": "沈禾", "kind": "character", "description": "交接人"}],
            "facts": [{"id": "receipt", "subject": "shen", "predicate": "凭据", "value": "收据",
                       "evidence": {"kind": "chapter", "chapter": 2, "sha256": story.digest(f.texts[2]), "quote": "她交出钥匙"}}]}, self.revision())
        history.branch_refresh(self.book, staged["branch"], self.revision())
        result = history.branch_update(self.book, staged["branch"], {"state_changes": [self.decision()]}, self.revision())
        self.assertEqual([item["chapter"] for item in result["affected"]], [1, 2, 3])
        self.assertTrue(result["scope_expanded"])
        self.assertEqual(result["added_chapter_count"], 2)
        self.assertEqual(result["required_state_ids"], ["key"])
        self.assertEqual([item["id"] for item in result["world_review_required"]], ["receipt"])
        self.assertFalse(result["semantic_reviewed"])
        inspected = history.branch_inspect(self.book, staged["branch"], chapter=1)
        self.assertIsNone(inspected["candidate"]["review"])
        _, data = history._branch(self.book, staged["branch"])
        self.assertEqual(set(data["base_heads"]), {"1", "2", "3"})
        self.assertEqual(data["base_shas"]["2"], story.digest(f.texts[2]))
        self.assertIn("plan:3", data["fences"])
        self.assertIn("world.facts:receipt", data["fences"])
        self.assertEqual(data["fences"]["card:key"], self.old_sha)
        self.assert_code("review_incomplete", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))

    def test_keep_decision_does_not_expand_unrelated_consumers(self):
        staged = self.seed()
        result = history.branch_update(self.book, staged["branch"], {"state_changes": [self.decision(keep=True)]}, self.revision())
        self.assertEqual([item["chapter"] for item in result["affected"]], [1])
        self.assertFalse(result["scope_expanded"])
        self.assertIsNotNone(history.branch_inspect(self.book, staged["branch"], chapter=1)["candidate"]["review"])

    def test_deleting_a_card_also_requires_its_consumers(self):
        staged = self.seed()
        decision = {**self.decision(), "after": None}
        expanded = history.branch_update(self.book, staged["branch"], {"state_changes": [decision]}, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        self.assertTrue(expanded["scope_expanded"])
        self.assertEqual(self.book.cards(["key"])["key"], self.original)
        self.assert_code("review_incomplete", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))

    def test_restoring_a_candidate_card_keeps_its_already_expanded_scope(self):
        staged = self.seed()
        history.branch_update(self.book, staged["branch"], {"state_changes": [self.decision()]}, self.revision())
        restored = history.branch_update(self.book, staged["branch"], {"state_changes": [self.decision(keep=True)]}, self.revision())
        self.assertEqual([item["chapter"] for item in restored["affected"]], [1, 2])
        self.assertFalse(restored["scope_expanded"])
        refreshed = history.branch_refresh(self.book, staged["branch"], self.revision())
        self.assertEqual([item["chapter"] for item in refreshed["affected"]], [1, 2])

    def test_refresh_keeps_saved_extra_card_scope_and_publication_requires_new_reviews(self):
        staged = self.seed()
        old_consumer = history._head(self.book, 2)["id"]
        expanded = history.branch_update(self.book, staged["branch"], {"state_changes": [self.decision()]}, self.revision())
        self.book.save_notes([{"id": "unrelated", "text": "另一支线的便条。", "source": "作者补充"}], self.revision())
        refreshed = history.branch_refresh(self.book, staged["branch"], self.revision())
        self.assertEqual([item["chapter"] for item in refreshed["affected"]], [1, 2])
        self.assert_code("review_incomplete", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        reviewed = self.fixture.stage(expanded)
        published = history.branch_publish(self.book, staged["branch"], reviewed["revision"])
        self.assertTrue(published["committed"])
        self.assertEqual(published["chapters"], [1, 2])
        self.assertNotEqual(history._head(self.book, 2)["id"], old_consumer)
        self.assertEqual(self.book.cards(["key"])["key"]["text"], "钥匙已经交出。")

    def test_missing_plan_recovers_by_plan_refresh_and_resubmission(self):
        staged = self.seed(imported_consumer=True)
        payload = {"state_changes": [self.decision()]}
        before = self.snapshot()
        error = self.assert_code("plan_missing", lambda: history.branch_update(self.book, staged["branch"], payload, self.revision()))
        self.assertEqual(error.details["chapter"], 1)
        self.assertEqual(self.snapshot(), before)
        self.book.save_plan(1, self.book.get_plan(2), self.revision())
        history.branch_refresh(self.book, staged["branch"], self.revision())
        expanded = history.branch_update(self.book, staged["branch"], payload, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        reviewed = self.fixture.stage(expanded)
        self.assertTrue(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["committed"])

    def test_legacy_candidate_cannot_publish_without_expanding_its_saved_changes(self):
        staged = self.seed()
        # Simulate the candidate JSON persisted by runtimes before scope expansion.
        _, data = history._branch(self.book, staged["branch"])
        data.pop("impact_cards", None)
        data["state_changes"] = [self.decision()]
        data["semantic_review"]["manifest_sha256"] = history._manifest(data)
        with self.book.transaction():
            self.book.db.execute("UPDATE history_branches SET data=? WHERE id=?", (story.dumps(data), staged["branch"]))
        before = self.snapshot()
        error = self.assert_code("history_scope_changed", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        self.assertEqual(error.details["recovery_command"], "history-update")
        self.assertEqual(self.snapshot(), before)
        expanded = history.branch_update(self.book, staged["branch"], {}, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        self.assertFalse(expanded["semantic_reviewed"])
        self.assertIsNone(history.branch_inspect(self.book, staged["branch"], chapter=1)["candidate"]["review"])

    def test_legacy_expansion_missing_plan_can_refresh_then_resume(self):
        staged = self.seed(imported_consumer=True)
        _, data = history._branch(self.book, staged["branch"])
        data.pop("impact_cards", None)
        data["state_changes"] = [self.decision()]
        data["semantic_review"]["manifest_sha256"] = history._manifest(data)
        with self.book.transaction():
            self.book.db.execute("UPDATE history_branches SET data=? WHERE id=?", (story.dumps(data), staged["branch"]))
        self.assert_code("plan_missing", lambda: history.branch_update(self.book, staged["branch"], {}, self.revision()))
        self.book.save_plan(1, self.book.get_plan(2), self.revision())
        refreshed = history.branch_refresh(self.book, staged["branch"], self.revision())
        self.assertEqual([item["chapter"] for item in refreshed["affected"]], [2])
        # Refresh only acknowledges the existing scope; publication still fences
        # the extra correction until update migrates it into the reviewed scope.
        self.assert_code("history_scope_changed", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        expanded = history.branch_update(self.book, staged["branch"], {}, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        self.assertFalse(expanded["semantic_reviewed"])
        reviewed = self.fixture.stage(expanded)
        self.assertTrue(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["committed"])

    def test_failed_expansion_budget_does_not_partially_change_the_branch(self):
        staged = self.seed()
        before = self.snapshot()
        self.assert_code("budget_exceeded", lambda: history.branch_update(
            self.book, staged["branch"], {"state_changes": [self.decision()]}, self.revision(), budget=256))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
