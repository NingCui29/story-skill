"""Optional, visible browser adapter for saving drafts in official author UIs.

Profiles contain locally inspected routes and DOM locators, never credentials.
The Fanqie builder uses the inspected host UI and independent preview reader;
other generic templates stay unconfigured. No private API is called here.
"""
from functools import wraps
from copy import deepcopy
from importlib import metadata, util
import os
from pathlib import Path
import re
import shutil
import sys
import time
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit


OFFICIAL_HOSTS = {"fanqie": "fanqienovel.com", "qimao": "zuozhe.qimao.com",
                  "qidian": "write.qq.com"}
MAX_PAGES = 10000
MAX_ROWS = 100000
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_UNSAFE_SAVE = re.compile(r"发布|送审|审核|下一步|定时|提交|删除|移除|放弃|取消|publish|submit|schedule|next|delete|remove|discard|cancel", re.I)
_DRAFT_LABEL = re.compile(r"草稿|\bdraft\b", re.I)
_SAVE_LABEL = re.compile(r"保存|存草稿|\bsave\b", re.I)
_CREATE_LABEL = re.compile(r"(?:新建|创建|新增).*草稿|\b(?:new|create)\b.*\bdraft\b", re.I)
api = None


# Shared by ordinary reads and the current editor snapshot; serialization stays exact.
_EDITOR_PARAGRAPHS_JS = r"""(el) => {
  // Only the observed spelling annotation is transparent; never apply its suggestion.
  const originalText = node => {
    if (node?.nodeType === 3 && typeof node.textContent === 'string') return node.textContent;
    if (node?.nodeType !== 1 || node.tagName !== 'TYPO') return null;
    const names = Array.from(node.attributes || [], attr => attr.name).sort();
    if (names.join(',') !== 'data-correct,data-source,data-tag,id,ignoretag') return null;
    const key = /^typo-([0-9]+)$/.exec(node.getAttribute('id') || '');
    const source = node.getAttribute('data-source'), correction = node.getAttribute('data-correct');
    if (!key || !source || !correction || node.getAttribute('ignoretag') !== 'true' ||
        node.getAttribute('data-tag') !== correction + '-' + key[1] ||
        !Array.from(node.childNodes).every(child => child.nodeType === 3) || node.textContent !== source) return null;
    annotated = true;
    return source;
  };
  let annotated = false;
  const reject = () => ({ok: false});
  if (!el || el.getAttribute('contenteditable') !== 'true') return reject();
  const nodes = Array.from(el.childNodes);
  const element = (node, tag) => node && node.nodeType === 1 && node.tagName === tag;
  const br = node => element(node, 'BR') && node.classList.contains('ProseMirror-trailingBreak');
  const empty = node => element(node, 'P') && node.childNodes.length === 1 && br(node.childNodes[0]);
  const classes = (node, expected) => node?.getAttribute('class') === expected;
  const aiSentinel = node => {
    if (node?.outerHTML !== '<p><syl-inline __syl_tag="true" ignoreel="true" contenteditable="false" class="ProseMirror-widget"><span class="editor-operability-shadow-wrap"><span class="editor-operability-shadow"><i class="tomato-ai-tool-inspire-v2"></i>暂时没有思路，使用卡文锦囊，AI帮你构思剧情</span></span></syl-inline><img class="ProseMirror-separator" alt=""><br class="ProseMirror-trailingBreak"></p>') return false;
    if (!element(node, 'P') || node.childNodes.length !== 3) return false;
    const [widget, image, lineBreak] = Array.from(node.childNodes);
    if (!element(widget, 'SYL-INLINE') || widget.getAttribute('__syl_tag') !== 'true' ||
        widget.getAttribute('ignoreel') !== 'true' || widget.getAttribute('contenteditable') !== 'false' ||
        !classes(widget, 'ProseMirror-widget') || widget.childNodes.length !== 1 ||
        !element(image, 'IMG') || !classes(image, 'ProseMirror-separator') || image.getAttribute('alt') !== '' || !br(lineBreak)) return false;
    const outer = widget.childNodes[0];
    if (!element(outer, 'SPAN') || !classes(outer, 'editor-operability-shadow-wrap') || outer.childNodes.length !== 1) return false;
    const inner = outer.childNodes[0];
    if (!element(inner, 'SPAN') || !classes(inner, 'editor-operability-shadow') || inner.childNodes.length !== 2) return false;
    const [icon, label] = Array.from(inner.childNodes);
    return element(icon, 'I') && classes(icon, 'tomato-ai-tool-inspire-v2') && icon.childNodes.length === 0 &&
      label.nodeType === 3 && label.textContent === '暂时没有思路，使用卡文锦囊，AI帮你构思剧情';
  };
  if (nodes.length === 1 && element(nodes[0], 'P')) {
    const c = Array.from(nodes[0].childNodes);
    if (c.length === 3 && element(c[0], 'SPAN') && c[0].classList.contains('syl-placeholder') &&
        c[0].classList.contains('ProseMirror-widget') && c[0].getAttribute('ignoreel') === 'true' &&
        c[0].getAttribute('contenteditable') === 'false' && c[0].textContent === '请输入正文' &&
        Array.from(c[0].childNodes).every(node => node.nodeType === 3) && element(c[1], 'IMG') &&
        c[1].classList.contains('ProseMirror-separator') && c[1].getAttribute('alt') === '' && br(c[2])) {
      return {ok: true, value: ''};
    }
  }
  if (nodes.length < 2 || (!empty(nodes.at(-1)) && !aiSentinel(nodes.at(-1)))) return reject();
  const paragraphs = [];
  for (const p of nodes.slice(0, -1)) {
    if (!element(p, 'P')) return reject();
    if (empty(p)) { paragraphs.push(''); continue; }
    if (!p.childNodes.length) return reject();
    let text = '';
    for (const child of Array.from(p.childNodes)) {
      const value = originalText(child);
      if (value === null) return reject();
      text += value;
    }
    paragraphs.push(text);
  }
  return {ok: true, value: paragraphs.join('\n'), ...(annotated ? {formatting_clean:false} : {})};
}"""

# Shared by the loading read and the final current-page snapshot.
_PREVIEW_PARAGRAPHS_JS = r"""(el) => {
  // Only the observed spelling annotation is transparent; never apply its suggestion.
  const originalText = node => {
    if (node?.nodeType === 3 && typeof node.textContent === 'string') return node.textContent;
    if (node?.nodeType !== 1 || node.tagName !== 'TYPO') return null;
    const names = Array.from(node.attributes || [], attr => attr.name).sort();
    if (names.join(',') !== 'data-correct,data-source,data-tag,id,ignoretag') return null;
    const key = /^typo-([0-9]+)$/.exec(node.getAttribute('id') || '');
    const source = node.getAttribute('data-source'), correction = node.getAttribute('data-correct');
    if (!key || !source || !correction || node.getAttribute('ignoretag') !== 'true' ||
        node.getAttribute('data-tag') !== correction + '-' + key[1] ||
        !Array.from(node.childNodes).every(child => child.nodeType === 3) || node.textContent !== source) return null;
    annotated = true;
    return source;
  };
  let annotated = false;
  if (!el) return {ok:false};
  const nodes = Array.from(el.childNodes);
  if (!nodes.length) return {ok:false,pending:true};
  const plainP = node => node?.nodeType === 1 && node.tagName === 'P' &&
    typeof node.outerHTML === 'string' && node.outerHTML.startsWith('<p>') && node.outerHTML.endsWith('</p>');
  const last = nodes[nodes.length-1];
  if (!plainP(last) || last.childNodes.length !== 0 || last.outerHTML !== '<p></p>') return {ok:false};
  const paragraphs = [];
  for (const paragraph of nodes.slice(0,-1)) {
    if (!plainP(paragraph)) return {ok:false};
    const only = paragraph.childNodes[0];
    if (paragraph.outerHTML === '<p><br></p>' && paragraph.childNodes.length === 1 &&
        only?.nodeType === 1 && only.tagName === 'BR' && only.childNodes.length === 0 && only.outerHTML === '<br>') {
      paragraphs.push(''); continue;
    }
    let text = '';
    for (const child of Array.from(paragraph.childNodes)) {
      const value = originalText(child);
      if (value === null) return {ok:false};
      text += value;
    }
    paragraphs.push(text);
  }
  return {ok:true,value:paragraphs.join('\n'),...(annotated ? {formatting_clean:false} : {})};
}"""

def _normal_text(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _chrome_executable_present():
    """Check installation evidence only; never open the author's Chrome profile."""
    if sys.platform == "darwin":
        suffix = "Google Chrome.app/Contents/MacOS/Google Chrome"
        candidates = [Path("/Applications") / suffix, Path.home() / "Applications" / suffix]
    elif sys.platform.startswith("linux"):
        candidates = [Path(path) for name in ("google-chrome", "google-chrome-stable")
                      if (path := shutil.which(name))]
    elif sys.platform == "win32":
        candidates = [Path(value) / "Google/Chrome/Application/chrome.exe"
                      for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                      if (value := os.environ.get(name))]
    else:
        return None
    try:
        return any(path.is_file() and os.access(path, os.X_OK) for path in candidates)
    except OSError:
        return None


def browser_runtime_status():
    """Inspect the optional Python dependency without importing or launching it.

    An installed Python package does not establish that Chromium is installed,
    executable, or able to open a visible window. Only an actual adapter run can
    establish that; a read-only catalog must leave that state unverified.
    """
    try:
        installed = util.find_spec("playwright") is not None
    except (ImportError, ValueError, AttributeError):
        installed = False
    version = None
    if installed:
        try:
            version = metadata.version("playwright")
        except (metadata.PackageNotFoundError, ValueError, OSError):
            pass
    return {"playwright_installed": installed, "playwright_version": version,
            "chromium_installed": None,
            "chrome_executable_present": _chrome_executable_present(),
            "status": "browser_unverified" if installed else "dependency_missing"}


class BrowserAdapterError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def inject(core):
    global api
    api = core


def _fail(code, message, **details):
    code = "platform_browser_" + code
    if api is not None:
        api.fail(code, message, **details)
    raise BrowserAdapterError(code, message, details)


def fanqie_profile_from_book_url(value):
    """Build the inspected host profile from a pasted official management URL.

    This is local configuration only. It neither opens a browser nor verifies
    the pasted book or the account target, and it never accepts credentials.
    No author's IDs, book title or account is embedded in the static template.
    """
    message = "请粘贴番茄作品的章节管理或草稿箱完整链接；不支持其他地址或额外参数。"
    if not isinstance(value, str) or len(value) > 8192:
        _fail("invalid_book_url", message)
    url = value.strip()
    match = re.fullmatch(
        r"https://fanqienovel\.com/main/writer/chapter-manage/([0-9]{1,200})&"
        r"((?:[A-Za-z0-9_.!~*'()-]|%[0-9A-Fa-f]{2})+)\?type=([12])", url)
    if not match:
        _fail("invalid_book_url", message)
    book_id, encoded_title, _ = match.groups()
    try:
        title = unquote(encoded_title, encoding="utf-8", errors="strict")
    except UnicodeError:
        _fail("invalid_book_url", message)
    if (not title.strip() or len(title) > 200 or any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in title)
            or "/" in title or "\\" in title):
        _fail("invalid_book_url", message)
    origin = "https://fanqienovel.com"
    path = origin + "/main/writer/chapter-manage/"
    chapter_url, draft_url = (path + book_id + "&" + encoded_title + "?type=" + mode for mode in ("1", "2"))
    def field(selector, attribute=None, **extra):
        return {"selector": selector, "attribute": attribute, **extra}
    active = ".arco-tabs-content-item-active "
    profile = {
        "schema_version": 1, "platform": "fanqie", "origin": origin,
        "login_origins": [], "browser_channel": "chrome", "execution_mode": "host_assisted_cua",
        "urls": {"drafts": draft_url, "create": draft_url,
                 "edit": origin + "/main/writer/" + book_id + "/publish/{remote_id}/?enter_from=modifydraft"},
        "identity": {
            "account": field('.user-info-line:has(> label:text-is("番茄小说网ID")) > div > span',
                             source="guard_dom", guard_url=origin + "/main/writer/user-info?enter_from=avatar",
                             login_return_urls=[origin + "/main/writer/home"]),
            "book": {"source": "page_url", "capture": "book_id", "id_kind": "digits", "templates": [
                path + "{book_id}&" + encoded_title + "?type=1",
                path + "{book_id}&" + encoded_title + "?type=2",
                origin + "/main/writer/{book_id}/publish/{remote_id}/?enter_from=modifydraft",
                origin + "/main/writer/{book_id}/publish/{remote_id}/?enter_from=modifychapter",
                origin + "/main/writer/{book_id}/publish/{remote_id}?enter_from=newdraft",
                origin + "/main/writer/preview/{book_id}&{remote_id}"]}},
        "chapter_lists": [],
        "create_action": {"mode": "button", "selector": active + '.draft-header a[href*="enter_from=newdraft"]',
                          "expected_text": "新建草稿", "opens": "popup"},
        "editor": {
            "chapter": field("span.left-input > input.serial-input"),
            "title": field('input[placeholder="请输入标题"]'),
            "body": field(".serial-editor-content .ProseMirror", source="prosemirror_paragraphs"),
            "remote_id": {"source": "page_url", "capture": "remote_id", "id_kind": "digits", "templates": [
                origin + "/main/writer/{book_id}/publish/{remote_id}/?enter_from=modifydraft",
                origin + "/main/writer/{book_id}/publish/{remote_id}?enter_from=newdraft"]},
            "state": {"source": "listing", "expected": "草稿"},
            "save": {"mode": "button", "selector": "button.auto-editor-save-btn", "expected_text": "存草稿"},
            "saved": field(".publish-maintain-info-status > span:first-child", expected="已保存到云端"),
            "ready": field("button.auto-editor-save-btn", expected="存草稿", enabled=True)},
        "readback": {
            "url": origin + "/main/writer/preview/" + book_id + "&{remote_id}",
            "remote_id": {"source": "page_url", "capture": "remote_id", "id_kind": "digits",
                          "templates": [origin + "/main/writer/preview/{book_id}&{remote_id}"]},
            "header": {"source": "dom", "selector": ".preview-title", "format": "chapter_title", "title_separator": " "},
            "body": {"source": "fanqie_preview_paragraphs_v1", "selector": ".preview-content > div"}}}
    for scope, table, list_url, label in (("non_drafts", "chapter-table", chapter_url, "章节管理"),
                                           ("drafts", "draft-table", draft_url, "草稿箱")):
        listing = {
            "scope": scope, "url": list_url,
            "coverage": field('[role=tab][aria-selected=true] .arco-tabs-header-title-text', expected=label),
            "ready": field(active + "." + table + " .arco-spin", "class", expected="arco-spin"),
            "filters": [],
            "rows": active + "." + table + (".auto-editor-chapter" if scope == "non_drafts" else ".auto-editor-draft") + " tbody tr.arco-table-tr",
            "chapter": field("td:first-child .table-title" if scope == "non_drafts" else ".table-title",
                             format="chapter_title", title_separator=" ",
                             unnumbered_values=[] if scope == "non_drafts" else ["未命名草稿"]),
            "state": field("td:nth-child(4) .arco-table-cell-wrap-value > span") if scope == "non_drafts" else {"source": "scope", "value": "草稿"},
            "link": field("td:last-child a.link", "href"),
            "empty": None if scope == "non_drafts" else {"selector": active + ':text-is("暂无草稿")'},
            "pagination": {"mode": "fanqie_pages_v1", "validated": True,
                           "container": {"selector": active + "." + table + " .arco-table-content-scroll"},
                           "next": field(active + '.arco-pagination-item-next'),
                           "previous": field(active + '.arco-pagination-item-prev'),
                           "current": field(active + '.arco-pagination-item[aria-current="true"]'),
                           "disabled_class": "arco-pagination-item-disabled",
                           "reject_selectors": [active + '[class*="virtual"]']}}
        if scope == "non_drafts":
            listing["filters"] = [field(active + ".chapter-status-select .byte-select-view-value", expected="全部"),
                                  field(active + ".chapter-search-trigger", expected="搜索章节")]
            listing["sections"] = {
                "trigger": {"selector": active + ".chapter-select-left > .byte-select"},
                "options": ".byte-select-popup-inner > li.byte-select-option",
                "selected": field(active + ".chapter-select-left > .byte-select .byte-select-view-value")}
        else:
            listing["count"] = field(active + ".draft-header > span", format="draft_count")
        profile["chapter_lists"].append(listing)
    # Static validation only: the adapter is not entered and starts no browser.
    BrowserAdapter(profile)
    return {"profile": profile, "remote_book_id": book_id, "remote_book_title": title}


