"""Read public ranking sources into the structured research snapshot contract.

The first source is Qimao's filtered book library, whose click order is public
but whose click counts are not. No cookies, authentication, browser challenges,
or undocumented interfaces are used. A browser-saved HTML file can be parsed
with the same checks when direct requests are unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


QIMAO_URL = "https://www.qimao.com/shuku/a-a-a-1-1-a-0-click-{page}/"
MAX_HTML_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = 15
PAGE_SIZE = 15
_VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_INERT_TAGS = frozenset({"script", "style", "template"})
# These HTML start tags leave SVG/MathML foreign content even when their
# spelling is followed by '/>'. Used only to reject ambiguous inert markup.
_FOREIGN_BREAKOUT_TAGS = frozenset(
    "b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 "
    "head hr i img li listing menu meta nobr ol p pre ruby s small span strong "
    "strike sub sup table tt u ul var".split()
)
_TEXT_BREAK_TAGS = frozenset(
    "br hr address article aside blockquote dd div dl dt fieldset figcaption figure footer "
    "form h1 h2 h3 h4 h5 h6 header li main nav ol p pre section table td th tr ul".split()
)


class _Element:
    def __init__(self, tag: str, attrs=(), namespace="html"):
        self.tag = tag
        self.attrs = dict(attrs)
        self.namespace = namespace
        self.children: list[_Element | str] = []

    def has_classes(self, *names: str) -> bool:
        return set(names).issubset((self.attrs.get("class") or "").split())

    def is_inert(self) -> bool:
        # A static collector cannot execute templates or resolve CSS overrides.
        # Treat an explicit hidden attribute conservatively, regardless of value.
        return ((self.tag in _INERT_TAGS and (self.tag != "template" or self.namespace == "html"))
                or "hidden" in self.attrs)

    def walk(self):
        if self.is_inert():
            return
        yield self
        for child in self.children:
            if isinstance(child, _Element):
                yield from child.walk()

    def _text_parts(self):
        if self.is_inert():
            return
        # Inline wrappers do not add spaces. Preserve source whitespace until
        # the whole field is assembled so nested markup cannot change a name.
        for child in self.children:
            if isinstance(child, str):
                yield child
            elif not child.is_inert():
                if child.tag in _TEXT_BREAK_TAGS:
                    yield " "
                yield from child._text_parts()
                if child.tag in _TEXT_BREAK_TAGS:
                    yield " "

    def text(self) -> str:
        return " ".join("".join(self._text_parts()).split())


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("document")
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 150_000 or len(self.stack) > 150:
            raise ValueError("HTML 结构超过解析上限")
        namespace = self._start_namespace(tag, attrs)
        element = _Element(tag, attrs, namespace)
        self.stack[-1].children.append(element)
        if tag not in _VOID_TAGS or namespace != "html":
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        element = self.stack[-1]
        if tag not in _VOID_TAGS and element.namespace == "html" and element.is_inert():
            # HTML ignores '/' on non-void elements, whereas HTMLParser calls
            # this handler. Do not silently close an inert container and then
            # accept its contents as live ranking evidence.
            raise ValueError("HTML 惰性容器使用无法确认的自闭合写法，请重新导出含完整结束标签的 HTML")
        if element.tag == tag:
            self.handle_endtag(tag)

    @staticmethod
    def _html_integration(node):
        return ((node.namespace == "svg" and node.tag in {"foreignobject", "desc", "title"}) or
                (node.namespace == "math" and node.tag == "annotation-xml" and
                 (node.attrs.get("encoding") or "").lower() in {"text/html", "application/xhtml+xml"}))

    @staticmethod
    def _math_text_integration(node):
        return node.namespace == "math" and node.tag in {"mi", "mo", "mn", "ms", "mtext"}

    def _leave_foreign(self):
        while (self.stack[-1].namespace != "html" and not self._html_integration(self.stack[-1]) and
               not self._math_text_integration(self.stack[-1])):
            self.stack.pop()

    def _start_namespace(self, tag, attrs):
        # Track only the namespace transitions needed to keep inert containers
        # and real SVG/MathML self-closing elements distinct. This is not a full
        # HTML5 tree builder. A breakout must remove foreign ancestors, or a
        # subsequent HTML sibling would incorrectly inherit their namespace.
        parent = self.stack[-1]
        html_context = (parent.namespace == "html" or self._html_integration(parent) or
                        (self._math_text_integration(parent) and tag not in {"mglyph", "malignmark"}))
        if not html_context and (tag in _FOREIGN_BREAKOUT_TAGS or
                                 (tag == "font" and {"color", "face", "size"} & dict(attrs).keys())):
            self._leave_foreign()
            html_context = True
        if html_context:
            return tag if tag in {"svg", "math"} else "html"
        if parent.namespace == "math" and parent.tag == "annotation-xml" and tag == "svg":
            return "svg"
        return parent.namespace

    def handle_endtag(self, tag):
        # These two legacy end tags also leave foreign content. Retaining an
        # obsolete SVG/MathML ancestor would misclassify the following sibling.
        if tag in {"br", "p"} and self.stack[-1].namespace != "html":
            self._leave_foreign()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break
            if self.stack[index].tag == "template" and self.stack[index].namespace == "html":
                # Template fragments have their own parsing scope. An end tag
                # inside one cannot close an ancestor outside that template.
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _class_text(element: _Element, name: str) -> str | None:
    match = next((node for node in element.walk() if node.has_classes(name)), None)
    return (match.text() or None) if match else None


def _word_count(raw: str | None) -> int | None:
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw).replace(",", "").replace("，", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿)?(?:字)?", text)
    if not match:
        return None
    try:
        value = Decimal(match[1]) * {None: 1, "万": 10_000, "亿": 100_000_000}[match[2]]
    except InvalidOperation:
        return None
    return int(value) if value == value.to_integral_value() else None


def _book_identity(href: str) -> tuple[str, str]:
    url = urlsplit(urljoin("https://www.qimao.com/", href))
    match = re.fullmatch(r"/shuku/(\d+)/?", url.path)
    if (url.scheme != "https" or url.hostname != "www.qimao.com" or
            url.username or url.password or url.port not in {None, 443} or not match):
        raise ValueError("作品链接不是可识别的七猫公开作品页")
    book_id = match[1]
    return book_id, f"https://www.qimao.com/shuku/{book_id}/"


def _parse_qimao_page(html: str, requested_page: int) -> tuple[str, list[dict]]:
    """Return the complete page title and cards after checking visible evidence."""
    if not isinstance(requested_page, int) or isinstance(requested_page, bool) or not 1 <= requested_page <= 10:
        raise ValueError("页码必须为 1–10 的整数")
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("HTML 超过 5 MiB 上限")
    if "aliyun_waf" in html.lower() or "acw_sc__v2" in html:
        raise ValueError(f"第 {requested_page} 页返回 WAF 验证页面，未取得书库数据")
    document = _Document()
    document.feed(html)
    document.close()
    nodes = list(document.root.walk())
    titles = [node.text() for node in nodes if node.tag == "title"]
    page_title = titles[0] if len(titles) == 1 else ""
    checks = (("3天内更新", "3天内更新" in page_title),
              ("30万以下", "30万以下" in page_title or "30万字以下" in page_title),
              ("连载中", "连载中" in page_title))
    missing = [name for name, present in checks if not present]
    if missing:
        raise ValueError(f"第 {requested_page} 页标题无法验证筛选：缺少 {'、'.join(missing)}")
    sorts = [node.text() for node in nodes if node.has_classes("tab-inner", "active")
             and node.text() in {"按点击量", "按总字数", "最近更新", "按收藏数"}]
    if sorts != ["按点击量"]:
        raise ValueError(f"第 {requested_page} 页无法验证当前排序为按点击量")
    active_pages = [node.text() for node in nodes if node.has_classes("page-btn", "num", "active")]
    if active_pages != [str(requested_page)]:
        raise ValueError(f"第 {requested_page} 页无法验证实际页码（页面标记：{active_pages}）")
    cards = [node for node in nodes if node.tag == "li" and node.has_classes("qm-cover-text-item")]
    if not cards:
        raise ValueError(f"第 {requested_page} 页没有可解析的作品，不能视为空榜成功")
    if len(cards) > PAGE_SIZE:
        raise ValueError(f"第 {requested_page} 页作品数超过已知每页 {PAGE_SIZE} 本，页面结构可能已改变")

    entries = []
    seen_ids = set()
    for index, card in enumerate(cards, 1):
        heading = next((node for node in card.walk() if node.has_classes("s-tit")), None)
        anchor = next((node for node in heading.walk() if node.tag == "a"), None) if heading else None
        if anchor is None or not anchor.text() or not anchor.attrs.get("href"):
            raise ValueError(f"第 {requested_page} 页第 {index} 本缺少可识别的书名或作品链接")
        book_id, book_url = _book_identity(anchor.attrs["href"])
        title = anchor.text()
        author = _class_text(card, "s-author")
        if any(unicodedata.category(char) == "Co" for char in title + (author or "")):
            raise ValueError(f"第 {requested_page} 页第 {index} 本书名或作者包含私用区字符，无法确认可读文本")
        if book_id in seen_ids:
            raise ValueError(f"第 {requested_page} 页重复出现作品 {book_id}，需核对页面结构")
        seen_ids.add(book_id)
        word_count = _word_count(_class_text(card, "s-words-num"))
        status = _class_text(card, "s-status")
        if status in {"未知", "未知状态", "[待补]", "--", "—", "暂无"}:
            status = None
        if word_count is not None and word_count >= 300_000:
            raise ValueError(f"第 {requested_page} 页第 {index} 本显示 {word_count} 字，与30万字以下筛选矛盾")
        if status is not None and status != "连载中":
            raise ValueError(f"第 {requested_page} 页第 {index} 本状态为「{status}」，与连载中筛选矛盾")
        entries.append({
            "book_id": book_id,
            "book_url": book_url,
            "title": title,
            "author": author,
            "rank": (requested_page - 1) * PAGE_SIZE + index,
            "category": _class_text(card, "s-category"),
            "tags": [],
            "word_count": word_count,
            "status": status,
            "metric_value": None,
            "metric_raw": None,
        })
    return page_title, entries


def parse_qimao_page(html: str, requested_page: int) -> list[dict]:
    """Parse cards after checking page conditions and card-level contradictions."""
    return _parse_qimao_page(html, requested_page)[1]


class _QimaoRedirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, request, response, code, message, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or target.hostname != "www.qimao.com" or
                target.username or target.password or target.port not in {None, 443} or
                not target.path.startswith("/shuku/")):
            raise ValueError("七猫请求被重定向到预期公开书库以外，已停止")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _read_html(stream) -> str:
    data = stream.read(MAX_HTML_BYTES + 1)
    if len(data) > MAX_HTML_BYTES:
        raise ValueError("HTML 超过 5 MiB 上限")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("页面不是可解析的 UTF-8 HTML") from error


def _fetch_qimao(page: int) -> str:
    request = Request(QIMAO_URL.format(page=page), headers={
        "User-Agent": "StorySkill-RankCollector/1.0",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
    }, method="GET")
    try:
        with build_opener(_QimaoRedirects()).open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                raise ValueError(f"七猫第 {page} 页返回 HTTP {response.status}")
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise ValueError(f"七猫第 {page} 页没有返回 HTML")
            return _read_html(response)
    except HTTPError as error:
        raise ValueError(f"七猫第 {page} 页返回 HTTP {error.code}，未取得可验证数据") from error
    except (URLError, TimeoutError, OSError, HTTPException) as error:
        raise ValueError(f"七猫第 {page} 页请求失败：{error}") from error


def collect_qimao(pages: int, html_files=None, html_urls=None) -> tuple[dict, dict[int, str]]:
    """Collect 1–10 consecutive pages, or validate matching saved HTML files.

    A failed page raises ValueError. The caller therefore cannot accidentally
    report a blocked or partially fetched request as a successful snapshot.
    """
    if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= 10:
        raise ValueError("--pages 必须为 1–10 的整数")
    files = None if html_files is None else list(html_files)
    urls = None if html_urls is None else list(html_urls)
    if files is not None and len(files) != pages:
        raise ValueError("导入 HTML 文件数必须与 --pages 一致，并按页码顺序提供")
    if files is None and urls is not None:
        raise ValueError("--html-url 只能与导入 HTML 文件一起使用")
    if files is not None:
        if urls is None or len(urls) != pages:
            raise ValueError("导入 HTML 必须逐页提供实际来源 URL，--html-url 数量须与文件数一致")
        for number, url in enumerate(urls, 1):
            expected = QIMAO_URL.format(page=number)
            if url != expected:
                raise ValueError(f"第 {number} 页来源 URL 必须严格匹配 {expected}；不接受错页、额外筛选或查询参数")
    raw_pages = {}
    entries = []
    first_page_title = None
    limitations = [
        "样本仅覆盖所选前几页，不代表全站题材占比或市场规模。",
        "仅按静态 HTML 的显式结构解析，忽略 template、script、style 和带 hidden 属性的子树；未执行 JavaScript 或计算 CSS，可见性仍需对照实际页面核对。",
        "页面标题包含30万字以下、3天内更新、连载中条件，缺失书卡字段不能进一步验证；不代表作品首次发表在近期。",
        "页面频道筛选未解析，channel 保留为空；不能确认样本覆盖全频道。",
        "rank 是书库按点击量排序的页序；页面没有公开点击数或热度数，不能推算读者人数。",
        "category 保留页面子分类，不推断或补造主分类。",
    ]
    if files is not None:
        limitations.append("使用浏览器保存的 HTML，逐页来源 URL 由导入者提供，未独立验证文件与 URL 的对应关系；collected_at 是本次导入时间，原页面采集时间未知，不代表源页采集时点。")
    for page in range(1, pages + 1):
        if files is not None:
            try:
                with Path(files[page - 1]).open("rb") as stream:
                    html = _read_html(stream)
            except OSError as error:
                raise ValueError(f"无法读取第 {page} 页 HTML：{error}") from error
        else:
            if page > 1:
                time.sleep(0.8)
            html = _fetch_qimao(page)
        page_title, parsed = _parse_qimao_page(html, page)
        if first_page_title is None:
            first_page_title = page_title
        elif page_title != first_page_title:
            raise ValueError(f"第 {page} 页完整标题与第1页不同，可能包含不同筛选，不能合并为同一榜单")
        if len(parsed) < PAGE_SIZE:
            limitations.append(f"第 {page} 页只有 {len(parsed)} 本，少于通常每页 {PAGE_SIZE} 本；未推断原因。")
        raw_pages[page] = html
        entries.extend(parsed)
    duplicates = len(entries) - len({entry["book_id"] for entry in entries})
    if duplicates:
        limitations.append(f"跨页出现 {duplicates} 条重复作品观测，可能在翻页期间排序变动，须按作品去重。")
    for key, label in (("author", "作者"), ("category", "分类"), ("word_count", "字数")):
        missing = sum(entry[key] is None for entry in entries)
        if missing:
            limitations.append(f"{missing} 本缺少可解析的{label}，保留为空值。")
    snapshot = {
        "schema_version": 1,
        "platform": "qimao",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "url": urls[0] if files is not None else QIMAO_URL.format(page=1),
            "board": f"书库点击新书 · {first_page_title}",
            "channel": None,
            "category": None,
            "period": None,
            "metric": None,
            "sample_method": "top_n; browser_html_import" if files is not None else "top_n; public_http_get",
            "method": "browser_html_import" if files is not None else "public_http_get",
            "pages": list(range(1, pages + 1)),
            "coverage": "partial",
            "expected_count": None,
            "cohort": "new",
            "limitations": limitations,
        },
        "entries": entries,
    }
    return snapshot, raw_pages
