"""Narrative-state behavior, using isolated SQLite fixtures and simulated gaps."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest


PATH = Path(__file__).resolve().parents[1] / "skills/story-codex/scripts/story_world.py"
SPEC = importlib.util.spec_from_file_location("world_behavior_tests", PATH)
world = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(world)


class WorldError(Exception):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def fail(code, message, **details):
    raise WorldError(code, message, **details)


def text_field(value, field, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        fail("invalid_input", field)
    return value


world.inject(SimpleNamespace(fail=fail, text_field=text_field,
                            dumps=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))


class FixtureBook:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE chapters(chapter INTEGER PRIMARY KEY,sha TEXT,text TEXT); CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER); INSERT INTO meta VALUES ('revision',0),('last_chapter',0); CREATE TABLE events(revision INTEGER,kind TEXT,data TEXT);")
        self.db.executescript(world.SCHEMA)

    def meta(self, key):
        return self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]

    def set_meta(self, key, value):
        self.db.execute("UPDATE meta SET value=? WHERE key=?", (value, key))

    @contextmanager
    def transaction(self, expected):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.meta("revision") != expected:
                fail("stale_revision", "Changed")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def event(self, kind, payload):
        revision = self.meta("revision") + 1
        self.set_meta("revision", revision)
        self.db.execute("INSERT INTO events VALUES (?,?,?)", (revision, kind, json.dumps(payload)))
        return revision

    def chapter(self, chapter, text="江棠收到钥匙。杜承安答应三日内归还。两人分别说出了自己的猜测。北库收到五枚铜钱。江棠用了听风术。她还欠一个回答。"):
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.db.execute("INSERT OR REPLACE INTO chapters VALUES (?,?,?)", (chapter, sha, text))
        self.set_meta("last_chapter", max(chapter, self.meta("last_chapter")))
        return {"kind": "chapter", "chapter": chapter, "sha256": sha, "quote": text}


def planned(note="作者的下一步设计，并非已发表事实"):
    return {"kind": "author_plan", "note": note}


def entity(cid, name=None, kind="character"):
    return {"id": cid, "name": name or cid, "kind": kind, "description": "本书独立实体"}


def fact(rid, subject, value, evidence, start=0, **kwargs):
    return {"id": rid, "subject": subject, "predicate": "所知线索", "value": value, "start": start, "evidence": evidence, **kwargs}


def plan(entities=None, at=20, end=None, **kwargs):
    return {"entities": entities or [], "time": {"clock": "main", "start": at, "end": at if end is None else end}, **kwargs}


class LongWorldTests(unittest.TestCase):
    def setUp(self):
        self.book = FixtureBook()
        self.ev = self.book.chapter(1)
        self.save(entities=[entity("jiang", "江棠"), entity("du", "杜承安"), entity("north", "北库", "place"), entity("coin", "铜钱", "resource")])

    def tearDown(self):
        self.book.db.close()

    def save(self, **payload):
        return world.save(self.book, payload, self.book.meta("revision"))

    def error(self, code, func, *args, **kwargs):
        with self.assertRaises(WorldError) as error:
            func(*args, **kwargs)
        self.assertEqual(error.exception.code, code)
        return error.exception

    def hook(self, rid="hook-seed", state="seeded", evidence=None, **kwargs):
        return {"id": rid, "hook": "return-key", "state": state, "description": "杜承安答应归还钥匙", "at": 0,
                "hard_deadline": 30, "window_start": 10, "window_end": 50, "entities": ["du"], "evidence": evidence or self.ev, **kwargs}

    def test_chinese_names_and_scoped_aliases_never_guess(self):
        self.save(entities=[entity("other", "江棠")], aliases=[{"alias": "老杜", "entity": "du", "scope": "north-line"}, {"alias": "老杜", "entity": "other", "scope": "south-line"}])
        self.error("ambiguous_entity", world.resolve, self.book, ["江棠"])
        self.assertEqual(world.resolve(self.book, ["jiang"]), ["jiang"])
        self.assertEqual(world.resolve(self.book, ["老杜"], "north-line"), ["du"])
        self.error("world_reference", world.resolve, self.book, ["老杜"], "elsewhere")

    def test_evidence_failure_rolls_back_whole_batch_and_revision(self):
        revision = self.book.meta("revision")
        bad = {**self.ev, "quote": "这句话从未出现"}
        self.error("world_evidence", self.save, entities=[entity("new")], facts=[fact("f", "new", "有钥匙", bad)])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_entities WHERE id='new'").fetchone())

    def test_author_plan_is_separate_and_can_promote_without_double_event(self):
        value = fact("f", "jiang", "拿到钥匙", planned())
        self.save(facts=[value])
        result = world.context(self.book, plan(["jiang"]), 2)
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["planned"]["facts"][0]["id"], "f")
        value["evidence"] = self.ev
        self.save(facts=[value])
        self.assertEqual(len(world.context(self.book, plan(["jiang"]), 2)["facts"]), 1)
        revision = self.book.meta("revision")
        self.assertTrue(self.save(facts=[value])["idempotent"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.error("world_record_conflict", self.save, facts=[{**value, "value": "钥匙其实在别人手里"}])

    def test_rolling_arc_replanning_does_not_change_published_facts(self):
        common = {"title": "停航交接", "goal": "查清去处", "entry_condition": "清册有缺项", "exit_condition": "实物有着落", "cost": "整理费延迟", "evidence": planned()}
        self.save(volumes=[{"id": "v1", **common}], arcs=[{"id": "a1", "volume": "v1", **common}], facts=[fact("f", "jiang", "拿到钥匙", self.ev)])
        self.save(arcs=[{"id": "a1", "volume": "v1", **common, "goal": "先保留证据再核实"}])
        result = world.context(self.book, plan(["jiang"], arc="a1"), 2)
        self.assertEqual(result["volume"]["id"], "v1")
        self.assertEqual(result["facts"][0]["value"], "拿到钥匙")
        self.assertEqual(result["arc"]["goal"], "先保留证据再核实")

    def test_each_story_line_resumes_its_own_checkpoint(self):
        ev80, ev90 = self.book.chapter(80), self.book.chapter(90)
        self.save(lines=[{"id": "north-80", "line": "north-line", "at": 10, "place": "north", "summary": "北库问话停在钥匙", "unfinished": "等待杜承安回答", "entities": ["jiang", "du"], "evidence": ev80},
                         {"id": "south-90", "line": "south-line", "at": 12, "place": "north", "summary": "另一条线", "unfinished": "等船", "evidence": ev90}])
        result = world.context(self.book, plan(["jiang"], at=11, line="north-line"), 91)
        self.assertEqual(result["line"]["id"], "north-80")
        self.assertEqual(result["line"]["unfinished"], "等待杜承安回答")

    def test_flashback_knowledge_excludes_future_acquisition(self):
        self.save(facts=[fact("secret", "north", "钥匙在夹层", self.ev)], knowledge=[
            {"id": "k10", "actor": "jiang", "fact": "secret", "state": "unknown", "at": 10, "channel": "承认不知道", "evidence": self.ev},
            {"id": "k50", "actor": "jiang", "fact": "secret", "state": "knows", "at": 50, "channel": "亲眼看见", "evidence": self.ev},
            {"id": "du10", "actor": "du", "fact": "secret", "state": "suspects", "at": 10, "channel": "根据旧痕迹猜测", "evidence": self.ev}])
        early = world.context(self.book, plan(["jiang", "du"], at=20), 2)
        self.assertEqual({r["actor"]: r["state"] for r in early["knowledge"]}, {"jiang": "unknown", "du": "suspects"})
        later = world.context(self.book, plan(["jiang"], at=60), 2)
        self.assertEqual(later["knowledge"][0]["state"], "knows")
        absent = world.context(self.book, plan(["jiang"], at=5), 2)
        self.assertEqual(absent["knowledge"], [])

    def test_belief_does_not_overwrite_world_truth(self):
        self.save(facts=[fact("truth", "north", "钥匙在夹层", self.ev), fact("rumor", "north", "钥匙被扔进河里", planned())], knowledge=[
            {"id": "belief", "actor": "du", "fact": "rumor", "state": "believes", "at": 5, "channel": "相信他人谎话", "evidence": self.ev}])
        result = world.context(self.book, plan(["du", "north"]), 2)
        self.assertEqual([f["value"] for f in result["facts"]], ["钥匙在夹层"])
        self.assertEqual(result["knowledge"][0]["state"], "believes")
        self.assertEqual(result["propositions"][0]["evidence"]["mode"], "author_plan")

    def test_simulated_chapter_300_recalls_seed_and_separates_soft_schedule(self):
        seed = self.book.chapter(30)
        self.save(hooks=[self.hook(evidence=seed)])
        result = world.context(self.book, plan(["du"], at=20), 300)
        self.assertEqual(result["hooks"][0]["evidence"]["chapter"], 30)
        check = world.check(self.book, plan(["du"], at=20, chapter=300))
        self.assertEqual(check["blockers"], [])
        self.assertIn("soft_schedule_due", {w["code"] for w in check["warnings"]})

    def test_hard_promise_due_even_without_related_entity_and_payoff_has_evidence(self):
        self.save(hooks=[self.hook()])
        overdue = world.check(self.book, plan([], at=31))
        self.assertEqual(overdue["blockers"], [])
        self.assertIn("hard_promise_overdue", {b["code"] for b in overdue["warnings"]})
        self.error("world_evidence", self.save, hooks=[self.hook("payoff", "fulfilled", planned())])
        ev2 = self.book.chapter(2)
        self.save(hooks=[self.hook("payoff", "fulfilled", ev2, at=28)])
        self.assertEqual(world.check(self.book, plan([], at=31))["blockers"], [])

    def test_character_can_break_a_promise_with_explicit_consequences(self):
        self.save(hooks=[self.hook()])
        ev2 = self.book.chapter(2, "杜承安把钥匙卖了，他知道关岚再不会相信自己。")
        self.save(hooks=[self.hook("breach", "breached", ev2, at=31, reason="卖掉钥匙，失去关岚的信任")])
        packet = world.context(self.book, plan(["du"], at=32), 3)
        self.assertEqual(packet["hooks"][0]["state"], "breached")
        self.assertNotIn("hard_promise_overdue", {w["code"] for w in world.check(self.book, plan(["du"], at=32))["warnings"]})

    def test_payoff_reached_through_seed_without_repeating_entity_tags(self):
        self.save(hooks=[self.hook()])
        ev2 = self.book.chapter(2, "杜承安在期限前交还钥匙，关岚当面收下。")
        self.save(hooks=[self.hook("payoff", "fulfilled", ev2, at=15, entities=[])])
        packet = world.context(self.book, plan(["du"], at=20), 3)
        self.assertEqual(packet["hooks"][0]["state"], "fulfilled")

    def test_history_rebinds_seed_before_payoff_without_false_reopening(self):
        self.save(hooks=[self.hook(at=1)])
        original11 = self.book.chapter(11, "杜承安解开蓝线，把铜钥匙交还。")
        self.save(hooks=[self.hook("payoff", "fulfilled", original11, at=11)])
        with self.book.transaction(self.book.meta("revision")):
            world.invalidate_chapters(self.book, [1, 11])
            revised1 = self.book.chapter(1, "江棠把系着红线的钥匙交给杜承安，他答应按时归还。")
            revised11 = self.book.chapter(11, "杜承安解开红线，把铜钥匙交还。")
            changes = world.apply_in_transaction(self.book, {"hooks": [self.hook(evidence=revised1, at=1), self.hook("payoff", "fulfilled", revised11, at=11)]}, repair=True)
            self.assertEqual(len(changes), 2)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_evidence WHERE valid=0").fetchone()[0], 0)
        self.assertEqual(world.context(self.book, plan(["du"], at=12), 12)["hooks"][0]["state"], "fulfilled")

    def test_normal_reinforcement_after_fulfillment_still_requires_reopening(self):
        self.save(hooks=[self.hook(at=1)])
        ev11 = self.book.chapter(11, "杜承安已经当面交还钥匙。")
        self.save(hooks=[self.hook("payoff", "fulfilled", ev11, at=11)])
        ev12 = self.book.chapter(12, "杜承安又说起那把钥匙。")
        revision = self.book.meta("revision")
        self.error("world_lifecycle", self.save, hooks=[self.hook("bad-reinforcement", "reinforced", ev12, at=12)])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_hooks WHERE id='bad-reinforcement'").fetchone())

    def test_later_published_flashback_does_not_inherit_future_payoff(self):
        self.save(hooks=[self.hook(at=1)])
        ev11 = self.book.chapter(11, "杜承安已经当面交还钥匙。")
        self.save(hooks=[self.hook("payoff", "fulfilled", ev11, at=11)])
        flashback = self.book.chapter(12, "那是更早的第五刻，杜承安再一次保证会还钥匙。")
        self.save(hooks=[self.hook("flashback", "reinforced", flashback, at=5)])
        self.assertEqual(world.context(self.book, plan(["du"], at=6), 13)["hooks"][0]["state"], "reinforced")
        self.assertEqual(world.context(self.book, plan(["du"], at=12), 13)["hooks"][0]["state"], "fulfilled")

    def test_rules_are_versioned_and_cooldown_is_per_actor_and_clock(self):
        self.save(rules=[{"id": "listen-v1", "rule": "listen", "version": 1, "start": 0, "cooldown": 10, "description": "听风术需休息十刻", "hard": True, "entities": ["jiang", "du"], "evidence": planned()}],
                  uses=[{"id": "used", "actor": "jiang", "rule": "listen", "at": 10, "evidence": self.ev}, {"id": "next", "actor": "jiang", "rule": "listen", "at": 15, "evidence": planned()}, {"id": "du-next", "actor": "du", "rule": "listen", "at": 15, "evidence": planned()}])
        check = world.check(self.book, plan(["jiang", "du"], at=15))
        self.assertEqual([b["id"] for b in check["blockers"]], ["next"])
        self.save(rules=[{"id": "listen-v2", "rule": "listen", "version": 2, "start": 30, "cooldown": 3, "description": "训练后缩短间隔", "hard": True, "entities": ["jiang"], "evidence": planned()}])
        old = world.context(self.book, plan(["jiang"], at=15), 2)
        self.assertEqual(old["planned"]["rules"][0]["version"], 1)
        later = world.context(self.book, plan(["jiang"], at=40), 2)
        self.assertEqual(later["planned"]["rules"][0]["version"], 2)

    def test_exact_resource_flow_and_unknown_quantities(self):
        self.save(transfers=[{"id": "opening", "resource": "coin", "receiver": "jiang", "amount": "5", "quantity_text": "五枚", "opening": True, "at": 0, "evidence": self.ev},
                             {"id": "paid", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "2", "quantity_text": "两枚", "at": 1, "evidence": self.ev},
                             {"id": "next-pay", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "4", "quantity_text": "四枚", "at": 2, "evidence": planned()}])
        result = world.context(self.book, plan(["jiang", "du"], at=2), 2)
        self.assertEqual(next(r for r in result["resources"] if r["holder"] == "jiang")["amount"], "3")
        self.assertIn("resource_overdraft", {b["code"] for b in world.check(self.book, plan(["jiang", "du"], at=2))["blockers"]})
        self.save(transfers=[{"id": "next-pay", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": None, "quantity_text": "约半数", "at": 2, "evidence": planned()}])
        unknown = world.check(self.book, plan(["jiang", "du"], at=2))
        self.assertEqual(unknown["blockers"], [])
        self.assertIn("quantity_or_time_unknown", {w["code"] for w in unknown["warnings"]})

    def test_planned_income_cannot_be_reported_as_actual_balance(self):
        self.save(transfers=[{"id": "future-opening", "resource": "coin", "receiver": "jiang", "amount": "100000", "quantity_text": "十万", "opening": True, "at": 10, "evidence": planned()}])
        self.assertEqual(world.context(self.book, plan(["jiang"], at=10), 2)["resources"], [])

    def test_repeated_arc_pattern_is_advice_with_comparable_evidence(self):
        base = {"title": "反复试探", "goal": "保住证据", "entry_condition": "有人阻拦", "exit_condition": "证据留存", "cost": "关系受损", "evidence": planned()}
        self.save(volumes=[{"id": "v", **base}], arcs=[{"id": "a", "volume": "v", **base}])
        self.save(arc_steps=[{"id": f"s{i}", "arc": "a", "actor": "jiang", "pattern": "受阻后公开证据", "desire": "取回实物", "strategy": "公开核对", "choice": "坚持署名", "cost": "失去报酬", "relationship": "关系更疏远", "result": "保存一份证据", "irreversible": i == 2, "evidence": self.ev} for i in range(3)])
        check = world.check(self.book, plan(["jiang"]))
        self.assertEqual(check["blockers"], [])
        advice = next(w for w in check["warnings"] if w["code"] == "repeated_arc_pattern")
        self.assertEqual(len(advice["examples"]), 3)
        self.assertTrue(any(e["irreversible"] for e in advice["examples"]))

    def test_latest_text_edit_stales_bound_facts_and_dependency(self):
        self.save(facts=[fact("f", "jiang", "拿到钥匙", self.ev)])
        self.assertIsNotNone(world.resolve_dependency(self.book, "world.facts", "f"))
        self.book.chapter(1, "江棠没有拿到钥匙。")
        self.assertIsNone(world.resolve_dependency(self.book, "facts", "f"))
        self.error("stale_world_evidence", world.context, self.book, plan(["jiang"]), 2)

    def test_history_invalidation_is_transactional_and_non_destructive(self):
        self.save(facts=[fact("f", "jiang", "拿到钥匙", self.ev)])
        before = world.resolve_dependency(self.book, "facts", "f")
        self.book.db.execute("BEGIN IMMEDIATE")
        world.invalidate_chapters(self.book, [1])
        self.assertIsNone(world.resolve_dependency(self.book, "facts", "f"))
        self.book.db.rollback()
        self.assertEqual(world.resolve_dependency(self.book, "facts", "f"), before)
        self.assertEqual(self.book.db.execute("SELECT value FROM world_facts WHERE id='f'").fetchone()[0], "拿到钥匙")

    def test_selected_hard_state_is_never_silently_truncated(self):
        self.save(facts=[fact("f", "jiang", "钥匙" * 200, self.ev, hard=True)])
        error = self.error("budget_exceeded", world.context, self.book, plan(["jiang"]), 2, 200)
        self.assertGreater(error.details["minimum_bytes"], 200)

    def test_schema_and_boolean_input_validation(self):
        self.error("invalid_input", self.save, facts=[fact("f", "jiang", "拿到钥匙", self.ev, hard=1)])
        self.error("invalid_input", self.save, facts=[fact("f", "jiang", "拿到钥匙", self.ev, surprise="隐藏字段")])
        self.error("invalid_input", world.context, self.book, plan(["jiang"], at=True), 2)

    def test_complete_template_is_valid_planned_data_only(self):
        value = world.template("all")
        self.save(**value)
        packet = world.context(self.book, plan(["lin"], at=20, volume="v1", arc="a1", line="bridge-line"), 2)
        self.assertEqual(packet["facts"], [])
        self.assertEqual(packet["knowledge"], [])
        self.assertEqual(packet["resources"], [])
        self.assertIsNone(packet["line"])

    def test_chapter_and_world_changes_share_one_transaction(self):
        revision = self.book.meta("revision")
        try:
            with self.book.transaction(revision):
                ev2 = self.book.chapter(2, "江棠从门房接过铜钥匙。")
                changes = world.apply_in_transaction(self.book, {"facts": [fact("f", "jiang", "铜钥匙", ev2)]})
                self.assertEqual(len(changes), 1)
                raise RuntimeError("Simulated failure before the chapter event")
        except RuntimeError:
            pass
        self.assertIsNone(self.book.db.execute("SELECT chapter FROM chapters WHERE chapter=2").fetchone())
        self.assertIsNone(self.book.db.execute("SELECT id FROM world_facts WHERE id='f'").fetchone())
        self.assertEqual(self.book.meta("revision"), revision)
        self.error("transaction_required", world.apply_in_transaction, self.book, {"facts": []})

    def test_history_can_rebind_or_retire_without_leaving_stale_live_state(self):
        self.save(facts=[fact("f", "jiang", "拿到钥匙", self.ev), fact("removed", "du", "也拿到钥匙", self.ev)])
        with self.book.transaction(self.book.meta("revision")):
            world.invalidate_chapters(self.book, [1])
            new = self.book.chapter(1, "江棠没有接钥匙，杜承安也把手收了回来。")
            changes = world.apply_in_transaction(self.book, {"facts": [fact("f", "jiang", "没有拿到钥匙", new)], "retirements": [{"kind": "facts", "id": "removed", "reason": "修订撤回交付动作", "evidence": new}]}, repair=True)
            self.assertEqual(len(changes), 2)
        self.assertIsNotNone(world.resolve_dependency(self.book, "facts", "f"))
        self.assertIsNone(world.resolve_dependency(self.book, "facts", "removed"))
        self.assertEqual([r["value"] for r in world.context(self.book, plan(["jiang", "du"]), 2)["facts"]], ["没有拿到钥匙"])
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_evidence WHERE valid=0").fetchone()[0], 0)

    def test_retired_fact_requires_live_knowledge_dependency_to_be_repaired(self):
        self.save(facts=[fact("f", "north", "钥匙在夹层", self.ev)], knowledge=[{"id": "k", "actor": "jiang", "fact": "f", "state": "knows", "at": 2, "channel": "看见夹层", "evidence": self.ev}])
        with self.assertRaises(WorldError) as error:
            with self.book.transaction(self.book.meta("revision")):
                world.invalidate_chapters(self.book, [1])
                new = self.book.chapter(1, "江棠没有打开夹层。")
                world.apply_in_transaction(self.book, {"retirements": [{"kind": "facts", "id": "f", "reason": "删掉查看夹层", "evidence": new}]}, repair=True)
        self.assertEqual(error.exception.code, "world_reference")
        self.assertIsNotNone(world.resolve_dependency(self.book, "facts", "f"))

    def test_future_observed_use_does_not_break_earlier_planned_scene(self):
        self.save(rules=[{"id": "r", "rule": "listen", "version": 1, "start": 0, "cooldown": 10, "hard": True, "description": "休息十刻", "entities": ["jiang"], "evidence": planned()}], uses=[{"id": "future", "actor": "jiang", "rule": "listen", "at": 50, "evidence": self.ev}, {"id": "early", "actor": "jiang", "rule": "listen", "at": 10, "evidence": planned()}])
        check = world.check(self.book, {"entities": ["jiang"]})
        self.assertEqual(check["blockers"], [])

    def test_stale_checkpoint_and_balance_cannot_bypass_evidence_check(self):
        self.save(lines=[{"id": "l", "line": "north-line", "at": 0, "place": "north", "summary": "正在问话", "unfinished": "等回答", "evidence": self.ev}], transfers=[{"id": "o", "resource": "coin", "receiver": "jiang", "amount": "5", "quantity_text": "五枚", "opening": True, "at": 0, "evidence": self.ev}])
        self.book.chapter(1, "问话没有发生，也没有收到铜钱。")
        self.error("stale_world_evidence", world.context, self.book, plan([], line="north-line"), 2)
        self.error("stale_world_evidence", world.context, self.book, plan(["jiang"]), 2)

    def test_unknown_opening_time_never_claims_exact_scene_balance(self):
        self.save(transfers=[{"id": "o", "resource": "coin", "receiver": "jiang", "amount": "5", "quantity_text": "某时有五枚", "opening": True, "at": None, "evidence": self.ev}])
        self.assertFalse(world.context(self.book, plan(["jiang"]), 2)["resources"][0]["known"])

    def test_allowed_decimal_quantities_remain_exact_above_default_precision(self):
        self.save(transfers=[{"id": "o", "resource": "coin", "receiver": "jiang", "amount": "999999999999999999999999.000000000003", "quantity_text": "有明确精度的账面量", "opening": True, "at": 0, "evidence": self.ev},
                             {"id": "p", "resource": "coin", "sender": "jiang", "receiver": "du", "amount": "0.000000000001", "quantity_text": "最小记账单位", "at": 1, "evidence": self.ev}])
        value = world.context(self.book, plan(["jiang"]), 2)["resources"][0]["amount"]
        self.assertEqual(value, "999999999999999999999999.000000000002")

    def test_historical_versions_are_filtered_before_text_materialization(self):
        self.save(facts=[fact(f"v{i}", "jiang", "钥匙状态" + str(i), self.ev, start=i) for i in range(500)])
        statements = []
        self.book.db.set_trace_callback(statements.append)
        result = world.context(self.book, plan(["jiang"], at=300), 2)
        self.book.db.set_trace_callback(None)
        self.assertEqual([r["id"] for r in result["facts"]], ["v300"])
        materialized = [s for s in statements if s.startswith("SELECT * FROM world_facts WHERE id=")]
        self.assertLessEqual(len(materialized), 2)


if __name__ == "__main__":
    unittest.main()