def _guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            if isinstance(error, BrowserAdapterError) or (
                    api is not None and isinstance(error, api.StoryError)):
                raise
            # Playwright exceptions can include DOM snapshots, text, URLs, and
            # browser logs. Never forward their string representation.
            _fail("operation_failed", "浏览器操作未能完成；请检查当前后台页面和本地配置，远端结果待核对。")
    return guarded


def template(platform="fanqie"):
    if platform not in OFFICIAL_HOSTS:
        _fail("invalid_profile", "平台必须为 fanqie、qimao 或 qidian。")
    def field():
        return {"selector": "<待配置：从真实后台核对唯一DOM定位器>", "attribute": None}
    origin = "https://" + OFFICIAL_HOSTS[platform]
    return {
        "schema_version": 1, "platform": platform, "origin": origin,
        "login_origins": [], "browser_channel": "chromium",
        "urls": {"drafts": "<待配置：目标作品草稿或章节目录HTTPS地址，用于人工登录和身份核对>",
                 "create": "<待配置：真实新建草稿HTTPS地址>",
                 "edit": "<待配置：真实草稿编辑HTTPS地址，仅含{remote_id}占位符>"},
        "identity": {"account": field(), "book": field()},
        "chapter_lists": [{
            "scope": "all", "url": "<待配置：含全部状态的完整目录；也可拆为drafts及non_drafts多份列表>",
            "coverage": {"selector": "<待配置：当前目录或tab的真实范围标志>", "attribute": None,
                         "expected": "<待配置：当前tab实际值，并确认所声明scope覆盖范围>"},
            "ready": {"selector": "<待配置：实际整页列表加载完成状态，不能使用初始空占位>",
                      "attribute": None, "expected": "<待配置：列表数据及分页加载完成的真实状态>"},
            "filters": [{"selector": "<待配置：当前章节搜索输入框或其他筛选状态>", "attribute": None, "expected": ""}],
            "rows": "<待配置：完整章节列表行定位器>", "chapter": field(), "state": field(),
            "link": {"selector": "<待配置：行内实际草稿链接>", "attribute": "href"},
            "empty": {"selector": "<待配置：明确的空列表标志>"},
            "pagination": {
                "next": {"selector": "<待配置：下一页按钮>"},
                "disabled_attribute": "disabled", "disabled_value": "",
                "end": {"selector": "<待配置：明确的末页标志>"}}}],
        "editor": {
            "chapter": field(), "title": field(), "body": field(),
            "remote_id": {"selector": "<待配置：真实远端草稿ID所在DOM>", "attribute": "data-id"},
            "state": {"selector": "<待配置：当前草稿状态>", "attribute": None, "expected": "草稿"},
            "save": {"mode": "button", "selector": "<待配置：仅保存草稿的按钮>",
                     "expected_text": "保存草稿"},
            "saved": {"selector": "<待配置：保存完成状态>", "attribute": None, "expected": "已保存"}}}


def _configured(value, field):
    if (not isinstance(value, str) or not value.strip() or
            "待配置" in value or any(ord(c) < 32 for c in value)):
        _fail("invalid_profile", "浏览器配置缺少已核实的值。", field=field)
    return value


def _object(value, field):
    if not isinstance(value, dict):
        _fail("invalid_profile", "浏览器配置字段必须是对象。", field=field)
    return value


def _field(value, name, *, expected=False):
    value = _object(value, name)
    _configured(value.get("selector"), name + ".selector")
    attribute = value.get("attribute")
    if attribute is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.-]*", attribute):
        _fail("invalid_profile", "DOM属性名无效。", field=name + ".attribute")
    if expected:
        _configured(value.get("expected"), name + ".expected")
    return value


def _editor_marker(value, name):
    value = _field(value, name, expected=True)
    if value.get("source") not in (None, "dom"):
        _fail("invalid_profile", "编辑器保存、就绪和新建标志只支持实际DOM字段。", field=name + ".source")
    return value


def _stable_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("invalid_remote_id", "未能从后台DOM取得有效的稳定草稿ID。")
    return value


