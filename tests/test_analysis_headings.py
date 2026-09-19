from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_story as fixtures


story = fixtures.story


class AnalysisHeadingTests(unittest.TestCase):
    def assert_chunks(self, text, expected_titles):
        chunks = story.split_source(text, 2400)
        self.assertEqual([title for _, _, title in chunks], expected_titles)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], len(text))
        self.assertTrue(all(left[1] == right[0] for left, right in zip(chunks, chunks[1:])))
        self.assertEqual("".join(text[start:end] for start, end, _ in chunks), text)
        return chunks

    def test_indented_chapter_titles_preserve_boundaries_and_source_bytes(self):
        for indent in ("", "  ", "\u3000\u3000", "\t"):
            with self.subTest(indent=repr(indent)):
                text = f"{indent}第1章 收据\r\n她把收据放进抽屉。\r\n{indent}第2章 归还\r\n他带着原件回来。\r\n"
                self.assert_chunks(text, ["第1章 收据", "第2章 归还"])

    def test_mixed_volume_chapter_and_bonus_headings_keep_exact_ranges(self):
        sections = [
            "前言中的说明。\n\n",
            "  # 第一卷 旧街\n卷前记。\n",
            "\u3000\u3000第1章 收据\n她把收据放进抽屉。\n",
            "\t## 第２章 归还\n他带着原件回来。\n",
            "\u3000\u3000番外 一封信\n信还没有拆开。\n",
        ]
        text = "".join(sections)
        chunks = self.assert_chunks(text, ["未命名文本 / 前言", "# 第一卷 旧街", "第1章 收据", "## 第２章 归还", "番外 一封信"])
        self.assertEqual([text[start:end] for start, end, _ in chunks], sections)

    def test_heading_indent_does_not_consume_leading_blank_lines(self):
        text = "\r\n\t \r\n\u3000\u3000第1章 门外\r\n她停在门外。\r\n"
        chunks = self.assert_chunks(text, ["未命名文本 / 前言", "第1章 门外"])
        self.assertEqual(text[:chunks[0][1]], "\r\n\t \r\n")

    def test_prose_mentions_do_not_create_heading_boundaries(self):
        text = "  第1章 收据\n她在第2章的标题旁画了一个圈。\n  她说：番外留到最后再看。\n  第2章 归还\n他回来了。\n"
        self.assert_chunks(text, ["第1章 收据", "第2章 归还"])

    def test_existing_source_keeps_saved_chunk_identity_after_splitter_change(self):
        # An old installation treated both indented chapters as one chunk.
        # Re-ingest must retain its saved ranges, analysis and resume position.
        with tempfile.TemporaryDirectory(prefix="story-analysis-headings-") as folder:
            root = Path(folder)
            source = root / "原文.txt"
            text = "\u3000\u3000第1章 收据\n她把收据放进抽屉。\n\u3000\u3000第2章 归还\n他带着原件回来。\n"
            source.write_text(text, encoding="utf-8")
            story.Book.create(root / "分析", "收据", "analysis")
            with closing(story.Book(root / "分析")) as book:
                with patch.object(story, "split_source", return_value=[(0, len(text), "未命名文本 / 前言")]):
                    sid = book.ingest(source, "partial")["source"]
                chunk = book.next_chunks(sid)["chunks"][0]
                book.record(sid, 1, {"chunk_sha256": chunk["sha"], "summary": "两章分别写收据收起和原件归还。",
                    "findings": [{"kind": "事实", "claim": "第一章写她收起收据。", "quote": "她把收据放进抽屉。"}]})
                before = book.findings(sid)
            with closing(story.Book(root / "分析")) as book:
                resumed = book.ingest(source, "partial")
                self.assertTrue(resumed["idempotent"])
                self.assertEqual(resumed["chunks_total"], 1)
                self.assertEqual(book.findings(sid), before)
                self.assertEqual(book.next_chunks(sid)["chunks"], [])
                item = book.findings(sid)["results"][0]
                self.assertEqual(book.source_read(sid, item["start"], item["end"], 12000)["text"], text)
                self.assertEqual(source.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
