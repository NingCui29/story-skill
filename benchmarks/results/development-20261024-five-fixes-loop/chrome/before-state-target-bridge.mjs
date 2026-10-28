/* Host-assisted Chrome UI driver. It never launches a browser or accesses
 * cookies, storage, CDP, transport internals, files, or network APIs. The host
 * supplies public CUA functions bound to an existing, connected Chrome browser.
 * Queue authentication, durable sending intent and one-time claims live in
 * story_platform_chrome_bridge.py. This module handles one claimed UI command.
 */
const OFFICIAL = {fanqie: 'https://fanqienovel.com', qimao: 'https://zuozhe.qimao.com', qidian: 'https://write.qq.com'};
const SESSIONS = new Map();
const CONSUMED_CREATES = new Set();
const UNSAFE = /发布|送审|审核|下一步|定时|提交|删除|移除|放弃|取消|publish|submit|schedule|next|delete|remove|discard|cancel/i;
const DRAFT = /草稿|\bdraft\b/i;
const SAVE = /保存|存草稿|\bsave\b/i;
const CREATE = /(?:新建|创建|新增).*草稿|\b(?:new|create)\b.*\bdraft\b/i;
const normal = text => text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const fail = code => { const error = new Error(code); error.code = 'platform_browser_' + code; throw error; };
const id = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(value) ? value : fail('invalid_remote_id');
const pause = () => new Promise(resolve => setTimeout(resolve, 100));

// Optional host diagnostics never include page strings or change an operation.
function progress(api, event) {
  try {
    if (typeof api?.onProgress !== 'function') return;
    const clean = {};
    if (['guard','catalog','listing','pages','all_rows','read','error'].includes(event.phase)) clean.phase = event.phase;
    if (['begin','end','before_scroll','after_scroll','checked'].includes(event.edge)) clean.edge = event.edge;
    if (['all','drafts','non_drafts'].includes(event.scope)) clean.scope = event.scope;
    if (['up','down'].includes(event.direction)) clean.direction = event.direction;
    for (const key of ['catalog_index','listing_index','section_index','page','row_count','guard_refreshes','line','column']) {
      if (Number.isInteger(event[key]) && event[key] >= 0) clean[key] = event[key];
    }
    for (const key of ['ready_equal','rows_equal','count_equal','dimensions_equal','edge_reached','document_valid','document_edge_reached']) {
      if (typeof event[key] === 'boolean') clean[key] = event[key];
    }
    for (const key of ['container_before','container_after','document_before','document_after']) {
      if (!event[key] || typeof event[key] !== 'object') continue;
      const metrics = {};
      for (const name of ['height','total','top','width','wide']) if (Number.isFinite(event[key][name])) metrics[name] = event[key][name];
      clean[key] = Object.freeze(metrics);
    }
    if (/^platform_browser_[a-z_]+$/.test(event.code || '')) clean.code = event.code;
    const pending = api.onProgress(Object.freeze(clean));
    if (typeof pending?.catch === 'function') pending.catch(() => {});
  } catch { /* Diagnostic failure cannot obscure an observed remote outcome. */ }
}

function errorLocation(error) {
  try {
    const stack = typeof error?.stack === 'string' ? error.stack : '';
    const frame = stack.split('\n').find(line => line.includes('story_chrome_bridge.mjs') && !/\bat fail\s*\(/.test(line));
    const matched = frame && /:(\d+):(\d+)\)?\s*$/.exec(frame);
    return matched ? {line:Number(matched[1]),column:Number(matched[2])} : {};
  } catch { return {}; }
}

// Read-only atomic DOM accessors are self-contained for CUA serialization.
export function readDOMValue(el, attribute = null) {
  if (attribute != null) return el.getAttribute(attribute);
  if (['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) return el.value;
  return el.getAttribute('contenteditable') === 'true' ? el.innerText : el.textContent;
}

// CUA exposes locators, not ElementHandles. Resolve a supported host selector
// to a read-only DOM path so a later callback can sample all editor fields in
// one JS turn without adding page globals or reimplementing selector engines.
export function readDOMPath(elements) {
  if (elements.length !== 1) return {count:elements.length};
  const el=elements[0],doc=el.ownerDocument,path=[];
  if (!doc || !el.isConnected) return {count:1,connected:false};
  let node=el;
  while (node !== doc) {
    const parent=node.parentNode;
    if (!parent) {
      if (node.host?.shadowRoot === node) { path.unshift('shadow');node=node.host;continue; }
      return {count:1,connected:false};
    }
    const index=Array.prototype.indexOf.call(parent.childNodes,node);
    if (index < 0) return {count:1,connected:false};
    path.unshift(index);node=parent;
  }
  return {count:1,connected:true,path,tag:el.tagName};
}

export function readActionState(elements) {
  if (elements.length !== 1) return {count:elements.length};
  const el=elements[0],doc=el.ownerDocument,focused=doc?.activeElement;
  let editableFocus=el.getAttribute('contenteditable')==='true'&&!!focused;
  let cursor=focused;
  while (cursor?.shadowRoot?.activeElement) cursor=cursor.shadowRoot.activeElement;
  while (editableFocus&&cursor!==el) {
    if (!cursor||['INPUT','TEXTAREA','SELECT','BUTTON','IFRAME'].includes(cursor.tagName)||
        cursor.getAttribute?.('contenteditable')?.toLowerCase()==='false') { editableFocus=false;break; }
    cursor=cursor.parentNode;
  }
  editableFocus=editableFocus&&cursor===el;
  return {count:1,url:doc?.location?.href,text:(el.innerText??el.textContent??'').trim(),
    aria:el.getAttribute('aria-label'),title:el.getAttribute('title'),href:el.getAttribute('href'),
    disabled:el.disabled===true||el.getAttribute('aria-disabled')==='true',
    focused:!!focused&&(focused===el||el.contains(focused)),editableFocus};
}

export function readRowFields(elements, specs) {
  if (elements.length > 100000) return {ok:false,error:'incomplete_list'};
  const rows = [];
  for (const element of elements) {
    const fields = [];
    for (const spec of specs) {
      if (spec === null) { fields.push(null); continue; }
      let nodes;
      try { nodes = Array.from(element.querySelectorAll(spec.selector)); }
      catch (error) { return {ok:false,error:error?.name === 'SyntaxError' ? 'unsupported_selector' : 'incomplete_dom'}; }
      if (nodes.length > 1) return {ok:false,error:'ambiguous_dom'};
      if (nodes.length === 0) return {ok:false,error:'missing_field'};
      const el = nodes[0]; let value;
      try {
        value = spec.attribute != null ? el.getAttribute(spec.attribute) :
          ['INPUT','TEXTAREA','SELECT'].includes(el.tagName) ? el.value :
          el.getAttribute('contenteditable') === 'true' ? el.innerText : el.textContent;
      } catch { return {ok:false,error:'incomplete_dom'}; }
      if (typeof value !== 'string') return {ok:false,error:'incomplete_dom'};
      fields.push(value);
    }
    rows.push(fields);
  }
  return {ok:true,rows};
}

// Observed Fanqie read-only preview format: plain, attribute-free P elements.
// Exactly one final zero-child P is UI padding; every other empty P is text.
export function readPreviewParagraphs_v1(el) {
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
      if (child.nodeType !== 3 || typeof child.textContent !== 'string') return {ok:false};
      text += child.textContent;
    }
    paragraphs.push(text);
  }
  return {ok:true,value:paragraphs.join('\n')};
}

// This exact read-only serializer is shared with the native adapter's observed
// Fanqie paragraph format. Exactly one final empty P is an editor sentinel.
// All other empty paragraphs, spaces and leading/trailing linefeeds survive.
export function readParagraphs(el, currentField = null) {
  // evaluateAll supplies a currently matched set without attachment waiting.
  // Keep this branch self-contained for CUA serialization and use the same
  // paragraph parser for both initial and final editor observations.
  if (currentField != null) {
    if (!Array.isArray(el)) return {ok:false};
    if (el.length !== 1) return {ok:false,count:el.length};
    const node = el[0];
    if (currentField.source === 'editor_snapshot') {
      const doc=node.ownerDocument;
      if (!doc || node !== doc.documentElement || !node.isConnected) return {ok:false,count:1};
      const fields={};
      for (const field of currentField.fields) {
        let resolved=doc;
        for (const index of field.path) resolved=index==='shadow'?resolved?.shadowRoot:resolved?.childNodes?.[index];
        if (!resolved?.isConnected || resolved.ownerDocument !== doc || resolved.tagName !== field.tag) return {ok:false,count:1};
        const packet=readParagraphs([resolved],field.spec);
        if (packet.ok !== true || typeof packet.value !== 'string') return {ok:false,count:1};
        fields[field.name]=packet.value;
      }
      return {ok:true,count:1,url:doc.location.href,fields};
    }
    if (currentField.source === 'fanqie_preview_paragraphs_v1') {
      // Keep the final callback self-contained. Parity tests bind this branch
      // to the standalone preview serializer, including its exact padding.
      const nodes=Array.from(node.childNodes);
      const plainP=item=>item?.nodeType===1&&item.tagName==='P'&&typeof item.outerHTML==='string'&&item.outerHTML.startsWith('<p>')&&item.outerHTML.endsWith('</p>');
      const last=nodes.at(-1);
      if (!plainP(last)||last.childNodes.length!==0||last.outerHTML!=='<p></p>') return {ok:false,count:1};
      const paragraphs=[];
      for (const paragraph of nodes.slice(0,-1)) {
        if (!plainP(paragraph)) return {ok:false,count:1};
        const only=paragraph.childNodes[0];
        if (paragraph.outerHTML==='<p><br></p>'&&paragraph.childNodes.length===1&&only?.nodeType===1&&only.tagName==='BR'&&only.childNodes.length===0&&only.outerHTML==='<br>') { paragraphs.push('');continue; }
        let value='';
        for (const child of Array.from(paragraph.childNodes)) {
          if (child.nodeType!==3||typeof child.textContent!=='string') return {ok:false,count:1};
          value+=child.textContent;
        }
        paragraphs.push(value);
      }
      return {ok:true,count:1,value:paragraphs.join('\n')};
    }
    const packet = currentField.source === 'prosemirror_paragraphs' ? readParagraphs(node) : {
      ok:true,value:currentField.attribute != null ? node.getAttribute(currentField.attribute) :
        ['INPUT','TEXTAREA','SELECT'].includes(node.tagName) ? node.value :
          node.getAttribute('contenteditable') === 'true' ? node.innerText : node.textContent
    };
    return {...packet,count:1,...(currentField.includeURL ? {url:node.ownerDocument?.location?.href} : {})};
  }
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
      if (child.nodeType !== 3) return reject();
      text += child.textContent;
    }
    paragraphs.push(text);
  }
  return {ok: true, value: paragraphs.join('\n')};
}

