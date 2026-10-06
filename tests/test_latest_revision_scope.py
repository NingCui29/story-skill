"""Latest-chapter repairs respect explicitly registered cross-chapter evidence."""
import json
import unittest

import test_long_history as base


story, history = base.story, base.history


class LatestRevisionScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.LongHistoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book, self.root, self.draft = self.fixture.book, self.fixture.root, self.fixture.draft

    def setup_pair(self, *, card_change=False):
        self.fixture.add(1)
        self.fixture.add(2, change=card_change)
        return self.fixture.texts[2].replace("一张收据", "两张收据")

    def delta(self, text, changes=()):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾核对交接并留下收据。", "changes": list(changes),
                "review": {"draft_sha256": story.digest(text), "checks": {
                    key: {"note": "复核交接与各章记载的一致性。", "quote": "灯还亮着。"}
                    for key in story.CHECKS}, "issues": []}}

    def snapshot(self):
        return list(self.book.db.iterdump()), {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and not path.name.startswith("state.sqlite3")}

    def assert_blocked(self, call):
        before = self.snapshot()
        with self.assertRaises(story.StoryError) as caught:
            call()
        self.assertEqual(caught.exception.code, "history_revision_required")
        self.assertEqual(caught.exception.details["dependent_chapters"], [1])
        self.assertEqual(caught.exception.details["recovery_command"], "history-start")
        self.assertEqual(self.snapshot(), before)

    def test_readonly_context_reports_scope_and_changed_prepare_is_blocked(self):
        changed = self.setup_pair()
        self.fixture.dep(1, [2])
        self.draft.write_bytes(changed.encode("utf-8"))
        before = self.snapshot()
        reader = story.Book(self.root, read_only=True)
        self.addCleanup(reader.close)
        for context in (reader.context(2), reader.reconcile(2)):
            self.assertEqual(context["replacement_scope"]["dependent_chapters"], [1])
            self.assertTrue(context["replacement_scope"]["history_required_if_changed"])
        self.assertEqual(self.snapshot(), before)
        self.assert_blocked(lambda: reader.prepare(2, self.draft))
        self.assert_blocked(lambda: reader.prepare(2, self.draft, reconcile=True))

    def test_changed_commit_and_external_reconcile_preserve_both_versions(self):
        changed = self.setup_pair()
        self.fixture.dep(1, [2])
        self.draft.write_bytes(changed.encode("utf-8"))
        self.assert_blocked(lambda: self.book.commit(2, self.draft, self.delta(changed), replace_last=True))
        exported = self.root / self.book.chapter_path(2)
        exported.write_bytes(changed.encode("utf-8"))
        delta = {**self.delta(changed), "external_sha256": story.digest(changed)}
        self.assert_blocked(lambda: self.book.reconcile(2, self.draft, delta))
        self.assertEqual(exported.read_bytes(), changed.encode("utf-8"))

    def test_produced_card_consumers_block_retraction_but_allow_exact_preservation(self):
        self.setup_pair(card_change=True)
        card = self.book.cards(["key"])["key"]
        self.fixture.dep(1, extra=[{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(card))}])
        original = self.fixture.texts[2]
        self.draft.write_bytes(original.encode("utf-8"))
        scope = self.book.context(2)["replacement_scope"]
        self.assertEqual(scope["dependent_chapters"], [1])
        prepared = self.book.prepare(2, self.draft)
        self.assertTrue(prepared["lint"]["ok"])
        self.assertEqual(prepared["replacement_scope"]["dependent_chapters"], [1])
        # Omitting the chapter's old delta would restore its pre-chapter card.
        self.assert_blocked(lambda: self.book.commit(2, self.draft, self.delta(original), replace_last=True))
        preserved = {"id": "key", "text": "钥匙已经交出。", "quote": "她交出钥匙"}
        result = self.book.commit(2, self.draft, self.delta(original, [preserved]), replace_last=True)
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.book.cards(["key"])["key"], card)

    def test_new_card_changes_cannot_bypass_consumers_outside_old_receipt(self):
        self.setup_pair()
        card = self.book.cards(["key"])["key"]
        self.fixture.dep(1, extra=[{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(card))}])
        original = self.fixture.texts[2]
        self.draft.write_bytes(original.encode("utf-8"))
        self.assertNotIn("replacement_scope", self.book.context(2))
        self.assertTrue(self.book.prepare(2, self.draft)["lint"]["ok"])
        change = {"id": "key", "text": "钥匙已经交出。", "quote": "她交出钥匙"}
        self.assert_blocked(lambda: self.book.commit(2, self.draft, self.delta(original, [change]), replace_last=True))

    def test_new_world_patch_requires_history_and_reviews_existing_consumers(self):
        entity = {"id": "shen", "name": "沈禾", "kind": "character", "description": "渡口交接人。"}
        story.world.save(self.book, {"entities": [entity]}, self.book.meta("revision"))
        dependency = {"kind": "entity", "ref": "shen", "sha": history._resolve(self.book, "entity", "shen")}
        self.fixture.add(1, dependency_fields={"dependencies": [dependency],
            "dependency_review": {"complete": True, "note": "已核对本章依赖的角色身份。"}})
        self.fixture.add(2, dependency_fields={"dependencies": [],
            "dependency_review": {"complete": True, "note": "已核对本章的独立交接。"}})
        original = self.fixture.texts[2]
        self.draft.write_bytes(original.encode("utf-8"))
        patch = {"entities": [{**entity, "name": "沈宁"}]}
        raw = {**self.delta(original), "world_changes": patch}
        before = self.snapshot()
        old_heads = {c: history._head(self.book, c)["id"] for c in (1, 2)}
        with self.assertRaises(story.StoryError) as caught:
            self.book.commit(2, self.draft, raw, replace_last=True)
        self.assertEqual(caught.exception.code, "history_revision_required")
        self.assertEqual(caught.exception.details["recovery_command"], "history-start")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(history._resolve(self.book, "entity", "shen"), dependency["sha"])

        packet = self.fixture.start(2)
        self.assertEqual([item["chapter"] for item in packet["affected"]], [2])
        revised = {c: self.fixture.texts[c].replace("沈禾", "沈宁") for c in (1, 2)}
        staged = history.branch_update(self.book, packet["branch"], {
            "chapters": [self.fixture.candidate(2, revised[2])], "world_changes": patch},
            self.book.meta("revision"))
        self.assertEqual([item["chapter"] for item in staged["affected"]], [1, 2])
        self.assertTrue(staged["scope_expanded"])
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [
            self.fixture.candidate(1, revised[1], [dependency]), self.fixture.candidate(2, revised[2])]},
            self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "已复核角色更名涉及的全部正文。",
                    "state_review": "卡片不变，世界角色定义与两章更名一致。",
                    "coverage_review": "角色消费者第1章已加入并复核。"}
        staged = history.branch_update(self.book, packet["branch"], {"semantic_review": semantic},
                                       self.book.meta("revision"))
        published = history.branch_publish(self.book, packet["branch"], staged["revision"])
        self.assertTrue(published["exports_complete"])
        self.assertEqual(published["chapters"], [1, 2])
        for chapter in (1, 2):
            self.assertNotEqual(history._head(self.book, chapter)["id"], old_heads[chapter])
            self.assertEqual((self.root / self.book.chapter_path(chapter)).read_text(encoding="utf-8"),
                             revised[chapter])
            self.fixture.texts[chapter] = revised[chapter]
        # Historical world publication requires explicitly rebinding the reviewed
        # consumer to its newly published world evidence version.
        current_dependency = {**dependency, "sha": history._resolve(self.book, "entity", "shen")}
        self.assertNotEqual(current_dependency["sha"], dependency["sha"])
        self.fixture.dep(1, extra=[current_dependency])
        self.assertTrue(history.cache_get(self.book, 1, "summary")["evidence_current"])

    def test_next_native_chapter_still_accepts_world_changes(self):
        entity = {"id": "shen", "name": "沈禾", "kind": "character", "description": "渡口交接人。"}
        self.fixture.add(1, dependency_fields={"world_changes": {"entities": [entity]}})
        self.assertIsNotNone(history._resolve(self.book, "entity", "shen"))
        receipt = json.loads(self.book.db.execute("SELECT receipt FROM chapters WHERE chapter=1").fetchone()[0])
        self.assertEqual(receipt["world_changes"][0]["id"], "shen")

    def test_older_version_of_same_card_does_not_block_latest_chapter_repair(self):
        self.fixture.add(1)
        old_card = self.book.cards(["key"])["key"]
        old_dependency = {"kind": "card", "ref": "key", "sha": story.digest(story.dumps(old_card))}
        self.fixture.dep(1, extra=[old_dependency])
        self.fixture.add(2, change=True)
        current_card = self.book.cards(["key"])["key"]
        self.assertNotEqual(story.digest(story.dumps(current_card)), old_dependency["sha"])
        # Chapter 1 still records the before-chapter-2 value, which is normal
        # when the same card advances as subsequent chapters are committed.
        self.assertNotIn("replacement_scope", self.book.context(2))
        changed = self.fixture.texts[2].replace("一张收据", "两张收据")
        self.draft.write_bytes(changed.encode("utf-8"))
        self.assertTrue(self.book.prepare(2, self.draft)["lint"]["ok"])
        change = {"id": "key", "text": "钥匙已经交出，交接留有收据。", "quote": "她交出钥匙"}
        result = self.book.commit(2, self.draft, self.delta(changed, [change]), replace_last=True)
        self.assertTrue(result["exports_complete"])
        self.assertEqual(history.read_dependencies(self.book, 1)["dependencies"], [old_dependency])
        self.assertEqual(self.book.cards(["key"])["key"]["text"], change["text"])
        # A declaration of the new current version must still block retraction.
        self.fixture.texts[2] = changed
        current = self.book.cards(["key"])["key"]
        self.fixture.dep(1, extra=[{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(current))}])
        self.assert_blocked(lambda: self.book.commit(2, self.draft, self.delta(changed), replace_last=True))

    def test_scope_ignores_noncurrent_body_hash_and_missing_card_baseline(self):
        self.setup_pair(card_change=True)
        card = self.book.cards(["key"])["key"]
        self.fixture.dep(1, [2], extra=[{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(card))}])
        # Model a stale declaration retained by an older version of the tool;
        # changing today's body cannot invalidate that already different SHA.
        with self.book.transaction():
            head = history._head(self.book, 1)
            dependencies = history._edges(self.book, head["id"])
            for dependency in dependencies:
                if dependency["kind"] == "chapter":
                    dependency["sha"] = story.digest("archived earlier body")
            history._version(self.book, 1, self.fixture.texts[1], head["summary"],
                             json.loads(head["receipt"]), json.loads(head["plan"]), dependencies, True)
        scope = history.replacement_scope(self.book, 2)
        self.assertEqual(scope["dependent_chapter_count"], 0)
        missing = history.replacement_scope(self.book, 2, body_changed=False,
                                             changed_cards=["key"], card_baseline={"key": None})
        self.assertEqual(missing["dependent_chapter_count"], 0)
        present = history.replacement_scope(self.book, 2, body_changed=False, changed_cards=["key"])
        self.assertEqual(present["dependent_chapters"], [1])

    def test_retired_edges_do_not_block_and_idempotent_retry_ignores_new_consumers(self):
        changed = self.setup_pair()
        self.fixture.dep(1, [2])
        self.fixture.dep(1, [])
        self.assertNotIn("replacement_scope", self.book.context(2))
        self.draft.write_bytes(changed.encode("utf-8"))
        self.assertTrue(self.book.prepare(2, self.draft)["lint"]["ok"])
        delta = self.delta(changed)
        self.assertTrue(self.book.commit(2, self.draft, delta, replace_last=True)["exports_complete"])
        self.fixture.texts[2] = changed
        self.fixture.dep(1, [2])
        revision = self.book.meta("revision")
        retried = self.book.commit(2, self.draft, delta, replace_last=True)
        self.assertTrue(retried["idempotent"])
        self.assertEqual(self.book.meta("revision"), revision)

    def test_same_body_review_and_summary_refresh_does_not_invalidate_consumers(self):
        self.setup_pair()
        self.fixture.dep(1, [2])
        original = self.fixture.texts[2]
        self.draft.write_bytes(original.encode("utf-8"))
        self.assertTrue(self.book.prepare(2, self.draft)["lint"]["ok"])
        result = self.book.commit(2, self.draft, self.delta(original), replace_last=True)
        self.assertTrue(result["exports_complete"])
        dependencies = history.read_dependencies(self.book, 1)["dependencies"]
        self.assertEqual(dependencies[0]["sha"], story.digest(original))

    def test_readonly_scope_never_seeds_missing_legacy_history(self):
        self.setup_pair()
        with self.book.transaction():
            self.book.db.execute("DELETE FROM history_heads")
        before = self.snapshot()
        reader = story.Book(self.root, read_only=True)
        self.addCleanup(reader.close)
        self.assertNotIn("replacement_scope", reader.context(2))
        self.assertTrue(reader.prepare(2, self.draft)["lint"]["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_history_branch_reviews_both_chapters_and_publishes_rebound_evidence(self):
        changed = self.setup_pair()
        self.fixture.dep(1, [2])
        packet = self.fixture.start(2)
        self.assertEqual([item["chapter"] for item in packet["affected"]], [1, 2])
        dependency = {"kind": "chapter", "ref": "2", "sha": story.digest(changed)}
        first = self.fixture.texts[1].replace("一张收据", "两张收据")
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [
            self.fixture.candidate(1, first, [dependency]), self.fixture.candidate(2, changed)]},
            self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "两章交接与倒叙证据已经完整复核。",
                    "state_review": "现行卡片与修改后的两章一致。",
                    "coverage_review": "已检查其他章节，没有遗漏消费者。"}
        staged = history.branch_update(self.book, packet["branch"], {"semantic_review": semantic},
                                       self.book.meta("revision"))
        result = history.branch_publish(self.book, packet["branch"], staged["revision"])
        self.assertTrue(result["exports_complete"])
        self.assertEqual(result["chapters"], [1, 2])
        self.assertEqual(history.read_dependencies(self.book, 1)["dependencies"], [dependency])
        for chapter, text in ((1, first), (2, changed)):
            self.assertEqual((self.root / self.book.chapter_path(chapter)).read_bytes(), text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
