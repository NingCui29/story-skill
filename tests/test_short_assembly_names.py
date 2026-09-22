"""Complete titles and protected output filenames have separate responsibilities."""
from pathlib import Path
import tempfile
import unittest

import test_short_assembly as fixtures


story = fixtures.story


class ShortAssemblyNameTests(unittest.TestCase):
    commit = fixtures.ShortAssemblyTests.commit

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-assembly-names-")
        self.book = None

    def tearDown(self):
        if self.book:
            self.book.close()
        self.temp.cleanup()

    def create(self, title):
        if self.book:
            self.book.close()
        self.root = Path(self.temp.name).resolve() / str(len(list(Path(self.temp.name).iterdir())))
        result = story.Book.create(self.root, title, "short")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/章.md"
        self.draft.parent.mkdir(parents=True)
        return result

    def test_portable_name_is_visible_at_init_and_full_title_survives_assembly(self):
        for title in ("雨夜的钥匙", '旧院:最后一把钥匙?', '旧院/来信\\回信*"<>|', "CON", "NUL.txt", ".", "..",
                      "长书名" * 60, " A " * 50, "旧院\n最后的来信"):
            with self.subTest(title=title):
                initialized = self.create(title)
                relative = initialized["short_assembly_path"]
                self.assertEqual(Path(relative).name, relative)
                self.assertTrue(relative.endswith(".txt"))
                self.assertEqual(story.filename_component(relative[:-4], "output"), relative[:-4])
                self.assertEqual(initialized["title"], title)
                self.assertEqual(self.book.status()["short_assembly_path"], relative)
                self.commit(1, "归还", "第1章 归还\n她归还钥匙，从此关上了旧院的门。\n")
                result = self.book.assemble_short(1)
                self.assertTrue(result["exports_complete"], result)
                self.assertEqual(Path(result["path"]), self.root / relative)
                content = Path(result["path"]).read_text(encoding="utf-8")
                self.assertTrue(content.startswith(title + "\n\n第1章 归还\n"))
                self.assertEqual(self.book.meta("title"), title)
                self.book.close()
                self.book = story.Book(self.root)
                self.assertEqual(self.book.status()["short_assembly_path"], relative)
                self.assertEqual(self.book.status()["short_assembly"]["state"], "current")

    def test_long_names_with_the_same_prefix_have_distinct_stable_suffixes(self):
        first_title, second_title = "归途" * 80 + "甲", "归途" * 80 + "乙"
        first = self.create(first_title)["short_assembly_path"]
        self.assertEqual(self.create(first_title)["short_assembly_path"], first)
        second = self.create(second_title)["short_assembly_path"]
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first[:-4].encode("utf-8")), 180)

    def test_unmapped_old_book_is_recoverable_without_metadata_reinitialization(self):
        title = "旧院:最后一把钥匙"
        self.create(title)
        with self.book.transaction():
            self.book.db.execute("DELETE FROM meta WHERE key='short_assembly_path'")
        revision = self.book.meta("revision")
        relative = self.book.status()["short_assembly_path"]
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertIsNone(self.book.db.execute("SELECT value FROM meta WHERE key='short_assembly_path'").fetchone())
        self.commit(1, "归还", "第1章 归还\n她归还钥匙，从此关上了旧院的门。\n")
        result = self.book.assemble_short(1)
        self.assertEqual(Path(result["path"]), self.root / relative)
        self.assertEqual(self.book.meta("short_assembly_path"), relative)

    def test_existing_registered_path_is_authoritative_for_revision_and_recovery(self):
        self.create("雨夜的钥匙")
        self.commit(1, "归还", "第1章 归还\n她归还钥匙，从此关上了旧院的门。\n")
        original = Path(self.book.assemble_short(1)["path"])
        # An older runtime's existing registration wins over a newly calculated preview.
        with self.book.transaction():
            self.book.set_meta("short_assembly_path", "另一个预览名称.txt")
        relative = original.name
        self.assertEqual(self.book.status()["short_assembly_path"], relative)
        self.commit(1, "归还", "第1章 归还\n她归还钥匙，把旧院的契书也一并交还。\n", replace_last=True)
        self.assertIn("契书", original.read_text(encoding="utf-8"))
        original.unlink()
        self.assertTrue(self.book.export(safe_only=True)["exports_complete"])
        self.assertTrue(original.exists())
        self.assertFalse((self.root / "另一个预览名称.txt").exists())

    def test_sanitized_name_collision_preserves_the_preexisting_file(self):
        initialized = self.create("旧院:最后一把钥匙")
        self.commit(1, "归还", "第1章 归还\n她归还钥匙，从此关上了旧院的门。\n")
        output = self.root / initialized["short_assembly_path"]
        output.write_bytes("这份手工原稿必须保留。\n".encode("utf-8"))
        with self.assertRaises(story.StoryError) as raised:
            self.book.assemble_short(1)
        self.assertEqual(raised.exception.code, "assembly_conflict")
        self.assertEqual(output.read_text(encoding="utf-8"), "这份手工原稿必须保留。\n")
        self.assertIsNone(self.book._short_assembly_record())
        self.assertEqual(self.book.meta("title"), "旧院:最后一把钥匙")


if __name__ == "__main__":
    unittest.main()