class BrowserAdapter:
    """One fresh, visible Chromium context. Login is performed by the author.

    The caller must durably record its sending state before create_draft(),
    including when opening the editor or clicking its create action allocates
    an empty cloud draft before any title or body has been filled.
    """

    @_guard
    def __init__(self, profile, timeout_ms=15000, login_timeout_ms=180000, progress_callback=None,
                 *, comparison_policy="strict"):
        self.profile = deepcopy(_object(profile, "profile"))
        self.timeout_ms, self.login_timeout_ms = timeout_ms, login_timeout_ms
        if (type(timeout_ms) is not int or not 1 <= timeout_ms <= 300000 or
                type(login_timeout_ms) is not int or not 1 <= login_timeout_ms <= 3600000):
            _fail("invalid_profile", "浏览器操作和人工登录等待时间必须为正整数毫秒。")
        self._playwright = self._browser = self._context = self._list_page = None
        self._guard_page = None
        self._guard_refresh_pending = False
        self._guard_wait_url = None
        self._section_expected = None
        self._native_navigation_timeout = ()
        self._target = None
        self._chapter_ids = {}
        self._navigation_blocked = False
        self._login_active, self._login_deadline = False, None
        if progress_callback is not None and not callable(progress_callback):
            _fail("invalid_profile", "进度回调必须是可调用对象。")
        self._progress_callback = progress_callback
        self._validate_profile()
        if (not isinstance(comparison_policy, str) or comparison_policy not in ("strict", "fanqie_terminal_lf_v1") or
                (comparison_policy != "strict" and self.platform != "fanqie")):
            _fail("invalid_comparison_policy", "回读比较策略无效；尾换行等价仅适用于番茄的明确授权批次。")
        self.comparison_policy = comparison_policy

    def _progress(self, stage, status="running"):
        # Callbacks receive only fixed workflow states, never URLs, identities,
        # manuscripts, DOM text, or underlying browser exceptions. A failed UI
        # update must not obscure an already completed remote write.
        if self._progress_callback is not None:
            try:
                self._progress_callback({"stage": stage, "status": status})
            except Exception:
                pass

    def _validate_profile(self):
        profile = self.profile
        if profile.get("schema_version") != 1 or profile.get("platform") not in OFFICIAL_HOSTS:
            _fail("invalid_profile", "浏览器配置的版本或平台不受支持。")
        self.platform = profile["platform"]
        self.browser_channel = profile.get("browser_channel", "chromium")
        if self.browser_channel not in ("chromium", "chrome"):
            _fail("invalid_profile", "browser_channel只支持chromium或chrome；不会读取已有浏览器个人资料。")
        self.origin = _configured(profile.get("origin"), "origin")
        origin = urlsplit(self.origin)
        if (self.origin != "https://" + OFFICIAL_HOSTS[self.platform] or
                origin.scheme != "https" or origin.netloc != OFFICIAL_HOSTS[self.platform] or
                origin.path or origin.query or origin.fragment):
            _fail("unsafe_url", "浏览器origin必须是该平台已知官方域名的HTTPS根地址。")
        login_origins = profile.get("login_origins", [])
        if not isinstance(login_origins, list) or len(login_origins) > 20:
            _fail("invalid_profile", "login_origins须为最多二十个已人工核实的精确HTTPS origin；未核实时使用空数组。")
        self.login_origins = set()
        for value in login_origins:
            value = _configured(value, "login_origins")
            parsed = self._https_url(value, allow_fragment=False)
            if (value != "https://" + parsed.hostname or parsed.path or parsed.query or
                    value in self.login_origins or value == self.origin):
                _fail("invalid_profile", "login_origins只能包含不重复的外部HTTPS根origin，禁止路径、端口、凭据、通配符或跳转规则。")
            self.login_origins.add(value)
        self.urls = _object(profile.get("urls"), "urls")
        for name in ("drafts", "create", "edit"):
            url = _configured(self.urls.get(name), "urls." + name)
            braces = re.findall(r"\{[^}]*\}", url)
            if (name == "edit" and braces != ["{remote_id}"]) or (name != "edit" and braces):
                _fail("invalid_profile", "只有编辑URL允许且必须包含一个{remote_id}占位符。", field="urls." + name)
            if "{" in url.replace("{remote_id}", "") or "}" in url.replace("{remote_id}", ""):
                _fail("invalid_profile", "URL模板含有未知占位符。", field="urls." + name)
            self._safe_url(url.replace("{remote_id}", "sample-id"))
        action = profile.get("create_action")
        if action is not None:
            action = _object(action, "create_action")
            if action.get("mode") != "button" or action.get("opens") not in ("popup", "same_page"):
                _fail("invalid_profile", "create_action须为button方式，并声明打开popup或same_page；不会猜测创建页面。")
            _configured(action.get("selector"), "create_action.selector")
            label = _configured(action.get("expected_text"), "create_action.expected_text")
            if _UNSAFE_SAVE.search(label) or not _CREATE_LABEL.search(label):
                _fail("unsafe_create", "新建入口必须明确创建草稿，不能是发布、送审、下一步或其他操作。")
        self._edit_pattern = re.compile(re.escape(self.urls["edit"]).replace(re.escape("{remote_id}"), r"([A-Za-z0-9_-]{1,200})") + r"\Z")
        identity = _object(profile.get("identity"), "identity")
        for name in ("account", "book"):
            spec = _object(identity.get(name), "identity." + name)
            source = spec.get("source", "dom")
            if source == "page_url" and name == "book":
                self._validate_url_field(spec, "identity." + name, "book_id")
            elif source == "guard_dom" and name == "account":
                _field(spec, "identity." + name)
                self._safe_url(_configured(spec.get("guard_url"), "identity.account.guard_url"))
                returns = spec.get("login_return_urls", [])
                if not isinstance(returns, list) or len(returns) > 20:
                    _fail("invalid_profile", "人工登录返回地址须为少量已观察的精确同源URL。")
                for value in returns:
                    self._safe_url(_configured(value, "identity.account.login_return_urls"))
            elif source == "dom":
                _field(spec, "identity." + name)
            else:
                _fail("invalid_profile", "身份字段只支持当前页面DOM、作品URL标识或账号核对页DOM。")
        self.lists = profile.get("chapter_lists")
        if not isinstance(self.lists, list) or not 1 <= len(self.lists) <= 20:
            _fail("invalid_profile", "须配置一至二十份能够覆盖全部章节状态的目录。", field="chapter_lists")
        scopes = set()
        for index, listing in enumerate(self.lists):
            name = "chapter_lists." + str(index)
            listing = _object(listing, name)
            scope = listing.get("scope")
            if scope not in ("all", "drafts", "non_drafts"):
                _fail("incomplete_scope", "列表scope须声明all、drafts或non_drafts。", field=name + ".scope")
            scopes.add(scope)
            url = _configured(listing.get("url"), name + ".url")
            if "{" in url or "}" in url:
                _fail("invalid_profile", "目录URL不允许动态占位符。", field=name + ".url")
            self._safe_url(url)
            _field(listing.get("coverage"), name + ".coverage", expected=True)
            _field(listing.get("ready"), name + ".ready", expected=True)
            filters = listing.get("filters")
            if not isinstance(filters, list):
                _fail("invalid_profile", "须显式列出当前目录的全部筛选状态；没有筛选控件时使用空数组。", field=name + ".filters")
            for filter_index, spec in enumerate(filters):
                _field(spec, name + ".filters." + str(filter_index))
                if not isinstance(spec.get("expected"), str):
                    _fail("invalid_profile", "筛选状态须有精确预期值；空搜索使用空字符串。", field=name + ".filters")
            _configured(listing.get("rows"), name + ".rows")
            chapter = _field(listing.get("chapter"), name + ".chapter")
            if chapter.get("format", "number") not in ("number", "chapter_title"):
                _fail("invalid_profile", "章节行只支持独立数字章号或经核对的章号标题合并格式。")
            if chapter.get("format") == "chapter_title":
                separator = chapter.get("title_separator")
                if not isinstance(separator, str) or len(separator) > 10 or any(ord(c) < 32 for c in separator):
                    _fail("invalid_profile", "合并章名须显式配置已核实的title_separator。")
            unnumbered = chapter.get("unnumbered_values", [])
            if not isinstance(unnumbered, list) or len(unnumbered) > 10 or (unnumbered and scope != "drafts"):
                _fail("invalid_profile", "只有草稿目录可配置少量已核实的未编号空稿原文。")
            for value in unnumbered:
                _configured(value, name + ".chapter.unnumbered_values")
            state = _object(listing.get("state"), name + ".state")
            if state.get("source", "dom") == "scope":
                value = _configured(state.get("value"), name + ".state.value")
                if scope not in ("drafts", "non_drafts"):
                    _fail("incomplete_scope", "仅草稿或非草稿专属目录可使用范围状态，all目录须逐行读取真实状态。")
                if scope == "drafts" and (not _DRAFT_LABEL.search(value) or _UNSAFE_SAVE.search(value)):
                    _fail("invalid_profile", "草稿目录的范围状态必须明确为草稿。")
                if scope == "drafts" and not _DRAFT_LABEL.search(listing["coverage"]["expected"]):
                    _fail("incomplete_scope", "草稿范围状态须由明确的当前草稿目录标志确证。")
            elif state.get("source", "dom") == "dom":
                _field(state, name + ".state")
            else:
                _fail("invalid_profile", "章节行状态只支持DOM字段或经核对的专属目录范围。")
            if _field(listing.get("link"), name + ".link").get("attribute") != "href":
                _fail("invalid_profile", "章节列表链接必须读取真实DOM的href属性。")
            if listing.get("empty") is not None:
                _field(listing["empty"], name + ".empty")
            if listing.get("count") is not None:
                count = _field(listing["count"], name + ".count")
                if count.get("format") != "draft_count" or scope != "drafts":
                    _fail("invalid_profile", "目录总数目前仅支持已核实的草稿数格式。")
            if listing.get("sections") is not None:
                sections = _object(listing["sections"], name + ".sections")
                _field(sections.get("trigger"), name + ".sections.trigger")
                _field(sections.get("selected"), name + ".sections.selected")
                _configured(sections.get("options"), name + ".sections.options")
            pagination = _object(listing.get("pagination"), name + ".pagination")
            if pagination.get("mode", "pages") in ("all_rows", "fanqie_pages_v1"):
                if pagination.get("validated") is not True:
                    _fail("invalid_profile", "无分页完整表格模式必须来自实际后台验证。")
                _field(pagination.get("container"), name + ".pagination.container")
                rejected = pagination.get("reject_selectors")
                if not isinstance(rejected, list) or not 1 <= len(rejected) <= 20:
                    _fail("invalid_profile", "完整表格模式须声明需拒绝的分页或虚拟列表DOM标志。")
                for value in rejected:
                    _configured(value, name + ".pagination.reject_selectors")
                if pagination.get("mode") == "fanqie_pages_v1":
                    if profile["platform"] != "fanqie" or pagination.get("disabled_class") != "arco-pagination-item-disabled":
                        _fail("invalid_profile", "番茄分页须使用已核实的末页禁用样式。")
                    for key in ("next", "previous", "current"):
                        _field(pagination.get(key), name + ".pagination." + key)
            elif pagination.get("mode", "pages") == "pages":
                _field(pagination.get("next"), name + ".pagination.next")
                if pagination.get("end") is not None:
                    _field(pagination["end"], name + ".pagination.end")
                if pagination.get("disabled_attribute") is not None:
                    _field({"selector": "x", "attribute": pagination["disabled_attribute"]}, name + ".pagination")
                    if not isinstance(pagination.get("disabled_value"), str):
                        _fail("invalid_profile", "分页禁用属性须配置精确的disabled_value。")
                elif pagination.get("end") is None:
                    _fail("invalid_profile", "分页须配置明确的末页标志或下一页禁用属性。")
            else:
                _fail("invalid_profile", "目录只支持明确分页或已验收完整表格模式。")
        if "all" not in scopes and not {"drafts", "non_drafts"} <= scopes:
            _fail("incomplete_scope", "必须核查全部章节；仅草稿箱不能排除目标章已发布或审核中。")
        editor = _object(profile.get("editor"), "editor")
        for name in ("chapter", "title", "body", "remote_id"):
            spec = _object(editor.get(name), "editor." + name)
            if name == "remote_id" and spec.get("source") == "page_url":
                self._validate_url_field(spec, "editor.remote_id", "remote_id")
                continue
            _field(spec, "editor." + name)
            if name in ("chapter", "title") and spec.get("source", "dom") != "dom":
                _fail("invalid_profile", "章号和标题须读取实际输入字段。")
            if name == "body" and spec.get("source", "dom") not in ("dom", "prosemirror_paragraphs"):
                _fail("invalid_profile", "正文须使用DOM值或已验收的段落序列化方式。")
            if name != "remote_id" and editor[name].get("attribute") is not None:
                _fail("invalid_profile", "编辑器章号、标题和正文必须读取实际输入值或完整文本，不能读取HTML属性。", field="editor." + name)
        if editor["remote_id"].get("source") != "page_url" and editor["remote_id"].get("attribute") is None:
            _fail("invalid_profile", "远端草稿ID必须读取DOM属性或真实链接，不能用章名代替。")
        state = _object(editor.get("state"), "editor.state")
        if state.get("source", "dom") == "listing":
            _configured(state.get("expected"), "editor.state.expected")
            if not {"drafts", "non_drafts"} <= scopes or "all" in scopes:
                _fail("incomplete_scope", "编辑器依赖目录草稿证据时，必须分别完整核对drafts和non_drafts目录。")
        elif state.get("source", "dom") == "dom":
            _field(state, "editor.state", expected=True)
        else:
            _fail("invalid_profile", "编辑器状态只支持真实DOM或完整草稿目录证据。")
        if not _DRAFT_LABEL.search(state["expected"]) or _UNSAFE_SAVE.search(state["expected"]):
            _fail("invalid_profile", "草稿状态的预期值必须明确表示草稿。")
        _editor_marker(editor.get("saved"), "editor.saved")
        if editor.get("ready") is not None:
            ready = _editor_marker(editor["ready"], "editor.ready")
            if type(ready.get("enabled", False)) is not bool:
                _fail("invalid_profile", "编辑器就绪字段enabled须为布尔值。")
        elif state.get("source") == "listing":
            _fail("invalid_profile", "使用目录草稿证据时，编辑器必须配置独立的正向加载就绪标志。")
        if editor.get("new") is not None:
            _editor_marker(editor["new"], "editor.new")
        save = _object(editor.get("save"), "editor.save")
        if save.get("mode") not in ("button", "autosave"):
            _fail("invalid_profile", "保存方式必须是button或autosave。")
        if save["mode"] == "button":
            _configured(save.get("selector"), "editor.save.selector")
            label = _configured(save.get("expected_text"), "editor.save.expected_text")
            if _UNSAFE_SAVE.search(label) or not _DRAFT_LABEL.search(label) or not _SAVE_LABEL.search(label):
                _fail("unsafe_save", "保存按钮必须明确为存草稿，不能是发布、送审、下一步或定时操作。")
        for listing in self.lists:
            state = listing["state"]
            if state.get("source") == "scope" and ((listing["scope"] == "drafts") != (state["value"] == editor["state"]["expected"])):
                _fail("incomplete_scope", "专属目录的范围状态与编辑器草稿状态不一致。")
        readback = profile.get("readback")
        if self.platform == "fanqie" and readback is None:
            _fail("readback_required", "番茄必须配置独立只读预览回读；不会打开编辑器核对旧稿。")
        if readback is not None:
            readback = _object(readback, "readback")
            if set(readback) != {"url", "remote_id", "header", "body"} or self.platform != "fanqie":
                _fail("invalid_profile", "只读回读仅支持已核实的番茄预览页完整配置。")
            url = _configured(readback.get("url"), "readback.url")
            if re.findall(r"\{[^}]*\}", url) != ["{remote_id}"] or "{" in url.replace("{remote_id}", "") or "}" in url.replace("{remote_id}", ""):
                _fail("invalid_profile", "只读预览URL必须且只能包含一个remote_id占位符。")
            sample = url.replace("{remote_id}", "456")
            self._safe_url(sample)
            editor_id = editor["remote_id"]
            if (self._edit_pattern.fullmatch(sample) or (editor_id.get("source") == "page_url" and
                    self._url_value(sample, editor_id, required=False) is not None)):
                _fail("invalid_profile", "只读预览地址不能同时匹配编辑器地址或编辑草稿ID模板。")
            remote = _object(readback.get("remote_id"), "readback.remote_id")
            if remote.get("source") != "page_url":
                _fail("invalid_profile", "只读预览草稿ID须来自已核实的精确URL。")
            self._validate_url_field(remote, "readback.remote_id", "remote_id")
            book = identity["book"]
            if (book.get("source") != "page_url" or self._url_value(sample, remote) != "456" or
                    self._url_value(sample, book, required=False) is None):
                _fail("invalid_profile", "只读预览URL须同时匹配作品和草稿ID的精确身份模板。")
            if not {"drafts", "non_drafts"} <= scopes or "all" in scopes:
                _fail("incomplete_scope", "只读预览须由草稿和非草稿目录共同确认状态及章号。")
            header = _object(readback.get("header"), "readback.header")
            _field(header, "readback.header")
            if (set(header) != {"source", "selector", "format", "title_separator"} or
                    header.get("source") != "dom" or header.get("format") != "chapter_title" or
                    not isinstance(header.get("title_separator"), str) or len(header["title_separator"]) > 10 or
                    any(ord(c) < 32 for c in header["title_separator"])):
                _fail("invalid_profile", "只读预览标题须声明精确的章号、分隔符和标题合并格式。")
            body = _object(readback.get("body"), "readback.body")
            _field(body, "readback.body")
            if set(body) != {"source", "selector"} or body.get("source") != "fanqie_preview_paragraphs_v1":
                _fail("invalid_profile", "只读预览正文须使用已核实的严格段落序列化方式。")

    def _validate_url_field(self, spec, name, capture):
        if spec.get("capture") != capture or spec.get("id_kind", "stable") not in ("stable", "digits"):
            _fail("invalid_profile", "URL字段须声明对应的稳定ID捕获名和类型。", field=name)
        templates = spec.get("templates")
        if not isinstance(templates, list) or not 1 <= len(templates) <= 20:
            _fail("invalid_profile", "URL字段须列出一至二十份已实际核对的完整URL模板。", field=name)
        for template_value in templates:
            template_value = _configured(template_value, name + ".templates")
            names = re.findall(r"\{([^{}]+)\}", template_value)
            if (capture not in names or len(names) != len(set(names)) or
                    not set(names) <= {"book_id", "remote_id"}):
                _fail("invalid_profile", "URL模板仅允许不重复的book_id和remote_id占位符。", field=name)
            sample = template_value.replace("{book_id}", "123").replace("{remote_id}", "456")
            if "{" in sample or "}" in sample:
                _fail("invalid_profile", "URL模板含有未知占位符。", field=name)
            self._safe_url(sample)

    def _url_value(self, url, spec, *, required=True):
        self._safe_url(url)
        for template_value in spec["templates"]:
            pattern = re.escape(template_value)
            for name in ("book_id", "remote_id"):
                pattern = pattern.replace(re.escape("{" + name + "}"), "(?P<" + name + ">[A-Za-z0-9_-]{1,200})")
            match = re.fullmatch(pattern, url)
            if match:
                value = match[spec["capture"]]
                if spec.get("id_kind") == "digits" and not re.fullmatch(r"[0-9]{1,200}", value):
                    _fail("identity_mismatch", "实际URL稳定标识与已核实的数字格式不符。")
                return _stable_id(value)
        if required:
            _fail("identity_mismatch", "实际页面URL不匹配已核实的身份模板。")
        return None

    def _https_url(self, url, *, allow_fragment=False):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            _fail("unsafe_url", "后台地址含有不允许的字符。")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or
                len(parsed.hostname) > 253 or not all(re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in parsed.hostname.split(".")) or
                parsed.username is not None or parsed.password is not None or parsed.port is not None or
                (parsed.fragment and not allow_fragment)):
            _fail("unsafe_url", "只允许不含凭据、端口或无效主机名的HTTPS地址。")
        return parsed

    def _safe_url(self, url):
        parsed = self._https_url(url)
        if parsed.netloc != OFFICIAL_HOSTS[self.platform] or parsed.netloc != urlsplit(self.origin).netloc:
            _fail("unsafe_url", "只允许同一官方origin的HTTPS地址，禁止凭据、端口和跳转到其他站点。")
        return url

    def _navigation_url(self, url):
        if self._login_active and self._login_deadline is not None and time.monotonic() < self._login_deadline:
            parsed = self._https_url(url, allow_fragment=True)
            if "https://" + parsed.netloc in self.login_origins | {self.origin}:
                return url
        return self._safe_url(url)

    @_guard
    def __enter__(self):
        try:
            from playwright.sync_api import TimeoutError as NavigationTimeout, sync_playwright
        except ImportError:
            _fail("dependency_missing", "尚未安装可选浏览器依赖；请安装playwright和Chromium后再执行真实草稿保存。")
        try:
            self._native_navigation_timeout = NavigationTimeout
            self._playwright = sync_playwright().start()
            launch_options = {"headless": False}
            if self.browser_channel == "chrome":
                launch_options["channel"] = "chrome"
            self._browser = self._playwright.chromium.launch(**launch_options)
            self._context = self._browser.new_context(accept_downloads=False, service_workers="block")
            self._context.set_default_timeout(self.timeout_ms)
            self._context.route("**/*", self._route)
            self._list_page = self._context.new_page()
        except Exception:
            self.close()
            name = "Google Chrome" if self.browser_channel == "chrome" else "Chromium"
            _fail("browser_unavailable", "可见" + name + "浏览器无法启动；请检查所选浏览器安装和本机图形环境。")
        return self

    def _route(self, route):
        request = route.request
        if request.is_navigation_request():
            try:
                self._navigation_url(request.url)
            except Exception:
                self._navigation_blocked = True
                route.abort()
                return
        route.continue_()

    def close(self):
        for value in (self._context, self._browser):
            if value is not None:
                try:
                    value.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = self._browser = self._context = self._list_page = None
        self._guard_page = None
        self._target = None
        self._login_active, self._login_deadline = False, None

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def _require_open(self):
        if self._context is None:
            _fail("not_open", "请在BrowserAdapter上下文中操作。")
        if self._navigation_blocked:
            _fail("unsafe_navigation", "后台试图导航到不允许的站点；已中止本次操作。")

    def _goto(self, page, url):
        self._require_open()
        self._safe_url(url)
        timeout = self.timeout_ms
        if self._login_active and self._login_deadline is not None:
            timeout = max(1, min(timeout, int((self._login_deadline - time.monotonic()) * 1000)))
        try:
            page.goto(url, wait_until="commit" if self._login_active else "domcontentloaded", timeout=timeout)
        except Exception as error:
            if not self._login_active or not isinstance(error, self._native_navigation_timeout):
                raise
            self._require_open()
            try:
                # A timed-out manual login navigation may already have
                # committed its official page. Keep that page alive and wait
                # for real account DOM instead of restarting the login.
                self._safe_url(page.url)
            except Exception:
                _fail("navigation_timeout", "登录入口导航超时且未确认已到达平台官方页面；已停止，未开始写入。")
            return
        self._require_open()
        self._navigation_url(page.url)

    def _one(self, root, spec):
        locator = root.locator(spec["selector"])
        if locator.count() == 0:
            try:
                locator.wait_for(state="attached", timeout=self.timeout_ms)
            except Exception:
                _fail("incomplete_dom", "等待后台关键字段后仍未取得完整页面；请核对DOM配置。")
        if locator.count() != 1:
            _fail("ambiguous_dom", "后台关键字段缺失或不唯一；请重新核对DOM配置。")
        return locator

    def _value(self, root, spec):
        if spec.get("source") == "page_url":
            return self._url_value(root.url, spec)
        locator = self._one(root, spec)
        if spec.get("source") == "prosemirror_paragraphs":
            return self._paragraph_value(locator)
        if spec.get("attribute") is not None:
            value = locator.get_attribute(spec["attribute"])
        else:
            kind = locator.evaluate("(el) => ({tag: el.tagName, editable: el.isContentEditable})")
            if kind["tag"] in ("INPUT", "TEXTAREA", "SELECT"):
                value = locator.input_value()
            elif kind["editable"]:
                value = locator.inner_text()
            else:
                value = locator.text_content()
        if not isinstance(value, str):
            _fail("incomplete_dom", "后台关键字段没有可读取的完整值。")
        return value

    def _try_value(self, root, spec):
        if spec.get("source") == "page_url":
            return self._url_value(root.url, spec, required=False)
        if spec.get("source") == "prosemirror_paragraphs":
            return self._value(root, spec)
        locator = root.locator(spec["selector"])
        if locator.count() == 0:
            return None
        if locator.count() == 1 and spec.get("attribute") is not None:
            return locator.get_attribute(spec["attribute"])
        return self._value(root, spec)

    def _visible(self, root, spec):
        locator = root.locator(spec["selector"])
        if locator.count() > 1:
            _fail("ambiguous_dom", "后台状态标志不唯一；不能确认当前状态。")
        return locator.count() == 1 and locator.is_visible()

    def _guard_account_value(self, spec, *, refresh=False):
        if getattr(self, "_guard_current_only", False):
            self._current_editor_guard()
            return self._target[1]
        if self._guard_page is None or self._guard_page.is_closed():
            self._guard_page = self._context.new_page()
            self._guard_refresh_pending = True
        page = self._guard_page
        if self._guard_refresh_pending or (refresh and not self._login_active):
            self._goto(page, spec["guard_url"])
            self._guard_refresh_pending = False
            self._guard_wait_url = page.url
        elif (self._login_active and page.url != self._guard_wait_url and
              page.url in spec.get("login_return_urls", [])):
            # A user-driven login may return this guard tab to an observed
            # home page. Resume its fixed account route once on that arrival;
            # an unchanged login page is never reloaded by polling.
            self._goto(page, spec["guard_url"])
            self._guard_wait_url = page.url
        self._require_open()
        self._navigation_url(page.url)
        parsed = urlsplit(page.url)
        if "https://" + parsed.netloc != self.origin or parsed.fragment or page.url != spec["guard_url"]:
            return None
        locator = page.locator(spec["selector"])
        try:
            timeout = self.timeout_ms
            if self._login_active and self._login_deadline is not None:
                timeout = max(1, min(timeout, int((self._login_deadline - time.monotonic()) * 1000)))
            locator.wait_for(state="attached", timeout=timeout)
        except Exception:
            return None
        return self._try_value(page, spec)

    def _identity_values(self, page, *, refresh_guard=False):
        account = self.profile["identity"]["account"]
        book = self._try_value(page, self.profile["identity"]["book"])
        if account.get("source") == "guard_dom":
            # Wait on the author's existing login page. Do not open/close a
            # new account page on every polling tick or interrupt a login.
            if (self._login_active and self._guard_page is None and book is None and
                    page.url not in account.get("login_return_urls", [])):
                return [None, book]
            actual_account = self._guard_account_value(account, refresh=refresh_guard)
        else:
            actual_account = self._try_value(page, account)
        return [actual_account, book]

    def _require_identity(self, page):
        if self._target is None:
            _fail("target_unverified", "写入前必须核对平台账号和作品。")
        self._require_open()
        self._safe_url(page.url)
        for key in ("account", "book"):
            spec = self.profile["identity"][key]
            if spec.get("source", "dom") == "dom":
                page.locator(spec["selector"]).wait_for(state="attached", timeout=self.timeout_ms)
        deadline = time.monotonic() + self.timeout_ms / 1000
        refresh_guard = True
        while True:
            self._require_open()
            self._safe_url(page.url)
            values = self._identity_values(page, refresh_guard=refresh_guard)
            refresh_guard = False
            for key, actual, expected in zip(("account", "book"), values, self._target[1:]):
                if actual is not None and actual.strip() and actual.strip() != expected:
                    _fail("identity_mismatch", "当前后台账号或作品与准备目标不符；已停止。", field=key)
            if all(isinstance(value, str) and value.strip() for value in values):
                return
            if time.monotonic() >= deadline:
                _fail("identity_unavailable", "等待后台身份字段加载后仍未取得完整账号和作品标识；已停止。")
            page.wait_for_timeout(100)

    @_guard
    def verify_target(self, platform, account_id, remote_book_id):
        self._require_open()
        if platform != self.platform:
            _fail("identity_mismatch", "浏览器配置的平台与准备目标不符。")
        for value in (account_id, remote_book_id):
            if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
                _fail("invalid_identity", "账号和作品须使用非空的稳定标识。")
        previous_target = self._target
        self._target = None
        if previous_target != (platform, account_id, remote_book_id):
            self._chapter_ids = {}
        self._login_active = True
        self._guard_refresh_pending = previous_target is not None and self._guard_page is not None
        self._login_deadline = time.monotonic() + self.login_timeout_ms / 1000
        self._progress("waiting_login")
        verified = False
        try:
            self._goto(self._list_page, self.urls["drafts"])
            while True:
                self._require_open()
                if time.monotonic() >= self._login_deadline:
                    _fail("login_required", "等待人工登录或验证码完成后仍未取得目标账号和作品标识；未开始写入。")
                self._navigation_url(self._list_page.url)
                # A permitted SSO origin is for manual login only. Its DOM can
                # never establish the account or work used for draft writes.
                parsed = urlsplit(self._list_page.url)
                if "https://" + parsed.netloc == self.origin and not parsed.fragment:
                    values = self._identity_values(self._list_page)
                    for key, actual, expected in zip(("account", "book"), values, (account_id, remote_book_id)):
                        if actual is not None and actual.strip() and actual.strip() != expected:
                            _fail("identity_mismatch", "当前后台账号或作品与准备目标不符；已停止。", field=key)
                    if values[0] == account_id and values[1] is None and self._list_page.url != self.urls["drafts"]:
                        self._goto(self._list_page, self.urls["drafts"])
                        continue
                    if all(isinstance(value, str) and value.strip() for value in values):
                        verified = True
                        break
                if time.monotonic() >= self._login_deadline:
                    _fail("login_required", "等待人工登录或验证码完成后仍未取得目标账号和作品标识；未开始写入。")
                self._list_page.wait_for_timeout(min(200, self.login_timeout_ms))
        finally:
            self._login_active, self._login_deadline = False, None
        if verified:
            # End SSO permission before accepting identity. Close any login
            # popups left in this fresh context so they cannot switch accounts
            # while the adapter is reading or writing the work.
            for page in self._context.pages:
                if page is not self._list_page and page is not self._guard_page:
                    page.close()
            self._target = (platform, account_id, remote_book_id)
            try:
                self._require_identity(self._list_page)
            except Exception:
                self._target = None
                raise
            self._progress("target_verified", "completed")

    def _chapter(self, raw, spec=None):
        if spec is not None and raw in spec.get("unnumbered_values", []):
            return None
        if spec is not None and spec.get("format") == "chapter_title":
            match = re.fullmatch(r"第([0-9]+)章" + re.escape(spec["title_separator"]) + r"([^\r\n]+)", raw.strip())
            if match and not match[2].strip():
                match = None
        else:
            match = re.fullmatch(r"\s*(?:第\s*)?([0-9]+)(?:\s*章)?\s*", raw)
        if not match or not 1 <= int(match[1]) <= 1000000:
            _fail("invalid_chapter", "后台未提供可核对的数字章号。")
        return int(match[1])

    def _id_from_url(self, url):
        self._safe_url(url)
        spec = self.profile["editor"]["remote_id"]
        if spec.get("source") == "page_url":
            return self._url_value(url, spec)
        match = self._edit_pattern.fullmatch(url)
        if not match:
            _fail("invalid_remote_id", "实际草稿链接与已核实的编辑URL模板不符。")
        return _stable_id(unquote(match[1]))

    def _rows(self, listing):
        if listing["state"].get("source") == "scope":
            self._require_scope(listing)
            if not self._list_ready(listing):
                _fail("incomplete_scope", "目录尚未加载完成，不能使用范围状态代替逐行状态。")
        rows = self._list_page.locator(listing["rows"])
        if rows.count() > MAX_ROWS:
            _fail("incomplete_list", "草稿列表超过安全读取上限，未能穷尽。")
        values = []
        for index in range(rows.count()):
            row = rows.nth(index)
            chapter = self._chapter(self._value(row, listing["chapter"]), listing["chapter"])
            state = (listing["state"]["value"] if listing["state"].get("source") == "scope"
                     else self._value(row, listing["state"]).strip())
            if not state:
                _fail("incomplete_list", "章节行缺少实际状态，不能确认目录完整性。")
            href = self._value(row, listing["link"])
            url = urljoin(self._list_page.url, href)
            self._safe_url(url)
            book = self.profile["identity"]["book"]
            if book.get("source") == "page_url" and self._url_value(url, book) != self._target[2]:
                _fail("identity_mismatch", "目录章节链接指向其他作品；已停止。")
            values.append((url, chapter, state))
        return values

    def _require_scope(self, listing):
        for spec in [listing["coverage"]] + listing["filters"]:
            self._list_page.locator(spec["selector"]).wait_for(state="attached", timeout=self.timeout_ms)
            if self._value(self._list_page, spec) != spec["expected"]:
                _fail("incomplete_scope", "当前目录范围或筛选状态与已核实配置不符；不能判断章节不存在。")
        if self._section_expected is not None and self._section_expected[0] is listing:
            if self._value(self._list_page, listing["sections"]["selected"]) != self._section_expected[1]:
                _fail("incomplete_scope", "读取期间当前分卷发生改变；目录未能穷尽。")

    def _empty_list(self, listing):
        return listing.get("empty") is not None and self._visible(self._list_page, listing["empty"])

    def _all_rows(self, listing):
        pagination = listing["pagination"]
        container = self._one(self._list_page, pagination["container"])
        before = self._rows(listing)
        dimensions = None
        metrics_script = "(el) => ({height:el.clientHeight,total:el.scrollHeight,width:el.clientWidth,wide:el.scrollWidth,top:el.scrollTop,left:el.scrollLeft})"
        for bottom in (False, True):
            for selector in pagination["reject_selectors"]:
                if self._list_page.locator(selector).count():
                    _fail("incomplete_list", "完整表格页面出现分页或虚拟列表标志；须重新验收。")
            container.evaluate("(el, bottom) => { el.scrollTop=bottom ? el.scrollHeight : 0; el.scrollLeft=0; }", bottom)
            document = self._list_page.evaluate("(bottom) => { const el=document.scrollingElement; if(!el)return null;el.scrollTop=bottom ? el.scrollHeight : 0;return {height:el.clientHeight,total:el.scrollHeight,top:el.scrollTop}; }", bottom)
            self._list_page.wait_for_timeout(100)
            self._require_identity(self._list_page)
            self._require_scope(listing)
            if not self._list_ready(listing) or self._rows(listing) != before:
                _fail("incomplete_list", "滚动上下端时目录行发生改变；不能声明已读完整表格。")
            metrics = container.evaluate(metrics_script)
            if (not isinstance(metrics, dict) or any(type(metrics.get(k)) not in (int, float) or metrics[k] < 0
                    for k in ("height", "total", "width", "wide", "top", "left")) or metrics["height"] <= 0):
                _fail("incomplete_list", "无法确认完整表格滚动范围。")
            current = tuple(metrics[k] for k in ("height", "total", "width", "wide"))
            if dimensions is not None and dimensions != current:
                _fail("incomplete_list", "滚动期间表格高度或宽度改变；目录尚未稳定。")
            dimensions = current
            if bottom and metrics["top"] + metrics["height"] < metrics["total"] - 1:
                _fail("incomplete_list", "未能到达表格底部；目录未能穷尽。")
            if (not isinstance(document, dict) or any(type(document.get(k)) not in (int, float) or document[k] < 0
                    for k in ("height", "total", "top")) or document["height"] <= 0 or
                    (bottom and document["top"] + document["height"] < document["total"] - 1)):
                _fail("incomplete_list", "无法确认页面已经滚动到完整目录底部。")
        if listing.get("count") is not None:
            match = re.fullmatch(r"共([0-9]+)篇草稿", self._value(self._list_page, listing["count"]))
            if not match or int(match[1]) != len(before):
                _fail("incomplete_list", "页面草稿总数与完整读取的行数不一致。")

    def _reset_fanqie_pages(self, listing):
        pagination = listing["pagination"]
        current = self._list_page.locator(pagination["current"]["selector"])
        previous = self._list_page.locator(pagination["previous"]["selector"])
        for _ in range(MAX_PAGES):
            self._ready_rows(listing)
            if current.count() == 0:
                return
            if current.count() != 1 or not current.is_visible():
                _fail("incomplete_list", "起始页码无法核对。")
            raw = current.inner_text().strip()
            if not re.fullmatch(r"[1-9][0-9]*", raw) or int(raw) > MAX_PAGES:
                _fail("incomplete_list", "起始页码无效。")
            number = int(raw)
            if number == 1:
                return
            if (previous.count() != 1 or not previous.is_visible() or not previous.is_enabled() or
                    pagination["disabled_class"] in (previous.get_attribute("class") or "").split()):
                _fail("incomplete_list", "无法返回首页读取完整目录。")
            before = self._rows(listing)
            self._require_identity(self._list_page)
            self._require_scope(listing)
            previous.click()
            deadline = time.monotonic() + self.timeout_ms / 1000
            while True:
                self._require_open()
                now = current.inner_text().strip()
                if now not in (str(number), str(number - 1)):
                    _fail("incomplete_list", "返回首页时页码变化异常。")
                if now == str(number - 1) and self._rows(listing) != before:
                    break
                if time.monotonic() >= deadline:
                    _fail("incomplete_list", "返回首页超时。")
                self._list_page.wait_for_timeout(100)
        _fail("incomplete_list", "分页超过安全读取上限。")

    def _pagination(self, listing, page_number=1):
        pagination = listing["pagination"]
        if pagination.get("mode") == "fanqie_pages_v1":
            controls = [self._list_page.locator(pagination[key]["selector"]) for key in ("previous", "current", "next")]
            counts = [control.count() for control in controls]
            next_locator = None
            if counts == [0, 0, 0]:
                if page_number != 1:
                    _fail("incomplete_list", "翻页后分页控件消失，目录未能确认完整。")
            else:
                if counts != [1, 1, 1] or not all(control.is_visible() for control in controls):
                    _fail("incomplete_list", "分页控件缺失、重复或不可见。")
                number = controls[1].inner_text().strip()
                disabled = lambda control: pagination["disabled_class"] in (control.get_attribute("class") or "").split()
                if not re.fullmatch(r"[1-9][0-9]*", number) or int(number) != page_number or disabled(controls[0]) != (page_number == 1):
                    _fail("incomplete_list", "当前页码未按顺序前进，或首页状态不一致。")
                if not disabled(controls[2]):
                    if not controls[2].is_enabled():
                        _fail("incomplete_list", "下一页状态无法核对。")
                    next_locator = controls[2]
            self._all_rows({**listing, "count": None})
            return next_locator
        if pagination.get("mode") == "all_rows":
            self._all_rows(listing)
            return None
        end = pagination.get("end")
        next_locator = self._list_page.locator(pagination["next"]["selector"])
        if end is not None and self._visible(self._list_page, end):
            if next_locator.count() == 1 and next_locator.is_visible() and next_locator.is_enabled():
                _fail("incomplete_list", "末页标志与可用下一页同时出现，分页证据不一致。")
            return None
        if next_locator.count() != 1 or not next_locator.is_visible():
            _fail("incomplete_list", "缺少下一页或明确末页证据；草稿列表未能穷尽。")
        attribute = pagination.get("disabled_attribute")
        if attribute is not None and next_locator.get_attribute(attribute) == pagination["disabled_value"]:
            return None
        if not next_locator.is_enabled():
            _fail("incomplete_list", "下一页不可用，但未匹配已核实的末页标志。")
        return next_locator

    def _list_ready(self, listing):
        spec = listing["ready"]
        return self._try_value(self._list_page, spec) == spec["expected"]

    @_guard
    def find_drafts(self, chapter):
        self._require_open()
        if type(chapter) is not int or chapter < 1:
            _fail("invalid_chapter", "章号必须是正整数。")
        if self._target is None:
            _fail("target_unverified", "读取草稿列表前必须核对平台账号和作品。")
        self._progress("listing_drafts")
        matches, seen_ids, catalog = [], set(), []
        for listing in self.lists:
            self._scan_list(listing, chapter, matches, seen_ids, catalog)
        if any(number is None for _, number, _, _ in catalog):
            _fail("unnumbered_draft", "目录含无法核对章号的既有草稿，查找与只读对账已停止；请先人工核对，未新建或覆盖。")
        return [self._read_draft(remote_id) for remote_id in matches]

    def _ready_rows(self, listing):
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            self._require_identity(self._list_page)
            self._require_scope(listing)
            if self._list_ready(listing):
                rows = self._rows(listing)
                if self._list_ready(listing) and (rows or self._empty_list(listing)):
                    return rows
            if time.monotonic() >= deadline:
                _fail("incomplete_list", "列表尚未提供已加载完成的完整行或明确空列表证据。")
            self._list_page.wait_for_timeout(100)

    def _section_options(self, listing):
        self._require_identity(self._list_page)
        self._one(self._list_page, listing["sections"]["trigger"]).click()
        options = self._list_page.locator(listing["sections"]["options"])
        if options.count() == 0:
            options.nth(0).wait_for(state="visible", timeout=self.timeout_ms)
        if not 1 <= options.count() <= 1000:
            _fail("incomplete_list", "分卷选项缺失或超过读取上限。")
        labels = []
        for index in range(options.count()):
            option = options.nth(index)
            label = option.inner_text()
            if not option.is_visible() or not isinstance(label, str) or not label.strip() or any(ord(c) < 32 for c in label):
                _fail("incomplete_list", "分卷选项缺少可见且完整的名称。")
            labels.append(label)
        if len(set(labels)) != len(labels):
            _fail("incomplete_list", "分卷选项名称重复，不能安全定位完整目录。")
        return options, labels

    def _scan_list(self, listing, chapter, matches, seen_ids, catalog=None):
        self._goto(self._list_page, listing["url"])
        if listing.get("sections") is None:
            return self._scan_pages(listing, chapter, matches, seen_ids, catalog)
        options, labels = self._section_options(listing)
        try:
            for index, label in enumerate(labels):
                if index:
                    options, current = self._section_options(listing)
                    if current != labels:
                        _fail("incomplete_list", "读取期间分卷选项集合发生改变；目录未能穷尽。")
                options.nth(index).click()
                deadline = time.monotonic() + self.timeout_ms / 1000
                while self._value(self._list_page, listing["sections"]["selected"]) != label:
                    if time.monotonic() >= deadline:
                        _fail("incomplete_scope", "点击后未能确认当前分卷。")
                    self._list_page.wait_for_timeout(100)
                self._section_expected = (listing, label)
                self._scan_pages(listing, chapter, matches, seen_ids, catalog)
            last_rows = self._rows(listing)
            _, current = self._section_options(listing)
            if current != labels:
                _fail("incomplete_list", "完成目录读取时分卷选项集合发生改变。")
            self._one(self._list_page, listing["sections"]["trigger"]).click()
            self._require_scope(listing)
            if not self._list_ready(listing) or self._rows(listing) != last_rows:
                _fail("incomplete_list", "核对分卷选项期间章节目录发生改变。")
        finally:
            self._section_expected = None

    def _scan_pages(self, listing, chapter, matches, seen_ids, catalog=None):
        seen_pages = set()
        expected_count, collected = None, 0
        if listing["pagination"].get("mode") == "fanqie_pages_v1":
            self._reset_fanqie_pages(listing)
        for page_number in range(MAX_PAGES):
            rows = self._ready_rows(listing)
            next_locator = self._pagination(listing, page_number + 1)
            if listing["pagination"].get("mode") == "fanqie_pages_v1" and listing.get("count"):
                count = re.fullmatch(r"共([0-9]+)篇草稿", self._value(self._list_page, listing["count"]))
                if not count or (expected_count is not None and int(count[1]) != expected_count):
                    _fail("incomplete_list", "翻页期间草稿总数发生变化，未能确认完整目录。")
                expected_count = int(count[1])
            self._require_identity(self._list_page)
            self._require_scope(listing)
            if not self._list_ready(listing) or self._rows(listing) != rows:
                _fail("incomplete_list", "读取行与分页期间目录仍在变化；未能确认完整章节列表。")
            signature = tuple(rows)
            if signature in seen_pages:
                _fail("incomplete_list", "草稿分页重复或未前进；未能穷尽列表。")
            seen_pages.add(signature)
            for url, number, state in rows:
                if url in seen_ids:
                    _fail("incomplete_list", "草稿在分页中重复，无法确认列表完整性。")
                seen_ids.add(url)
                collected += 1
                if len(seen_ids) > MAX_ROWS:
                    _fail("incomplete_list", "草稿列表超过安全读取上限，未能穷尽。")
                if catalog is not None:
                    catalog.append((url, number, state, listing["scope"]))
                if chapter is not None and number == chapter:
                    if state != self.profile["editor"]["state"]["expected"]:
                        _fail("chapter_exists_not_draft", "目标章号已存在已发布、审核中或其他非草稿章节；已停止创建。")
                    if listing["scope"] == "non_drafts":
                        _fail("incomplete_scope", "非草稿目录中出现草稿，配置的目录覆盖范围无法成立。")
                    remote_id = self._id_from_url(url)
                    self._chapter_ids[remote_id] = chapter
                    matches.append(remote_id)
            if next_locator is None:
                if expected_count is not None and expected_count != collected:
                    _fail("incomplete_list", "全部分页读取数与草稿总数不一致。")
                return
            if not rows:
                _fail("incomplete_list", "空列表仍存在下一页，分页证据不一致。")
            if page_number + 1 == MAX_PAGES:
                break
            next_locator.click()
            deadline = time.monotonic() + self.timeout_ms / 1000
            while True:
                self._require_open()
                self._require_identity(self._list_page)
                self._require_scope(listing)
                if self._list_ready(listing):
                    current = self._rows(listing)
                    if current and tuple(current) != signature:
                        break
                    if not current and self._empty_list(listing):
                        break
                if time.monotonic() >= deadline:
                    _fail("incomplete_list", "下一页未能取得新的完整列表。")
                self._list_page.wait_for_timeout(100)
        _fail("incomplete_list", "草稿列表分页超过安全上限，未能穷尽。")

    def _confirm_listed_draft(self, remote_id, chapter=None, *, current_guard=False):
        catalog, matches, seen_ids = [], [], set()
        previous_guard_mode = getattr(self, "_guard_current_only", False)
        self._guard_current_only = previous_guard_mode or current_guard
        try:
            for listing in self.lists:
                self._scan_list(listing, chapter, matches, seen_ids, catalog)
        finally:
            self._guard_current_only = previous_guard_mode
        for url, number, _, scope in catalog:
            if scope == "drafts" and number is None and self._id_from_url(url) != remote_id:
                _fail("unnumbered_draft", "目录含其他无法核对章号的既有草稿；本次草稿确认已停止，远端结果待核对。")
        evidence = [(number, state) for url, number, state, scope in catalog
                    if scope == "drafts" and self._id_from_url(url) == remote_id]
        if len(evidence) != 1 or evidence[0][1] != self.profile["editor"]["state"]["expected"]:
            _fail("not_draft", "完整草稿目录未唯一确认目标ID仍为草稿；已停止。")
        if chapter is not None and evidence[0][0] != chapter:
            _fail("identity_mismatch", "草稿目录中的实际章号与新建或回读目标不符。")
        if chapter is not None and matches != [remote_id]:
            _fail("ambiguous_draft", "完整目录中同章草稿不唯一或与回读ID不一致；已停止。")

    def _editor_remote_id(self, page):
        spec = self.profile["editor"]["remote_id"]
        value = self._value(page, spec)
        return self._id_from_url(urljoin(page.url, value)) if spec.get("attribute") == "href" else _stable_id(value)

    def _bind_created_editor_id(self, page, expected=None, *, source_url=None):
        spec = self.profile["editor"]["remote_id"]
        value = self._try_value(page, spec)
        if value is None or value == "":
            if expected is not None:
                _fail("identity_mismatch", "已分配的新草稿ID在保存期间消失；远端结果待核对。")
            if source_url is not None and page.url != source_url:
                _fail("identity_mismatch", "尚未取得新草稿ID时创建地址发生变化；未继续填写或保存。")
            return None
        actual = (self._id_from_url(urljoin(page.url, value))
                  if spec.get("attribute") == "href" else _stable_id(value))
        if expected is not None and actual != expected:
            _fail("identity_mismatch", "已分配的新草稿ID在保存期间改变；远端结果待核对。")
        if source_url is not None and page.url != source_url and self._id_from_url(page.url) != actual:
            _fail("identity_mismatch", "新草稿页面地址与已分配ID不一致；远端结果待核对。")
        return actual

    def _check_new_editor_anchor(self, page, anchors, source_url, *, allow_allocation=False):
        self._require_open()
        self._safe_url(page.url)
        if not allow_allocation and page.url != source_url:
            _fail("identity_mismatch", "开始填写前新建页面地址发生变化；未继续填写。")
        for locator, handle in anchors:
            stable = locator.evaluate_all("""(elements, expected) => ({
  count: elements.length, same: elements.length === 1 && elements[0] === expected &&
    expected.isConnected && expected.ownerDocument === document
})""", handle)
            if not isinstance(stable, dict) or stable.get("count") != 1 or stable.get("same") is not True:
                _fail("editor_changed", "尚未确认草稿ID时原新建字段或文档发生变化；未继续填写。")

    def _require_editor_draft(self, page):
        spec = self.profile["editor"]["state"]
        if spec.get("source") == "listing":
            remote_id = self._editor_remote_id(page)
            self._confirm_listed_draft(remote_id, self._chapter_ids.get(remote_id))
        else:
            page.locator(spec["selector"]).wait_for(state="visible", timeout=self.timeout_ms)
            if self._value(page, spec).strip() != spec["expected"]:
                _fail("not_draft", "后台章节状态不明确为草稿；已停止。")

    def _wait_editor_ready(self, page):
        spec = self.profile["editor"].get("ready")
        if spec is None:
            return
        locator = self._one(page, spec)
        locator.wait_for(state="visible", timeout=self.timeout_ms)
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            self._require_identity(page)
            if (self._value(page, spec) == spec["expected"] and locator.is_visible() and
                    (not spec.get("enabled", False) or locator.is_enabled())):
                return
            if time.monotonic() >= deadline:
                _fail("incomplete_dom", "编辑器尚未显示已核实的完整加载状态；已停止。")
            page.wait_for_timeout(100)

    def _editor_chapter(self, page, expected=None):
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            raw = self._value(page, self.profile["editor"]["chapter"])
            if raw.strip():
                chapter = self._chapter(raw)
                if expected is not None and chapter != expected:
                    _fail("identity_mismatch", "实际草稿章号与列表或创建目标不符。")
                return chapter
            if time.monotonic() >= deadline:
                _fail("incomplete_dom", "等待编辑器加载后仍未取得实际章号；已停止。")
            page.wait_for_timeout(100)

    def _current_editor_guard(self):
        account = self.profile["identity"]["account"]
        if account.get("source") != "guard_dom":
            return
        page = self._guard_page
        if page is None or page.is_closed():
            _fail("identity_unavailable", "最终账号核对页已不可用；不会重新加载。")
        self._safe_url(page.url)
        if page.url != account["guard_url"]:
            _fail("identity_mismatch", "最终账号核对页地址发生变化。")
        actual = self._preview_current_value(page, account).strip()
        if not actual:
            _fail("identity_unavailable", "最终账号标识为空；不会重新等待加载。")
        if page.is_closed() or page.url != account["guard_url"] or actual != self._target[1]:
            _fail("identity_mismatch", "最终账号或核对页地址与准备目标不符。")

    def _current_editor_snapshot(self, page, expected_url):
        editor = self.profile["editor"]
        specs = {name: editor[name] for name in ("remote_id", "chapter", "title", "body", "state")}
        specs.update(self.profile["identity"])
        return self._current_readback_snapshot(page, expected_url, specs)

    def _current_readback_snapshot(self, page, expected_url, specs):
        # Resolve through Playwright so its supported selector engines keep
        # working. Read every resolved DOM value in one JS turn, rejecting
        # detached/replaced documents instead of waiting for replacements.
        fields, handles, locators = [], [], []
        try:
            for name, spec in specs.items():
                if spec.get("source") in ("page_url", "guard_dom", "listing"):
                    continue
                locator = page.locator(spec["selector"])
                elements = locator.element_handles()
                handles.extend(elements)
                fields.append([name, spec, elements])
                locators.append((name, locator, elements))
            snapshot_script = """fields => {
  // editorReadbackSnapshot: no awaits or locator retries in this observation.
  const paragraphValue = """ + _EDITOR_PARAGRAPHS_JS + """;
  const previewValue = """ + _PREVIEW_PARAGRAPHS_JS + """;
  const values = {};
  for (const [name, spec, elements] of fields) {
    if (elements.length !== 1) { values[name] = {count: elements.length}; continue; }
    const el = elements[0];
    if (!el.isConnected || el.ownerDocument !== document) {
      values[name] = {count: 1, connected: false}; continue;
    }
    if (spec.source === 'prosemirror_paragraphs') {
      values[name] = {count: 1, connected: true, ...paragraphValue(el)}; continue;
    }
    if (spec.source === 'fanqie_preview_paragraphs_v1') {
      values[name] = {count: 1, connected: true, ...previewValue(el)}; continue;
    }
    const value = spec.attribute != null ? el.getAttribute(spec.attribute) :
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ? el.value :
      el.isContentEditable ? el.innerText : el.textContent;
    values[name] = {count: 1, connected: true, value};
  }
  return {url: location.href, fields: values};
}"""
            packet = page.evaluate(snapshot_script, fields)
            # Resolving a later locator may allow an earlier selector to gain
            # another match. Reject that drift without attachment waits.
            for name, locator, elements in locators:
                check = locator.evaluate_all("""(elements, expected) => ({
  count: elements.length, same: elements.length === 1 && elements[0] === expected
})""", elements[0] if len(elements) == 1 else None)
                count = check.get("count") if isinstance(check, dict) else None
                if type(count) is not int or count != 1:
                    _fail("ambiguous_dom" if type(count) is int and count > 1 else "incomplete_dom",
                          "最终编辑页字段匹配数发生变化。", field=name)
                if check.get("same") is not True:
                    _fail("editor_changed", "最终编辑页字段目标节点发生变化。", field=name)
            terminal = page.evaluate(snapshot_script, fields)
            if terminal != packet:
                _fail("editor_changed", "最终字段定位核对期间回读页内容发生变化。")
            packet = terminal
        finally:
            for handle in handles:
                try:
                    handle.dispose()
                except Exception:
                    pass
        self._require_open()
        self._safe_url(page.url)
        if (page.is_closed() or page.url != expected_url or not isinstance(packet, dict) or
                packet.get("url") != expected_url):
            _fail("identity_mismatch", "最终编辑页地址发生变化；已停止核对。")
        values = packet.get("fields")
        if not isinstance(values, dict):
            _fail("incomplete_dom", "最终编辑页没有完整的当前字段快照。")
        result = {}
        for name, spec in specs.items():
            if spec.get("source") == "page_url":
                result[name] = self._url_value(packet["url"], spec)
                continue
            if spec.get("source") in ("guard_dom", "listing"):
                continue
            item = values.get(name)
            if not isinstance(item, dict) or type(item.get("count")) is not int:
                _fail("incomplete_dom", "最终编辑页字段缺失。", field=name)
            if item["count"] > 1:
                _fail("ambiguous_dom", "最终编辑页字段不唯一。", field=name)
            if item["count"] != 1 or item.get("connected") is not True:
                _fail("incomplete_dom", "最终编辑页字段已消失；不会重新等待加载。", field=name)
            if (spec.get("source") in ("prosemirror_paragraphs", "fanqie_preview_paragraphs_v1") and
                    item.get("ok") is not True):
                _fail("unsupported_editor", "正文段落结构与已验收格式不同；未猜测或裁剪文字。")
            if not isinstance(item.get("value"), str):
                _fail("incomplete_dom", "最终编辑页字段没有完整值。", field=name)
            result[name] = item["value"]
            if name == "body" and item.get("formatting_clean") is False:
                result["body_formatting_clean"] = False
        return result

    def _confirm_editor_snapshot(self, page, expected_url, remote_id, chapter, observed):
        self._require_open()
        self._safe_url(page.url)
        if page.url != expected_url:
            _fail("identity_mismatch", "读取正文后编辑页地址发生变化；已停止核对。")
        self._current_editor_guard()
        final = self._current_editor_snapshot(page, expected_url)
        self._current_editor_guard()
        if self.profile["identity"]["account"].get("source") == "guard_dom":
            after_guard = self._current_editor_snapshot(page, expected_url)
            if after_guard != final:
                _fail("editor_changed", "最终账号核对期间编辑页字段发生变化。")
        # Guard reads use a different page. Bind both sides to the editor URL;
        # they must never refresh or wait away a changed account.
        self._require_open()
        self._safe_url(page.url)
        if page.is_closed() or page.url != expected_url:
            _fail("identity_mismatch", "最终账号核对期间编辑页地址发生变化。")
        for name, expected in zip(("account", "book"), self._target[1:]):
            if name in final and final[name].strip() != expected:
                _fail("identity_mismatch", "最终编辑页账号或作品与准备目标不符。", field=name)
        spec = self.profile["editor"]["remote_id"]
        actual = (self._id_from_url(urljoin(expected_url, final["remote_id"]))
                  if spec.get("attribute") == "href" else _stable_id(final["remote_id"]))
        if actual != remote_id or self._chapter(final["chapter"]) != chapter:
            _fail("identity_mismatch", "最终编辑页草稿ID或章号与回读目标不符。")
        if "state" in final and final["state"].strip() != self.profile["editor"]["state"]["expected"]:
            _fail("not_draft", "最终编辑页状态已不明确为草稿；已停止。")
        if final["title"] != observed["title"] or final["body"] != observed["body"]:
            _fail("editor_changed", "编辑页标题或正文在核对期间发生变化。")

    def _read_draft(self, remote_id):
        self._require_open()
        if self._target is None:
            _fail("target_unverified", "回读草稿前必须核对平台账号和作品。")
        self._progress("reading_back")
        remote_id = _stable_id(remote_id)
        if self.platform == "fanqie" and self.profile.get("readback") is None:
            _fail("readback_required", "番茄必须配置独立只读预览回读；不会打开编辑器核对旧稿。")
        if self.profile.get("readback") is not None:
            return self._read_preview(remote_id)
        page = self._context.new_page()
        try:
            url = self.urls["edit"].replace("{remote_id}", remote_id)
            self._goto(page, url)
            # A configured same-draft route may redirect to its canonical
            # editor URL during navigation. Pin that arrival, then reject any
            # later navigation while reading the editor.
            readback_url = page.url
            if self._id_from_url(readback_url) != remote_id:
                _fail("identity_mismatch", "实际到达的编辑地址与回读草稿ID不符。")
            self._require_identity(page)
            self._wait_editor_ready(page)
            editor = self.profile["editor"]
            self._require_editor_draft(page)
            actual = self._editor_remote_id(page)
            if actual != remote_id:
                _fail("identity_mismatch", "实际编辑页面的草稿ID与目标不符。")
            chapter = self._editor_chapter(page, self._chapter_ids.get(remote_id))
            if remote_id in self._chapter_ids and chapter != self._chapter_ids[remote_id]:
                _fail("identity_mismatch", "实际草稿章号与列表或创建目标不符。")
            observed = {"remote_id": remote_id, "title": self._value(page, editor["title"]),
                        "body": self._value(page, editor["body"]), "state": "draft", "url": page.url}
            if editor["state"].get("source") == "listing":
                self._confirm_listed_draft(remote_id, chapter, current_guard=True)
            self._confirm_editor_snapshot(page, readback_url, remote_id, chapter, observed)
            observed["url"] = readback_url
            return observed
        finally:
            page.close()

    @_guard
    def read_draft(self, remote_id):
        return self._read_draft(remote_id)

    def _preview_current_value(self, page, spec):
        # evaluate_all reads the current matches without locator attachment
        # waits. A vanished final field must not become another loading phase.
        packet = page.locator(spec["selector"]).evaluate_all("""(elements, attribute) => {
  if (elements.length !== 1) return {count: elements.length};
  const el = elements[0];
  const value = attribute != null ? el.getAttribute(attribute) :
    ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ? el.value :
    el.isContentEditable ? el.innerText : el.textContent;
  return {count: 1, value};
}""", spec.get("attribute"))
        if not isinstance(packet, dict) or type(packet.get("count")) is not int or packet["count"] < 0:
            _fail("incomplete_dom", "最终预览字段没有可核对的当前节点。")
        if packet["count"] > 1:
            _fail("ambiguous_dom", "最终预览字段不唯一；已停止核对。")
        if packet["count"] != 1 or not isinstance(packet.get("value"), str):
            _fail("incomplete_dom", "最终预览字段已消失或没有完整值；不会重新等待加载。")
        return packet["value"]

    def _preview_header(self, page, *, current=False):
        spec = self.profile["readback"]["header"]
        value = self._preview_current_value(page, spec) if current else self._value(page, spec)
        return self._preview_header_value(value)

    def _preview_header_value(self, value):
        spec = self.profile["readback"]["header"]
        match = re.fullmatch(r"第([0-9]+)章" + re.escape(spec["title_separator"]) + r"([^\r\n]+)", value)
        if not match or not 1 <= int(match[1]) <= 1000000 or not match[2].strip():
            _fail("invalid_chapter", "只读预览页未提供精确的章号和完整标题。")
        return int(match[1]), match[2]

    def _preview_paragraph_value(self, locator, *, allow_pending=False, evidence=False):
        packet = locator.evaluate(_PREVIEW_PARAGRAPHS_JS)
        # Only a completely unpopulated container is a known loading state.
        # A partial paragraph tree remains unsupported, and the final stable
        # recheck must never wait away a disappearance of already read text.
        if allow_pending and packet == {"ok": False, "pending": True}:
            return None
        if (not isinstance(packet, dict) or packet.get("ok") is not True or
                not isinstance(packet.get("value"), str)):
            _fail("unsupported_editor", "只读预览正文结构与已核实的纯段落结构不同；未猜测或裁剪。")
        return packet if evidence else packet["value"]

    def _read_preview(self, remote_id):
        config = self.profile["readback"]
        expected_url = config["url"].replace("{remote_id}", remote_id)
        self._confirm_listed_draft(remote_id, self._chapter_ids.get(remote_id))
        page = self._context.new_page()
        try:
            self._goto(page, expected_url)
            self._require_identity(page)
            if self._url_value(page.url, config["remote_id"]) != remote_id:
                _fail("identity_mismatch", "实际预览页草稿ID与目标不符。")
            header = self._one(page, config["header"])
            body = self._one(page, config["body"])
            header.wait_for(state="visible", timeout=self.timeout_ms)
            body.wait_for(state="visible", timeout=self.timeout_ms)
            deadline = time.monotonic() + self.timeout_ms / 1000
            while not self._value(page, config["header"]):
                if time.monotonic() >= deadline:
                    _fail("incomplete_dom", "只读预览标题尚未完整加载；已停止。")
                page.wait_for_timeout(100)
            chapter, title = self._preview_header(page)
            if remote_id in self._chapter_ids and self._chapter_ids[remote_id] != chapter:
                _fail("identity_mismatch", "只读预览章号与目标不符。")
            while True:
                self._require_identity(page)
                if self._url_value(page.url, config["remote_id"]) != remote_id:
                    _fail("identity_mismatch", "等待预览正文时草稿ID发生变化；已停止。")
                if self._preview_header(page) != (chapter, title):
                    _fail("editor_changed", "等待预览正文时章号或标题发生变化；已停止。")
                value = self._preview_paragraph_value(body, allow_pending=True, evidence=True)
                if time.monotonic() >= deadline:
                    _fail("incomplete_dom", "只读预览正文尚未完整加载；已停止，需回读对账。")
                if value is not None:
                    break
                page.wait_for_timeout(100)
            self._confirm_listed_draft(remote_id, chapter)
            self._require_identity(page)
            if (self._url_value(page.url, config["remote_id"]) != remote_id or
                    self._preview_header(page) != (chapter, title) or self._preview_paragraph_value(body, evidence=True) != value):
                _fail("editor_changed", "只读预览身份、标题或正文在核对期间发生变化。")
            # Final identity reads must not refresh a guard or poll missing DOM
            # after the final body was observed. Pin both sides of those reads.
            self._require_open()
            self._safe_url(page.url)
            if page.url != expected_url:
                _fail("identity_mismatch", "最终正文读取后预览地址发生变化；已停止核对。")
            account = self.profile["identity"]["account"]
            account_page = self._guard_page if account.get("source") == "guard_dom" else page
            if account_page is None or account_page.is_closed():
                _fail("identity_unavailable", "最终账号核对页已不可用；不会重新加载。")
            if account.get("source") == "guard_dom":
                self._safe_url(account_page.url)
                if account_page.url != account["guard_url"]:
                    _fail("identity_mismatch", "最终账号核对页地址发生变化。")
            actual_account = self._preview_current_value(account_page, account).strip()
            if account.get("source") == "guard_dom" and account_page.url != account["guard_url"]:
                _fail("identity_mismatch", "读取最终账号时核对页地址发生变化。")
            if not actual_account:
                _fail("identity_unavailable", "最终账号标识为空；不会重新等待加载。")
            if actual_account != self._target[1]:
                _fail("identity_mismatch", "最终账号与准备目标不符；已停止核对。")
            self._safe_url(page.url)
            if page.url != expected_url or self._url_value(page.url, self.profile["identity"]["book"]) != self._target[2]:
                _fail("identity_mismatch", "读取最终账号时预览作品或地址发生变化。")
            if self._preview_header(page, current=True) != (chapter, title):
                _fail("editor_changed", "最终预览章号或标题发生变化。")
            specs = {name: config[name] for name in ("header", "body")}
            specs.update(self.profile["identity"])
            final = self._current_readback_snapshot(page, expected_url, specs)
            for name, expected in zip(("account", "book"), self._target[1:]):
                if name in final and final[name].strip() != expected:
                    _fail("identity_mismatch", "最终预览账号或作品发生变化。", field=name)
            if self._preview_header_value(final["header"]) != (chapter, title) or final["body"] != value["value"] or final.get("body_formatting_clean", True) != value.get("formatting_clean", True):
                _fail("editor_changed", "最终预览标题或正文在核对账号期间发生变化。")
            self._require_open()
            self._safe_url(page.url)
            if page.url != expected_url:
                _fail("identity_mismatch", "读取最终标题时预览地址发生变化。")
            return {"remote_id": remote_id, "title": title, "body": value["value"], "state": "draft", "url": expected_url,
                    **({"formatting_clean": False} if value.get("formatting_clean") is False else {})}
        finally:
            page.close()

    def _paragraph_value(self, locator):
        packet = locator.evaluate(_EDITOR_PARAGRAPHS_JS)
        if (not isinstance(packet, dict) or packet.get("ok") is not True or
                not isinstance(packet.get("value"), str)):
            _fail("unsupported_editor", "正文段落结构与已验收格式不同；未猜测或裁剪文字。")
        return packet["value"]

    def _checked_editor_action(self, locator, action, before_write, *args, before_action=None):
        if before_write is None:
            if before_action is not None:
                before_action(locator)
            return getattr(locator, action)(*args)
        handles = locator.element_handles()
        try:
            if len(handles) != 1:
                _fail("incomplete_dom", "实际写入前目标字段缺失或不唯一；未继续写入。")
            handle = handles[0]
            before_write()
            packet = locator.evaluate_all("""(elements, expected) => ({
  count: elements.length, same: elements.length === 1 && elements[0] === expected &&
    expected.isConnected && expected.ownerDocument === document
})""", handle)
            if not isinstance(packet, dict) or packet.get("count") != 1 or packet.get("same") is not True:
                _fail("editor_changed", "实际写入前目标字段或文档发生变化；未继续写入。")
            before_write()
            if before_action is not None:
                before_action(handle)
            # Bind the action to the already inspected element. A Locator
            # action could wait for a replacement field on a different draft.
            return getattr(handle, action)(*args)
        finally:
            for handle in handles:
                try:
                    handle.dispose()
                except Exception:
                    pass

    def _paragraph_fill(self, page, locator, value, before_write=None):
        if not isinstance(value, str) or "\x00" in value:
            _fail("invalid_chapter", "正文必须是完整的无NUL纯文本。")
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        if locator.evaluate('(el) => el.getAttribute("contenteditable") === "true"') is not True:
            _fail("unsupported_editor", "正文目标不再是可编辑段落容器。")
        self._checked_editor_action(locator, "click", before_write)
        self._checked_editor_action(locator, "press", before_write, "Meta+A" if sys.platform == "darwin" else "Control+A")
        self._checked_editor_action(locator, "press", before_write, "Backspace")
        for index, line in enumerate(normalized.split("\n")):
            if index:
                self._checked_editor_action(locator, "press", before_write, "Enter")
            if line:
                self._checked_editor_action(locator, "focus", before_write)
                if before_write is not None:
                    before_write()
                    focused = locator.evaluate_all("""elements => {
  if (elements.length !== 1) return false;
  const root = elements[0];
  let active = document.activeElement;
  while (active && active.shadowRoot && active.shadowRoot.activeElement) {
    active = active.shadowRoot.activeElement;
  }
  if (!root.isContentEditable || !active) return false;
  if (active === root) return true;
  if (!root.contains(active) || !active.isContentEditable) return false;
  let node = active;
  for (; node && node !== root; node = node.parentElement) {
    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'IFRAME'].includes(node.tagName) ||
        node.getAttribute('contenteditable') === 'false') return false;
  }
  return node === root;
}""")
                    if focused is not True:
                        _fail("editor_changed", "输入正文前焦点已离开当前正文；未继续输入。")
                page.keyboard.insert_text(line)
        if self._paragraph_value(locator) != normalized:
            _fail("editor_changed", "逐段输入后的完整正文与冻结清单不一致；远端自动保存结果待核对。")


    def _fill(self, page, spec, value, before_write=None):
        locator = self._one(page, spec)
        if spec.get("attribute") is not None:
            _fail("invalid_profile", "可编辑字段必须读取实际输入值，不能配置属性读取。")
        if spec.get("source") == "prosemirror_paragraphs":
            return self._paragraph_fill(page, locator, value, before_write)
        kind = locator.evaluate("(el) => ({tag: el.tagName, editable: el.isContentEditable})")
        if kind["tag"] not in ("INPUT", "TEXTAREA") and not kind["editable"]:
            _fail("unsupported_editor", "后台编辑器不是原生输入框或contenteditable；需要另行验收适配。")
        self._checked_editor_action(locator, "fill", before_write, value)

    def _draft_button_link(self, button, operation):
        packet = button.evaluate("""el => {
  // draftActionLink: include the effective destination, including <base>.
  const href = el.getAttribute('href');
  let resolvedHref = null, documentHref = null;
  try { if (href !== null) {
    resolvedHref = new URL(href, el.ownerDocument.baseURI).href;
    documentHref = new URL(href, el.ownerDocument.URL).href;
  } } catch {}
  return {href, resolvedHref, documentHref, url: el.ownerDocument.URL};
}""")
        if (not isinstance(packet, dict) or "href" not in packet or "resolvedHref" not in packet or
                (packet["href"] is not None and
                 (not isinstance(packet["href"], str) or not isinstance(packet["resolvedHref"], str)))):
            _fail("unsafe_" + operation, "无法核对草稿按钮的完整链接目的地；未点击。")
        return packet

    def _same_draft_button_link(self, observed, current, operation, expected_remote):
        if any(name not in current for name in ("href", "resolvedHref")) or current["href"] != observed["href"]:
            return False
        if current["resolvedHref"] == observed["resolvedHref"]:
            return True
        href = current["href"]
        if operation != "save" or not expected_remote or not isinstance(href, str):
            return False
        # A configured route alias for the same draft can move a self-link.
        # A <base> override must never inherit that exception.
        for packet in (observed, current):
            if (not isinstance(packet.get("url"), str) or
                    not isinstance(packet.get("resolvedHref"), str) or
                    packet["resolvedHref"] != packet.get("documentHref") or
                    packet["documentHref"].split("#", 1)[0] != packet["url"].split("#", 1)[0] or
                    self._id_from_url(packet["url"]) != expected_remote):
                return False
            book = self.profile["identity"]["book"]
            if book.get("source") == "page_url" and self._url_value(packet["url"], book) != self._target[2]:
                return False
        return True

    def _confirm_draft_button(self, button, specification, operation, expected_link, expected_remote=None):
        packet = button.evaluate("""el => ({
  // currentDraftActionButton: inspect the bound button after identity checks.
  connected: el.isConnected && el.ownerDocument === document,
  text: (el.innerText || el.value || '').trim(),
  aria: el.getAttribute('aria-label') || '', title: el.getAttribute('title') || '',
  href: el.getAttribute('href'), url: el.ownerDocument.URL,
  documentHref: el.getAttribute('href') === null ? null :
    new URL(el.getAttribute('href'), el.ownerDocument.URL).href,
  resolvedHref: el.getAttribute('href') === null ? null :
    new URL(el.getAttribute('href'), el.ownerDocument.baseURI).href
})""")
        if not isinstance(packet, dict) or packet.get("connected") is not True:
            _fail("editor_changed", "实际操作前按钮或文档发生变化；未点击。")
        values = [packet.get(name) for name in ("text", "aria", "title")]
        if any(not isinstance(value, str) for value in values):
            _fail("unsafe_" + operation, "实际操作前按钮标签不完整；未点击。")
        if not self._same_draft_button_link(expected_link, packet, operation, expected_remote):
            _fail("unsafe_" + operation, "实际操作前草稿按钮链接发生变化；未点击。")
        actual = values[0]
        permitted = (_CREATE_LABEL.search(actual) if operation == "create" else
                     _DRAFT_LABEL.search(actual) and _SAVE_LABEL.search(actual))
        if actual != specification["expected_text"] or _UNSAFE_SAVE.search(" ".join(values)) or not permitted:
            _fail("unsafe_" + operation, "实际点击前按钮已不是核实过的草稿操作；未点击。")

    def _wait_expected(self, page, spec):
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            self._require_identity(page)
            value = self._try_value(page, spec)
            if value is not None and self._visible(page, spec) and value.strip() == spec["expected"]:
                return
            if time.monotonic() >= deadline:
                _fail("save_unverified", "未取得后台明确的草稿保存完成状态；远端结果待核对。")
            page.wait_for_timeout(100)

    def _open_create_editor(self, page):
        # With create_action, urls.create is a verified landing page. The
        # action itself may already allocate an empty cloud draft, so this
        # method is called only inside create_draft(), after the caller's
        # durable sending fence. Never retry a failed click or popup wait.
        self._goto(page, self.urls["create"])
        action = self.profile.get("create_action")
        if action is None:
            return page
        self._require_identity(page)
        button = self._one(page, action)
        actual = (button.inner_text() or button.get_attribute("value") or "").strip()
        accessible = " ".join(filter(None, [actual, button.get_attribute("aria-label"), button.get_attribute("title")]))
        action_link = self._draft_button_link(button, "create")
        if (actual != action["expected_text"] or _UNSAFE_SAVE.search(accessible) or
                not _CREATE_LABEL.search(actual) or not button.is_visible() or not button.is_enabled()):
            _fail("unsafe_create", "实际入口不是已核实且可用的新建草稿操作；未点击。")
        self._require_identity(page)
        def click_create():
            self._checked_editor_action(button, "click", lambda: self._require_identity(page),
                before_action=lambda handle: self._confirm_draft_button(handle, action, "create", action_link))
        if action["opens"] == "popup":
            with page.expect_popup(timeout=self.timeout_ms) as popup_info:
                click_create()
            page = popup_info.value
        else:
            click_create()
        page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        self._require_open()
        self._safe_url(page.url)
        self._require_identity(page)
        return page

    @_guard
    def create_draft(self, chapter, title, body):
        self._require_open()
        if self._target is None:
            _fail("target_unverified", "创建草稿前必须核对平台账号和作品。")
        if type(chapter) is not int or not 1 <= chapter <= 1000000 or not isinstance(title, str) or not title or not isinstance(body, str) or not body:
            _fail("invalid_chapter", "新建草稿须提供正整数章号及非空完整标题、正文。")
        self._progress("saving_draft")
        # Opening an autosaving route or clicking a create action is itself a
        # possible write, even while the editor remains completely empty.
        previous_pages = list(self._context.pages)
        creation_anchors = []
        try:
            page = self._open_create_editor(self._context.new_page())
            self._require_identity(page)
            self._wait_editor_ready(page)
            editor = self.profile["editor"]
            self._require_editor_draft(page)
            created_remote_id = self._bind_created_editor_id(page)
            creation_url = page.url
            def before_write():
                nonlocal created_remote_id
                self._require_open()
                self._safe_url(page.url)
                self._current_editor_guard()
                for name, expected in zip(("account", "book"), self._target[1:]):
                    spec = self.profile["identity"][name]
                    if spec.get("source") == "guard_dom":
                        continue
                    actual = (self._url_value(page.url, spec) if spec.get("source") == "page_url" else
                              self._preview_current_value(page, spec))
                    if actual.strip() != expected:
                        _fail("identity_mismatch", "实际写入前账号或作品发生变化；未继续写入。")
                state = editor["state"]
                if (state.get("source") != "listing" and
                        self._preview_current_value(page, state).strip() != state["expected"]):
                    _fail("not_draft", "实际写入前章节状态已不明确为草稿；未继续填写或保存。")
                if created_remote_id is None:
                    self._check_new_editor_anchor(page, creation_anchors, creation_url, allow_allocation=True)
                created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            if editor.get("new") is not None:
                if self._value(page, editor["new"]) != editor["new"]["expected"]:
                    _fail("not_new_draft", "后台未显示已核实的新建草稿标志；未开始填写。")
            for name in ("title", "body"):
                locator = self._one(page, editor[name])
                handles = locator.element_handles()
                for handle in handles:
                    creation_anchors.append((locator, handle))
                if len(handles) != 1:
                    _fail("ambiguous_dom", "开始填写前新建字段不唯一。")
            if self._value(page, editor["title"]) != "" or self._value(page, editor["body"]) != "":
                _fail("not_new_draft", "新建地址打开了已有文稿；已停止，未覆盖原标题或正文。")
            self._check_new_editor_anchor(page, creation_anchors, creation_url)
            created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            self._fill(page, editor["chapter"], str(chapter), before_write)
            self._require_identity(page)
            if created_remote_id is None:
                self._check_new_editor_anchor(page, creation_anchors, creation_url, allow_allocation=True)
            created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            self._fill(page, editor["title"], title, before_write)
            self._require_identity(page)
            if created_remote_id is None:
                self._check_new_editor_anchor(page, creation_anchors, creation_url, allow_allocation=True)
            created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            self._fill(page, editor["body"], body, before_write)
            self._require_identity(page)
            if created_remote_id is None:
                self._check_new_editor_anchor(page, creation_anchors, creation_url, allow_allocation=True)
            created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            self._require_editor_draft(page)
            if (self._chapter(self._value(page, editor["chapter"])) != chapter or
                    _normal_text(self._value(page, editor["title"])) != _normal_text(title) or
                    _normal_text(self._value(page, editor["body"])) != _normal_text(body)):
                _fail("editor_changed", "保存前编辑器内容或章号发生变化；已停止，远端自动保存状态待核对。")
            created_remote_id = self._bind_created_editor_id(page, created_remote_id, source_url=creation_url)
            save = editor["save"]
            if save["mode"] == "button":
                button = self._one(page, save)
                actual = (button.inner_text() or button.get_attribute("value") or "").strip()
                accessible = " ".join(filter(None, [actual, button.get_attribute("aria-label"), button.get_attribute("title")]))
                action_link = self._draft_button_link(button, "save")
                if (actual != save["expected_text"] or _UNSAFE_SAVE.search(accessible) or
                        not _DRAFT_LABEL.search(actual) or not _SAVE_LABEL.search(actual)):
                    _fail("unsafe_save", "实际按钮不是已核实的存草稿操作；已停止。")
                self._checked_editor_action(button, "click", before_write,
                                            before_action=lambda handle: self._confirm_draft_button(handle, save, "save", action_link, created_remote_id))
            self._wait_expected(page, editor["saved"])
            remote_id = self._editor_remote_id(page)
            if created_remote_id is not None and remote_id != created_remote_id:
                _fail("identity_mismatch", "保存后草稿ID与已分配的创建目标不符；远端结果待核对。")
            self._chapter_ids[remote_id] = chapter
            if self.profile.get("readback") is not None:
                # Closing every page owned by this create attempt prevents an
                # autosaving editor from modifying cloud data during readback.
                for owned in list(self._context.pages):
                    if owned not in previous_pages and owned is not self._guard_page:
                        owned.close()
            # A saved badge alone is insufficient. Reopen in a fresh page and
            # read the actual state, ID, chapter, full title and full body.
            # An initially visible saved badge must not suffice for success.
            # Apply this batch's explicit policy only to the complete readback.
            # Input verification above remains exact, and the ledger rereads
            # and records actual observed content rather than this canonical form.
            observed = self._read_draft(remote_id)
            observed_body, expected_body = _normal_text(observed["body"]), _normal_text(body)
            if self.comparison_policy == "fanqie_terminal_lf_v1":
                observed_body, expected_body = observed_body.rstrip("\n"), expected_body.rstrip("\n")
            if _normal_text(observed["title"]) != _normal_text(title) or observed_body != expected_body:
                _fail("save_unverified", "重新打开云端草稿后完整标题或正文不一致；远端结果待核对。")
            return remote_id
        finally:
            for _, handle in creation_anchors:
                try:
                    handle.dispose()
                except Exception:
                    pass
            # Capture and close all pages created by this attempt, including a
            # popup whose wait failed after the server allocated an empty draft.
            for page in list(self._context.pages):
                if page not in previous_pages and page is not self._guard_page:
                    try:
                        page.close()
                    except Exception:
                        pass
