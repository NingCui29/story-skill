from contextlib import closing
import hashlib
from itertools import product
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_story as fixtures


story = fixtures.story
LINE_ENDINGS = ("\n", "\r\n", "\r")


def chapter_sections(indent, endings):
    first, between, second = endings
    return [
        f"{indent}第1章 门外{first}甲🙂e\u0301𠮷乙。{between}",
        f"{indent}第2章 归来{second}她带着原件回来。",
    ]


class AnalysisLineEndingTests(unittest.TestCase):
    def assert_sections(self, text, chunks, sections, titles):
        expected = []
        start = 0
        for section, title in zip(sections, titles):
            expected.append((start, start + len(section), title))
            start += len(section)
        self.assertEqual(chunks, expected)
        self.assertEqual("".join(text[start:end] for start, end, _ in chunks), text)
        self.assertEqual(start, len(text))

    def test_all_line_ending_combinations_keep_indented_chapter_boundaries(self):
        for endings in product(LINE_ENDINGS, repeat=3):
            for indent in ("", "  ", "\u3000\u3000", "\t"):
                with self.subTest(endings=endings, indent=repr(indent)):
                    sections = chapter_sections(indent, endings)
                    text = "".join(sections)
                    self.assert_sections(text, story.split_source(text, 2400), sections,
                                         ["第1章 门外", "第2章 归来"])

    def test_mixed_lines_preserve_blank_prefix_and_heading_types(self):
        sections = [
            "\r\n\t \r\u3000\r\n",
            "  # 第一卷 旧街\r卷前记。\r",
            "\u3000\u3000第1章 门外\n她在第2章的标题旁画圈。\r她说：番外以后再看。\r\n",
            "\t## 第２章 归来\r\n她带着原件回来。\r",
            "\u3000\u3000番外 一封信\r信还没有拆开。",
        ]
        text = "".join(sections)
        self.assert_sections(text, story.split_source(text, 2400), sections,
                             ["未命名文本 / 前言", "# 第一卷 旧街", "第1章 门外",
                              "## 第２章 归来", "番外 一封信"])

    def test_ingest_and_source_read_preserve_original_unicode_ranges(self):
        endings_cases = [(ending,) * 3 for ending in LINE_ENDINGS] + [
            ("\n", "\r", "\r\n"),
            ("\r\n", "\n", "\r"),
            ("\r", "\r\n", "\n"),
        ]
        for endings in endings_cases:
            for indent in ("  ", "\u3000\u3000"):
                with self.subTest(endings=endings, indent=repr(indent)):
                    with tempfile.TemporaryDirectory(prefix="story-analysis-line-endings-") as folder:
                        root = Path(folder)
                        sections = chapter_sections(indent, endings)
                        text = "".join(sections)
                        raw = text.encode("utf-8")
                        source = root / "原文.txt"
                        source.write_bytes(raw)
                        story.Book.create(root / "分析", "门外", "analysis")
                        with closing(story.Book(root / "分析")) as book:
                            imported = book.ingest(source, "partial")
                            sid = imported["source"]
                            self.assertEqual(sid, hashlib.sha256(raw).hexdigest())
                            self.assertEqual(book.source_read(sid, 0, len(text), 12000)["text"], text)
                            quote = "🙂e\u0301𠮷"
                            start = text.index(quote)
                            self.assertEqual(book.source_read(sid, start, start + len(quote), 12000)["text"], quote)
                            self.assertEqual(source.read_bytes(), raw)
                            chunks = book.next_chunks(sid, limit=10)["chunks"]
                            for chunk in chunks:
                                expected = text[chunk["start"]:chunk["end"]]
                                self.assertEqual(chunk["text"], expected)
                                self.assertEqual(chunk["sha"], story.digest(expected))
                                self.assertEqual(book.source_read(sid, chunk["start"], chunk["end"], 12000)["text"], expected)
                            self.assert_sections(text,
                                                 [(chunk["start"], chunk["end"], chunk["title"]) for chunk in chunks],
                                                 sections, ["第1章 门外", "第2章 归来"])
                            self.assertEqual(imported["chunks_total"], 2)
                            self.assertTrue(imported["text_coverage_contiguous"])

    def test_reingest_keeps_old_ranges_saved_analysis_and_pending_position(self):
        with tempfile.TemporaryDirectory(prefix="story-analysis-old-line-endings-") as folder:
            root = Path(folder)
            first_two = "  第1章 门外\r她把🙂𠮷字样写在收据上。\r\u3000\u3000第2章 归来\r他带着原件回来。\r\n"
            last = "第3章 后续\n她还没有拆信。\n"
            text = first_two + last
            source = root / "原文.txt"
            raw = text.encode("utf-8")
            source.write_bytes(raw)
            old_chunks = [(0, len(first_two), "未命名文本 / 前言"),
                          (len(first_two), len(text), "第3章 后续")]
            story.Book.create(root / "分析", "门外", "analysis")
            with closing(story.Book(root / "分析")) as book:
                with patch.object(story, "split_source", return_value=old_chunks):
                    sid = book.ingest(source, "partial")["source"]
                chunk = book.next_chunks(sid)["chunks"][0]
                book.record(sid, 1, {"chunk_sha256": chunk["sha"],
                    "summary": "前两章写收据上的字样和原件归还。",
                    "findings": [{"kind": "事实", "claim": "第一章写她在收据上写字。",
                                  "quote": "她把🙂𠮷字样写在收据上。"}]})
                findings_before = book.findings(sid)
                next_before = book.next_chunks(sid)
                coverage_before = book.coverage(sid)
            with closing(story.Book(root / "分析")) as book:
                with patch.object(story, "split_source", side_effect=AssertionError("Existing source must not be split again")) as splitter:
                    imported = book.ingest(source, "partial")
                splitter.assert_not_called()
                self.assertTrue(imported["idempotent"])
                self.assertEqual(imported["chunks_total"], 2)
                self.assertEqual(imported["next_chunk"], 2)
                self.assertEqual(book.findings(sid), findings_before)
                self.assertEqual(book.next_chunks(sid), next_before)
                self.assertEqual(book.coverage(sid), coverage_before)
                item = book.findings(sid)["results"][0]
                self.assertEqual((item["start"], item["end"]), (0, len(first_two)))
                self.assertEqual(book.source_read(sid, item["start"], item["end"], 12000)["text"], first_two)
                self.assertEqual(next_before["chunks"][0]["text"], last)
                self.assertEqual(source.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