class Driver {
  constructor(api, request) {
    if (!api || !['listTabs', 'getTab', 'newTab'].every(key => typeof api[key] === 'function')) fail('invalid_profile');
    if (!request || request.schema !== 1 || !request.session_id || !request.binding_sha256 ||
        !request.profile || !request.binding?.target) fail('invalid_profile');
    this.api = api; this.request = request; this.profile = request.profile;
    // Reuse only this command's loaded guard DOM during read-only scans. A
    // fresh Driver is constructed per queue command; sessions never cache IDs.
    this.guardFreshThisOperation = false;
    this.readbackURLs = new WeakMap();
    this.createdEditors = new WeakMap();
    this.catalogIndex = 0; this.guardRefreshes = 0;
    this.target = request.binding.target; this.deadline = Date.parse(request.deadline);
    this.comparisonPolicy = Object.hasOwn(request.binding,'comparison_policy') ? request.binding.comparison_policy : 'strict';
    if (!['strict','fanqie_terminal_lf_v1'].includes(this.comparisonPolicy) ||
        (this.comparisonPolicy !== 'strict' && this.target.platform !== 'fanqie')) fail('invalid_profile');
    this.bodyEncoding = Object.hasOwn(request.binding,'body_encoding') ? request.binding.body_encoding : 'verbatim';
    if (!['verbatim','markdown_paragraphs_v1','markdown_paragraphs_v2'].includes(this.bodyEncoding) ||
        (this.bodyEncoding !== 'verbatim' && this.target.platform !== 'fanqie')) fail('invalid_profile');
    this.origin = OFFICIAL[this.target.platform];
    if (!this.origin || this.profile.origin !== this.origin || this.profile.platform !== this.target.platform ||
        this.profile.execution_mode !== 'host_assisted_cua' || !Number.isFinite(this.deadline)) fail('invalid_profile');
    const binding = JSON.stringify([request.binding_sha256, request.binding.profile_sha256, this.target, this.comparisonPolicy, this.bodyEncoding]);
    if (this.profile.identity?.account?.source !== 'guard_dom' || this.profile.identity?.book?.source !== 'page_url' ||
        this.profile.editor?.remote_id?.source !== 'page_url') fail('invalid_profile');
    if (!this.profile.editor.ready) fail('invalid_profile');
    if (['saved','ready','new'].some(name => {
      const spec = this.profile.editor[name];
      return spec?.source != null && spec.source !== 'dom';
    })) fail('invalid_profile');
    if ((this.profile.chapter_lists || []).some(listing => [listing.chapter,listing.link].some(
      spec => spec?.source != null && spec.source !== 'dom'))) fail('invalid_profile');
    if (this.target.platform === 'fanqie' && !this.profile.readback) fail('readback_required');
    if (this.profile.readback) {
      const spec = this.profile.readback; const header = spec.header; const body = spec.body;
      const keys = (value,names) => value && typeof value === 'object' && !Array.isArray(value) &&
        JSON.stringify(Object.keys(value).sort()) === JSON.stringify(names.sort());
      const scopes = new Set((this.profile.chapter_lists || []).map(listing=>listing.scope));
      if (this.target.platform !== 'fanqie' || !scopes.has('drafts') || !scopes.has('non_drafts') ||
          !keys(spec,['url','remote_id','header','body']) || !keys(header,['source','selector','format','title_separator']) ||
          !keys(body,['source','selector']) || header.source !== 'dom' || header.format !== 'chapter_title' ||
          typeof header.selector !== 'string' || !header.selector.trim() || typeof header.title_separator !== 'string' || !header.title_separator ||
          body.source !== 'fanqie_preview_paragraphs_v1' || typeof body.selector !== 'string' || !body.selector.trim() ||
          spec.remote_id?.source !== 'page_url' || spec.remote_id.capture !== 'remote_id' || typeof spec.url !== 'string' ||
          (spec.url.match(/\{remote_id\}/g)||[]).length !== 1 || /[{}]/.test(spec.url.replace('{remote_id}',''))) fail('invalid_profile');
      const sample = spec.url.replace('{remote_id}','1');
      if (this.template(sample,this.profile.identity.book) !== this.target.remote_book_id || this.template(sample,spec.remote_id) !== '1' ||
          this.template(sample,this.profile.editor.remote_id,false) != null || sample === this.profile.urls.edit.replace('{remote_id}','1')) fail('invalid_profile');
    }
    const previous = SESSIONS.get(request.session_id);
    if (previous && previous.binding !== binding) fail('identity_mismatch');
    this.state = previous || {binding, owned: new Set(), chapters: new Map(), verified: false};
    SESSIONS.set(request.session_id, this.state);
    this.check();
  }
  check() { if (Date.now() >= this.deadline) fail('operation_failed'); }
  timeoutOptions() { this.check(); return {timeoutMs: Math.max(1, Math.min(15000, this.deadline - Date.now()))}; }
  safe(url) {
    if (typeof url !== 'string' || /[\s\\\u0000-\u001f]/.test(url)) fail('unsafe_url');
    let parsed; try { parsed = new URL(url); } catch { fail('unsafe_url'); }
    if (parsed.origin !== this.origin || parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port || parsed.hash ||
        url.slice(8).split(/[/?#]/)[0] !== new URL(this.origin).host) fail('unsafe_url');
    return url;
  }
  template(url, spec, required = true) {
    this.safe(url);
    for (const template of spec.templates || []) {
      let pattern = escape(template);
      for (const name of ['book_id', 'remote_id']) pattern = pattern.replace(escape('{' + name + '}'), '(?<' + name + '>[A-Za-z0-9_-]{1,200})');
      const matched = new RegExp('^' + pattern + '$').exec(url);
      if (matched) {
        const value = id(matched.groups[spec.capture]);
        if (spec.id_kind === 'digits' && !/^[0-9]+$/.test(value)) fail('identity_mismatch');
        return value;
      }
    }
    if (required) fail('identity_mismatch');
    return null;
  }
  async readQuery(tab, query, {retry = true} = {}) {
    const checkURL = async expected => {
      this.check();const current = await this.url(tab);this.check();this.safe(current);
      if (expected != null && current !== expected) fail(expected === this.profile.identity.account.guard_url ? 'login_required' : 'identity_mismatch');
      return current;
    };
    const before = await checkURL();let result;
    try { this.check();result = await query(); }
    catch (failure) {
      if (!retry || !['Timed out after 3000ms waiting for CDP command Runtime.evaluate.',
            'Timed out after 3000ms waiting for CDP command Emulation.setFocusEmulationEnabled.'].includes(failure?.message) ||
          !['Error','TimeoutError'].includes(failure?.name) ||
          (typeof failure.code === 'string' && /auth|permission|denied|disconnect|unattach|closed|cancel|abort|forbid/i.test(failure.code))) throw failure;
      await checkURL(before);this.check();
      result = await query(); // Retry this read once, never its caller or preceding action.
    }
    this.check();await checkURL(before);return result;
  }
  async ax(tab) { await this.readQuery(tab,()=>tab.getAXState({emit:false})); }
  async url(tab) {
    const value = typeof tab.url === 'function' ? await tab.url() : tab.url;
    if (typeof value !== 'string') fail('unsafe_url');
    const expected = this.readbackURLs.get(tab);
    if (expected != null) {
      this.safe(value);
      if (value !== expected) fail('identity_mismatch');
    }
    const created = this.createdEditors.get(tab);
    if (created != null && (this.template(value,this.profile.identity.book) !== this.target.remote_book_id ||
        this.template(value,this.profile.editor.remote_id) !== created)) fail('identity_mismatch');
    return value;
  }
  async tabs() {
    const value = await this.api.listTabs();
    const tabs = Array.isArray(value) ? value : value?.tabs;
    if (!Array.isArray(tabs)) fail('operation_failed');
    return tabs.map(tab => ({...tab, id: String(tab.id ?? tab.tabId)}));
  }
  async newTab(url, {readOnly = false, pinReadback = false} = {}) {
    this.check(); this.safe(url);
    const before = new Set((await this.tabs()).map(tab => tab.id));
    const context = readOnly === true && this.readOnlyDestination(url) ? {readOnly:true,target:{...this.target}} : {readOnly:false};
    const result = await this.api.newTab(url,context);
    const tab = result?.tab || result;
    const tabId = result?.id ?? result?.tabId ?? tab?.id ?? tab?.tabId;
    if (tabId == null || before.has(String(tabId))) fail('ambiguous_dom');
    let arrivalError;
    let arrivalURL=url;
    if (pinReadback) {
      // Capture arrival before inventory or guard awaits can hide a redirect.
      // Register ownership below before rejecting, so cleanup retains the tab.
      try {
        arrivalURL=await this.url(tab);
        const remoteSpec=this.template(url,this.profile.editor.remote_id,false)!=null ? this.profile.editor.remote_id : this.profile.readback?.remote_id;
        if (!remoteSpec || this.template(arrivalURL,this.profile.identity.book)!==this.target.remote_book_id ||
            this.template(arrivalURL,remoteSpec)!==this.template(url,remoteSpec)) fail('identity_mismatch');
        // Configured aliases for the same document may redirect on arrival.
        // Every observation after arrival remains pinned to that actual URL.
        this.readbackURLs.set(tab,arrivalURL);
      } catch (error) { arrivalError = error; }
    }
    const candidate = (await this.tabs()).filter(item => item.id === String(tabId));
    if (candidate.length !== 1) fail('ambiguous_dom');
    this.state.owned.add(String(tabId));
    if (arrivalError) throw arrivalError;
    await this.ax(tab);
    this.safe(await this.url(tab));
    return {tab, id: String(tabId), url:arrivalURL};
  }
  readOnlyDestination(url) {
    if (this.target.platform !== 'fanqie') return false;
    try {
      if (this.template(url,this.profile.editor.remote_id,false) != null ||
          (!this.profile.create_action && url === this.profile.urls.create)) return false;
      if (url === this.profile.identity.account.guard_url) return true;
      if (this.template(url,this.profile.identity.book,false) !== this.target.remote_book_id) return false;
      if (this.profile.chapter_lists.some(listing => listing.url === url)) return true;
      return !!this.profile.readback && this.template(url,this.profile.readback.remote_id,false) != null;
    } catch { return false; }
  }
  navigationTimeout(failure, url) {
    if (typeof failure?.message !== 'string' || !['Error','TimeoutError'].includes(failure.name) ||
        (typeof failure.code === 'string' && /auth|permission|denied|disconnect|unattach|closed|cancel|abort|forbid/i.test(failure.code))) return false;
    const waiting = /^Timed out waiting for tab [0-9]{1,30} to navigate to (.+)\.$/.exec(failure.message);
    return failure.message === 'Timed out after 10000ms waiting for CDP command Page.navigate.' || waiting?.[1] === url;
  }
  managementTransition(tab, beforeURL, url) {
    if (this.target.platform !== 'fanqie' || beforeURL === url) return null;
    let before,target;
    try { before = new URL(beforeURL);target = new URL(url); } catch { return null; }
    if (before.origin !== this.origin || target.origin !== this.origin || before.pathname !== target.pathname ||
        !before.pathname.startsWith('/main/writer/chapter-manage/') ||
        !/^\?type=[12]$/.test(before.search) || !/^\?type=[12]$/.test(target.search) || before.search === target.search) return null;
    if (this.state.list?.tab !== tab || !this.state.owned.has(this.state.list.id)) fail('bridge_scope');
    const labels = {'?type=1':['non_drafts','章节管理'],'?type=2':['drafts','草稿箱']};
    const listings = [beforeURL,url].map((value,index) => {
      const matches = this.profile.chapter_lists.filter(listing=>listing.url === value);
      if (matches.length !== 1) fail('invalid_profile');
      const listing = matches[0],spec = listing.coverage,[scope,label] = labels[index ? target.search : before.search];
      if (!this.readOnlyDestination(value) || this.template(value,this.profile.identity.book) !== this.target.remote_book_id ||
          listing.scope !== scope || !spec || ![undefined,'dom'].includes(spec.source) || spec.attribute != null ||
          typeof spec.selector !== 'string' || !spec.selector || spec.expected !== label) fail('invalid_profile');
      return listing;
    });
    return {source:listings[0],target:listings[1]};
  }
  async switchManagement(tab,beforeURL,url,transition) {
    const selected = async(spec,expectedURL,remaining) => {
      const locator = tab.playwright.locator(spec.selector);
      const checkURL = async() => {
        remaining();const current = await this.url(tab);remaining();this.safe(current);
        if (current !== expectedURL) fail('identity_mismatch');
      };
      while (true) {
        await checkURL();const count = await locator.count();await checkURL();
        if (!Number.isSafeInteger(count) || count < 0 || count > 1) fail('incomplete_scope');
        if (count === 1) {
          const visible = await locator.isVisible();await checkURL();
          if (typeof visible !== 'boolean') fail('incomplete_scope');
          if (visible) {
            const value = await locator.evaluate(readDOMValue,null);await checkURL();
            if (value === spec.expected) return;
            if (value !== '') fail('incomplete_scope');
          }
        }
        await new Promise(resolve=>setTimeout(resolve,Math.min(100,remaining())));
      }
    };
    const sourceDeadline = Math.min(this.deadline,Date.now()+5000);
    const sourceRemaining = () => { this.check();const value=sourceDeadline-Date.now();if (value <= 0) fail('incomplete_scope');return value; };
    this.check();await selected(transition.source.coverage,beforeURL,sourceRemaining);
    if (typeof tab.playwright.getByRole !== 'function') fail('invalid_profile');
    const locator = tab.playwright.getByRole('tab',{name:transition.target.coverage.expected,exact:true});
    if (await locator.count() !== 1 || !await locator.isVisible() || !await locator.isEnabled() ||
        await locator.getAttribute('aria-selected') !== 'false') fail('incomplete_scope');
    const listed = (await this.tabs()).filter(item=>item.id === this.state.list.id);
    if (listed.length !== 1 || listed[0].url !== beforeURL || await this.url(tab) !== beforeURL) fail('identity_mismatch');
    await selected(transition.source.coverage,beforeURL,sourceRemaining);this.check();
    await locator.click(this.timeoutOptions()); // One observed UI action; never retry or fall back to goto.
    const deadline = Math.min(this.deadline,Date.now()+5000);
    const remaining = () => { this.check();const value=deadline-Date.now();if (value <= 0) fail('navigation_timeout');return value; };
    while (true) {
      remaining();const current = await this.url(tab);remaining();
      if (current !== beforeURL && current !== url) fail('unsafe_navigation');
      if (current === url) {
        const snapshot = await tab.getAXState({emit:false});remaining();
        const after = await this.url(tab);remaining();
        if (after !== beforeURL && after !== url) fail('unsafe_navigation');
        if (typeof snapshot !== 'string') fail('incomplete_dom');
        if (after === url && snapshot.trim().length) {
          await selected(transition.target.coverage,url,remaining);remaining();
          if (await this.url(tab) !== url) fail('identity_mismatch');
          remaining();return;
        }
      }
      await new Promise(resolve=>setTimeout(resolve,Math.min(100,remaining())));
    }
  }
  async navigate(tab, url, {readOnly = false} = {}) {
    this.check(); this.safe(url);
    const beforeURL = await this.url(tab), attemptedGoto = beforeURL !== url;
    const transition = readOnly === true ? this.managementTransition(tab,beforeURL,url) : null;
    if (transition) return this.switchManagement(tab,beforeURL,url,transition);
    try {
      if (!attemptedGoto) await tab.reload();
      else await tab.goto(url);
    } catch (failure) {
      if (attemptedGoto !== true || readOnly !== true || !this.readOnlyDestination(beforeURL) ||
          !this.readOnlyDestination(url) || !this.navigationTimeout(failure,url)) throw failure;
      // A timed-out SDK acknowledgement is not a second navigation attempt.
      // Only an already-arrived read-only page can proceed to the normal
      // identity, scope, readiness and complete-content checks at the caller.
      try {
        const arrivalDeadline = Math.min(this.deadline,Date.now()+5000);
        const remaining = () => { this.check();const value = arrivalDeadline-Date.now();if (value <= 0) throw failure;return value; };
        while (true) {
          remaining();const current = await this.url(tab);remaining();
          if (current !== beforeURL && current !== url) throw failure;
          const snapshot = await tab.getAXState({emit:false});remaining();
          const after = await this.url(tab);remaining();
          if (after !== beforeURL && after !== url) throw failure;
          if (current === url && after === url && typeof snapshot === 'string' && snapshot.trim().length) return;
          if (typeof snapshot !== 'string' || snapshot.trim().length) throw failure;
          await new Promise(resolve=>setTimeout(resolve,Math.min(100,remaining())));
        }
      } catch { throw failure; }
    }
    await this.ax(tab); this.safe(await this.url(tab));
  }
  async action(tab, operation) {
    this.check(); let before=this.safe(await this.url(tab));
    // A draft can become published or lose its loaded state without changing
    // the editor URL. Check the current DOM state before every later write;
    // disappearance must not restart readiness or be repaired by a retry.
    const state=this.profile.editor.state;
    if (this.createdEditors.has(tab)&&state.source!=='listing') {
      const packet=await tab.playwright.locator(state.selector).evaluateAll(readParagraphs,{
        source:state.source??'dom',attribute:state.attribute??null,includeURL:true
      });
      this.check();
      if (packet?.count!==1) fail(packet?.count>1?'ambiguous_dom':'editor_changed');
      if (this.template(packet.url,this.profile.identity.book)!==this.target.remote_book_id||
          this.template(packet.url,this.profile.editor.remote_id)!==this.createdEditors.get(tab)) fail('identity_mismatch');
      // Saving/new-draft aliases may settle while state is sampled. Keep the
      // confirmed document identity and use its currently observed URL.
      before=packet.url;
      if (packet.ok!==true||packet.value!==state.expected) fail('not_draft');
    }
    await operation(before); await this.ax(tab); this.safe(await this.url(tab));
  }
  async actionState(locator, expectedURL) {
    this.check();const state=await locator.evaluateAll(readActionState);this.check();
    if (state?.count !== 1) fail(state?.count > 1 ? 'ambiguous_dom' : 'editor_changed');
    if (state.url !== expectedURL) fail('identity_mismatch');
    return state;
  }
  async clickDraftButton(locator, spec, expectedURL, kind, expectedHref) {
    const state=await this.actionState(locator,expectedURL);
    const accessible=[state.text,state.aria,state.title].filter(Boolean).join(' ');
    if (state.disabled || state.text !== spec.expected_text || UNSAFE.test(accessible) ||
        (kind==='create' ? !CREATE.test(state.text) : !DRAFT.test(state.text)||!SAVE.test(state.text)) ||
        state.href!==expectedHref) fail(kind==='create'?'unsafe_create':'unsafe_save');
    await locator.click(this.timeoutOptions());
  }
  async one(tab, spec, root = null) {
    const locator = (root || tab.playwright).locator(spec.selector);
    while (true) {
      this.check(); const count = await this.readQuery(tab,()=>locator.count());
      if (count > 1) fail('ambiguous_dom');
      if (count === 1) return locator;
      await pause(); await this.ax(tab);
    }
  }
  async value(tab, spec, root = null) {
    if (spec.source === 'page_url') return this.template(await this.url(tab), spec);
    const locator = await this.one(tab, spec, root);
    let value;
    if (spec.source === 'prosemirror_paragraphs') {
      const packet = await this.readQuery(tab,()=>locator.evaluate(readParagraphs));
      if (packet?.ok !== true || typeof packet.value !== 'string') fail('unsupported_editor');
      value = packet.value;
    } else value = await this.readQuery(tab,()=>locator.evaluate(readDOMValue,spec.attribute ?? null));
    if (typeof value !== 'string') fail('incomplete_dom');
    return value;
  }
  async currentValue(tab, spec, missing = 'editor_changed') {
    const locator = tab.playwright.locator(spec.selector);
    const packet = await this.readQuery(tab,()=>locator.evaluateAll(readParagraphs,{
      source:spec.source ?? 'dom',attribute:spec.attribute ?? null
    }),{retry:false});
    if (packet?.count !== 1) fail(packet?.count > 1 ? 'ambiguous_dom' : missing);
    if (packet.ok !== true || typeof packet.value !== 'string') fail('editor_changed');
    return packet.value;
  }
  async editorSnapshot(tab, expectedURL, expected, specs=this.profile.editor) {
    const fields=[];
    const resolve=async name=>{
      const spec=specs[name],locator=tab.playwright.locator(spec.selector);
      const packet=await this.readQuery(tab,()=>locator.evaluateAll(readDOMPath),{retry:false});
      if (packet?.count !== 1) fail(packet?.count > 1 ? 'ambiguous_dom' : 'editor_changed');
      if (packet.connected !== true || !Array.isArray(packet.path) || typeof packet.tag !== 'string') fail('editor_changed');
      return packet;
    };
    for (const name of Object.keys(expected)) {
      const spec=specs[name],packet=await resolve(name);
      fields.push({name,path:packet.path,tag:packet.tag,spec:{source:spec.source??'dom',attribute:spec.attribute??null}});
    }
    const root=tab.playwright.locator(':root');
    const sample=async()=>{
      const packet=await this.readQuery(tab,()=>root.evaluateAll(readParagraphs,{source:'editor_snapshot',fields}),{retry:false});
      if (packet?.ok !== true || packet.count !== 1 || packet.url !== expectedURL) fail('editor_changed');
      for (const [name,value] of Object.entries(expected)) if (packet.fields?.[name] !== value) fail('editor_changed');
    };
    await sample();
    // A later locator can expose duplicates or retarget an earlier selector
    // while its old DOM node is still connected. Recheck the supported host
    // selectors once; observed drift is terminal, never a readiness retry.
    for (const field of fields) {
      const current=await resolve(field.name);
      if (current.tag!==field.tag||JSON.stringify(current.path)!==JSON.stringify(field.path)) fail('editor_changed');
    }
    // The selector checks can themselves yield. This last single-turn sample
    // binds the accepted values to the validated paths at one reading point.
    await sample();
  }
  async visible(tab, spec) {
    if (!spec) return false;
    const locator = tab.playwright.locator(spec.selector);
    const count = await locator.count();
    if (count > 1) fail('ambiguous_dom');
    return count === 1 && await locator.isVisible();
  }
  async guard(refresh = false, {allowPending = true, current = false} = {}) {
    const spec = this.profile.identity.account;
    if (spec.source !== 'guard_dom') fail('invalid_profile');
    const fresh = !this.state.guard || refresh || !this.guardFreshThisOperation;
    if (!allowPending && fresh) fail('login_required');
    if (fresh) progress(this.api,{phase:'guard',edge:'begin',guard_refreshes:++this.guardRefreshes});
    if (!this.state.guard) this.state.guard = await this.newTab(spec.guard_url,{readOnly:true});
    else if (fresh) await this.navigate(this.state.guard.tab, spec.guard_url, {readOnly:true});
    if (await this.url(this.state.guard.tab) !== spec.guard_url) fail('login_required');
    let value;
    if (current) value = await this.currentValue(this.state.guard.tab,spec,'login_required');
    else if (allowPending) value = await this.value(this.state.guard.tab, spec);
    else {
      // A final identity check reads the already loaded guard once. Missing
      // DOM or a timed-out read cannot restart readiness after the body check.
      const tab = this.state.guard.tab, locator = tab.playwright.locator(spec.selector);
      const count = await this.readQuery(tab,()=>locator.count(),{retry:false});
      if (count !== 1) fail(count > 1 ? 'ambiguous_dom' : 'login_required');
      value = await this.readQuery(tab,()=>locator.evaluate(readDOMValue,spec.attribute ?? null),{retry:false});
      if (typeof value !== 'string') fail('incomplete_dom');
    }
    if (await this.url(this.state.guard.tab) !== spec.guard_url) fail('login_required');
    if (!value.trim()) fail('login_required');
    if (value.trim() !== this.target.account_id) fail('identity_mismatch');
    this.guardFreshThisOperation = true;
    if (fresh) progress(this.api,{phase:'guard',edge:'end',guard_refreshes:this.guardRefreshes});
  }
  async identity(tab, refresh = false, {allowPending = true, current = false} = {}) {
    this.safe(await this.url(tab));
    if (this.template(await this.url(tab), this.profile.identity.book) !== this.target.remote_book_id) fail('identity_mismatch');
    await this.guard(refresh,{allowPending,current});
    this.safe(await this.url(tab));
    if (this.template(await this.url(tab), this.profile.identity.book) !== this.target.remote_book_id) fail('identity_mismatch');
  }
  async listPage() {
    if (!this.state.list) this.state.list = await this.newTab(this.profile.urls.drafts,{readOnly:true});
    return this.state.list.tab;
  }
  async verify() {
    this.state.verified = false;
    const tab = await this.listPage();
    await this.navigate(tab, this.profile.urls.drafts, {readOnly:true});
    await this.identity(tab, true);
    this.state.verified = true;
    return {...this.target};
  }
  async scope(tab, listing, section = null) {
    for (const spec of [listing.coverage, ...listing.filters]) if (await this.value(tab, spec) !== spec.expected) fail('incomplete_scope');
    if (section != null && await this.value(tab, listing.sections.selected) !== section) fail('incomplete_scope');
  }
  chapter(raw, spec = {}) {
    if ((spec.unnumbered_values || []).includes(raw)) return null;
    const matched = spec.format === 'chapter_title' ?
      new RegExp('^第([0-9]+)章' + escape(spec.title_separator) + '([^\\r\\n]+)$').exec(raw.trim()) :
      /^\s*(?:第\s*)?([0-9]+)(?:\s*章)?\s*$/.exec(raw);
    if (!matched || Number(matched[1]) < 1 || Number(matched[1]) > 1000000 ||
        (spec.format === 'chapter_title' && !matched[2].trim())) fail('invalid_chapter');
    return Number(matched[1]);
  }
  async rows(tab, listing) {
    const specs = [listing.chapter,listing.state.source === 'scope' ? null : listing.state,listing.link];
    if (specs.some(spec => spec && spec.source != null && spec.source !== 'dom')) fail('invalid_profile');
    const rows = tab.playwright.locator(listing.rows); let fields;
    while (true) {
      this.check(); const packet = await rows.evaluateAll(readRowFields,specs);
      if (packet?.ok === true && Array.isArray(packet.rows)) { fields = packet.rows; break; }
      if (packet?.error === 'missing_field') { await pause(); await this.ax(tab); continue; }
      if (packet?.error === 'unsupported_selector') {
        // Only native CSS syntax incompatibility falls back to CUA locators.
        // Missing, duplicate or unreadable DOM remains a positive-check failure.
        const count = await rows.count(); if (count > 100000) fail('incomplete_list');
        fields = [];
        for (let index = 0; index < count; index++) {
          this.check(); const row = rows.nth(index); const values = [];
          for (const spec of specs) values.push(spec === null ? null : await this.value(tab,spec,row));
          fields.push(values);
        }
        break;
      }
      if (['ambiguous_dom','incomplete_dom','incomplete_list'].includes(packet?.error)) fail(packet.error);
      fail('incomplete_list');
    }
    if (fields.length > 100000) fail('incomplete_list');
    const result = []; const base = await this.url(tab);
    for (const values of fields) {
      this.check();
      if (!Array.isArray(values) || values.length !== 3 || typeof values[0] !== 'string' || typeof values[2] !== 'string' ||
          (listing.state.source !== 'scope' && typeof values[1] !== 'string')) fail('incomplete_dom');
      const chapter = this.chapter(values[0], listing.chapter);
      const state = listing.state.source === 'scope' ? listing.state.value : values[1].trim();
      if (!state) fail('incomplete_list');
      const url = new URL(values[2],base).href;
      this.safe(url);
      if (this.template(url, this.profile.identity.book) !== this.target.remote_book_id) fail('identity_mismatch');
      result.push({url, chapter, state, scope: listing.scope});
    }
    return result;
  }
  async readyRows(tab, listing, section) {
    while (true) {
      this.check(); await this.identity(tab); await this.scope(tab, listing, section);
      if (await this.emptyDraftRows(tab, listing, section)) return [];
      if (await this.listReady(tab, listing)) {
        const rows = await this.rows(tab, listing);
        if ((rows.length || await this.visible(tab, listing.empty)) &&
            await this.value(tab, listing.ready) === listing.ready.expected) return rows;
      }
      await pause(); await this.ax(tab);
    }
  }
  async listReady(tab, listing) {
    const locator = tab.playwright.locator(listing.ready.selector); const count = await locator.count();
    if (count > 1) fail('ambiguous_dom');
    if (!count) return false;
    return await this.value(tab, listing.ready) === listing.ready.expected;
  }
  async emptyDraftRows(tab, listing, section) {
    // Fanqie's observed empty draft box omits the table, spinner and scroll
    // container. A missing table alone never proves an empty catalog.
    if (this.target.platform !== 'fanqie' || listing.scope !== 'drafts' ||
        !await this.visible(tab, listing.empty)) return false;
    const pagination = listing.pagination;
    if (pagination.mode !== 'all_rows' || pagination.validated !== true ||
        listing.count?.format !== 'draft_count' || !Array.isArray(pagination.reject_selectors) ||
        !pagination.reject_selectors.length) fail('incomplete_list');
    for (let pass = 0; pass < 2; pass++) {
      this.check(); await this.identity(tab); await this.scope(tab, listing, section);
      if (!await this.visible(tab, listing.empty)) return false;
      if ((await this.rows(tab, listing)).length) fail('incomplete_list');
      const counter = tab.playwright.locator(listing.count.selector); const count = await counter.count();
      if (count !== 1) fail(count > 1 ? 'ambiguous_dom' : 'incomplete_list');
      if (await this.value(tab, listing.count) !== '共0篇草稿') fail('incomplete_list');
      for (const selector of pagination.reject_selectors) if (await tab.playwright.locator(selector).count()) fail('incomplete_list');
      const ready = tab.playwright.locator(listing.ready.selector); const readyCount = await ready.count();
      if (readyCount > 1) fail('ambiguous_dom');
      if (readyCount && !await this.listReady(tab, listing)) return false;
      await this.identity(tab); await this.scope(tab, listing, section);
      if (!pass) { await pause(); await this.ax(tab); }
    }
    return true;
  }
  async metrics(locator) {
    return locator.evaluate(el => { const r = el.getBoundingClientRect(); return {
      height: el.clientHeight, total: el.scrollHeight, width: el.clientWidth, wide: el.scrollWidth,
      top: el.scrollTop, x: r.left + r.width / 2, y: r.top + r.height / 2}; });
  }
  async allRows(tab, listing, rows, section) {
    const pagination = listing.pagination;
    if (pagination.validated !== true) fail('incomplete_list');
    if (!rows.length && this.target.platform === 'fanqie' && listing.scope === 'drafts') {
      if (!await this.emptyDraftRows(tab, listing, section)) fail('incomplete_list');
      return;
    }
    const container = await this.one(tab, pagination.container);
    const signature = JSON.stringify(rows); let dimensions;
    progress(this.api,{...this.scan,phase:'all_rows',edge:'begin',row_count:rows.length});
    for (const down of [false, true]) {
      for (const selector of pagination.reject_selectors) if (await tab.playwright.locator(selector).count()) fail('incomplete_list');
      let metrics = await this.metrics(container);
      if (!Number.isFinite(metrics.height) || metrics.height <= 0 || !Number.isFinite(metrics.total)) fail('incomplete_list');
      const current = JSON.stringify([metrics.height, metrics.total, metrics.width, metrics.wide]);
      progress(this.api,{...this.scan,phase:'all_rows',edge:'checked',direction:down?'down':'up',container_before:metrics,dimensions_equal:!dimensions||current===dimensions});
      if (dimensions && current !== dimensions) fail('incomplete_list'); dimensions = current;
      const docMetrics = await tab.playwright.evaluate(() => { const el = document.scrollingElement; return {
        height: el.clientHeight, total: el.scrollHeight, top: el.scrollTop,
        width: document.documentElement.clientWidth}; });
      progress(this.api,{...this.scan,phase:'all_rows',edge:'before_scroll',direction:down?'down':'up',container_before:metrics,document_before:docMetrics});
      const x = Math.max(1, Math.min(docMetrics.width - 1, metrics.x));
      const y = Math.max(1, Math.min(docMetrics.height - 1, metrics.y));
      await this.action(tab, () => tab.scroll([x, y], down ? 'down' : 'up', Math.max(1, Math.ceil(metrics.total / metrics.height))));
      await this.action(tab, () => tab.scroll([Math.max(1, docMetrics.width / 2), Math.max(1, docMetrics.height / 2)], down ? 'down' : 'up', Math.max(1, Math.ceil(docMetrics.total / docMetrics.height))));
      metrics = await this.metrics(container);
      const dimensionsEqual = JSON.stringify([metrics.height, metrics.total, metrics.width, metrics.wide]) === dimensions;
      const edgeReached = down ? metrics.top + metrics.height >= metrics.total - 1 : metrics.top <= 1;
      const afterDocument = await tab.playwright.evaluate(() => { const el = document.scrollingElement; return {height: el.clientHeight, total: el.scrollHeight, top: el.scrollTop}; });
      const documentValid = [afterDocument.height,afterDocument.total,afterDocument.top].every(Number.isFinite) && afterDocument.height > 0;
      const documentEdgeReached = documentValid && (down ? afterDocument.top + afterDocument.height >= afterDocument.total - 1 : afterDocument.top <= 1);
      progress(this.api,{...this.scan,phase:'all_rows',edge:'after_scroll',direction:down?'down':'up',container_after:metrics,document_after:afterDocument,dimensions_equal:dimensionsEqual,edge_reached:edgeReached,document_valid:documentValid,document_edge_reached:documentEdgeReached});
      if (!dimensionsEqual || !edgeReached) fail('incomplete_list');
      if (!documentValid || !documentEdgeReached) fail('incomplete_list');
      await this.identity(tab); await this.scope(tab, listing, section);
      const readyEqual = await this.value(tab, listing.ready) === listing.ready.expected;
      const rowsEqual = JSON.stringify(await this.rows(tab, listing)) === signature;
      progress(this.api,{...this.scan,phase:'all_rows',edge:'checked',direction:down?'down':'up',ready_equal:readyEqual,rows_equal:rowsEqual});
      if (!readyEqual || !rowsEqual) fail('incomplete_list');
    }
    if (listing.count) {
      const count = /^共([0-9]+)篇草稿$/.exec(await this.value(tab, listing.count));
      const countEqual = !!count && Number(count[1]) === rows.length;
      progress(this.api,{...this.scan,phase:'all_rows',edge:'checked',count_equal:countEqual});
      if (!countEqual) fail('incomplete_list');
    }
    progress(this.api,{...this.scan,phase:'all_rows',edge:'end',row_count:rows.length});
  }
  async options(tab, listing) {
    await this.identity(tab);
    const trigger = await this.one(tab, listing.sections.trigger);
    await this.action(tab, () => trigger.click(this.timeoutOptions()));
    const options = tab.playwright.locator(listing.sections.options);
    while (true) {
      this.check(); const count = await options.count();
      if (count > 1000) fail('incomplete_list');
      let visible = count > 0;
      for (let i = 0; i < count && visible; i++) visible = await options.nth(i).isVisible();
      if (!visible) { await pause(); await this.ax(tab); continue; }
      const labels = [];
      for (let i = 0; i < count; i++) {
        const value = await options.nth(i).innerText();
        if (!value.trim() || /[\u0000-\u001f]/.test(value)) fail('incomplete_list'); labels.push(value);
      }
      if (new Set(labels).size !== labels.length) fail('incomplete_list');
      const secondCount = await options.count();
      if (secondCount === 0) { await pause(); await this.ax(tab); continue; }
      if (secondCount !== count) fail('incomplete_list');
      let stillVisible = true;
      for (let i = 0; i < count && stillVisible; i++) stillVisible = await options.nth(i).isVisible();
      if (!stillVisible) { await pause(); await this.ax(tab); continue; }
      const second = [];
      for (let i = 0; i < count; i++) second.push(await options.nth(i).innerText());
      if (JSON.stringify(second) !== JSON.stringify(labels)) fail('incomplete_list');
      return {options, labels};
    }
  }
  async pages(tab, listing, section, seen) {
    const result = []; const pages = new Set();
    for (let page = 0; page < 10000; page++) {
      this.scan = {...this.scan,page};
      progress(this.api,{...this.scan,phase:'pages',edge:'begin'});
      const rows = await this.readyRows(tab, listing, section); let next = null;
      if (listing.pagination.mode === 'all_rows') await this.allRows(tab, listing, rows, section);
      else {
        const pagination = listing.pagination; const locator = tab.playwright.locator(pagination.next.selector);
        const end = await this.visible(tab, pagination.end);
        if (end) { if (await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled()) fail('incomplete_list'); }
        else {
          if (await locator.count() !== 1 || !await locator.isVisible()) fail('incomplete_list');
          const disabled = pagination.disabled_attribute != null && await locator.getAttribute(pagination.disabled_attribute) === pagination.disabled_value;
          if (!disabled) { if (!await locator.isEnabled()) fail('incomplete_list'); next = locator; }
        }
      }
      await this.identity(tab); await this.scope(tab, listing, section);
      const emptyCandidate = !rows.length && this.target.platform === 'fanqie' && listing.scope === 'drafts';
      const emptyDrafts = emptyCandidate && await this.emptyDraftRows(tab, listing, section);
      if (emptyCandidate && !emptyDrafts) fail('incomplete_list');
      if ((!emptyDrafts && await this.value(tab, listing.ready) !== listing.ready.expected) || JSON.stringify(await this.rows(tab, listing)) !== JSON.stringify(rows)) fail('incomplete_list');
      const signature = JSON.stringify(rows); if (pages.has(signature)) fail('incomplete_list'); pages.add(signature);
      for (const row of rows) { if (seen.has(row.url)) fail('incomplete_list'); seen.add(row.url); result.push(row); }
      if (seen.size > 100000) fail('incomplete_list');
      if (!next) { progress(this.api,{...this.scan,phase:'pages',edge:'end',row_count:result.length}); return result; }
      if (!rows.length) fail('incomplete_list');
      await this.action(tab, () => next.click(this.timeoutOptions()));
      while (JSON.stringify(await this.rows(tab, listing)) === signature) { this.check(); await pause(); await this.ax(tab); }
    }
    fail('incomplete_list');
  }
  async catalog({fresh = false} = {}) {
    const tab = await this.listPage(); const result = []; const seen = new Set();
    const catalogIndex = ++this.catalogIndex;
    progress(this.api,{phase:'catalog',edge:'begin',catalog_index:catalogIndex});
    if (fresh) {
      // A new popup can allocate a draft without invalidating the management
      // page's SPA cache. Tab switching alone is not a fresh draft observation.
      const current = await this.url(tab),list = this.state.list;
      if (!list || list.tab !== tab || !this.state.owned.has(list.id)) fail('bridge_scope');
      if (this.profile.chapter_lists.filter(listing => listing.url === current).length !== 1 || !this.readOnlyDestination(current)) fail('unsafe_navigation');
      await this.identity(tab);
      const listed = (await this.tabs()).filter(item => item.id === list.id);
      if (listed.length !== 1 || listed[0].url !== current || await this.url(tab) !== current) fail('identity_mismatch');
      // If the scan already starts at this URL, its first navigate reloads it.
      // Otherwise refresh before SPA tab switching can reuse the old snapshot.
      if (current !== this.profile.chapter_lists[0]?.url) await this.navigate(tab,current,{readOnly:true});
      if (await this.url(tab) !== current) fail('identity_mismatch');
    }
    for (let listingIndex = 0; listingIndex < this.profile.chapter_lists.length; listingIndex++) {
      const listing = this.profile.chapter_lists[listingIndex];
      this.scan = {catalog_index:catalogIndex,listing_index:listingIndex,scope:listing.scope};
      progress(this.api,{...this.scan,phase:'listing',edge:'begin'});
      await this.navigate(tab, listing.url, {readOnly:true});
      if (!listing.sections) {
        result.push(...await this.pages(tab, listing, null, seen));
        progress(this.api,{...this.scan,phase:'listing',edge:'end'}); continue;
      }
      let {options, labels} = await this.options(tab, listing);
      for (let index = 0; index < labels.length; index++) {
        this.scan = {...this.scan,section_index:index};
        if (index) { const current = await this.options(tab, listing); if (JSON.stringify(current.labels) !== JSON.stringify(labels)) fail('incomplete_list'); options = current.options; }
        await this.action(tab, () => options.nth(index).click(this.timeoutOptions()));
        while (await this.value(tab, listing.sections.selected) !== labels[index]) { this.check(); await pause(); await this.ax(tab); }
        result.push(...await this.pages(tab, listing, labels[index], seen));
      }
      const rows = await this.rows(tab, listing); const current = await this.options(tab, listing);
      if (JSON.stringify(current.labels) !== JSON.stringify(labels)) fail('incomplete_list');
      const trigger = await this.one(tab, listing.sections.trigger); await this.action(tab, () => trigger.click(this.timeoutOptions()));
      await this.scope(tab, listing, labels.at(-1));
      if (JSON.stringify(await this.rows(tab, listing)) !== JSON.stringify(rows)) fail('incomplete_list');
      progress(this.api,{...this.scan,phase:'listing',edge:'end'});
    }
    progress(this.api,{phase:'catalog',edge:'end',catalog_index:catalogIndex,row_count:result.length});
    return result;
  }
  remote(url) { return this.template(url, this.profile.editor.remote_id); }
  async confirm(remote, chapter = null) {
    const catalog = await this.catalog({fresh:this.target.platform === 'fanqie' && this.request.operation === 'create_draft' && this.createdRemote === remote});
    for (const row of catalog) if (row.scope === 'drafts' && row.chapter == null && this.remote(row.url) !== remote) fail('unnumbered_draft');
    const evidence = catalog.filter(row => row.scope === 'drafts' && this.remote(row.url) === remote);
    if (evidence.length !== 1 || evidence[0].state !== this.profile.editor.state.expected) fail('not_draft');
    if (chapter != null && evidence[0].chapter !== chapter) fail('identity_mismatch');
    if (chapter != null && catalog.filter(row => row.chapter === chapter).length !== 1) fail('ambiguous_draft');
    if (chapter != null && catalog.some(row => row.chapter === chapter && row.scope === 'non_drafts')) fail('chapter_exists_not_draft');
  }
  async editorReady(tab) {
    const spec = this.profile.editor.ready; if (!spec) fail('invalid_profile');
    while (true) {
      const locator = await this.one(tab, spec);
      if (await locator.isVisible() && (!spec.enabled || await locator.isEnabled()) && await this.value(tab, spec) === spec.expected) return;
      this.check(); await pause(); await this.ax(tab);
    }
  }
  async editorState(tab, chapter = null) {
    const state = this.profile.editor.state;
    if (state.source === 'listing') await this.confirm(this.remote(await this.url(tab)), chapter);
    else if (await this.value(tab, state) !== state.expected) fail('not_draft');
  }
  pathField(pathname, spec) {
    for (const template of spec.templates || []) {
      const path = new URL(template.replace('{book_id}','BOOK_ID').replace('{remote_id}','REMOTE_ID')).pathname;
      let pattern = escape(path);
      pattern = pattern.replace('BOOK_ID','(?<book_id>[A-Za-z0-9_-]{1,200})').replace('REMOTE_ID','(?<remote_id>[A-Za-z0-9_-]{1,200})');
      const matched = new RegExp('^'+pattern+'$').exec(pathname);
      if (matched?.groups?.[spec.capture]) {
        const value = matched.groups[spec.capture];
        if (spec.id_kind !== 'digits' || /^[0-9]+$/.test(value)) return value;
      }
    }
    return null;
  }
  matchingEditor(url, remote) {
    try {
      const parsed = new URL(url);
      if (parsed.origin !== this.origin || parsed.username || parsed.password || parsed.port ||
          url.slice(8).split(/[/?#]/)[0] !== new URL(this.origin).host) return false;
      // Query and hash do not distinguish documents for concurrency detection.
      // Read/write identity checks still use the exact configured full URLs.
      return this.pathField(parsed.pathname,this.profile.identity.book) === this.target.remote_book_id &&
        this.pathField(parsed.pathname,this.profile.editor.remote_id) === remote;
    } catch { return false; }
  }
  async ensureEditorFree(remote, closeOwned = false) {
    this.check(); const matches = (await this.tabs()).filter(tab=>this.matchingEditor(tab.url,remote));
    if (matches.some(tab=>!this.state.owned.has(tab.id)) || (!closeOwned && matches.length)) fail('editor_in_use');
    for (const item of matches) {
      this.check(); const tab = await this.api.getTab(item.id);
      if (!this.matchingEditor(await this.url(tab),remote) || typeof tab.close !== 'function') fail('editor_in_use');
      await tab.close();
      const closeDeadline = Math.min(this.deadline,Date.now()+5000);
      while (true) {
        if (Date.now() >= closeDeadline) fail('editor_in_use');
        const current = await this.tabs();
        if (Date.now() >= closeDeadline || current.some(candidate=>this.matchingEditor(candidate.url,remote) && !this.state.owned.has(candidate.id))) fail('editor_in_use');
        const remaining = current.find(candidate=>candidate.id===item.id);
        if (!remaining) { this.state.owned.delete(item.id); break; }
        if (!this.matchingEditor(remaining.url,remote)) fail('editor_in_use');
        await new Promise(resolve=>setTimeout(resolve,Math.max(1,Math.min(100,closeDeadline-Date.now()))));
      }
    }
    if ((await this.tabs()).some(tab=>this.matchingEditor(tab.url,remote))) fail('editor_in_use');
  }
  async previewBody(tab, spec, {allowPending = true} = {}) {
    const locator = allowPending ? await this.one(tab,spec) : tab.playwright.locator(spec.selector);
    if (!allowPending) {
      const count = await this.readQuery(tab,()=>locator.count(),{retry:false});
      if (count !== 1) fail(count > 1 ? 'ambiguous_dom' : 'editor_changed');
    }
    while (true) {
      this.check(); const packet = await this.readQuery(tab,()=>locator.evaluate(readPreviewParagraphs_v1),{retry:allowPending});
      if (packet?.ok === true && typeof packet.value === 'string') return packet.value;
      if (packet?.pending !== true) fail('unsupported_editor');
      // Once complete text was observed, disappearance is a changed document,
      // not another initial loading state that may be waited away.
      if (!allowPending) fail('editor_changed');
      await pause(); await this.ax(tab);
    }
  }
  async readPreview(remote) {
    id(remote); const spec = this.profile.readback;
    if (spec?.remote_id?.source !== 'page_url' || spec?.header?.source !== 'dom' ||
        spec.header.format !== 'chapter_title' || typeof spec.header.title_separator !== 'string' || !spec.header.title_separator ||
        spec?.body?.source !== 'fanqie_preview_paragraphs_v1') fail('invalid_profile');
    const forceGuard = !(this.target.platform === 'fanqie' && ['find_drafts','read_draft'].includes(this.request.operation));
    await this.guard(forceGuard); await this.ensureEditorFree(remote,true);
    progress(this.api,{phase:'read',edge:'begin'});
    const requestedURL = spec.url.replace('{remote_id}',remote);
    // Pin the configured destination across every locator/AX read, including
    // separate reads on either side of a loading wait or account guard await.
    const {tab,id:tabId,url:expectedURL} = await this.newTab(requestedURL,{readOnly:true,pinReadback:true});
    this.state.last_readback_tab_id = tabId;
    await this.identity(tab);
    if (await this.url(tab) !== expectedURL || this.template(expectedURL,spec.remote_id) !== remote) fail('identity_mismatch');
    await this.confirm(remote,this.state.chapters.get(remote));
    let header;
    while (!(header=await this.value(tab,spec.header))) { this.check(); await pause(); await this.ax(tab); }
    const matched = new RegExp('^第([0-9]+)章'+escape(spec.header.title_separator)+'([^\\r\\n]+)$').exec(header);
    if (!matched || Number(matched[1])<1 || Number(matched[1])>1000000 || !matched[2].trim()) fail('invalid_chapter');
    const chapter = Number(matched[1]);
    if (this.state.chapters.has(remote) && this.state.chapters.get(remote)!==chapter) fail('identity_mismatch');
    const body = await this.previewBody(tab,spec.body);
    const observation = {remote_id:remote,title:matched[2],body,state:'draft',url:expectedURL};
    await this.confirm(remote,chapter); await this.identity(tab,forceGuard);
    if (await this.url(tab)!==expectedURL) fail('identity_mismatch');
    if (await this.previewBody(tab,spec.body,{allowPending:false})!==body) fail('editor_changed');
    await this.ensureEditorFree(remote);
    // Read the current account DOM without another read-only guard reload.
    // Both identity and the complete chapter header must survive the final
    // body and editor-inventory awaits before this observation is accepted.
    await this.identity(tab,false,{allowPending:false});
    if (await this.url(tab)!==expectedURL) fail('identity_mismatch');
    const finalHeader = tab.playwright.locator(spec.header.selector);
    const headerCount = await this.readQuery(tab,()=>finalHeader.count(),{retry:false});
    if (headerCount !== 1) fail(headerCount > 1 ? 'ambiguous_dom' : 'editor_changed');
    if (await this.readQuery(tab,()=>finalHeader.evaluate(readDOMValue,null),{retry:false})!==header) fail('editor_changed');
    await this.editorSnapshot(tab,expectedURL,{header,body},spec);
    progress(this.api,{phase:'read',edge:'end'});
    return observation;
  }
  async read(remote) {
    if (this.profile.readback) return this.readPreview(remote);
    progress(this.api,{phase:'read',edge:'begin'});
    id(remote); const requestedURL = this.profile.urls.edit.replace('{remote_id}', remote);
    // Pin every observed URL from arrival through all field/guard/list awaits.
    const {tab, id: tabId, url:expectedURL} = await this.newTab(requestedURL,{pinReadback:true});
    this.state.last_readback_tab_id = tabId;
    await this.identity(tab, true); await this.editorReady(tab); await this.editorState(tab, this.state.chapters.get(remote));
    if (this.remote(await this.url(tab)) !== remote) fail('identity_mismatch');
    const editor = this.profile.editor;
    let raw; while (!(raw = await this.value(tab, editor.chapter)).trim()) { this.check(); await pause(); await this.ax(tab); }
    const chapter = this.chapter(raw);
    if (this.state.chapters.has(remote) && this.state.chapters.get(remote) !== chapter) fail('identity_mismatch');
    const observation = {remote_id: remote, title: await this.value(tab, editor.title), body: await this.value(tab, editor.body), state: 'draft', url: expectedURL};
    const currentIdentity = () => this.identity(tab,false,{allowPending:false,current:true});
    // A changed loaded account must fail before directory readiness can hide
    // it. Initial readiness above remains available for a newly opened page.
    await currentIdentity();
    // Preserve the ordinary editor's fresh server-account check, but finish
    // this preparation before any final field snapshot. It cannot repair an
    // already observed missing/changed loaded account.
    await this.identity(tab,true);
    if (editor.state.source === 'listing') await this.confirm(remote, chapter);
    if (await this.currentValue(tab,editor.body) !== observation.body) fail('editor_changed');
    await currentIdentity();
    // Check the header and DOM state after the final body and guard awaits.
    // Locator resolution preserves supported host selectors such as :visible;
    // no final field may restart loading or retry a timed-out read.
    if (await this.currentValue(tab,editor.chapter) !== raw ||
        await this.currentValue(tab,editor.title) !== observation.title) fail('editor_changed');
    if (editor.state.source !== 'listing' && await this.currentValue(tab,editor.state) !== editor.state.expected) fail('not_draft');
    // Guard and header reads can yield while the body changes. Close the
    // observation with one current editor sample, rather than chasing changes
    // through another sequence of separately awaited field reads.
    await this.editorSnapshot(tab,expectedURL,{chapter:raw,title:observation.title,body:observation.body,
      ...(editor.state.source === 'listing' ? {} : {state:editor.state.expected})});
    if (await this.url(tab) !== expectedURL || this.remote(expectedURL) !== remote) fail('identity_mismatch');
    progress(this.api,{phase:'read',edge:'end'});
    return observation;
  }
  async find(chapter) {
    if (!Number.isInteger(chapter) || chapter < 1) fail('invalid_chapter');
    const catalog = await this.catalog();
    if (catalog.some(row => row.chapter == null)) fail('unnumbered_draft');
    const found = catalog.filter(row => row.chapter === chapter);
    if (found.some(row => row.scope === 'non_drafts' || row.state !== this.profile.editor.state.expected)) fail('chapter_exists_not_draft');
    const observations = [];
    for (const row of found) { const remote = this.remote(row.url); this.state.chapters.set(remote, chapter); observations.push(await this.read(remote)); }
    return observations;
  }
  async fill(tab, spec, text) {
    const locator = await this.one(tab, spec);
    if (spec.source !== 'prosemirror_paragraphs') return this.action(tab, () => locator.fill(text, this.timeoutOptions()));
    const normalized = normal(text);
    const tail = /\n*$/.exec(normalized)[0].length,core = normalized.slice(0,normalized.length-tail);
    if ((core && typeof tab.pasteText !== 'function') || await locator.evaluate(el => el.getAttribute('contenteditable')) !== 'true') fail('unsupported_editor');
    await this.action(tab, () => locator.click(this.timeoutOptions()));
    const selectAll = this.api.selectAllKey || 'Meta+A';
    if (!['Meta+A', 'Control+A'].includes(selectAll)) fail('invalid_profile');
    await this.action(tab, () => locator.press(selectAll, this.timeoutOptions()));
    await this.action(tab, () => locator.press('Backspace', this.timeoutOptions()));
    if (await this.value(tab,spec) !== '') fail('editor_changed');
    if (await locator.evaluate(el => {
      const focused = el.ownerDocument?.activeElement;
      return !!focused && (focused === el || el.contains(focused));
    }) !== true) fail('editor_changed');
    // Plain-text clipboard insertion can consume terminal line breaks. Keep
    // them in the input plan, never infer or repair them after a mismatch.
    if (core) await this.action(tab, async expectedURL => {
      // Paste targets the focused element. Sample focus after the action URL
      // await and do not insert another asynchronous read before the write.
      const state=await this.actionState(locator,expectedURL);
      if (!state.editableFocus) fail('editor_changed');
      await tab.pasteText(core);
    });
    if (await this.value(tab,spec) !== core) fail('editor_changed');
    for (let index = 0; index < tail; index++) await this.action(tab, () => locator.press('Enter',this.timeoutOptions()));
    if (await this.value(tab, spec) !== normalized) fail('editor_changed');
  }
  async create(chapter, title, body) {
    if (!Number.isInteger(chapter) || chapter < 1 || typeof title !== 'string' || !title || typeof body !== 'string' || !body) fail('invalid_chapter');
    const action = this.profile.create_action;
    let tab;
    if (action) {
      tab = await this.listPage(); await this.navigate(tab, this.profile.urls.create); await this.identity(tab);
      const locator = await this.one(tab, action); const actual = (await locator.innerText()).trim();
      const accessible = [actual, await locator.getAttribute('aria-label'), await locator.getAttribute('title')].filter(Boolean).join(' ');
      if (action.mode !== 'button' || !CREATE.test(actual) || UNSAFE.test(accessible) || actual !== action.expected_text || !await locator.isVisible() || !await locator.isEnabled()) fail('unsafe_create');
      let popupQuery = null;
      const href = await locator.getAttribute('href');
      if (action.opens === 'popup') {
        if (typeof href !== 'string' || !href) fail('unsafe_create');
        const source = new URL(href, await this.url(tab)); this.safe(source.href);
        // The observed create link and resulting popup share an exact query.
        // This keeps a concurrently opened old draft from being claimed.
        if (!source.search) fail('unsafe_create');
        popupQuery = source.search;
      }
      await this.identity(tab, true); const before = new Set((await this.tabs()).map(item => item.id));
      this.check(); // This click can already create an empty cloud draft. Never retry it.
      // Same-page creation consumes the management tab. Future catalog scans
      // need a separate page, while this owned tab remains the editor.
      if (action.opens === 'same_page') this.state.list = null;
      await this.action(tab, expectedURL => this.clickDraftButton(locator,action,expectedURL,'create',href));
      if (action.opens === 'popup') {
        while (true) {
          this.check(); const added = (await this.tabs()).filter(item => !before.has(item.id));
          const candidates = added.filter(item => {
            if (typeof item.url !== 'string' || item.url === 'about:blank') return false;
            try { return new URL(item.url).search === popupQuery && this.template(item.url, this.profile.identity.book) === this.target.remote_book_id && this.remote(item.url); }
            catch { return false; }
          });
          if (candidates.length > 1) fail('ambiguous_dom');
          if (candidates.length === 1) {
            const candidate = await this.api.getTab(candidates[0].id); await this.ax(candidate);
            if (await this.url(candidate) !== candidates[0].url || this.template(await this.url(candidate), this.profile.identity.book) !== this.target.remote_book_id) fail('identity_mismatch');
            this.remote(await this.url(candidate));
            this.state.owned.add(candidates[0].id); tab = candidate; break;
          }
          await pause();
        }
      } else if (action.opens !== 'same_page') fail('invalid_profile');
    } else {
      await this.guard(true); // Direct creation URLs may allocate a cloud draft.
      ({tab} = await this.newTab(this.profile.urls.create));
    }
    await this.identity(tab); await this.editorReady(tab);
    this.createdRemote = this.remote(await this.url(tab));
    // Only bind after the platform has allocated an ID and reached readiness.
    // The same ID may change from its new-draft URL to its saved-editor URL;
    // another draft or book must fail before the next fill/save action.
    this.createdEditors.set(tab,this.createdRemote);
    await this.url(tab);
    await this.editorState(tab);
    const editor = this.profile.editor;
    if (editor.new && await this.value(tab, editor.new) !== editor.new.expected) fail('not_new_draft');
    if (await this.value(tab, editor.title) !== '' || await this.value(tab, editor.body) !== '') fail('not_new_draft');
    await this.identity(tab, true); await this.fill(tab, editor.chapter, String(chapter)); await this.identity(tab);
    await this.identity(tab, true); await this.fill(tab, editor.title, title); await this.identity(tab);
    await this.identity(tab, true); await this.fill(tab, editor.body, body); await this.identity(tab); await this.editorState(tab);
    if (this.chapter(await this.value(tab, editor.chapter)) !== chapter || normal(await this.value(tab, editor.title)) !== normal(title) || normal(await this.value(tab, editor.body)) !== normal(body)) fail('editor_changed');
    if (editor.save.mode === 'button') {
      const locator = await this.one(tab, editor.save); const actual = (await locator.innerText()).trim();
      const accessible = [actual, await locator.getAttribute('aria-label'), await locator.getAttribute('title')].filter(Boolean).join(' ');
      if (actual !== editor.save.expected_text || UNSAFE.test(accessible) || !DRAFT.test(actual) || !SAVE.test(actual)) fail('unsafe_save');
      const href = await locator.getAttribute('href');
      await this.identity(tab, true);
      await this.action(tab, expectedURL => this.clickDraftButton(locator,editor.save,expectedURL,'save',href));
    } else if (editor.save.mode !== 'autosave') fail('invalid_profile');
    while (await this.value(tab, editor.saved) !== editor.saved.expected || !await this.visible(tab, editor.saved)) {
      this.check(); await this.identity(tab); await pause(); await this.ax(tab);
    }
    const remote = this.remote(await this.url(tab));
    if (remote !== this.createdRemote) fail('identity_mismatch');
    this.state.chapters.set(remote, chapter);
    const observation = await this.read(remote);
    const observedBody = normal(observation.body), frozenBody = normal(body);
    const equalBody = this.comparisonPolicy === 'fanqie_terminal_lf_v1' ?
      observedBody.replace(/\n+$/,'') === frozenBody.replace(/\n+$/,'') : observedBody === frozenBody;
    if (normal(observation.title) !== normal(title) || !equalBody) fail('save_unverified');
    return remote;
  }
  async execute() {
    const operation = this.request.operation; const params = this.request.params || {};
    if (operation === 'verify_target') return this.verify();
    if (!this.state.verified) fail('target_unverified');
    if (operation === 'find_drafts') return this.find(params.chapter);
    if (operation === 'create_draft') {
      const key = this.request.session_id + ':' + id(this.request.id);
      if (CONSUMED_CREATES.has(key) || CONSUMED_CREATES.size >= 10000) fail('operation_failed');
      CONSUMED_CREATES.add(key);
      return this.create(params.chapter, params.title, params.body);
    }
    if (operation === 'read_draft') return this.read(params.remote_id);
    fail('invalid_profile');
  }
}

export async function executeChromeBridgeRequest(api, request) {
  const envelope = {schema: 1, id: request?.id, nonce: request?.nonce, session_id: request?.session_id, binding_sha256: request?.binding_sha256};
  try { return {...envelope, ok: true, result: await new Driver(api, request).execute()}; }
  catch (error) {
    const code = /^platform_browser_[a-z_]+$/.test(error?.code || '') ? error.code : 'platform_browser_operation_failed';
    try {
      const pending = typeof api?.onDriverError === 'function' ? api.onDriverError(error) : null;
      if (typeof pending?.catch === 'function') pending.catch(() => {});
    } catch { /* Diagnostic callbacks never alter or retry the operation. */ }
    progress(api,{phase:'error',code,...errorLocation(error)});
    return {...envelope, ok: false, error:code};
  }
}

// Closing is explicit and touches only tabs created by this driver. The latest
// readback tab is kept for the host's screenshot and the author's inspection.
export async function closeChromeBridgeSession(api, sessionId, {keepReadback = true} = {}) {
  const state = SESSIONS.get(sessionId); if (!state) return;
  for (const tabId of state.owned) {
    if (keepReadback && tabId === state.last_readback_tab_id) continue;
    try { const tab = await api.getTab(tabId); if (typeof tab.close === 'function') await tab.close(); } catch { /* Only cleanup; never hide a prior write result. */ }
  }
  SESSIONS.delete(sessionId);
}
