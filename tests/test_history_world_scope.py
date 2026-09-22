"""Historical world edits include consumers of mutable definitions and plans."""
import unittest

import test_long_history as fixture

history, story = fixture.history, fixture.story


class HistoryWorldScopeTests(unittest.TestCase):
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

    def seed(self, kind="entities", alias="entity", imported_consumer=False):
        self.kind = kind
        self.entity = {"id": "shen", "name": "沈禾", "kind": "character", "description": "渡口交接人。"}
        story.world.save(self.book, {"entities": [self.entity]}, self.revision())
        self.original = self.entity
        self.change = {**self.entity, "name": "沈宁"}
        if kind == "rules":
            self.original = {"id": "joint-v1", "rule": "joint", "version": 1, "start": 0,
                             "description": "钥匙交给守门人。", "entities": ["shen"],
                             "evidence": {"kind": "author_plan", "note": "作者采用的交接规则。"}}
            story.world.save(self.book, {kind: [self.original]}, self.revision())
            self.change = {**self.original, "description": "钥匙留在库房。"}
        self.record = self.original["id"]
        self.old_sha = history._resolve(self.book, alias, self.record)
        dependency = {"kind": alias, "ref": self.record, "sha": self.old_sha}
        independent = {"dependencies": [], "dependency_review": {"complete": True, "note": "本章独立，已核全。"}}
        dependent = {"dependencies": [dependency], "dependency_review": {"complete": True, "note": "本章依赖该世界记录，已核全。"}}
        f = self.fixture
        if imported_consumer:
            text = "# 第1章 交接\n她交出钥匙，灯还亮着。\n"
            f.draft.write_bytes(text.encode("utf-8"))
            self.book.adopt(1, f.draft, "核对交接。", self.revision(), "第一卷 雨夜")
            f.texts[1] = text
            f.dep(1, extra=[dependency])
            f.add(2, dependency_fields=independent)
            self.target, self.consumer = 2, 1
        else:
            f.add(1, dependency_fields=independent)
            f.add(2, dependency_fields=dependent)
            self.target, self.consumer = 1, 2
        started = f.start(self.target)
        self.assertEqual([item["chapter"] for item in started["affected"]], [self.target])
        return f.stage(started)

    def patch(self, keep=False):
        return {"world_changes": {self.kind: [self.original if keep else self.change]}}

    def review(self, packet, texts=None):
        texts = {item["chapter"]: (texts or {}).get(item["chapter"], self.fixture.texts[item["chapter"]])
                 for item in packet["affected"]}
        candidates = []
        for chapter, text in texts.items():
            deps = history._edges(self.book, history._head(self.book, chapter)["id"])
            deps = [{**dep, "sha": story.digest(texts[int(dep["ref"])])}
                    if dep["kind"] == "chapter" and int(dep["ref"]) in texts else dep for dep in deps]
            candidates.append(self.fixture.candidate(chapter, text, deps))
        staged = history.branch_update(self.book, packet["branch"], {"chapters": candidates}, self.revision())
        return self.fixture.semantic_after(staged, {})

    def assert_alias_expansion(self, kind, alias):
        staged = self.seed(kind, alias)
        f = self.fixture
        f.add(3, dependency_fields={"dependencies": [{"kind": "world." + kind, "ref": self.record, "sha": self.old_sha}],
            "dependency_review": {"complete": True, "note": "本章使用同一记录的 typed 引用。"}})
        f.add(4, dependency_fields={"dependencies": [{"kind": "chapter", "ref": 2, "sha": story.digest(f.texts[2])}],
            "dependency_review": {"complete": True, "note": "本章沿用第二章。"}})
        f.add(5, dependency_fields={"dependencies": [], "dependency_review": {"complete": True, "note": "本章独立。"}})
        history.branch_refresh(self.book, staged["branch"], self.revision())
        result = history.branch_update(self.book, staged["branch"], self.patch(), self.revision())
        self.assertEqual([item["chapter"] for item in result["affected"]], [1, 2, 3, 4])
        self.assertEqual(result["added_chapter_count"], 3)
        self.assertTrue(result["scope_expanded"])
        self.assertFalse(result["semantic_reviewed"])
        self.assertIsNone(history.branch_inspect(self.book, staged["branch"], chapter=1)["candidate"]["review"])
        _, data = history._branch(self.book, staged["branch"])
        self.assertEqual(data["fences"]["world." + kind + ":" + self.record], self.old_sha)
        self.assertIn("world." + kind + ":" + self.record, data["entity_search_hints"])
        self.assertIn("plan:4", data["fences"])
        self.assertEqual(history._resolve(self.book, alias, self.record), self.old_sha)
        self.assert_code("review_incomplete", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        old_heads = {chapter: history._head(self.book, chapter)["id"] for chapter in (2, 3, 4)}
        reviewed = self.review(result)
        published = history.branch_publish(self.book, staged["branch"], reviewed["revision"])
        self.assertEqual(published["chapters"], [1, 2, 3, 4])
        for chapter in (2, 3, 4):
            self.assertNotEqual(history._head(self.book, chapter)["id"], old_heads[chapter])
        for chapter, dep_kind in ((2, alias), (3, "world." + kind)):
            self.assertEqual(history._edges(self.book, history._head(self.book, chapter)["id"]),
                             [{"kind": dep_kind, "ref": self.record, "sha": self.old_sha}])
            self.assertFalse(history.cache_get(self.book, chapter, "summary")["evidence_current"])
        self.assertEqual([item["chapter"] for item in self.fixture.start(1)["affected"]], [1, 2, 3, 4])

    def test_entity_aliases_and_transitive_consumers_expand(self):
        self.assert_alias_expansion("entities", "entity")

    def test_author_plan_rule_aliases_and_transitive_consumers_expand(self):
        self.assert_alias_expansion("rules", "rule")

    def test_normalized_unchanged_record_does_not_expand(self):
        staged = self.seed("rules", "rule")
        # Explicit defaults and repeated links normalize to the original record.
        same = {**self.original, "clock": "main", "end": None, "hard": False, "line": None,
                "cooldown": None, "entities": ["shen", "shen"], "requires": []}
        result = history.branch_update(self.book, staged["branch"], {"world_changes": {"rules": [same]}}, self.revision())
        self.assertEqual([item["chapter"] for item in result["affected"]], [1])
        self.assertFalse(result["scope_expanded"])
        self.assertIsNotNone(history.branch_inspect(self.book, staged["branch"], chapter=1)["candidate"]["review"])
        reviewed = self.review(result)
        self.assertTrue(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["committed"])
        receipt = story.json.loads(history._head(self.book, 1)["receipt"])
        self.assertNotIn("history_world_ids", receipt)
        self.assertEqual([item["chapter"] for item in self.fixture.start(1)["affected"]], [1])

    def test_refresh_preserves_world_scope_and_rejects_changed_record_fence(self):
        staged = self.seed("rules", "rule")
        history.branch_update(self.book, staged["branch"], self.patch(), self.revision())
        self.book.save_notes([{"id": "unrelated", "text": "另一张便条。", "source": "作者补充"}], self.revision())
        refreshed = history.branch_refresh(self.book, staged["branch"], self.revision())
        self.assertEqual([item["chapter"] for item in refreshed["affected"]], [1, 2])
        story.world.save(self.book, {"rules": [{**self.original, "description": "另一会话修改了规则。"}]}, self.revision())
        before = self.snapshot()
        self.assert_code("stale_dependency", lambda: history.branch_refresh(self.book, staged["branch"], self.revision()))
        self.assertEqual(self.snapshot(), before)

    def test_restoring_world_record_does_not_shrink_expanded_scope(self):
        staged = self.seed()
        history.branch_update(self.book, staged["branch"], self.patch(), self.revision())
        restored = history.branch_update(self.book, staged["branch"], self.patch(keep=True), self.revision())
        self.assertEqual([item["chapter"] for item in restored["affected"]], [1, 2])
        self.assertFalse(restored["scope_expanded"])
        self.assertEqual([item["chapter"] for item in history.branch_refresh(self.book, staged["branch"], self.revision())["affected"]], [1, 2])

    def legacy(self, staged):
        _, data = history._branch(self.book, staged["branch"])
        data.pop("impact_world", None)
        data["world_changes"] = self.patch()["world_changes"]
        data["semantic_review"]["manifest_sha256"] = history._manifest(data)
        with self.book.transaction():
            self.book.db.execute("UPDATE history_branches SET data=? WHERE id=?", (story.dumps(data), staged["branch"]))

    def test_legacy_world_patch_cannot_publish_without_expanding_consumers(self):
        staged = self.seed()
        self.legacy(staged)
        before = self.snapshot()
        self.assert_code("history_scope_changed", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        self.assertEqual(self.snapshot(), before)
        result = history.branch_update(self.book, staged["branch"], {}, self.revision())
        self.assertEqual([item["chapter"] for item in result["affected"]], [1, 2])
        old_consumer = history._head(self.book, 2)["id"]
        reviewed = self.review(result)
        published = history.branch_publish(self.book, staged["branch"], reviewed["revision"])
        self.assertEqual(published["chapters"], [1, 2])
        self.assertNotEqual(history._head(self.book, 2)["id"], old_consumer)
        self.assertEqual(history._edges(self.book, history._head(self.book, 2)["id"]),
                         [{"kind": "entity", "ref": self.record, "sha": self.old_sha}])
        self.assertNotEqual(history._resolve(self.book, "entity", self.record), self.old_sha)
        self.assertEqual([item["chapter"] for item in self.fixture.start(1)["affected"]], [1, 2])

    def test_missing_plan_and_legacy_world_patch_recover_without_restarting(self):
        staged = self.seed(imported_consumer=True)
        self.legacy(staged)
        before = self.snapshot()
        self.assert_code("plan_missing", lambda: history.branch_update(self.book, staged["branch"], {}, self.revision()))
        self.assertEqual(self.snapshot(), before)
        self.book.save_plan(1, self.book.get_plan(2), self.revision())
        history.branch_refresh(self.book, staged["branch"], self.revision())
        self.assert_code("history_scope_changed", lambda: history.branch_publish(self.book, staged["branch"], self.revision()))
        expanded = history.branch_update(self.book, staged["branch"], {}, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        reviewed = self.review(expanded)
        self.assertTrue(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["committed"])

    def test_pending_chapter_evidence_and_same_batch_new_entity_remain_stageable(self):
        staged = self.seed()
        revised = self.fixture.texts[1] + "沈禾确认了新的称呼。\n"
        patch = {"entities": [self.change, {"id": "other", "name": "另一人", "kind": "character", "description": "新人物。"}],
                 "facts": [{"id": "new-location", "subject": "other", "predicate": "位置", "value": "渡口",
                            "evidence": {"kind": "chapter", "chapter": 1, "sha256": story.digest(revised), "quote": "灯还亮着。"}}]}
        expanded = history.branch_update(self.book, staged["branch"], {
            "chapters": [{"chapter": 1, "text": revised}], "world_changes": patch}, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1, 2])
        reviewed = self.review(expanded, {1: revised})
        self.assertTrue(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["committed"])
        self.assertIsNotNone(story.world.resolve_dependency(self.book, "facts", "new-location"))

    def test_budget_failure_rolls_back_world_scope_and_saved_patch(self):
        staged = self.seed()
        before = self.snapshot()
        self.assert_code("budget_exceeded", lambda: history.branch_update(self.book, staged["branch"], self.patch(), self.revision(), budget=256))
        self.assertEqual(self.snapshot(), before)

    def fact_consumer(self, kind, observed=True, retired=False, source_observed=False):
        f = self.fixture
        independent = {"dependencies": [], "dependency_review": {"complete": True, "note": "本章依赖已核全。"}}
        f.add(1, dependency_fields=independent)
        f.add(2, dependency_fields=independent)
        planned = {"kind": "author_plan", "note": "作者采用的保管条件。"}
        source = {"kind": "chapter", "chapter": 1, "sha256": story.digest(f.texts[1]), "quote": "她交出钥匙"} if source_observed else planned
        fact = {"id": "condition", "subject": "shen", "predicate": "保管要求", "value": "交出钥匙后留下收据。", "evidence": source}
        evidence = {"kind": "chapter", "chapter": 2, "sha256": story.digest(f.texts[2]), "quote": "她交出钥匙"} if observed else planned
        record = ({"id": "handoff-v1", "rule": "handoff", "version": 1, "start": 0,
                   "description": "交接需要遵守保管条件。", "requires": [fact["id"]], "evidence": evidence}
                  if kind == "rules" else {"id": "shen-knows", "actor": "shen", "fact": fact["id"],
                                          "state": "knows" if observed else "unknown", "channel": "交接现场", "evidence": evidence})
        story.world.save(self.book, {"entities": [{"id": "shen", "name": "沈禾", "kind": "character", "description": "交接人。"}],
                                    "facts": [fact], kind: [record]}, self.revision())
        if retired:
            staged = f.stage(f.start(2))
            retired_record = {"kind": kind, "id": record["id"], "reason": "复核撤回该记录。", "evidence": evidence}
            reviewed = f.semantic_after(staged, {"world_changes": {"retirements": [retired_record]}})
            history.branch_publish(self.book, staged["branch"], reviewed["revision"])
            f.add(3, dependency_fields=independent)
        else:
            dep = {"kind": "world." + kind, "ref": record["id"], "sha": history._resolve(self.book, "world." + kind, record["id"])}
            f.add(3, dependency_fields={"dependencies": [dep], "dependency_review": {"complete": True, "note": "依赖已核全。"}})
        staged = f.stage(f.start(1))
        patch = {"world_changes": {"facts": [{**fact, "value": "交出钥匙后不得保留收据。"}]}}
        return staged, patch, record

    def assert_fact_consumer_expansion(self, kind, observed):
        staged, patch, record = self.fact_consumer(kind, observed)
        expanded = history.branch_update(self.book, staged["branch"], patch, self.revision())
        expected = [1, 2, 3] if observed else [1, 3]
        self.assertEqual([item["chapter"] for item in expanded["affected"]], expected)
        self.assertTrue(expanded["scope_expanded"])
        _, data = history._branch(self.book, staged["branch"])
        self.assertIn("world." + kind + ":" + record["id"], data["fences"])
        self.assertEqual([item["id"] for item in expanded["world_review_required"]], [record["id"]] if observed else [])
        if observed:
            # A preserved observed dependent still needs explicit evidence revalidation.
            patch["world_changes"][kind] = [record]
            expanded = history.branch_update(self.book, staged["branch"], patch, self.revision())
        old_dependencies = history._edges(self.book, history._head(self.book, 3)["id"])
        reviewed = self.review(expanded)
        self.assertEqual(history.branch_publish(self.book, staged["branch"], reviewed["revision"])["chapters"], expected)
        self.assertEqual(history._edges(self.book, history._head(self.book, 3)["id"]), old_dependencies)
        receipt = story.json.loads(history._head(self.book, 1)["receipt"])
        self.assertEqual(receipt["history_world_ids"], [{"kind": "world.facts", "ref": "condition"}])
        self.assertEqual([item["chapter"] for item in self.fixture.start(1)["affected"]], expected)

    def test_changed_planned_fact_reaches_observed_rule_and_its_consumer(self):
        self.assert_fact_consumer_expansion("rules", True)

    def test_changed_planned_fact_reaches_observed_knowledge_and_its_consumer(self):
        self.assert_fact_consumer_expansion("knowledge", True)

    def test_changed_planned_fact_reaches_planned_rule_consumer(self):
        self.assert_fact_consumer_expansion("rules", False)

    def test_changed_planned_fact_reaches_planned_knowledge_consumer(self):
        self.assert_fact_consumer_expansion("knowledge", False)

    def test_source_body_reaches_planned_rule_consumer(self):
        staged, _, record = self.fact_consumer("rules", observed=False, source_observed=True)
        self.assertEqual([item["chapter"] for item in staged["affected"]], [1, 3])
        self.assertEqual([item["id"] for item in staged["world_review_required"]], ["condition"])
        _, data = history._branch(self.book, staged["branch"])
        self.assertIn("world.rules:" + record["id"], data["fences"])
        self.assertIn("world.rules:" + record["id"], data["entity_search_hints"])
        self.assertNotIn("world.facts:condition", data["entity_search_hints"])
        self.assertIn("world.facts:condition", data["fences"])

    def test_source_body_reaches_planned_knowledge_consumer(self):
        staged, _, _ = self.fact_consumer("knowledge", observed=False, source_observed=True)
        self.assertEqual([item["chapter"] for item in staged["affected"]], [1, 3])
        self.assertEqual([item["id"] for item in staged["world_review_required"]], ["condition"])

    def test_changed_indirect_plan_fence_blocks_refresh(self):
        staged, patch, record = self.fact_consumer("rules", observed=False)
        history.branch_update(self.book, staged["branch"], patch, self.revision())
        story.world.save(self.book, {"rules": [{**record, "description": "另一会话修改了间接规则。"}]}, self.revision())
        before = self.snapshot()
        self.assert_code("stale_dependency", lambda: history.branch_refresh(self.book, staged["branch"], self.revision()))
        self.assertEqual(self.snapshot(), before)

    def test_retired_fact_consumer_does_not_expand_scope(self):
        staged, patch, _ = self.fact_consumer("knowledge", retired=True)
        expanded = history.branch_update(self.book, staged["branch"], patch, self.revision())
        self.assertEqual([item["chapter"] for item in expanded["affected"]], [1])
        self.assertFalse(expanded["scope_expanded"])


if __name__ == "__main__":
    unittest.main()
