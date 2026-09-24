"""Writing dependency candidates retain the structure and ambiguity already read."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("context_dependencies_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class ContextDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-context-dependencies-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "夜信", "long")
        self.book = story.Book(self.root)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def seed(self, first_at=10):
        text = "# 第1章 开信\n甲先在东屋等候，后来移到西屋拆信。\n"
        draft = self.root / "source.md"
        draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(1, draft, "移到西屋拆信。", self.book.meta("revision"), "第一卷 夜信")
        evidence = {"kind": "chapter", "chapter": 1, "sha256": story.digest(text),
                    "quote": "甲先在东屋等候，后来移到西屋拆信。"}
        structure = {"goal": "确认信中线索", "entry_condition": "收到信件", "exit_condition": "查清寄信人",
                     "cost": "暂缓返程", "evidence": {"kind": "author_plan", "note": "本书已采用的卷段安排。"}}
        story.world.save(self.book, {
            "entities": [{"id": rid, "name": name, "kind": kind, "description": name}
                         for rid, name, kind in (("actor", "甲", "character"), ("east", "东屋", "place"),
                                                ("west", "西屋", "place"))],
            "volumes": [{**structure, "id": "volume", "title": "第一卷 夜信"}],
            "arcs": [{**structure, "id": "arc", "volume": "volume", "title": "查信"}],
            "lines": [{"id": rid, "line": "home", "at": at, "place": place,
                       "summary": "甲位于" + place, "unfinished": "继续查信", "evidence": evidence}
                      for rid, place, at in (("east-checkpoint", "east", first_at), ("west-checkpoint", "west", 10))],
        }, self.book.meta("revision"))
        self.book.save_plan(2, {
            "title": "查信", "volume_dir": "第一卷 夜信", "volume": "volume", "arc": "arc",
            "line": "home", "entities": ["actor"], "time": {"clock": "main", "start": 11, "end": 11},
            "goal": "核对寄信人的身份", "stop": "辨认线索后停笔", "constraints": [], "requires": [],
            "tags": [], "length": [1, 100], "beats": [{"choice": "回看信封", "change": "找到新线索"}],
        }, self.book.meta("revision"))

    def assert_selected_dependencies(self):
        context = self.book.context(2)
        self.assertIsNone(context["world"]["line"])
        self.assertEqual(len(context["world"]["line_candidates"]), 2)
        before = self.book.meta("revision")
        result = self.book.dependency_candidates(2)
        by_key = {(r["kind"], r["ref"]): r["sha"] for r in result["candidates"]}
        expected = {("world.volumes", "volume"), ("world.arcs", "arc"),
                    ("world.lines", "east-checkpoint"), ("world.lines", "west-checkpoint")}
        self.assertTrue(expected <= by_key.keys(), by_key)
        for kind, rid in expected:
            self.assertEqual(by_key[(kind, rid)], story.world.resolve_dependency(self.book, kind, rid))
        self.assertEqual(result["world_warnings"], context["world"]["warnings"])
        self.assertEqual(len(by_key), len(result["candidates"]))
        self.assertEqual(result["revision"], before)
        self.assertEqual(self.book.meta("revision"), before)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))

    def test_tied_line_candidates_and_selected_structures_are_available_for_review(self):
        self.seed()
        self.assert_selected_dependencies()

    def test_undated_line_candidates_are_available_without_claiming_current_scene(self):
        self.seed(first_at=None)
        self.assert_selected_dependencies()

    def test_unknown_time_warning_is_not_lost_from_dependency_candidates(self):
        self.seed(first_at=None)
        result = self.book.dependency_candidates(2)
        self.assertIn("world_time_unknown", {r["code"] for r in result["world_warnings"]})
        with self.assertRaises(story.StoryError) as caught:
            self.book.dependency_candidates(2, budget=256)
        self.assertEqual(caught.exception.code, "budget_exceeded")

    def test_selected_record_hashes_can_be_saved_without_resolving_the_ambiguity(self):
        self.seed()
        dependencies = self.book.dependency_candidates(2)
        quote = "甲回看信封，决定先核对寄信人的身份。"
        text = "# 第2章 查信\n" + quote + "\n"
        draft = self.root / "second.md"
        draft.write_bytes(text.encode("utf-8"))
        raw = {
            "book_id": dependencies["book_id"], "base_revision": dependencies["revision"],
            "summary": "甲开始核对信封上的身份线索。", "changes": [],
            "dependencies": dependencies["candidates"],
            "dependency_review": {"complete": False, "note": "保存已读卷段与断点证据，地点先后仍待复核。"},
            "review": {"draft_sha256": story.digest(text), "checks": {
                key: {"note": "本句只表明核对线索，不补写未确认的地点。", "quote": quote}
                for key in story.CHECKS}, "issues": []},
        }
        committed = self.book.commit(2, draft, raw)
        self.assertTrue(committed["exports_complete"], committed)
        stored = story.history.read_dependencies(self.book, 2)
        self.assertEqual(stored["dependencies"], dependencies["candidates"])
        self.assertFalse(stored["complete"])
        world = story.world.context(self.book, self.book.get_plan(2), 3)
        self.assertIsNone(world["line"])
        self.assertEqual(len(world["line_candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
