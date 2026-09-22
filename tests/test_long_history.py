"""Behavioral regression tests for evidence-bound historical revision branches."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-codex/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("long_history_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)
history = story.history


class LongHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-history-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "远处的账本", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        self.texts = {}
        self.book.save_notes([{"id": "key", "kind": "fact", "text": "钥匙留在库房。", "source": "作者设定"}], self.rev())

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def rev(self):
        return self.book.meta("revision")

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as error:
            call(*args, **kwargs)
        self.assertEqual(error.exception.code, code, error.exception.details)
        return error.exception

    def add(self, chapter, change=False, dependency_fields=None):
        text = f"# 第{chapter}章\n沈禾在渡口核对第{chapter}册账本。她交出钥匙，留下一张收据。灯还亮着。\n"
        plan = {"volume_dir": "第一卷 雨夜", "title": "核对交接", "goal": "核对交接", "stop": "留在渡口", "requires": [], "tags": [],
                "constraints": ["不离开渡口"], "beats": [{"choice": "交出钥匙", "change": "保留收据"}], "length": [10, 200]}
        self.book.save_plan(chapter, plan, self.rev())
        self.draft.write_bytes(text.encode("utf-8"))
        raw = {"book_id": self.book.meta("id"), "base_revision": self.rev(), "summary": "她交出钥匙，保留收据。",
               "changes": [{"id": "key", "text": "钥匙已经交出。", "quote": "她交出钥匙"}] if change else [],
               "review": {"draft_sha256": story.digest(text), "checks": {
                   name: {"note": "交接选择与收据后果相接。", "quote": "她交出钥匙"} for name in story.CHECKS}, "issues": []}}
        raw.update(dependency_fields or {})
        result = self.book.commit(chapter, self.draft, raw)
        self.assertTrue(result["exports_complete"], result)
        self.texts[chapter] = text
        return text

    def dep(self, chapter, refs=(), complete=True, extra=()):
        dependencies = [{"kind": "chapter", "ref": c, "sha": story.digest(self.texts[c])} for c in refs]
        dependencies.extend(extra)
        return history.save_dependencies(self.book, {"chapter": chapter, "chapter_sha": story.digest(self.texts[chapter]),
                                                    "dependencies": dependencies, "complete": complete,
                                                    "note": "逐章核查视角信息与因果；列出的证据覆盖此章依赖。"}, self.rev())

    def start(self, chapter=1):
        return history.branch_start(self.book, chapter, self.rev())

    def candidate(self, chapter, text=None, deps=()):
        text = text or self.texts[chapter]
        candidate = {"sha": story.digest(text), "summary": "她核清账本，并留下交接凭据。",
                     "dependencies": sorted(list(deps), key=lambda d: (d["kind"], d["ref"])), "complete": True}
        review = {"draft_sha256": candidate["sha"], "candidate_sha256": history.candidate_fingerprint(candidate),
                  "checks": {name: {"note": "核对版本后复查选择、连续性与代价。", "quote": "灯还亮着。"}
                             for name in story.CHECKS}, "issues": []}
        return {"chapter": chapter, "text": text, "summary": candidate["summary"],
                "dependencies": candidate["dependencies"], "complete": True, "review": review}

    def stage(self, packet, texts=None, state_changes=None):
        entries = [self.candidate(c["chapter"], (texts or {}).get(c["chapter"])) for c in packet["affected"]]
        payload = {"chapters": entries}
        if state_changes is not None:
            payload["state_changes"] = state_changes
        result = history.branch_update(self.book, packet["branch"], payload, self.rev())
        semantic = {**result["review_template"], "note": "全部受影响章节已经逐段复核。",
                    "state_review": "最终卡片与全文当前结局一致，其他卡片已逐一核查。",
                    "coverage_review": "检查未声明的人物与规则关联；不把机械依赖视为完整语义证明。"}
        return history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())

    def test_far_dependency_transitive_closure_excludes_reviewed_side_story(self):
        for chapter in range(1, 27):
            self.add(chapter)
            self.dep(chapter, [1] if chapter == 25 else [25] if chapter == 26 else [])
        packet = self.start()
        self.assertEqual([a["chapter"] for a in packet["affected"]], [1, 25, 26])
        before = self.book.db.execute("SELECT sha,receipt FROM chapters WHERE chapter=2").fetchone()
        new = self.texts[1].replace("一张收据", "两张收据")
        staged = self.stage(packet, {1: new})
        result = history.branch_publish(self.book, staged["branch"], self.rev())
        self.assertTrue(result["committed"])
        self.assertEqual(tuple(before), tuple(self.book.db.execute("SELECT sha,receipt FROM chapters WHERE chapter=2").fetchone()))
        self.assertEqual(self.book.meta("last_chapter"), 26)
        self.assertEqual(self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0], new)

    def test_incomplete_dependencies_require_semantic_scope_including_later_chapters(self):
        for chapter in range(1, 4):
            self.add(chapter)
        packet = self.start()
        self.assertEqual([a["chapter"] for a in packet["affected"]], [1, 2, 3])
        self.assertFalse(packet["semantic_reviewed"])
        history.branch_update(self.book, packet["branch"], {"chapters": [self.candidate(1)]}, self.rev())
        self.assert_code("review_incomplete", history.branch_publish, self.book, packet["branch"], self.rev())

    def test_draft_stays_candidate_and_unreviewed_publish_cannot_change_canonical(self):
        self.add(1)
        self.dep(1)
        packet = self.start()
        candidate = self.candidate(1, self.texts[1].replace("一张收据", "两张收据"))
        candidate.pop("review")
        history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
        self.assertEqual(self.book.db.execute("SELECT text FROM chapters").fetchone()[0], self.texts[1])
        self.assert_code("review_incomplete", history.branch_publish, self.book, packet["branch"], self.rev())
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(encoding="utf-8"), self.texts[1])

    def test_state_requires_explicit_keep_or_update_and_is_atomic_with_text(self):
        self.add(1, change=True)
        self.dep(1)
        old_version = history._head(self.book, 1)["id"]
        old_card = self.book.cards(["key"])["key"]
        packet = self.start()
        staged = self.stage(packet)
        self.assert_code("state_review_incomplete", history.branch_publish, self.book, staged["branch"], self.rev())
        decisions = [{"id": "key", "before_sha": story.digest(story.dumps(old_card)),
                      "after": {**old_card, "text": "钥匙交给守门人，沈禾保留收据。"}, "chapter": 1,
                      "quote": "她交出钥匙", "note": "核查终局，保留交接事实并补足持有人。"}]
        staged = self.stage(packet, state_changes=decisions)
        result = history.branch_publish(self.book, staged["branch"], self.rev())
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.book.cards(["key"])["key"]["text"], decisions[0]["after"]["text"])
        self.assertEqual(history._body(self.book, self.book.db.execute("SELECT sha FROM history_versions WHERE id=?", (old_version,)).fetchone()[0]), self.texts[1])
        self.assertIn("key", self.start()["required_state_ids"])

    def test_published_retry_recovers_export_without_repeating_state_or_events(self):
        self.add(1)
        self.dep(1)
        staged = self.stage(self.start(), {1: self.texts[1].replace("一张收据", "两张收据")})
        expected = self.rev()
        with patch.object(story, "atomic_write", side_effect=OSError("temporary publication failure")):
            result = history.branch_publish(self.book, staged["branch"], expected)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        revision = self.rev()
        count = self.book.db.execute("SELECT count(*) FROM history_versions").fetchone()[0]
        self.book.close()
        self.book = story.Book(self.root)
        retry = history.branch_publish(self.book, staged["branch"], expected)
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM history_versions").fetchone()[0], count)

    def test_publication_queue_failure_rolls_back_all_candidate_state(self):
        self.add(1, change=True)
        self.dep(1)
        card = self.book.cards(["key"])["key"]
        decisions = [{"id": "key", "before_sha": story.digest(story.dumps(card)), "after": {**card, "text": "钥匙已封存。"},
                      "chapter": 1, "quote": "她交出钥匙", "note": "复查全部状态后决定封存。"}]
        staged = self.stage(self.start(), state_changes=decisions)
        revision = self.rev()
        with patch.object(self.book, "queue_artifact", side_effect=sqlite3.OperationalError("simulated disk write failure")):
            with self.assertRaises(sqlite3.OperationalError):
                history.branch_publish(self.book, staged["branch"], revision)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.cards(["key"])["key"], card)
        self.assertEqual(history.branch_inspect(self.book, staged["branch"])["status"], "candidate")
        self.assertTrue(history.branch_publish(self.book, staged["branch"], revision)["committed"])

    def test_unrelated_revision_needs_explicit_refresh_and_keeps_review(self):
        self.add(1)
        self.dep(1)
        staged = self.stage(self.start())
        self.book.save_notes([{"id": "other", "text": "远城正在下雨。", "source": "另一支线设定"}], self.rev())
        self.assert_code("stale_branch", history.branch_publish, self.book, staged["branch"], self.rev())
        refreshed = history.branch_refresh(self.book, staged["branch"], self.rev())
        self.assertTrue(refreshed["semantic_reviewed"])
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["committed"])

    def test_changed_evidence_and_plan_prevent_refresh_of_old_reviews(self):
        self.add(1)
        sha = story.digest(story.dumps(self.book.cards(["key"])["key"]))
        self.dep(1, extra=[{"kind": "card", "ref": "key", "sha": sha}])
        staged = self.stage(self.start())
        self.book.save_notes([{"id": "key", "text": "钥匙已经丢失。", "source": "作者修订"}], self.rev())
        self.assert_code("stale_dependency", history.branch_refresh, self.book, staged["branch"], self.rev())
        other = self.start()
        plan = self.book.get_plan(1)
        self.book.save_plan(1, {**plan, "goal": "交接失败，不留收据"}, self.rev())
        self.assert_code("stale_dependency", history.branch_refresh, self.book, other["branch"], self.rev())

    def test_new_dependent_chapter_cannot_hide_behind_refresh(self):
        self.add(1)
        self.dep(1)
        staged = self.stage(self.start())
        self.add(2)
        self.assert_code("stale_branch", history.branch_refresh, self.book, staged["branch"], self.rev())

    def test_changed_summary_cannot_reuse_body_only_review(self):
        self.add(1)
        self.dep(1)
        packet = self.start()
        candidate = self.candidate(1)
        candidate["summary"] = "她没有交出钥匙。"
        self.assert_code("stale_review", history.branch_update, self.book, packet["branch"], {"chapters": [candidate]}, self.rev())

    def test_advice_and_legacy_minor_survive_historical_publication(self):
        self.add(1)
        self.dep(1)
        for severity in ("advice", "minor"):
            with self.subTest(severity=severity):
                packet = self.start()
                candidate = self.candidate(1)
                issues = [{"severity": severity, "issue": "灯光描写可精简，但不影响本章成立。"}]
                candidate["review"]["issues"] = issues
                staged = history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
                semantic = {**staged["review_template"], "note": "复核全文，保留局部语气建议。",
                            "state_review": "本次修订未改变交接状态。",
                            "coverage_review": "已检查本章及未声明的关联，没有遗漏受影响的后文。"}
                history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())
                result = history.branch_publish(self.book, packet["branch"], self.rev())
                self.assertTrue(result["exports_complete"], result)
                receipt = json.loads(self.book.db.execute("SELECT receipt FROM chapters WHERE chapter=1").fetchone()[0])
                self.assertEqual(receipt["review"]["issues"], issues)

    def test_major_and_blocker_still_block_history_and_unknown_severity_is_rejected(self):
        self.add(1)
        self.dep(1)
        packet = self.start()
        revision = self.rev()
        original = tuple(self.book.db.execute("SELECT text,receipt FROM chapters WHERE chapter=1").fetchone())
        for severity, code in (("major", "review_blocked"), ("blocker", "review_blocked"), ("suggestion", "invalid_input")):
            with self.subTest(severity=severity):
                candidate = self.candidate(1)
                candidate["review"]["issues"] = [{"severity": severity, "issue": "交接事实与本章选择仍矛盾。"}]
                self.assert_code(code, history.branch_update, self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
                self.assertEqual(self.rev(), revision)
                self.assertEqual(tuple(self.book.db.execute("SELECT text,receipt FROM chapters WHERE chapter=1").fetchone()), original)
        self.assertIsNone(history.branch_inspect(self.book, packet["branch"], chapter=1)["candidate"])
        staged = self.stage(packet)
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["committed"])

    def test_second_branch_cannot_publish_after_first_changes_same_history(self):
        self.add(1)
        self.dep(1)
        first = self.start()
        second = self.start()
        history.branch_refresh(self.book, first["branch"], self.rev())
        staged = self.stage(first, {1: self.texts[1].replace("一张收据", "两张收据")})
        history.branch_publish(self.book, staged["branch"], self.rev())
        self.assert_code("stale_branch", history.branch_refresh, self.book, second["branch"], self.rev())

    def test_external_edit_blocks_publication_and_preserves_user_bytes(self):
        self.add(1)
        self.dep(1)
        staged = self.stage(self.start())
        path = self.root / self.book.chapter_path(1)
        path.write_bytes("用户刚增加的结尾。".encode("utf-8"))
        revision = self.rev()
        self.assert_code("exports_unresolved", history.branch_publish, self.book, staged["branch"], revision)
        self.assertEqual(path.read_text(encoding="utf-8"), "用户刚增加的结尾。")
        self.assertEqual(self.rev(), revision)

    def test_snapshot_uses_body_and_card_pointers_and_nearest_predecessor(self):
        self.add(1)
        snap = history.snapshot(self.book, "volume:one", self.rev())
        manifest = json.loads(history._body(self.book, snap["manifest_sha256"]))
        self.assertIn("1", manifest["heads"])
        self.assertEqual(json.loads(history._body(self.book, manifest["cards"]["key"])), self.book.cards(["key"])["key"])
        self.add(2)
        packet = self.start(2)
        self.assertEqual(packet["snapshot"]["id"], snap["snapshot"])
        self.assertEqual(packet["snapshot"]["chapter"], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.book.db.execute("UPDATE history_versions SET summary='tamper'")
        self.book.db.rollback()

    def test_cache_requires_complete_evidence_and_misses_after_dependency_change(self):
        self.add(1)
        self.assert_code("cache_unverified", history.cache_put, self.book, 1, "retrieval", {"note": "cache"}, self.rev())
        sha = story.digest(story.dumps(self.book.cards(["key"])["key"]))
        self.dep(1, extra=[{"kind": "card", "ref": "key", "sha": sha}])
        history.cache_put(self.book, 1, "retrieval", {"note": "原文证据摘要"}, self.rev())
        self.assertTrue(history.cache_get(self.book, 1, "retrieval")["hit"])
        self.book.save_notes([{"id": "other", "text": "旁支无关事项。", "source": "设定"}], self.rev())
        self.assertTrue(history.cache_get(self.book, 1, "retrieval")["hit"])
        self.book.save_notes([{"id": "key", "text": "钥匙归还库房。", "source": "新设定"}], self.rev())
        self.assertFalse(history.cache_get(self.book, 1, "retrieval")["hit"])

    def test_imported_baseline_can_only_publish_after_plan_and_full_reviews(self):
        text = "# 旧稿\n沈禾留在渡口。灯还亮着。\n"
        self.draft.write_bytes(text.encode("utf-8"))
        # Simulate a legacy import that has not materialized history versions yet.
        with patch.object(history, "on_commit", return_value=None):
            self.book.adopt(20, self.draft, "旧稿停在渡口。", self.rev(), volume_dir="第一卷 雨夜")
        self.texts[20] = text
        revision = self.rev()
        tables = ("history_versions", "history_heads", "history_edges", "history_branches", "core_objects", "events")
        counts = {table: self.book.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
        error = self.assert_code("plan_missing", self.start, 20)
        self.assertEqual(error.details["chapters"], [20])
        self.assertEqual(error.details["recovery_command"], "plan")
        self.assertEqual(self.rev(), revision)
        self.assertEqual({table: self.book.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}, counts)
        self.assertEqual(counts["history_heads"], 0)
        self.book.save_plan(20, {"volume_dir": "第一卷 雨夜", "goal": "继续等候", "stop": "留在渡口",
                               "beats": [{"choice": "留下等候", "change": "继续守灯"}], "length": [10, 200]}, self.rev())
        packet = self.start(20)
        self.assert_code("review_incomplete", history.branch_publish, self.book, packet["branch"], self.rev())
        staged = self.stage(packet, {20: text + "她决定继续等候。\n"})
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["exports_complete"])
        self.assertEqual(self.book.meta("imported_through"), 20)
        self.assertEqual(self.book.db.execute("SELECT imported FROM chapters").fetchone()[0], 1)

    def test_history_dependencies_use_branch_candidates_and_current_records(self):
        self.add(1)
        self.add_fact()
        self.add(2)
        self.add(3)
        self.book.save_plan(2, {**self.book.get_plan(2), "requires": ["key"], "entities": ["shen"]}, self.rev())
        packet = self.start(1)
        changed = self.texts[1].replace("一张收据", "两张收据")
        history.branch_update(self.book, packet["branch"], {"chapters": [self.candidate(1, changed)]}, self.rev())
        revision = self.rev()
        arguments = story.parser().parse_args(["history-dependencies", "--book", str(self.root),
                                               "--branch", packet["branch"], "--chapter", "2"])
        with patch.object(self.book, "context", side_effect=AssertionError("Historical review must not enter write context")):
            result = history.run(self.book, arguments)
        candidates = {(v["kind"], v["ref"]): v["sha"] for v in result["candidates"]}
        self.assertEqual(candidates[("chapter", "1")], story.digest(changed))
        self.assertEqual(candidates[("card", "key")], story.digest(story.dumps(self.book.cards(["key"])["key"])))
        self.assertEqual(candidates[("world.facts", "gave-key")], story.world.resolve_dependency(self.book, "facts", "gave-key"))
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.chapter_read(1)["text"], self.texts[1])
        self.assert_code("chapter_missing", history.branch_dependencies, self.book, packet["branch"], 4)
        self.assert_code("budget_exceeded", history.branch_dependencies, self.book, packet["branch"], 2, budget=256)
        self.book.save_notes([{**self.book.cards(["key"])["key"], "text": "作者调整了钥匙状态。"}], self.rev())
        self.assert_code("stale_branch", history.branch_dependencies, self.book, packet["branch"], 2)

    def test_saved_candidate_can_be_read_in_bounded_pieces_without_publishing(self):
        self.add(1)
        self.book.save_plan(1, {**self.book.get_plan(1), "length": [10, 50000]}, self.rev())
        packet = self.start()
        changed = self.texts[1] + "窗外雨声。" * 6000
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [self.candidate(1, changed)]}, self.rev())
        sha = staged["affected"][0]["draft_sha256"]
        self.assert_code("budget_exceeded", history.branch_inspect, self.book, packet["branch"], chapter=1)
        chunks, start = [], 0
        while start is not None:
            part = self.book.chapter_read(1, sha, start=start, budget=12000)
            chunks.append(part["text"])
            start = part["next_start"]
        self.assertEqual("".join(chunks), changed)
        self.assertEqual(self.book.chapter_read(1)["text"], self.texts[1])
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(encoding="utf-8"), self.texts[1])
        self.assert_code("chapter_missing", self.book.chapter_read, 2, sha)
        self.assert_code("chapter_missing", self.book.chapter_read, 1, packet["baseline_state_sha256"])

    def test_later_world_baseline_blocks_replace_until_history_rebinds_evidence(self):
        self.add(1)
        self.add_fact()
        changed = self.texts[1] + "她抬头看雨。\n"
        self.draft.write_bytes(changed.encode("utf-8"))
        raw = {"book_id": self.book.meta("id"), "base_revision": self.rev(), "summary": "她交出钥匙，留在渡口。", "changes": [],
               "review": {"draft_sha256": story.digest(changed), "checks": {
                   key: {"note": "新增景物描写未改变交接。", "quote": "灯还亮着。"} for key in story.CHECKS}, "issues": []}}
        revision = self.rev()
        for operation in (lambda: self.book.context(1), lambda: self.book.prepare(1, self.draft),
                          lambda: self.book.reconcile(1), lambda: self.book.commit(1, self.draft, raw, replace_last=True)):
            error = self.assert_code("history_revision_required", operation)
            self.assertEqual(error.details["recovery_command"], "history-start")
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.chapter_read(1)["text"], self.texts[1])
        self.assertIsNotNone(story.world.resolve_dependency(self.book, "facts", "gave-key"))
        staged = self.stage(self.start(), {1: changed})
        self.semantic_after(staged, {"world_changes": {"facts": [self.fact(changed)]}})
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["exports_complete"])
        self.book.save_plan(2, {**self.book.get_plan(1), "entities": ["shen"]}, self.rev())
        self.assertIsNotNone(story.world.resolve_dependency(self.book, "facts", "gave-key"))
        self.assertEqual(self.book.context(2)["world"]["facts"][0]["evidence"]["sha"], story.digest(changed))

    def test_historical_state_replays_notes_and_published_card_changes_after_snapshot(self):
        self.add(1, change=True)
        snapshot = history.snapshot(self.book, "卷一校验点", self.rev())
        previous = self.book.cards(["key"])["key"]
        changed = {**previous, "text": "钥匙已经交给账房。", "source": "作者补记交接"}
        self.book.save_notes([changed], self.rev())
        self.add(2, change=True)
        before = history.history_state(self.book, 2, before=True)
        after = history.history_state(self.book, 2)
        self.assertEqual(before["snapshot"]["id"], snapshot["snapshot"])
        self.assertEqual(before["cards"][0]["text"], changed["text"])
        self.assertEqual(after["cards"][0]["text"], "钥匙已经交出。")
        self.assertGreater(before["replayed_events"], 0)
        packet = self.start(2)
        pointer = json.loads(history._body(self.book, packet["baseline_state_sha256"]))["key"]
        self.assertEqual(json.loads(history._body(self.book, pointer))["text"], changed["text"])

    def test_historical_state_replays_replacement_rollback_and_history_final_state(self):
        self.add(1, change=True)
        text = self.texts[1].replace("一张收据", "两张收据")
        self.draft.write_bytes(text.encode("utf-8"))
        raw = {"book_id": self.book.meta("id"), "base_revision": self.rev(), "summary": "她交出钥匙。", "changes": [],
               "review": {"draft_sha256": story.digest(text), "checks": {
                   c: {"note": "修订后不再认领钥匙的终局状态。", "quote": "她交出钥匙"} for c in story.CHECKS}, "issues": []}}
        self.book.commit(1, self.draft, raw, replace_last=True)
        self.texts[1] = text
        restored = history.history_state(self.book, 1)
        self.assertEqual(restored["cards"][0]["text"], "钥匙留在库房。")
        self.dep(1)
        old = self.book.cards(["key"])["key"]
        staged = self.stage(self.start(), state_changes=[{"id": "key", "before_sha": story.digest(story.dumps(old)),
                            "after": {**old, "text": "钥匙交给守门人。"}, "chapter": 1, "quote": "她交出钥匙",
                            "note": "对完整结局作最终状态修正。"}])
        history.branch_publish(self.book, staged["branch"], self.rev())
        self.assertEqual(history.history_state(self.book, 1)["cards"][0]["text"], "钥匙交给守门人。")

    def test_historical_state_rejects_unknown_events_instead_of_guessing(self):
        self.add(1)
        with self.book.transaction():
            self.book.event("future_card_command", {"unspecified_state_effect": True})
        self.add(2)
        self.assert_code("history_event_unsupported", history.history_state, self.book, 2)

    def test_outside_edit_can_be_explicitly_bound_and_export_retry_keeps_hash(self):
        self.add(1)
        self.dep(1)
        outside = self.texts[1].replace("一张收据", "两张收据")
        path = self.root / self.book.chapter_path(1)
        path.write_bytes(outside.encode("utf-8"))
        packet = self.start()
        sha = packet["affected"][0]["external_edit"]["sha256"]
        final_text = outside.replace("两张收据", "三张收据")
        candidate = self.candidate(1, final_text)
        candidate["external_sha256"] = sha
        candidate["review"]["candidate_sha256"] = history.candidate_fingerprint({**candidate, "sha": story.digest(final_text)})
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
        semantic = {**staged["review_template"], "note": "复核外部版本到新候选的改动。", "state_review": "卡片保持一致。", "coverage_review": "核查全部依赖。"}
        history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())
        expected = self.rev()
        with patch.object(story, "atomic_write", side_effect=OSError("locked external edit")):
            result = history.branch_publish(self.book, packet["branch"], expected)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(self.book.db.execute("SELECT written_sha FROM artifacts").fetchone()[0], sha)
        self.assertEqual(path.read_bytes(), outside.encode("utf-8"))
        retry = history.branch_publish(self.book, packet["branch"], expected)
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(path.read_bytes(), final_text.encode("utf-8"))

    def test_bound_outside_edit_does_not_accept_later_save_or_other_drift(self):
        self.add(1)
        self.add(2)
        self.dep(1)
        self.dep(2)
        path = self.root / self.book.chapter_path(1)
        outside = self.texts[1].replace("一张收据", "两张收据")
        path.write_bytes(outside.encode("utf-8"))
        packet = self.start()
        candidate = self.candidate(1, outside)
        candidate["external_sha256"] = story.digest(outside)
        candidate["review"]["candidate_sha256"] = history.candidate_fingerprint({**candidate, "sha": story.digest(outside)})
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
        semantic = {**staged["review_template"], "note": "外改已经核查。", "state_review": "状态保持。", "coverage_review": "影响范围已核查。"}
        history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())
        other = self.root / self.book.chapter_path(2)
        other.write_bytes(b"another outside save")
        self.assert_code("exports_unresolved", history.branch_publish, self.book, packet["branch"], self.rev())
        other.write_bytes(self.texts[2].encode("utf-8"))
        path.write_bytes((outside + "新的追加。\n").encode("utf-8"))
        self.assert_code("stale_external", history.branch_publish, self.book, packet["branch"], self.rev())
        self.assertTrue(path.read_bytes().endswith("新的追加。\n".encode("utf-8")))

    def fact(self, text=None):
        return {"id": "gave-key", "subject": "shen", "predicate": "交接物", "value": "一张收据",
                "evidence": {"kind": "chapter", "chapter": 1, "sha256": story.digest(text or self.texts[1]), "quote": "她交出钥匙"}}

    def add_fact(self):
        story.world.save(self.book, {"entities": [{"id": "shen", "name": "沈禾", "kind": "character", "description": "核查渡口交接的人"}],
                                     "facts": [self.fact()]}, self.rev())

    def semantic_after(self, packet, payload):
        result = history.branch_update(self.book, packet["branch"], payload, self.rev())
        semantic = {**result["review_template"], "note": "章间语义与状态补丁已复核。", "state_review": "卡片与结构事实一致。",
                    "coverage_review": "复查具名人物、物件、后续引用和规则，不遗漏记录。"}
        return history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())

    def test_world_evidence_requires_atomic_rebind_and_keeps_old_state_on_failure(self):
        self.add(1)
        self.dep(1)
        self.add_fact()
        packet = self.start()
        self.assertEqual(packet["world_review_required"][0]["id"], "gave-key")
        new = self.texts[1].replace("一张收据", "两张收据")
        staged = self.stage(packet, {1: new})
        revision = self.rev()
        self.assert_code("world_review_incomplete", history.branch_publish, self.book, packet["branch"], revision)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.db.execute("SELECT sha FROM chapters").fetchone()[0], story.digest(self.texts[1]))
        self.assertEqual(self.book.db.execute("SELECT valid FROM world_evidence").fetchone()[0], 1)
        fact = {**self.fact(new), "value": "两张收据"}
        self.semantic_after(staged, {"world_changes": {"facts": [fact]}})
        result = history.branch_publish(self.book, packet["branch"], self.rev())
        self.assertTrue(result["committed"])
        self.assertEqual(self.book.db.execute("SELECT value FROM world_facts").fetchone()[0], "两张收据")
        self.assertEqual(self.book.db.execute("SELECT sha,valid FROM world_evidence").fetchone()[0], story.digest(new))
        self.assertIsNotNone(story.world.resolve_dependency(self.book, "world.facts", "gave-key"))
        recorded = json.loads(self.book.db.execute("SELECT data FROM events WHERE kind='history_publish'").fetchone()[0])
        self.assertEqual(recorded["world_changes"][0]["before"][0]["value"], "一张收据")

    def test_world_record_can_be_retired_with_evidence_and_remains_retired_on_next_revision(self):
        self.add(1)
        self.dep(1)
        self.add_fact()
        new = self.texts[1].replace("一张收据", "一份当场作废的旧收据")
        staged = self.stage(self.start(), {1: new})
        retirement = {"kind": "facts", "id": "gave-key", "reason": "改稿明确交接凭据已经作废，撤销其仍有效的记录。",
                      "evidence": {"kind": "chapter", "chapter": 1, "sha256": story.digest(new), "quote": "当场作废的旧收据"}}
        self.semantic_after(staged, {"world_changes": {"retirements": [retirement]}})
        history.branch_publish(self.book, staged["branch"], self.rev())
        self.assertIsNone(story.world.resolve_dependency(self.book, "world.facts", "gave-key"))
        self.texts[1] = new
        later = self.stage(self.start())
        self.assertFalse(later["world_review_required"])
        self.assertTrue(history.branch_publish(self.book, later["branch"], self.rev())["committed"])
        self.assertEqual(tuple(self.book.db.execute("SELECT valid,retired FROM world_evidence").fetchone()), (1, 1))

    def test_world_evidence_creates_far_dependency_without_duplicate_chapter_edge(self):
        self.add(1)
        self.dep(1)
        self.add_fact()
        self.add(2)
        self.dep(2)
        self.add(3)
        self.dep(3, extra=[{"kind": "world.facts", "ref": "gave-key", "sha": story.world.resolve_dependency(self.book, "world.facts", "gave-key")}])
        self.assertEqual([c["chapter"] for c in self.start()["affected"]], [1, 3])

    def test_history_cannot_bypass_known_world_rule_conflicts(self):
        self.add(1)
        story.world.save(self.book, {"entities": [{"id": "shen", "name": "沈禾", "kind": "character", "description": "渡口守灯人"}],
                                    "rules": [{"id": "lamp-v1", "rule": "lamp", "version": 1, "start": 0, "cooldown": 10,
                                               "description": "再次点灯前必须冷却十刻", "hard": True, "entities": ["shen"],
                                               "evidence": {"kind": "author_plan", "note": "明确的能力限制"}}],
                                    "uses": [{"id": "used", "actor": "shen", "rule": "lamp", "at": 10,
                                              "evidence": self.fact()["evidence"]},
                                             {"id": "again", "actor": "shen", "rule": "lamp", "at": 15,
                                              "evidence": {"kind": "author_plan", "note": "拟写下一次点灯"}}]}, self.rev())
        self.add(2)
        self.book.save_plan(2, {**self.book.get_plan(2), "entities": ["shen"],
                               "time": {"clock": "main", "start": 15, "end": 15}}, self.rev())
        staged = self.stage(self.start(2))
        revision = self.rev()
        self.assert_code("world_constraint", history.branch_publish, self.book, staged["branch"], revision)
        self.assertEqual(self.rev(), revision)

    def test_inspection_provides_exact_candidate_review_scaffold_without_passing_it(self):
        self.add(1)
        packet = self.start()
        candidate = self.candidate(1)
        candidate.pop("review")
        history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
        inspected = history.branch_inspect(self.book, packet["branch"], chapter=1)
        self.assertEqual(inspected["candidate"]["text"], self.texts[1])
        self.assertIsNone(inspected["candidate"]["review"])
        self.assertEqual(inspected["chapter_review_template"]["checks"]["causality"], {"note": "", "quote": ""})
        self.assertEqual(inspected["chapter_review_template"]["candidate_sha256"], inspected["affected"][0]["candidate_sha256"])

    def test_commit_dependency_declaration_is_preserved_without_extra_history_command(self):
        self.add(1, dependency_fields={"dependencies": [], "dependency_review": {"complete": True, "note": "本章独立建立交接场面，没有沿用前章证据。"}})
        self.assertEqual(history._head(self.book, 1)["complete"], 1)
        self.add(2, dependency_fields={"dependencies": [], "dependency_review": {"complete": True, "note": "另一支线，已复核不依赖第一章的物件去向。"}})
        self.add(3, dependency_fields={"dependencies": [{"kind": "chapter", "ref": 1, "sha": story.digest(self.texts[1])}],
                                       "dependency_review": {"complete": True, "note": "第三章直接使用第一章的交接凭据；其他线索没有参与。"}})
        self.assertEqual([c["chapter"] for c in self.start()["affected"]], [1, 3])

    def test_commit_dependency_validation_rejects_missing_fields_self_and_stale_sha(self):
        self.add(1)
        for payload, code in [({"dependency_review": {"complete": True, "note": "已查"}}, "invalid_input"),
                              ({"dependencies": []}, "invalid_input"),
                              ({"dependencies": [{"kind": "chapter", "ref": 1, "sha": story.digest(self.texts[1])}],
                                "dependency_review": {"complete": True, "note": "已查"}}, "invalid_input"),
                              ({"dependencies": [{"kind": "card", "ref": "key", "sha": "a" * 64}],
                                "dependency_review": {"complete": True, "note": "已查"}}, "stale_dependency")]:
            with self.subTest(payload=payload):
                self.assert_code(code, history.validate_commit_dependencies, self.book, payload, 1)
        self.book.save_plan(1, {**self.book.get_plan(1), "requires": ["key"]}, self.rev())
        self.assert_code("missing_required_dependencies", history.validate_commit_dependencies, self.book,
                         {"dependencies": [], "dependency_review": {"complete": True, "note": "声明完整但漏了明确必读卡"}}, 1)

    def test_replace_dependency_validation_uses_recorded_preimage_not_postchapter_card(self):
        original = self.book.cards(["key"])["key"]
        self.add(1, change=True)
        fields = history.validate_commit_dependencies(self.book, {"dependencies": [{"kind": "card", "ref": "key", "sha": story.digest(story.dumps(original))}],
                                                                 "dependency_review": {"complete": True, "note": "修订从旧章前像出发复核"}}, 1)
        self.assertEqual(fields["dependencies"][0]["sha"], story.digest(story.dumps(original)))
        self.assertNotEqual(self.book.cards(["key"])["key"], original)

    def test_legacy_seed_does_not_guess_required_card_history_from_current_state(self):
        self.add(1)
        self.book.save_plan(1, {**self.book.get_plan(1), "requires": ["key"]}, self.rev())
        self.add(2)
        self.book.save_notes([{**self.book.cards(["key"])["key"], "text": "第二章之后才获得的消息", "source": "chapter:2 quote:灯还亮着。"}], self.rev())
        # Simulate schema-1 migration before its new history_heads are seeded.
        with self.book.transaction():
            self.book.db.execute("DELETE FROM history_heads")
        history.history_state(self.book, 1)
        head = history._head(self.book, 1)
        self.assertFalse(head["complete"])
        self.assertEqual(history._edges(self.book, head["id"]), [])
        self.assertLess(head["publication_revision"], history._head(self.book, 2)["publication_revision"])

    def test_typed_cognition_reference_expands_history_scope_before_retirement(self):
        self.add(1)
        self.dep(1)
        self.add_fact()
        self.add(2)
        self.dep(2)
        self.add(3)
        self.dep(3)
        story.world.save(self.book, {"knowledge": [{"id": "shen-knows", "actor": "shen", "fact": "gave-key", "state": "knows",
                                                   "at": 3, "channel": "亲眼看过交接收据", "evidence": {"kind": "chapter", "chapter": 3,
                                                   "sha256": story.digest(self.texts[3]), "quote": "灯还亮着。"}}]}, self.rev())
        self.assertEqual([c["chapter"] for c in self.start()["affected"]], [1, 3])

    def test_actual_world_patch_is_checked_even_without_time_or_entities_in_plan(self):
        self.add(1)
        story.world.save(self.book, {"entities": [{"id": "shen", "name": "沈禾", "kind": "character", "description": "渡口守灯人"}],
                                    "rules": [{"id": "lamp-v1", "rule": "lamp", "version": 1, "start": 0, "cooldown": 10,
                                               "description": "再次点灯前必须冷却十刻", "hard": True, "entities": ["shen"],
                                               "evidence": self.fact()["evidence"]}],
                                    "uses": [{"id": "used", "actor": "shen", "rule": "lamp", "at": 10,
                                              "evidence": self.fact()["evidence"]}]}, self.rev())
        self.add(2)
        staged = self.stage(self.start(2))
        use = {"id": "again", "actor": "shen", "rule": "lamp", "at": 15,
               "evidence": {"kind": "chapter", "chapter": 2, "sha256": story.digest(self.texts[2]), "quote": "灯还亮着。"}}
        self.semantic_after(staged, {"world_changes": {"uses": [use]}})
        revision = self.rev()
        self.assert_code("world_constraint", history.branch_publish, self.book, staged["branch"], revision)
        self.assertEqual(self.rev(), revision)
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_uses WHERE id='again'").fetchone())
        self.semantic_after(staged, {"world_changes": {"uses": [{**use, "at": 25}]}})
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["committed"])

    def test_cli_state_review_template_supplies_exact_preimage_but_requires_explicit_decision(self):
        self.add(1, change=True)
        packet = self.start()
        template = packet["state_review_template"][0]
        current = self.book.cards(["key"])["key"]
        self.assertEqual(template["before"], current)
        self.assertEqual(template["before_sha"], story.digest(story.dumps(current)))
        self.assertNotIn("after", template)
        self.assertEqual(packet["state_review_total"], 1)
        self.assertIsNone(packet["state_review_next_offset"])
        history.branch_update(self.book, packet["branch"], {"chapters": [self.candidate(1)]}, self.rev())
        self.assert_code("invalid_input", history.branch_update, self.book, packet["branch"],
                         {"state_changes": [{**template, "chapter": 1, "quote": "她交出钥匙", "note": "确认卡片保持"}]}, self.rev())
        decision = {**template, "after": template["before"], "chapter": 1, "quote": "她交出钥匙", "note": "确认卡片保持"}
        staged = self.semantic_after(packet, {"state_changes": [decision]})
        self.assertTrue(history.branch_publish(self.book, staged["branch"], self.rev())["committed"])

    def large_scope(self, count=4000):
        """Lightweight real SQLite fixture; shared short body, no novel/export load."""
        text = "# 范围夹具\n这一段仅用于验证分页范围与版本，不作小说质量验收。\n"
        cards = [story.valid_card({"id": f"scope{i:04d}", "text": f"第{i}项状态", "source": "范围夹具"}) for i in range(1, count + 1)]
        plan = story.valid_plan({"volume_dir": "第一卷 范围夹具", "goal": "核对范围", "stop": "结束范围核对",
                                 "beats": [{"choice": "核对范围", "change": "保留版本证据"}], "length": [1, 200]})
        with self.book.transaction():
            sha = self.book.intern_body(text)
            self.book.db.executemany("INSERT INTO cards VALUES (?,?)", [(c["id"], story.dumps(c)) for c in cards])
            publication_revision = self.book.event("history_publish", {"branch": "fixture", "chapters": list(range(1, count + 1)),
                                                                         "before": {}, "after": [{"id": c["id"], "after": c} for c in cards]})
            self.book.db.execute("INSERT INTO world_entities VALUES ('scope-entity','范围人物','character','分页测试的具名实体')")
            for chapter, card in enumerate(cards, 1):
                receipt = {"before": {card["id"]: None}, "after": {card["id"]: card}}
                self.book.db.execute("INSERT INTO plans VALUES (?,?)", (chapter, story.dumps(plan)))
                self.book.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,0)",
                                     (chapter, text, sha, "分页测试摘要", story.dumps(receipt), story.digest(str(chapter))))
                self.book.db.execute("INSERT INTO artifacts(path,content,sha) VALUES (?,?,?)", (f"chapters/{chapter:04d}.md", text, sha))
                self.book.db.execute("UPDATE artifacts SET written_sha=? WHERE path=?", (sha, f"chapters/{chapter:04d}.md"))
                history._version(self.book, chapter, text, "分页测试摘要", receipt, None,
                                 [{"kind": "card", "ref": card["id"], "sha": story.digest(story.dumps(card))}], False, publication_revision)
                rid = f"scope-fact-{chapter:04d}"
                self.book.db.execute("INSERT INTO world_facts VALUES (?, 'scope-entity','范围事实','仅用于分页','main',0,NULL,0)", (rid,))
                self.book.db.execute("INSERT INTO world_evidence(kind,record_id,mode,chapter,sha,quote) VALUES ('facts',?,'chapter',?,?,?)",
                                     (rid, chapter, sha, "这一段仅用于验证分页范围与版本"))
            self.book.set_meta("last_chapter", count)
        path = self.root / "chapters"
        path.mkdir(exist_ok=True)
        for chapter in list(range(1, 26)) + list(range(count - 24, count + 1)):
            (path / f"{chapter:04d}.md").write_bytes(text.encode("utf-8"))

    def test_4000_chapter_inspection_pages_all_scopes_without_hashing_omitted_exports(self):
        self.large_scope()
        with patch.object(history, "_external", wraps=history._external) as checked:
            packet = self.start()
        self.assertEqual([int(call.args[1]) for call in checked.call_args_list], list(range(1, 26)))
        self.assertLessEqual(len(story.dumps(packet).encode("utf-8")), history.DEFAULT_BUDGET)
        self.assertEqual(packet["budget"]["used"], len(story.dumps(packet).encode("utf-8")))
        self.assertFalse(packet["complete"])
        self.assertIsNone(packet["review_template"]["reviewed_chapters"])
        for section in ("affected", "world", "hints", "state"):
            self.assertEqual(packet["pages"][section]["total"], 4000)
            self.assertFalse(packet["pages"][section]["complete"])
        self.assertEqual(len(packet["affected"]), 25)
        self.assertEqual(len(packet["world_review_required"]), 25)
        self.assertEqual(len(packet["entity_search_hints"]), 25)
        self.assertEqual(len(packet["required_state_ids"]), 50)
        with patch.object(history, "_external", wraps=history._external) as checked:
            final = history.branch_inspect(self.book, packet["branch"], affected_offset=3975, world_offset=3975,
                                           hints_offset=3975, state_offset=3950)
        self.assertEqual([int(call.args[1]) for call in checked.call_args_list], list(range(3976, 4001)))
        self.assertEqual(final["affected"][-1]["chapter"], 4000)
        self.assertEqual(final["required_state_ids"][-1], "scope4000")
        self.assertFalse(final["complete"])
        for page in final["pages"].values():
            self.assertIsNone(page["next_offset"])
            self.assertFalse(page["complete"], "The last page alone must never claim the entire scope")
        review = {**packet["review_template"], "reviewed_chapters": packet["review_scope"]["chapter_ids_page"],
                  "note": "只审阅了第一页", "state_review": "第一页状态已审", "coverage_review": "不能把第一页当全量"}
        self.assert_code("review_incomplete", history.branch_update, self.book, packet["branch"], {"semantic_review": review}, self.rev())

    def test_inspection_budget_failure_rolls_back_start_update_and_refresh(self):
        self.add(1)
        revision = self.rev()
        self.assert_code("budget_exceeded", history.branch_start, self.book, 1, revision, budget=300)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM history_branches").fetchone()[0], 0)
        packet = self.start()
        revision = self.rev()
        saved = self.book.db.execute("SELECT data,revision FROM history_branches").fetchone()
        self.assert_code("budget_exceeded", history.branch_update, self.book, packet["branch"],
                         {"chapters": [self.candidate(1)]}, revision, budget=300)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(tuple(self.book.db.execute("SELECT data,revision FROM history_branches").fetchone()), tuple(saved))
        self.book.save_notes([{"id": "unrelated", "text": "无关记录", "source": "作者补充"}], self.rev())
        revision = self.rev()
        self.assert_code("budget_exceeded", history.branch_refresh, self.book, packet["branch"], revision, budget=300)
        self.assertEqual(self.rev(), revision)
        self.assertEqual(tuple(self.book.db.execute("SELECT data,revision FROM history_branches").fetchone()), tuple(saved))

    def test_single_chapter_inspection_has_budget_and_never_truncates_review_evidence(self):
        self.add(1)
        packet = self.start()
        candidate = self.candidate(1)
        history.branch_update(self.book, packet["branch"], {"chapters": [candidate]}, self.rev())
        full = history.branch_inspect(self.book, packet["branch"], chapter=1)
        self.assertEqual(full["candidate"]["text"], self.texts[1])
        self.assertEqual(full["candidate"]["review"], candidate["review"])
        self.assertEqual(full["budget"]["used"], len(story.dumps(full).encode("utf-8")))
        self.assert_code("budget_exceeded", history.branch_inspect, self.book, packet["branch"], chapter=1,
                         budget=full["budget"]["used"] - 400)

    def test_large_cache_can_be_saved_but_read_requires_an_explicit_sufficient_budget(self):
        self.add(1)
        self.dep(1)
        value = {"derived_notes": "长篇证据" * 9000}
        history.cache_put(self.book, 1, "large-derived-notes", value, self.rev())
        self.assert_code("budget_exceeded", history.cache_get, self.book, 1, "large-derived-notes")
        result = history.cache_get(self.book, 1, "large-derived-notes", budget=150000)
        self.assertEqual(result["value"], value)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))

    def test_200kb_imported_chapter_comparison_requires_a_larger_explicit_budget(self):
        text = "# 大篇幅旧稿\n" + "这一句仅验证读取预算。" * 7000
        self.assertGreater(len(text.encode("utf-8")), 200000)
        self.draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(1, self.draft, "大篇幅导入，只验证输出上限。", self.rev(), volume_dir="第一卷 雨夜")
        self.book.save_plan(1, {"volume_dir": "第一卷 雨夜", "goal": "核对读取范围", "stop": "读完旧稿",
                              "beats": [{"choice": "分段核对", "change": "确认完整范围"}], "length": [1, 200000]}, self.rev())
        packet = self.start()
        self.assert_code("budget_exceeded", history.branch_inspect, self.book, packet["branch"], chapter=1)
        result = history.branch_inspect(self.book, packet["branch"], chapter=1, budget=300000)
        self.assertEqual(result["base"]["text"], text)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
