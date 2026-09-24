"""World-state ambiguity, applicable rules, and zero-baseline regressions."""
import json
import unittest

import test_long_integration_review as integration
from test_long_world import FixtureBook, WorldError, entity, plan, planned, world


class WorldRulesRegressions(unittest.TestCase):
    def setUp(self):
        self.book = FixtureBook()
        self.ev1 = self.book.chapter(1, "甲在第十刻得知钥匙所在并施术。甲持有钥匙和账册，身上没有铜钱。")
        self.ev2 = self.book.chapter(2, "甲后来失忆，又施术一次，确切时间未知。乙仍须遵守原来的限制。")
        self.save(entities=[entity("a", "甲"), entity("b", "乙"), entity("key", "钥匙", "item"),
                            entity("ledger", "账册", "item"), entity("coin", "铜钱", "resource"),
                            entity("room", "房间", "place")])

    def tearDown(self):
        self.book.db.close()

    def save(self, **payload):
        return world.save(self.book, payload, self.book.meta("revision"))

    def fact(self, rid="where", subject="key", **overrides):
        return {"id": rid, "subject": subject, "predicate": "持有人", "value": "甲", "entities": ["a"],
                "start": 0, "evidence": self.ev1, **overrides}

    def rule(self, rid="r1", **overrides):
        return {"id": rid, "rule": "listen", "version": 1, "start": 0, "cooldown": 10,
                "description": "相邻两次施术须间隔十刻", "hard": True,
                "entities": ["a", "b"], "evidence": self.ev1, **overrides}

    def use(self, rid, at, **overrides):
        return {"id": rid, "actor": "a", "rule": "listen", "at": at, "evidence": self.ev1, **overrides}

    def hook(self, rid, state, at, **overrides):
        return {"id": rid, "hook": "return-key", "state": state, "at": at, "hard_deadline": 20,
                "description": "归还钥匙", "entities": ["a"], "evidence": self.ev1, **overrides}

    def test_distinct_items_keep_their_concurrent_holders(self):
        self.save(facts=[self.fact("key-held"), self.fact("ledger-held", "ledger", start=12)])
        result = world.context(self.book, plan(["a"], at=15), 3)
        self.assertEqual({r["subject"] for r in result["facts"]}, {"key", "ledger"})
        example = world.template()["facts"][0]
        self.assertEqual(example["predicate"], "持有人")
        self.assertEqual(example["subject"], "scarf")
        self.assertIn("lin", example["entities"])

    def test_evidenced_zero_opening_blocks_positive_spending(self):
        self.save(transfers=[{"id": "opening", "resource": "coin", "receiver": "a", "amount": "0",
                              "quantity_text": "没有铜钱", "at": 0, "opening": True, "evidence": self.ev1},
                             {"id": "spend", "resource": "coin", "sender": "a", "amount": "1",
                              "quantity_text": "一枚铜钱", "at": 10, "evidence": planned()}])
        packet = world.context(self.book, plan(["a"], at=10), 3)
        self.assertTrue(packet["resources"][0]["known"])
        self.assertEqual(packet["resources"][0]["amount"], "0")
        check = world.check(self.book, plan(["a"], at=10), 3)
        self.assertEqual(check["blockers"][0]["code"], "resource_overdraft")

    def test_zero_ordinary_transfer_still_rejected_atomically(self):
        revision = self.book.meta("revision")
        with self.assertRaises(WorldError) as caught:
            self.save(transfers=[{"id": "zero-income", "resource": "coin", "receiver": "a", "amount": "0.00",
                                  "quantity_text": "零枚", "at": 0, "evidence": self.ev1}])
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_transfers").fetchone()[0], 0)

    def knowledge(self, rid, state, at, **overrides):
        return {"id": rid, "actor": "a", "fact": "where", "state": state, "at": at,
                "channel": "正文明确交代", "evidence": self.ev1, **overrides}

    def test_undated_cognition_is_retained_without_claiming_latest_state(self):
        self.save(facts=[self.fact()], knowledge=[self.knowledge("known", "knows", 10),
                  self.knowledge("forgot", "unknown", None, evidence=self.ev2)])
        packet = world.context(self.book, plan(["a"], at=30), 3)
        self.assertEqual({r["id"] for r in packet["knowledge"]}, {"known", "forgot"})
        self.assertTrue(all(r["time_uncertain"] for r in packet["knowledge"]))
        self.assertIn("world_time_unknown", {w["code"] for w in packet["warnings"]})
        # Publication order cannot place an undated event before a flashback.
        earlier = world.context(self.book, plan(["a"], at=5), 3)
        self.assertEqual([r["id"] for r in earlier["knowledge"]], ["forgot"])
        self.assertTrue(earlier["knowledge"][0]["time_uncertain"])

    def test_undated_author_plan_does_not_erase_observed_cognition(self):
        self.save(facts=[self.fact()], knowledge=[self.knowledge("known", "knows", 10),
                  self.knowledge("future-loss", "unknown", None, evidence=planned())])
        packet = world.context(self.book, plan(["a"], at=30), 3)
        self.assertEqual([r["id"] for r in packet["knowledge"]], ["known"])
        self.assertNotIn("time_uncertain", packet["knowledge"][0])
        self.assertEqual(packet["planned"]["knowledge"][0]["id"], "future-loss")

    def test_unknown_use_survives_known_prior_time_and_requests_review(self):
        self.save(rules=[self.rule()], uses=[self.use("known-use", 10),
                  self.use("undated-use", None, evidence=self.ev2), self.use("next", 30, evidence=planned())])
        packet = world.context(self.book, plan(["a"], at=30), 3)
        self.assertEqual({r["id"] for r in packet["uses"]}, {"known-use", "undated-use"})
        result = world.check(self.book, plan(["a"], at=30), 3)
        self.assertIn("cooldown_time_unknown", {w["code"] for w in result["warnings"]})
        self.assertFalse(result["blockers"])
        self.save(uses=[self.use("next", 15, evidence=planned())])
        result = world.check(self.book, plan(["a"], at=15), 3)
        self.assertIn("cooldown_unfinished", {w["code"] for w in result["blockers"]})

    def test_unknown_record_evidence_must_still_be_current(self):
        self.save(rules=[self.rule()], uses=[self.use("known-use", 10), self.use("undated-use", None, evidence=self.ev2)])
        world.invalidate_chapters(self.book, [2])
        with self.assertRaises(WorldError) as caught:
            world.context(self.book, plan(["a"], at=30), 3)
        self.assertEqual(caught.exception.code, "stale_world_evidence")

    def test_undated_line_checkpoint_is_a_candidate_not_current_scene(self):
        base = {"line": "north", "place": "room", "summary": "候选场景", "unfinished": "等待回答", "entities": ["a"]}
        self.save(lines=[{**base, "id": "line-known", "at": 10, "evidence": self.ev1},
                         {**base, "id": "line-unknown", "at": None, "evidence": self.ev2}])
        packet = world.context(self.book, plan(["a"], at=30, line="north"), 3)
        self.assertIsNone(packet["line"])
        self.assertEqual({r["id"] for r in packet["line_candidates"]}, {"line-known", "line-unknown"})

    def test_actor_specific_rule_update_keeps_other_actors_old_cooldown(self):
        self.save(rules=[self.rule(), self.rule("r2", version=2, start=20, cooldown=1, entities=["a"], evidence=self.ev2)],
                  uses=[self.use("b-last", 15, actor="b"), self.use("b-next", 21, actor="b", evidence=planned()),
                        self.use("a-last", 19), self.use("a-next", 21, evidence=planned())])
        for actors in (["b"], ["a", "b"]):
            with self.subTest(actors=actors):
                result = world.check(self.book, plan(actors, at=21), 3)
                self.assertEqual([v["id"] for v in result["blockers"]], ["b-next"])
                self.assertNotIn("ability_state_unknown", {w["code"] for w in result["warnings"]})
        packet = world.context(self.book, plan(["a", "b"], at=21), 3)
        self.assertEqual({r["id"] for r in packet["rules"]}, {"r1", "r2"})

    def test_rule_line_restriction_does_not_apply_to_other_lines(self):
        self.save(rules=[self.rule(entities=[], line="north")],
                  uses=[self.use("prior", 10), self.use("next", 12, evidence=planned())])
        north = world.check(self.book, plan(["a"], at=12, line="north"), 3)
        self.assertIn("cooldown_unfinished", {v["code"] for v in north["blockers"]})
        south = world.check(self.book, plan(["a"], at=12, line="south"), 3)
        self.assertFalse(south["blockers"])
        self.assertIn("ability_state_unknown", {v["code"] for v in south["warnings"]})

    def test_undated_rule_change_is_not_silently_older_than_dated_rule(self):
        self.save(rules=[self.rule(), self.rule("r2", version=2, start=None, cooldown=1, evidence=self.ev2)],
                  uses=[self.use("prior", 10), self.use("next", 12, evidence=planned())])
        result = world.check(self.book, plan(["a"], at=12), 3)
        self.assertFalse(result["blockers"])
        self.assertIn("ability_state_unknown", {v["code"] for v in result["warnings"]})

    def test_new_same_tick_hook_events_are_rejected_atomically(self):
        revision = self.book.meta("revision")
        with self.assertRaises(WorldError) as caught:
            self.save(hooks=[self.hook("z-seed", "seeded", 10), self.hook("a-payoff", "fulfilled", 10)])
        self.assertEqual(caught.exception.code, "world_time_ambiguous")
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_hooks").fetchone()[0], 0)

    def legacy_tied_hooks(self):
        self.save(hooks=[self.hook("z-seed", "seeded", 10), self.hook("a-payoff", "fulfilled", 11)])
        # Simulate a pre-fix database whose accepted data has no event ordinal.
        self.book.db.execute("UPDATE world_hooks SET at=10 WHERE id='a-payoff'")

    def test_legacy_same_tick_hooks_report_ambiguity_after_vacuum(self):
        self.legacy_tied_hooks()
        self.book.db.execute("VACUUM")
        packet = world.context(self.book, plan(["a"], at=21), 3)
        self.assertEqual({r["state"] for r in packet["hooks"]}, {"seeded", "fulfilled"})
        self.assertTrue(all(r["order_uncertain"] for r in packet["hooks"]))
        check = world.check(self.book, plan(["a"], at=21), 3)
        self.assertIn("world_order_ambiguous", {v["code"] for v in check["warnings"]})
        self.assertNotIn("hard_promise_overdue", {v["code"] for v in check["warnings"]})

    def test_legacy_hook_rebind_preserves_ambiguity_and_idempotence(self):
        self.legacy_tied_hooks()
        with self.book.transaction(self.book.meta("revision")):
            world.invalidate_chapters(self.book, [1])
            revised = self.book.chapter(1, "甲在同一刻许诺并交还了钥匙；两件事的更细时间仍然未知。")
            changes = {"hooks": [self.hook("a-payoff", "fulfilled", 10, evidence=revised),
                                  self.hook("z-seed", "seeded", 10, evidence=revised)]}
            world.apply_in_transaction(self.book, changes, repair=True)
        before = world.context(self.book, plan(["a"], at=21), 3)
        self.assertTrue(world.save(self.book, changes, self.book.meta("revision"))["idempotent"])
        self.book.db.execute("VACUUM")
        after = world.context(self.book, plan(["a"], at=21), 3)
        self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))

    def test_resolved_hook_timing_clears_legacy_ambiguity(self):
        self.legacy_tied_hooks()
        with self.book.transaction(self.book.meta("revision")):
            world.invalidate_chapters(self.book, [1])
            revised = self.book.chapter(1, "甲第十刻许诺，第十一刻归还钥匙。")
            world.apply_in_transaction(self.book, {"hooks": [self.hook("z-seed", "seeded", 10, evidence=revised),
                                      self.hook("a-payoff", "fulfilled", 11, evidence=revised)]}, repair=True)
        packet = world.context(self.book, plan(["a"], at=21), 3)
        self.assertEqual([r["state"] for r in packet["hooks"]], ["fulfilled"])
        self.assertNotIn("world_order_ambiguous", {v["code"] for v in packet["warnings"]})

    def test_history_cannot_create_a_new_same_tick_hook_ambiguity(self):
        self.save(hooks=[self.hook("seed", "seeded", 10), self.hook("payoff", "fulfilled", 11)])
        with self.assertRaises(WorldError) as caught:
            with self.book.transaction(self.book.meta("revision")):
                world.invalidate_chapters(self.book, [1])
                revised = self.book.chapter(1, "甲先许诺，再交还钥匙。")
                world.apply_in_transaction(self.book, {"hooks": [self.hook("seed", "seeded", 10, evidence=revised),
                                          self.hook("payoff", "fulfilled", 10, evidence=revised)]}, repair=True)
        self.assertEqual(caught.exception.code, "world_time_ambiguous")
        packet = world.context(self.book, plan(["a"], at=21), 3)
        self.assertEqual([r["state"] for r in packet["hooks"]], ["fulfilled"])


class WorldRulesCommitRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.LongIntegrationReviewTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_zero_opening_is_committed_and_overdraft_rolls_back_next_chapter(self):
        f = self.fixture
        text = "# 第一章\n江棠摊开空钱袋，身上确实没有一枚铜钱。\n"
        changes = {"transfers": [{"id": "zero-opening", "resource": "coin", "receiver": "jiang", "amount": "0",
                                  "quantity_text": "一枚铜钱也没有", "opening": True, "at": 0, "evidence": f.evidence(1, text)}]}
        self.assertTrue(f.book.commit(1, f.draft, f.prepare_input(1, text, 0, changes))["exports_complete"])
        text = "# 第二章\n江棠没有任何进项，却从空钱袋拿出一枚铜钱交给杜承安。\n"
        changes = {"transfers": [{"id": "overdraft", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "1",
                                  "quantity_text": "一枚", "at": 1, "evidence": f.evidence(2, text)}]}
        f.assert_atomic_rejection(f.prepare_input(2, text, 1, changes), "resource_overdraft")

    def test_another_actors_upgrade_does_not_allow_committed_cooldown_violation(self):
        f = self.fixture
        text = "# 第一章\n二人施术都须间隔十刻。杜承安在第十五刻施展听风术。\n"
        rules = {"id": "listen-v1", "rule": "listen", "version": 1, "start": 0, "cooldown": 10,
                 "description": "二人施术都须间隔十刻", "hard": True, "entities": ["jiang", "du"], "evidence": f.evidence(1, text)}
        f.book.commit(1, f.draft, f.prepare_input(1, text, 15, {"rules": [rules], "uses": [
            {"id": "du-first", "actor": "du", "rule": "listen", "at": 15, "evidence": f.evidence(1, text)}]}))
        text = "# 第二章\n第二十刻，江棠独自突破，冷却缩至一刻，杜承安仍须遵守十刻的限制。\n"
        upgrade = {**rules, "id": "listen-v2", "version": 2, "start": 20, "cooldown": 1, "entities": ["jiang"],
                   "description": "仅江棠冷却缩至一刻", "evidence": f.evidence(2, text)}
        f.book.commit(2, f.draft, f.prepare_input(2, text, 20, {"rules": [upgrade]}))
        text = "# 第三章\n第二十一刻，杜承安未等足十刻，又一次施展听风术。\n"
        delta = f.prepare_input(3, text, 21, {"uses": [
            {"id": "du-too-soon", "actor": "du", "rule": "listen", "at": 21, "evidence": f.evidence(3, text)}]})
        revision = f.rev()
        with self.assertRaises(integration.story.StoryError) as caught:
            f.book.commit(3, f.draft, delta)
        self.assertEqual(caught.exception.code, "world_constraint")
        self.assertEqual(caught.exception.details["checks"]["blockers"][0]["code"], "cooldown_unfinished")
        self.assertEqual(f.rev(), revision)
        self.assertEqual(f.book.meta("last_chapter"), 2)
        self.assertIsNone(f.book.db.execute("SELECT id FROM world_uses WHERE id='du-too-soon'").fetchone())


if __name__ == "__main__":
    unittest.main()
