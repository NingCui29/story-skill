"""Synthetic source fixtures; passing these tests is not live-site acceptance."""

import importlib.util
from io import BytesIO
from http.client import BadStatusLine
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rank_sources", ROOT / "skills/story-skill/scripts/story_rank_sources.py"
)
sources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sources)


def card(book_id="100", words="12.34万字", author="样本作者", category="东方玄幻", status="连载中"):
    return f'''<li data-v-fixture class="qm-cover-text-item other">
      <span class="s-tit"><a href="/shuku/{book_id}/?from=sample">样本&amp;书名</a></span>
      {f'<a class="s-category">{category}</a>' if category is not None else ''}
      {f'<span class="s-author">{author}</span>' if author is not None else ''}
      {f'<span class="s-status">{status}</span>' if status is not None else ''}
      {f'<span class="s-words-num">{words}</span>' if words is not None else ''}
    </li>'''


def page(number=1, cards=None, title="30万以下-3天内更新-连载中-小说书库", sort="按点击量"):
    if cards is None:
        cards = card()
    return f'''<!doctype html><html><head><title>{title}</title></head><body>
      <a class="tab-inner active">{sort}</a><ul>{cards}</ul>
      <a class="page-btn num active">{number}</a></body></html>'''


class QimaoSourceTests(unittest.TestCase):
    def test_rank_only_page_preserves_identity_and_nullable_metric(self):
        entries = sources.parse_qimao_page(page(), 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], {
            "book_id": "100", "book_url": "https://www.qimao.com/shuku/100/",
            "title": "样本&书名", "author": "样本作者", "rank": 1,
            "category": "东方玄幻", "tags": [], "word_count": 123400,
            "status": "连载中", "metric_value": None, "metric_raw": None,
        })

    def test_word_count_units_and_missing_data_are_not_inferred_from_filter(self):
        for raw, expected in (("1.25 万 字", 12500), ("1,234字", 1234),
                              ("1234", 1234),
                              ("0字", 0), (None, None), ("待更新", None), ("1.23字", None)):
            with self.subTest(raw=raw):
                entry = sources.parse_qimao_page(page(cards=card(words=raw, author=None, category=None, status=None)), 1)[0]
                self.assertEqual(entry["word_count"], expected)
                self.assertIsNone(entry["author"])
                self.assertIsNone(entry["category"])
                self.assertIsNone(entry["status"])

    def test_word_count_conversion_does_not_establish_filter_membership(self):
        self.assertEqual(sources._word_count("2亿字"), 200000000)
        self.assertEqual(sources._word_count("30万字"), 300000)
        for raw in ("2亿字", "30万字", "300,001字"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "筛选矛盾"):
                sources.parse_qimao_page(page(cards=card(words=raw)), 1)
        self.assertEqual(sources.parse_qimao_page(page(cards=card(words="299999字")), 1)[0]["word_count"], 299999)

    def test_visible_status_contradiction_fails_even_when_page_title_matches(self):
        for status in ("已完结", "完结", "暂停更新"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "连载中筛选矛盾"):
                sources.parse_qimao_page(page(cards=card(status=status)), 1)
        self.assertIsNone(sources.parse_qimao_page(page(cards=card(status="未知")), 1)[0]["status"])

    def test_second_page_ranks_use_real_page_size(self):
        entries = sources.parse_qimao_page(page(2, cards=card("101") + card("102")), 2)
        self.assertEqual([entry["rank"] for entry in entries], [16, 17])

    def test_filter_sort_and_page_must_each_be_verified(self):
        cases = [
            (page(title="小说书库"), "筛选"),
            (page(sort="最近更新"), "排序"),
            (page().replace('class="tab-inner active"', 'class="tab-inner"'), "排序"),
            (page(2), "页码"),
            (page().replace('class="page-btn num active"', 'class="page-btn num"'), "页码"),
        ]
        for html, message in cases:
            with self.subTest(message=message, html=html):
                with self.assertRaisesRegex(ValueError, message):
                    sources.parse_qimao_page(html, 1)

    def test_challenge_and_empty_pages_fail_instead_of_returning_empty_success(self):
        for html, message in (("<meta name='aliyun_waf_aa' content='fixture'>", "WAF"),
                              ("<script>document.cookie='acw_sc__v2=fixture'</script>", "WAF"),
                              (page(cards=""), "空榜成功")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    sources.parse_qimao_page(html, 1)

    def test_missing_title_and_foreign_links_are_rejected(self):
        invalid = [card().replace("样本&amp;书名", ""),
                   card().replace("/shuku/100/?from=sample", "https://example.com/shuku/100/"),
                   card().replace("/shuku/100/?from=sample", "http://www.qimao.com/shuku/100/")]
        for html in invalid:
            with self.subTest(html=html):
                with self.assertRaises(ValueError):
                    sources.parse_qimao_page(page(cards=html), 1)

    def test_private_use_title_or_author_is_not_accepted_as_readable_text(self):
        for html in (card().replace("样本&amp;书名", "样本\ue001书名"),
                     card(author="作者\U000F0001")):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, "私用区字符"):
                sources.parse_qimao_page(page(cards=html), 1)

    def test_duplicate_cards_and_unexpected_page_size_fail(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            sources.parse_qimao_page(page(cards=card() + card()), 1)
        with self.assertRaisesRegex(ValueError, "每页"):
            sources.parse_qimao_page(page(cards="".join(card(str(i)) for i in range(16))), 1)

    def test_html_import_has_explicit_time_and_source_limitations(self):
        with tempfile.TemporaryDirectory() as directory:
            files = [Path(directory) / f"page-{number}.html" for number in (1, 2)]
            for number, path in enumerate(files, 1):
                path.write_text(page(number, cards=card(str(number))), encoding="utf-8")
            urls = [sources.QIMAO_URL.format(page=number) for number in (1, 2)]
            snapshot, raw = sources.collect_qimao(2, files, urls)
        self.assertEqual(snapshot["source"]["method"], "browser_html_import")
        self.assertEqual(snapshot["source"]["sample_method"], "top_n; browser_html_import")
        self.assertEqual(snapshot["source"]["cohort"], "new")
        self.assertIsNone(snapshot["source"]["channel"])
        self.assertIn("频道筛选未解析", " ".join(snapshot["source"]["limitations"]))
        self.assertEqual(snapshot["source"]["pages"], [1, 2])
        self.assertEqual(snapshot["source"]["coverage"], "partial")
        self.assertIsNone(snapshot["source"]["metric"])
        self.assertEqual(sorted(raw), [1, 2])
        self.assertIn("导入时间", " ".join(snapshot["source"]["limitations"]))
        self.assertIn("原页面采集时间未知", " ".join(snapshot["source"]["limitations"]))
        self.assertIn("URL 由导入者提供", " ".join(snapshot["source"]["limitations"]))
        self.assertIn("缺失书卡字段不能进一步验证", " ".join(snapshot["source"]["limitations"]))
        self.assertEqual(snapshot["source"]["url"], urls[0])

    def test_complete_page_title_separates_additional_category_filters(self):
        boards = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.html"
            for category in ("东方玄幻", "古代言情"):
                title = f"{category}-30万以下-3天内更新-连载中-小说书库"
                path.write_text(page(title=title), encoding="utf-8")
                snapshot, _ = sources.collect_qimao(1, [path], [sources.QIMAO_URL.format(page=1)])
                board = snapshot["source"]["board"]
                self.assertIn(title, board)
                boards.append(board)
        self.assertNotEqual(boards[0], boards[1])

    def test_import_does_not_merge_pages_with_different_complete_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for number, category in enumerate(("东方玄幻", "古代言情"), 1):
                path = Path(directory) / f"page-{number}.html"
                path.write_text(page(number, title=f"{category}-30万以下-3天内更新-连载中-小说书库"), encoding="utf-8")
                files.append(path)
            urls = [sources.QIMAO_URL.format(page=number) for number in (1, 2)]
            with self.assertRaisesRegex(ValueError, "完整标题与第1页不同"):
                sources.collect_qimao(2, files, urls)

    def test_import_requires_actual_source_url_for_each_page(self):
        with self.assertRaisesRegex(ValueError, "实际来源 URL"):
            sources.collect_qimao(1, ["page.html"])
        with self.assertRaisesRegex(ValueError, "数量须与文件数一致"):
            sources.collect_qimao(2, ["page1.html", "page2.html"], [sources.QIMAO_URL.format(page=1)])
        with self.assertRaisesRegex(ValueError, "只能与导入 HTML 文件一起使用"):
            sources.collect_qimao(1, html_urls=[sources.QIMAO_URL.format(page=1)])

    def test_import_rejects_wrong_page_extra_filters_and_query_parameters(self):
        valid = sources.QIMAO_URL.format(page=1)
        invalid = (sources.QIMAO_URL.format(page=2), valid.replace("a-a-a", "a-1-2"),
                   valid + "?category=玄幻", valid.replace("https://", "http://"),
                   valid.replace("www.qimao.com", "example.com"), "")
        for url in invalid:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "来源 URL 必须严格匹配"):
                sources.collect_qimao(1, ["page.html"], [url])

    def test_failed_later_page_does_not_return_partial_success(self):
        with patch.object(sources, "_fetch_qimao", side_effect=[page(), ValueError("HTTP 403")]), \
                patch.object(sources.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                sources.collect_qimao(2)

    def test_network_collection_marks_http_method_and_missing_metadata(self):
        with patch.object(sources, "_fetch_qimao", return_value=page(cards=card(words=None))):
            snapshot, _ = sources.collect_qimao(1)
        self.assertEqual(snapshot["source"]["method"], "public_http_get")
        self.assertEqual(snapshot["source"]["sample_method"], "top_n; public_http_get")
        self.assertIn("缺少可解析的字数", " ".join(snapshot["source"]["limitations"]))
        self.assertIsNone(snapshot["entries"][0]["word_count"])

    def test_page_and_import_count_bounds(self):
        for value in (0, 11, True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sources.collect_qimao(value)
        with self.assertRaisesRegex(ValueError, "文件数"):
            sources.collect_qimao(2, ["one.html"])

    def test_response_body_and_html_import_are_bounded(self):
        with patch.object(sources, "MAX_HTML_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "上限"):
                sources._read_html(BytesIO(b"x" * 11))
            with self.assertRaisesRegex(ValueError, "上限"):
                sources.parse_qimao_page(page(), 1)
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            sources._read_html(BytesIO(b"\xff"))

    def test_http_denials_and_connection_errors_are_explicit(self):
        for error in (HTTPError(sources.QIMAO_URL.format(page=1), 403, "Forbidden", {}, None),
                      URLError("test connection failure"), BadStatusLine("test invalid response")):
            with self.subTest(error=error), patch.object(sources, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(ValueError):
                    sources._fetch_qimao(1)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 15)

    def test_redirects_stay_on_https_public_library(self):
        handler = sources._QimaoRedirects()
        request = Request(sources.QIMAO_URL.format(page=1))
        for url in ("https://example.com/shuku/", "http://www.qimao.com/shuku/",
                    "https://www.qimao.com/login", "https://user@www.qimao.com/shuku/",
                    "https://www.qimao.com:444/shuku/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
