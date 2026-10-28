/* Offline public-CUA contract tests. No Chrome, network, account or credentials. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executeChromeBridgeRequest, closeChromeBridgeSession, readParagraphs, readDOMValue, readDOMPath, readActionState, readRowFields, readPreviewParagraphs_v1} from './before-bridge.mjs';

const ORIGIN = 'https://fanqienovel.com';
const field = (selector, extra = {}) => ({selector, ...extra});
function legacyProfile() {
  return {
    schema_version: 1, platform: 'fanqie', origin: ORIGIN, execution_mode: 'host_assisted_cua',
    urls: {drafts: ORIGIN + '/book/10/drafts', create: ORIGIN + '/book/10/drafts', edit: ORIGIN + '/book/10/edit/{remote_id}?enter_from=modifydraft'},
    identity: {
      account: field('#account', {source:'guard_dom', guard_url:ORIGIN + '/account'}),
      book: {source:'page_url', templates:[ORIGIN + '/book/{book_id}/drafts', ORIGIN + '/book/{book_id}/chapters', ORIGIN + '/book/{book_id}/edit/{remote_id}?enter_from=modifydraft', ORIGIN + '/book/{book_id}/edit/{remote_id}?enter_from=modifychapter', ORIGIN + '/book/{book_id}/edit/{remote_id}?enter_from=newdraft'], capture:'book_id', id_kind:'digits'}
    },
    create_action: field('#create', {mode:'button', opens:'popup', expected_text:'新建草稿'}),
    chapter_lists: ['drafts', 'non_drafts'].map(scope => ({
      scope, url:ORIGIN + '/book/10/' + (scope === 'drafts' ? 'drafts' : 'chapters'),
      coverage:field('#scope',{expected:scope}), ready:field('#ready',{expected:'ready'}), filters:[field('#filter',{expected:'全部'})],
      rows:'.row', chapter:field('.chapter',{format:'chapter_title', title_separator:' ', unnumbered_values:['未命名草稿']}),
      state:scope === 'drafts' ? {source:'scope',value:'草稿'} : field('.state'), link:field('.link',{attribute:'href'}), empty:field('#empty'),
      sections:{trigger:field('#sections'), selected:field('#section'), options:'.option'},
      count:scope === 'drafts' ? field('#count',{format:'draft_count'}) : null,
      pagination:{mode:'all_rows',validated:true,container:field('#scroll'),reject_selectors:['.virtual','.pagination']}
    })),
    editor:{chapter:field('#chapter'),title:field('#title'),body:field('#body',{source:'prosemirror_paragraphs'}),
      ready:field('#save',{expected:'存草稿',enabled:true}),state:{source:'listing',expected:'草稿'},
      remote_id:{source:'page_url',templates:[ORIGIN + '/book/10/edit/{remote_id}?enter_from=modifydraft',ORIGIN + '/book/10/edit/{remote_id}?enter_from=newdraft'],capture:'remote_id',id_kind:'digits'},
      save:field('#save',{mode:'button',expected_text:'存草稿'}),saved:field('#saved',{expected:'已保存到云端'})}
  };
}
function previewProfile() {
  const config=legacyProfile();config.identity.book.templates.push(ORIGIN+'/preview/{book_id}&{remote_id}');
  config.readback={url:ORIGIN+'/preview/10&{remote_id}',
    remote_id:{source:'page_url',templates:[ORIGIN+'/preview/10&{remote_id}'],capture:'remote_id',id_kind:'digits'},
    header:field('#preview-title',{source:'dom',format:'chapter_title',title_separator:' '}),
    body:field('#preview-body',{source:'fanqie_preview_paragraphs_v1'})};
  return config;
}
const profile=previewProfile;
function element(tag, attrs = {}, children = []) {
  const node = {nodeType:1, tagName:tag.toUpperCase(), childNodes:children,
    getAttribute:key => Object.hasOwn(attrs,key) ? attrs[key] : null,
    classList:{contains:value => (attrs.class || '').split(/\s+/).includes(value)}};
  Object.defineProperty(node,'textContent',{get:() => children.map(child => child.textContent).join('')});
  Object.defineProperty(node,'outerHTML',{get:() => '<'+tag.toLowerCase()+Object.entries(attrs).map(([key,value])=>' '+key+'="'+value+'"').join('')+'>'+children.map(child=>child.nodeType===3?child.textContent:child.outerHTML).join('')+(['br','img'].includes(tag.toLowerCase())?'':'</'+tag.toLowerCase()+'>')});
  for(const child of children)child.parentNode=node;
  return node;
}
const textNode = textContent => ({nodeType:3,textContent});
const br = () => element('BR', {class:'ProseMirror-trailingBreak'});
function bodyDOM(value) {
  if (!value) return element('DIV',{contenteditable:'true'},[element('P',{},[
    element('SPAN',{class:'syl-placeholder ProseMirror-widget',ignoreel:'true',contenteditable:'false'},[textNode('请输入正文')]),
    element('IMG',{class:'ProseMirror-separator',alt:''}),br()])]);
  return element('DIV',{contenteditable:'true'},[...value.split('\n').map(line => element('P',{},[line ? textNode(line) : br()])),element('P',{},[br()])]);
}
function previewDOM(value) {
  return element('DIV',{},[...value.split('\n').map(line=>element('P',{},line?[textNode(line)]:[])),element('P')]);
}
function parse(html) {
  const root = element('DIV',{contenteditable:'true'}); const stack = [root];
  for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
    if (token.startsWith('</')) { stack.pop(); continue; }
    if (token.startsWith('<')) {
      const [,tag,raw] = /^<([^\s>]+)([^>]*)>/.exec(token); const attrs = {};
      for (const match of raw.matchAll(/([^\s=]+)="([^"]*)"/g)) attrs[match[1]] = match[2];
      const node = element(tag,attrs); stack.at(-1).childNodes.push(node);
      if (!['br','img'].includes(tag.toLowerCase())) stack.push(node);
    } else stack.at(-1).childNodes.push(textNode(token));
  }
  return root;
}

class Locator {
  constructor(tab, lookup, selector) { this.tab=tab; this.lookup=lookup; this.selector=selector; }
  async count() {
    if(this.selector==='#new')this.tab.env.newMarkerChecks=(this.tab.env.newMarkerChecks||0)+1;
    const env=this.tab.env;
    await env.beforeLocatorCount?.(this.tab,this.selector,this);
    if(env.alwaysMissing===this.selector)return 0;
    if(env.countOverrides?.[this.selector]!=null)return env.countOverrides[this.selector];
    if((env.delayCounts?.[this.selector] || 0)>0){env.delayCounts[this.selector]--;return 0;}
    if(env.duplicateSelector===this.selector)return 2;
    return this.lookup().length;
  }
  nth(index) { return new Locator(this.tab,() => [this.lookup()[index]],this.selector); }
  locator(selector) { return new Locator(this.tab,() => this.lookup()[0].children?.[selector] || [],selector); }
  node() { const nodes=this.lookup(); assert.equal(nodes.length,1,'unique fake locator '+this.selector); return nodes[0]; }
  async waitFor() { throw new Error('CUA waitFor attached is intentionally unsupported in polling contracts'); }
  async getAttribute(key) { return this.node().attrs?.[key] ?? null; }
  async innerText() { const value=this.node().text ?? '';await this.tab.env.afterLocatorInnerText?.(this.tab,this.selector);return value; }
  async textContent() { return this.node().text ?? ''; }
  async isVisible() {
    if(this.selector==='.option'&&(this.tab.env.hiddenOptionReads||0)>0){this.tab.env.hiddenOptionReads--;return false;}
    return this.node().visible !== false;
  }
  async isEnabled() { return this.node().enabled !== false; }
  async evaluate(fn,arg) {
    await this.tab.env.beforeLocatorEvaluate?.(this.tab,this.selector,fn,this);
    const node=this.node();
    if(['.chapter','.state','.link','.chapter:visible'].includes(this.selector))this.tab.env.events.push(['fieldRead',this.selector]);
    const result=node.dom?fn(node.dom()):node.metrics?node.metrics():fn(this.dom(node),arg);
    await this.tab.env.afterLocatorEvaluate?.(this.tab,this.selector,fn);
    return result;
  }
  dom(node) {
    return {tagName:node.tag || 'DIV',value:node.value?.() ?? '',innerText:node.text ?? '',textContent:node.text ?? '',
      ownerDocument:{location:{href:this.tab.documentURL?this.tab.documentURL():this.tab.current}},
      getAttribute:key => node.attrs?.[key] ?? null,
      querySelectorAll:selector => {
        if((this.tab.env.cssUnsupported || []).includes(selector))throw new SyntaxError('PRIVATE unsupported selector');
        if((this.tab.env.cssErrors || []).includes(selector))throw new Error('PRIVATE DOM proxy error');
        return (node.children?.[selector] || []).map(child=>this.dom(child));
      }};
  }
  async evaluateAll(fn,arg) {
    this.tab.env.events.push(['evaluateAll',this.selector]);
    await this.tab.env.beforeLocatorEvaluateAll?.(this.tab,this.selector,fn,this);
    let nodes=this.lookup();
    const count=this.tab.env.currentFieldCounts?.[this.selector];
    if(fn===readParagraphs&&arg!=null&&count!=null)nodes=Array.from({length:count},()=>nodes[0]);
    const live=fn===readDOMPath||this.selector===':root'?this.tab.liveDocument():null;
    let domNodes=live?(this.selector===':root'?[live.documentElement]:[live.fields[this.selector]].filter(Boolean)):
      nodes.map(node=>node.dom?node.dom():this.dom(node));
    if(live&&count!=null)domNodes=Array.from({length:count},()=>domNodes[0]);
    const result=fn(domNodes,arg);
    await this.tab.env.afterLocatorEvaluateAll?.(this.tab,this.selector,fn,this);
    return result;
  }
  async click() {
    this.tab.env.events.push(['click',this.selector]);
    if(this.selector==='#body'&&this.tab.env.failBodyFocus)throw new Error('PRIVATE manuscript focus failure');
    if(this.selector==='#body')this.tab.focusedBody=true;
    await this.node().click?.();
  }
  async fill(value) {
    this.tab.env.events.push(['fill',this.selector]); this.node().set(value);
    await this.tab.env.afterFill?.(this.tab,this.selector,value);
    if(this.tab.env.fillFailure)throw this.tab.env.fillFailure;
    if(this.tab.env.changeAccountAfterFill===this.selector)this.tab.env.account='different-account';
  }
  async press(value) {
    this.tab.env.events.push(['press',this.selector,value]);
    assert.equal(this.selector,'#body');
    if (value === 'Backspace') {
      this.tab.data.body=this.tab.env.clearBodyResult??'';
      if(this.tab.env.loseBodyFocusAfterClear)this.tab.focusedBody=false;
    }
    else if (value === 'Enter') {this.tab.data.body+='\n';if(this.tab.env.enterFailure)throw this.tab.env.enterFailure;}
    else assert.ok(['Meta+A','Control+A'].includes(value));
  }
  async type() { throw new Error('Paste-like CUA type must not write paragraph bodies'); }
  async pressSequentially(value, options) {
    this.tab.env.events.push(['pressSequentially',this.selector,value,options]); assert.equal(this.selector,'#body');
    assert.deepEqual(Object.keys(options),['timeoutMs']); assert.ok(Number.isInteger(options.timeoutMs)&&options.timeoutMs>0);
    assert.ok(!value.includes('\n')); this.tab.data.body+=value;
    if(this.tab.env.changeAccountAfterBodyText)this.tab.env.account='different-account';
  }
}
class Tab {
  constructor(env,id,url) { this.env=env; this.id=id; this.current=url; this.section=0; this.menu=false; this.playwright={
    locator:selector => new Locator(this,() => this.lookup(selector),selector),
    evaluate:async () => ({height:100,total:100,top:0,width:100})}; this.load(url); }
  load(url) { this.current=url; this.section=0;
    if(url===ORIGIN+'/account')this.guardAccount=this.env.account;
    const match=/\/edit\/(\d+)\?/.exec(url); if(match)this.data=this.env.editors.get(match[1]);
    const preview=/\/preview\/\d+&(\d+)$/.exec(url);
    this.preview=Boolean(preview);if(preview)this.data=this.env.editors.get(preview[1]);
  }
  async url() { return this.current; }
  async goto(url) { assert.notEqual(url,this.current,'same URL must explicitly reload'); this.env.events.push(['goto',url]);this.load(url);await this.env.afterNavigation?.(this,'goto',url); }
  async reload() { this.env.events.push(['reload',this.current]);this.load(this.current);await this.env.afterNavigation?.(this,'reload',this.current); }
  async getAXState(options) { assert.equal(options.emit,false);this.env.events.push(['ax',this.id]);await this.env.beforeAX?.(this);return 'visible page'; }
  async pasteText(value) {
    this.env.events.push(['pasteText',this.id,value]);assert.equal(typeof value,'string');assert.ok(this.focusedBody);
    const pasted=this.env.stripPastedTail?value.replace(/\n+$/,''):value;
    this.data.body=typeof this.env.pastedBody==='function'?this.env.pastedBody(pasted):pasted;
    if(this.env.changeAccountAfterBodyText)this.env.account='different-account';
    if(this.env.pasteFailure)throw this.env.pasteFailure;
  }
  async scroll(coords,direction,pages) { assert.ok(Array.isArray(coords) && coords.length===2);assert.ok(['up','down'].includes(direction));assert.ok(pages>=1);this.env.events.push(['scroll',coords]); }
  async close() {
    this.env.closed.push(this.id);
    if(this.env.closeEditorFailure&&this.current.includes('/edit/'))return;
    this.env.tabs.delete(this.id);
  }
  rows() { return [...this.env.editors.entries()].filter(([,item]) => item.scope===this.scope() && (item.section || 0)===this.section); }
  scope() { return this.current.endsWith('/chapters')?'non_drafts':'drafts'; }
  liveDocument() {
    const fields={},root=element('HTML'),doc={childNodes:[root],documentElement:root,fields,
      location:{href:this.documentURL?this.documentURL():this.current}};
    for(const selector of this.preview?['#preview-title','#preview-body']:['#chapter','#title','#body','#state']){
      const node=this.lookup(selector)[0];if(!node)continue;
      const dom=node.dom?node.dom():new Locator(this,()=>[node],selector).dom(node);
      fields[selector]=dom;root.childNodes.push(dom);
    }
    if(this.env.retargetChapter&&fields['#chapter']){
      const alternate={...fields['#chapter']};root.childNodes.push(alternate);fields['#chapter']=alternate;
    }
    const link=(node,parent)=>{node.parentNode=parent;node.ownerDocument=doc;node.isConnected=true;
      for(const child of node.childNodes||[])link(child,node);};
    link(root,doc);return doc;
  }
  lookup(selector) {
    const env=this.env; const plain=text => [{text}];
    if(this.current===ORIGIN+'/account') {
      if(selector==='#account'){env.accountReads=(env.accountReads||0)+1;return plain(this.guardAccount);}
      return [];
    }
    if(this.preview){
      if(selector==='#preview-title'){
        if((env.previewHeaderPending||0)>0){env.previewHeaderPending--;return plain('');}
        return plain(env.previewHeader ?? ('第'+this.data.chapter+'章 '+this.data.title));
      }
      if(selector==='#preview-body')return [{dom:()=>{
        if((env.previewBodyPending||0)>0){env.previewBodyPending--;return element('DIV');}
        if(env.invalidPreviewBody)return element('DIV',{},[element('P',{class:'unknown'},[textNode('正文')]),element('P')]);
        if(env.changeAccountOnReadBody||env.changeAccountOnPreviewBody)env.account='different-account';
        env.onPreviewBody?.();
        return previewDOM(typeof env.previewBody === 'function' ? env.previewBody(this.data.body) : this.data.body);
      }}];
      return [];
    }
    if(this.data) {
      if(selector==='#body')return [{dom:() => {
        const result=bodyDOM(typeof env.editorBody === 'function' ? env.editorBody(this.data.body) : this.data.body);
        let focused=this.focusedBody?(env.focusBodyChild?result.childNodes[0]:result):null;
        if(this.focusedBody&&env.focusBodyControl){
          focused=element(env.focusBodyControl==='noneditable'?'SPAN':env.focusBodyControl,
            env.focusBodyControl==='noneditable'?{contenteditable:'false'}:{});
          result.childNodes.push(focused);focused.parentNode=result;
        }
        result.ownerDocument={activeElement:focused,location:{href:this.documentURL?this.documentURL():this.current}};
        result.contains=node=>result.childNodes.includes(node);
        if(env.invalidEditorBody)result.childNodes.push(element('IMG'));
        if(env.bodyNotEditable){const getAttribute=result.getAttribute;result.getAttribute=key=>key==='contenteditable'?'false':getAttribute(key);}
        if(env.changeAccountOnReadBody)env.account='different-account';
        return result;
      }}];
      if(['#chapter','#title'].includes(selector)) {const key=selector.slice(1);return [{tag:'INPUT',value:() => String(this.data[key] ?? ''),set:value => {this.data[key]=key==='chapter'?Number(value):value;}}];}
      if(selector==='#save')return [{text:env.saveLabel || '存草稿',click:() => {
        env.saves++;this.data.saved=true;
        if(env.dropTrailingNewlinesOnSave)this.data.body=this.data.body.replace(/\n\n$/,'');
        if(env.saveFailure)throw env.saveFailure;
      }}];
      if(selector==='#saved')return plain(this.data.saved?'已保存到云端':'尚未保存');
      if(selector==='#state')return plain('草稿');
      if(selector==='#new')return env.newMarkerMissing?[]:plain(env.newMarker??'新建草稿');
      return [];
    }
    if(selector==='#scope')return plain(env.wrongDraftScope&&this.scope()==='drafts'?'non_drafts':this.scope());
    if(selector==='#filter')return plain(env.wrongDraftFilter&&this.scope()==='drafts'?'筛选结果':'全部');
    if(selector==='#ready')return env.emptyDraftTableOmitted&&this.scope()==='drafts'&&!this.rows().length?[]:plain(env.listReadyText??'ready');
    if(selector==='#empty')return env.missingEmptyMarker||(this.rows().length&&!env.forceEmptyMarker)?[]:[{text:'暂无草稿',visible:!env.hiddenEmptyMarker}];
    if(selector==='#count'){
      env.draftCountReads=(env.draftCountReads||0)+1;env.onDraftCountRead?.(env);
      return plain(env.draftCountText??('共'+this.rows().length+'篇草稿'));
    }
    if(selector==='#scroll')return env.emptyDraftTableOmitted&&this.scope()==='drafts'&&!this.rows().length?[]:[{metrics:() => ({height:100,total:100,width:100,wide:100,top:0,x:50,y:50})}];
    if(selector==='.virtual')return env.virtual?[{}]:[];
    if(selector==='.pagination')return env.pagination?[{}]:[];
    if(selector==='#sections')return [{click:() => {this.menu=!this.menu;}}];
    if(selector==='#section')return plain(env.sections[this.section]);
    if(selector==='.option')return this.menu?env.sections.map((text,index) => ({text,click:() => {this.section=index;this.menu=false;}})):[];
    if(selector==='.row')return this.rows().map(([remote,item]) => {
      const children={'.chapter':[{text:env.rowChapterRaw ?? (item.chapter?'第'+item.chapter+'章 '+item.title:'未命名草稿')}],
        '.state':[{text:env.rowState ?? item.state ?? '已发布'}],
        '.link':[{attrs:env.missingLinkAttribute?{}:{href:env.rowURL ?? ORIGIN+'/book/10/edit/'+remote+'?enter_from='+(item.scope==='drafts'?'modifydraft':'modifychapter')}}]};
      if(env.rowChapterSelector)children[env.rowChapterSelector]=children['.chapter'];
      if(env.duplicateRowField)children[env.duplicateRowField].push(children[env.duplicateRowField][0]);
      return {children};
    });
    if(selector==='#create') {
      if(env.changeAccountBeforeCreateClick)env.account='different-account';
      return [{text:'新建草稿',attrs:{href:ORIGIN+'/book/10/edit?enter_from=newdraft'},click:() => {
      env.creates++;if(env.concurrent)env.add('https://example.com/user-tab');
      if(env.concurrentOldDraft)env.add(ORIGIN+'/book/10/edit/11?enter_from=modifydraft');
      env.editors.set('99',{scope:'drafts',chapter:null,title:'',body:'',saved:false,...env.createdData});
      env.add(ORIGIN+'/book/10/edit/99?enter_from=newdraft');
      if(env.concurrentTarget)env.add(ORIGIN+'/book/10/edit/98?enter_from=newdraft');
      if(env.changeAccountOnCreate)env.account='different-account';
      if(env.createClickFailure)throw env.createClickFailure;
    }}];
    }
    return [];
  }
}
function environment(options = {}) {
  const env={events:[],closed:[],tabs:new Map(),editors:new Map([['11',{scope:'drafts',chapter:11,title:'原稿',body:'原始正文',saved:true}],['1',{scope:'non_drafts',chapter:1,title:'正式章',body:'正式',saved:true}]]),
    account:'stable-id',sections:['第一卷'],creates:0,saves:0,...options};
  let next=0;env.add=url => {const tab=new Tab(env,String(++next),url);if(env.missingPasteSupport)tab.pasteText=undefined;env.tabs.set(tab.id,tab);return tab;};
  env.add('https://example.com/already-open');
  env.api={listTabs:async () => [...env.tabs.values()].map(tab => ({id:tab.id,url:tab.current})),
    getTab:async id => {env.events.push(['getTab',id]);return env.tabs.get(id);},newTab:async url => {env.events.push(['newTab',url]);return env.add(url);}};
  return env;
}
let session=0;
function request(operation,params={},sid=String(++session),config=profile()) {
  return {schema:1,id:operation.replaceAll('_','')+sid,nonce:'nonce',session_id:sid,binding_sha256:'binding',
    binding:{profile_sha256:'profile',target:{platform:'fanqie',account_id:'stable-id',remote_book_id:'10'}},
    profile:config,operation,params,deadline:new Date(Date.now()+10000).toISOString()};
}
async function verified(env,config=profile()) { const req=request('verify_target',{},undefined,config); const result=await executeChromeBridgeRequest(env.api,req);assert.equal(result.ok,true,JSON.stringify(result));return req; }
const next=(base,operation,params={}) => ({...base,id:base.id+operation,operation,params});
const MANAGEMENT='https://fanqienovel.com/main/writer/chapter-manage/10&test';
const MANAGEMENT_COVERAGE='[role=tab][aria-selected=true] .arco-tabs-header-title-text';
function managementFixture(options={}) {
  const config=profile();config.urls.drafts=MANAGEMENT+'?type=2';config.urls.create=config.urls.drafts;
  config.identity.book.templates.push(...[1,2].map(type=>ORIGIN+'/main/writer/chapter-manage/{book_id}&test?type='+type));
  config.chapter_lists.reverse();
  for(const listing of config.chapter_lists){const type=listing.scope==='drafts'?2:1;
    listing.url=MANAGEMENT+'?type='+type;
    listing.coverage=field(MANAGEMENT_COVERAGE,{expected:type===1?'章节管理':'草稿箱'});}
  const env=environment(options),add=env.add;
  env.add=url=>{
    const tab=add(url);if(!url.startsWith(MANAGEMENT+'?type='))return tab;
    const lookup=tab.lookup.bind(tab);
    tab.scope=()=>tab.current.endsWith('?type=1')?'non_drafts':'drafts';
    tab.lookup=selector=>selector===MANAGEMENT_COVERAGE?[{text:env.managementCoverage??(tab.scope()==='non_drafts'?'章节管理':'草稿箱'),
      visible:env.managementCoverageVisible!==false}]:lookup(selector);
    tab.playwright.getByRole=(role,settings)=>{
      env.events.push(['getByRole',role,settings]);assert.equal(role,'tab');assert.equal(settings.exact,true);
      const target=config.chapter_lists.find(listing=>listing.coverage.expected===settings.name);
      return new Locator(tab,()=>target?Array.from({length:env.managementRoleCount??1},()=>({
        text:settings.name,visible:!env.managementHidden,enabled:!env.managementDisabled,
        attrs:{'aria-selected':env.managementAlreadySelected?'true':String(tab.current===target.url)},
        click:async()=>{
          env.events.push(['managementClick',settings.name]);if(env.managementClickFailure)throw env.managementClickFailure;
          const source=tab.current;if(!env.managementStaySource)tab.load(target.url);
          await env.afterManagementClick?.(tab,source,target.url);
        }})):[],'role=tab:'+settings.name);
    };
    return tab;
  };
  return {env,config};
}
function cacheManagementRows(env) {
  const add=env.add;env.catalogRefreshes=0;
  env.add=url=>{
    const tab=add(url);if(!url.startsWith(MANAGEMENT+'?type='))return tab;
    const snapshot=()=>new Map([...env.editors].map(([remote,data])=>[remote,{...data}]));
    let cached=snapshot();
    tab.rows=()=>[...cached].filter(([,item])=>item.scope===tab.scope()&&(item.section||0)===tab.section);
    const reload=tab.reload.bind(tab);
    tab.reload=async()=>{
      await reload();cached=snapshot();env.catalogRefreshes++;
      await env.afterCatalogRefresh?.(tab,cached);
    };
    return tab;
  };
}

test('new owned draft confirmations refresh a cached management catalog before and after the single save',async()=>{
  const {env,config}=managementFixture();cacheManagementRows(env);
  let freshBeforeInput=0,freshAfterSave=0;
  env.afterCatalogRefresh=async()=>{if(env.creates){if(env.saves)freshAfterSave++;else freshBeforeInput++;}};
  const base=await verified(env,config);env.events=[];
  const body='甲段\n乙段\n';
  const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.result,'99');
  assert.equal(env.creates,1);assert.equal(env.saves,1);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
  assert.equal(env.editors.get('99').body,body);assert.equal(env.editors.get('99').chapter,12);
  assert.equal(freshBeforeInput,2,'both editor-state checks observe the cloud allocation through a new catalog load');
  assert.equal(freshAfterSave,2,'independent preview confirmations observe the saved chapter through new catalog loads');
  assert.ok(env.events.some(row=>row[0]==='managementClick'));
  assert.ok(!env.events.some(row=>row[0]==='goto'&&row[1].startsWith(MANAGEMENT)));
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  assert.ok(env.tabs.has('1'),'pre-existing user tab is not touched');
});

test('ordinary find and independent read keep their current management navigation without proactive creation refreshes',async()=>{
  const {env,config}=managementFixture();cacheManagementRows(env);const base=await verified(env,config);
  env.catalogRefreshes=0;env.events=[];
  assert.deepEqual((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).result,[]);
  const read=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));assert.equal(read.ok,true,JSON.stringify(read));
  assert.equal(env.catalogRefreshes,0);assert.equal(env.creates,0);assert.equal(env.saves,0);
});

test('configured new-draft marker rejects an existing blank chapter before any input and never retries allocation',async()=>{
  const env=environment({createdData:{chapter:11,saved:true},newMarker:'已有草稿'}),config=profile();
  config.editor.new=field('#new',{expected:'新建草稿'});
  config.editor.state=field('#state',{expected:'草稿'});
  const base=await verified(env,config),create=next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'});
  const result=await executeChromeBridgeRequest(env.api,create);
  assert.equal(result.error,'platform_browser_not_new_draft');assert.equal(env.creates,1);assert.equal(env.saves,0);
  assert.equal(env.editors.get('99').chapter,11);assert.equal(env.editors.get('99').body,'');
  assert.ok(!env.events.some(row=>['fill','pasteText'].includes(row[0])));
  assert.equal((await executeChromeBridgeRequest(env.api,create)).error,'platform_browser_operation_failed');
  assert.equal(env.creates,1);
});

test('missing configured new marker expires without filling or saving; exact marker allows one normal save',async()=>{
  const config=profile();config.editor.new=field('#new',{expected:'新建草稿'});
  const env=environment({newMarkerMissing:true}),base=await verified(env,config);
  const now=Date.now;let clock=now();const create=next(base,'create_draft',{chapter:12,title:'新草稿',body:'正文'});
  create.deadline=new Date(clock+1000).toISOString();env.beforeAX=async()=>{if(env.newMarkerChecks)clock+=500;};
  Date.now=()=>clock;
  try{
    const result=await executeChromeBridgeRequest(env.api,create);
    assert.equal(result.error,'platform_browser_operation_failed');assert.equal(env.saves,0);assert.equal(env.creates,1);
    assert.ok(env.newMarkerChecks>0);
    assert.ok(!env.events.some(row=>['fill','pasteText'].includes(row[0])));
  }finally{Date.now=now;}
  const ready=environment(),verifiedReady=await verified(ready,config);
  const result=await executeChromeBridgeRequest(ready.api,next(verifiedReady,'create_draft',{chapter:12,title:'新草稿',body:'正文'}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(ready.saves,1);assert.equal(ready.creates,1);
});

test('creation catalog refresh failures never replay allocation or accept an old matching URL and DOM',async()=>{
  for(const failure of [Error('Timed out after 10000ms waiting for CDP command Page.navigate.'),
    Object.assign(Error('PRIVATE authorization refusal'),{code:'permission_denied'}),Error('Debugger unattached')]){
    const {env,config}=managementFixture();cacheManagementRows(env);const base=await verified(env,config);let attempts=0;
    env.afterCatalogRefresh=async()=>{if(env.creates){attempts++;throw failure;}};
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'}));
    assert.equal(result.ok,false);assert.equal(result.error,'platform_browser_operation_failed');assert.equal(attempts,1);
    assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,0);
    assert.equal(env.events.filter(row=>row[0]==='fill').length,0);assert.equal(env.editors.get('99').body,'');
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'}))).ok,false);
    assert.equal(env.creates,1,'the consumed create request stays consumed after a fresh-read failure');
  }
});

test('a fresh create catalog still rejects a missing remote wrong state changed account route scope or incomplete pages',async()=>{
  for(const mode of ['missing','state','account','route','scope','virtual','unnumbered']){
    const {env,config}=managementFixture();cacheManagementRows(env);
    if(mode==='state')config.chapter_lists.find(listing=>listing.scope==='drafts').state=field('.state');
    const base=await verified(env,config);
    env.afterCatalogRefresh=async(tab,cached)=>{
      if(!env.creates)return;
      if(mode==='missing')cached.delete('99');
      if(mode==='state')cached.get('99').state='已发布';
      if(mode==='account')env.account='different-account';
      if(mode==='route')tab.current=MANAGEMENT.replace('/10&','/11&')+'?type=2';
      if(mode==='scope')env.wrongDraftFilter=true;
      if(mode==='virtual')env.virtual=true;
      if(mode==='unnumbered')cached.set('100',{scope:'drafts',chapter:null,title:'',body:''});
    };
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'}));
    const expected={missing:'not_draft',state:'not_draft',account:'identity_mismatch',route:'identity_mismatch',
      scope:'incomplete_scope',virtual:'incomplete_list',unnumbered:'unnumbered_draft'};
    assert.equal(result.error,'platform_browser_'+expected[mode],mode);assert.equal(env.creates,1);assert.equal(env.saves,0);
    assert.equal(env.events.filter(row=>row[0]==='fill'||row[0]==='pasteText').length,0,mode);
  }
});

test('a create catalog refresh observes the original deadline before any title or body input',async()=>{
  const {env,config}=managementFixture();cacheManagementRows(env);const base=await verified(env,config);
  const now=Date.now;let clock=now();Date.now=()=>clock;
  const create=next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'});
  create.deadline=new Date(clock+1000).toISOString();
  env.afterCatalogRefresh=async()=>{if(env.creates)clock+=1000;};
  try{
    const result=await executeChromeBridgeRequest(env.api,create);assert.equal(result.error,'platform_browser_operation_failed');
    assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal(env.events.filter(row=>row[0]==='fill'||row[0]==='pasteText').length,0);
  }finally{Date.now=now;}
});

test('actual observed paragraph samples and exact AI sentinel preserve content',() => {
  const data=JSON.parse(readFileSync(new URL('../benchmarks/results/development-20261019-platform-drafts/fanqie-richtext-observations.json',import.meta.url)));
  for(const sample of data.observations)assert.deepEqual(readParagraphs(parse(sample.dom)),{ok:true,value:sample.plain_text});
  for(const value of ['\n中文甲\n\n中文乙\n\n','  空格　\n末尾  ','\n\n'])assert.deepEqual(readParagraphs(bodyDOM(value)),{ok:true,value});
  const ai=data.observations.find(sample=>sample.dom.includes('syl-inline')).dom;
  for(const changed of [ai.replace('AI帮你构思剧情','AI帮你修改正文'),ai.replace('ignoreel="true"','ignoreel="false"'),
    ai.replace('contenteditable="false"','contenteditable="true"'),ai.replace('</i>','实际文字</i>'),
    ai.replace('</syl-inline>','</syl-inline>实际文字'),ai.replace('alt=""','alt="实际文字"'),ai.replace('<syl-inline ','<syl-inline data-unknown="true" '),ai+'<p><br class="ProseMirror-trailingBreak"></p>'])assert.deepEqual(readParagraphs(parse(changed)),{ok:false});
});
test('complete existing-Chrome verify find create read flow uses native paragraphs and fresh readback',async () => {
  const env=environment();const base=await verified(env);
  assert.deepEqual((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).result,[]);
  const body='\n中文甲\n\n中文乙\n\n';
  const created=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收章',body}));
  assert.equal(created.ok,true,JSON.stringify(created));assert.equal(created.result,'99');assert.equal(env.creates,1);assert.equal(env.saves,1);
  const observed=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'99'}));
  assert.equal(observed.result.body,body);assert.equal(observed.result.title,'验收章');
  assert.deepEqual(env.events.filter(row=>row[0]==='pasteText').map(row=>row[2]),[body.replace(/\n+$/,'')]);
  assert.equal(env.events.filter(row=>row[0]==='press'&&row[2]==='Enter').length,2);
  assert.ok(!env.events.some(row=>row[0]==='pressSequentially'));
  assert.ok(env.events.some(row=>row[0]==='reload'));assert.ok(env.events.some(row=>row[0]==='scroll'));
  await closeChromeBridgeSession(env.api,base.session_id);assert.ok(env.tabs.has('1'),'pre-existing user tab survives');
  assert.equal(env.tabs.size,2,'latest readback remains');
});
test('bulk body paste preserves encoded characters whitespace and boundary newlines with one paste independent of paragraph count',async()=>{
  const bodies=['  甲　 \n乙  ','\n甲\n\n乙\n\n','甲\r\n乙\r丙\n',
    Array.from({length:140},(_,index)=>'第'+index+'段正文  ').join('\n')];
  for(const [index,body] of bodies.entries()){
    const env=environment({focusBodyChild:index===1,stripPastedTail:true}),base=await verified(env);
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'粘贴验收',body}));
    const normalized=body.replace(/\r\n/g,'\n').replace(/\r/g,'\n');
    assert.equal(result.ok,true,JSON.stringify(result));assert.equal(env.editors.get('99').body,normalized);
    const tail=/\n*$/.exec(normalized)[0].length;
    assert.deepEqual(env.events.filter(row=>row[0]==='pasteText').map(row=>row[2]),[normalized.slice(0,normalized.length-tail)]);
    assert.deepEqual(env.events.filter(row=>row[0]==='press').map(row=>row[2]),['Meta+A','Backspace',...Array(tail).fill('Enter')]);
    assert.ok(!env.events.some(row=>row[0]==='pressSequentially'));
    assert.equal(env.creates,1);assert.equal(env.saves,1);
    assert.ok(env.events.some(row=>row[0]==='newTab'&&row[1]===ORIGIN+'/preview/10&99'),'independent preview remains required');
  }
});
test('bulk paste requires empty serialization supported input and body focus before sending any body text',async()=>{
  for(const [options,code] of [[{clearBodyResult:'残留正文'},'editor_changed'],[{loseBodyFocusAfterClear:true},'editor_changed'],
    [{bodyNotEditable:true},'unsupported_editor'],[{missingPasteSupport:true},'unsupported_editor']]){
    const env=environment(options),base=await verified(env);
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文'}));
    assert.equal(result.error,'platform_browser_'+code);assert.equal(env.creates,1);assert.equal(env.saves,0);
    assert.ok(!env.events.some(row=>row[0]==='pasteText'||row[0]==='pressSequentially'||row[0]==='press'&&row[2]==='Enter'));
    if(options.clearBodyResult)assert.equal(env.editors.get('99').body,options.clearBodyResult);
    assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/preview/')));
  }
});
test('all-LF bodies retain their original input plan without a clipboard call or required paste support',async()=>{
  for(const body of ['\n','\n\n','\r\n\r\n']){
    const env=environment({missingPasteSupport:true}),base=await verified(env),normalized=body.replace(/\r\n/g,'\n');
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body}));
    assert.equal(result.ok,true,JSON.stringify(result));assert.equal(env.editors.get('99').body,normalized);
    assert.equal(env.events.filter(row=>row[0]==='pasteText'||row[0]==='pressSequentially').length,0);
    assert.deepEqual(env.events.filter(row=>row[0]==='press').map(row=>row[2]),['Meta+A','Backspace',...Array(normalized.length).fill('Enter')]);
    assert.equal(env.creates,1);assert.equal(env.saves,1);
  }
});
test('a planned trailing Enter failure stops without adding another Enter or repeating the successful core paste',async()=>{
  const original=Error('PRIVATE planned Enter interrupted'),env=environment({enterFailure:original}),base=await verified(env);
  let observed;env.api.onDriverError=failure=>{observed=failure;};
  const req=next(base,'create_draft',{chapter:12,title:'验收',body:'完整核心\n\n'});
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(observed,original);
  assert.equal(env.editors.get('99').body,'完整核心\n');assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
  assert.equal(env.events.filter(row=>row[0]==='press'&&row[2]==='Enter').length,1);assert.equal(env.saves,0);
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
  assert.equal(env.creates,1);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
  assert.equal(env.events.filter(row=>row[0]==='press'&&row[2]==='Enter').length,1);
});
test('one partial failing paste or changed serialization stops before save and cannot replay create or paste',async()=>{
  for(const mode of ['throws','partial','extra_newline','changed_spaces']){
    const original=Error('PRIVATE paste interrupted'),body='甲段\n乙段  \n\n';
    const env=environment({pastedBody:value=>mode==='extra_newline'?value+'\n':mode==='changed_spaces'?value.trim():value.slice(0,2),
      ...(mode==='throws'?{pasteFailure:original}:{})});
    const base=request('verify_target');base.binding.comparison_policy='fanqie_terminal_lf_v1';
    assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);let observed;env.api.onDriverError=failure=>{observed=failure;};
    const req=next(base,'create_draft',{chapter:12,title:'验收',body});
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_'+(mode==='throws'?'operation_failed':'editor_changed'));
    if(mode==='throws')assert.equal(observed,original);
    assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
    assert.ok(!env.events.some(row=>row[0]==='pressSequentially'||row[0]==='press'&&row[2]==='Enter'));
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
    assert.equal(env.creates,1);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
  }
});
test('observed empty Fanqie draft box with no table spinner or scroll container is positively verified',async()=>{
  const env=environment({emptyDraftTableOmitted:true});env.editors.delete('11');
  const base=await verified(env);env.events=[];
  const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.deepEqual(result.result,[]);
  assert.ok(env.draftCountReads>=6,'all three catalog checkpoints repeat the positive zero-count observation');
  assert.equal(env.creates,0);assert.equal(env.saves,0);
  const created=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'首篇草稿',body:'正式正文'}));
  assert.equal(created.ok,true,JSON.stringify(created));assert.equal(created.result,'99');
  assert.equal(env.creates,1);assert.equal(env.saves,1);
  assert.ok(env.events.some(row=>row[0]==='scroll'),'nonempty saved draft uses the original table completeness checks');
});
test('empty Fanqie catalog rejects absent ambiguous malformed or nonzero count and contradictory rows',async()=>{
  const cases=[
    [{alwaysMissing:'#count'},'incomplete_list'],[{duplicateSelector:'#count'},'ambiguous_dom'],
    [{draftCountText:'共1篇草稿'},'incomplete_list'],[{draftCountText:'共00篇草稿'},'incomplete_list'],
    [{draftCountText:'共0篇草稿 '},'incomplete_list'],[{draftCountText:'0'},'incomplete_list'],
    [{forceEmptyMarker:true,keepDraft:true},'incomplete_list']
  ];
  for(const [options,code] of cases){
    const env=environment({emptyDraftTableOmitted:true,...options});if(!options.keepDraft)env.editors.delete('11');
    const base=await verified(env);const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(result.error,'platform_browser_'+code,JSON.stringify(options));
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
  const config=profile();delete config.chapter_lists[0].count;
  const env=environment({emptyDraftTableOmitted:true});env.editors.delete('11');const base=await verified(env,config);
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_incomplete_list');
});
test('empty Fanqie catalog requires unique visible marker and rejects pagination or virtualization',async()=>{
  for(const [options,code] of [[{duplicateSelector:'#empty'},'ambiguous_dom'],[{virtual:true},'incomplete_list'],[{pagination:true},'incomplete_list']]){
    const env=environment({emptyDraftTableOmitted:true,...options});env.editors.delete('11');const base=await verified(env);
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_'+code);
  }
  for(const options of [{alwaysMissing:'#empty'},{hiddenEmptyMarker:true},{listReadyText:'loading'}]){
    const env=environment({...options,emptyDraftTableOmitted:!options.listReadyText});env.editors.delete('11');const base=await verified(env);
    const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(Date.now()+125).toISOString();
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed',JSON.stringify(options));
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('empty Fanqie catalog repeats count identity scope and filters before accepting zero rows',async()=>{
  const cases=[
    [env=>{env.draftCountText='共1篇草稿';},'incomplete_list'],
    [env=>{env.account='different-account';},'identity_mismatch'],
    [env=>{env.wrongDraftScope=true;},'incomplete_scope'],
    [env=>{env.wrongDraftFilter=true;},'incomplete_scope']
  ];
  for(const [change,code] of cases){
    const env=environment({emptyDraftTableOmitted:true,onDraftCountRead:state=>{if(state.draftCountReads===3)change(state);}});
    env.editors.delete('11');const base=await verified(env);
    if(code==='identity_mismatch')env.onDraftCountRead=state=>{
      if(state.draftCountReads===3){change(state);for(const tab of state.tabs.values())if(tab.current===ORIGIN+'/account')tab.guardAccount=state.account;}
    };
    const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(result.error,'platform_browser_'+code);assert.ok(env.draftCountReads>=3);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('a disappearing empty marker cannot fall back to a ready zero-row table at later checkpoints',async()=>{
  for(const pass of [3,5]){
    const env=environment({emptyDraftTableOmitted:true,onDraftCountRead:state=>{
      if(state.draftCountReads===pass*3){state.missingEmptyMarker=true;state.emptyDraftTableOmitted=false;}
    }});
    env.editors.delete('11');const base=await verified(env);
    const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(result.error,'platform_browser_incomplete_list');assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('absence of a spinner while the empty marker is still rendering remains pollable',async()=>{
  const env=environment({emptyDraftTableOmitted:true,delayCounts:{'#empty':2}});env.editors.delete('11');
  const base=await verified(env);const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.deepEqual(result.result,[]);assert.equal(env.delayCounts['#empty'],0);
});
test('popup concurrent unrelated and old-draft user tabs are never claimed, inspected or closed',async () => {
  const env=environment({concurrent:true,concurrentOldDraft:true});const base=await verified(env);
  const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
  assert.equal(response.ok,true,JSON.stringify(response));
  const unrelated=[...env.tabs.values()].find(tab=>tab.current==='https://example.com/user-tab');
  assert.ok(unrelated);assert.ok(!env.events.some(row=>row[0]==='getTab'&&row[1]===unrelated.id));
  assert.ok(!env.events.some(row=>row[0]==='ax'&&row[1]===unrelated.id));
  const old=[...env.tabs.values()].find(tab=>tab.current===ORIGIN+'/book/10/edit/11?enter_from=modifydraft');
  assert.ok(!env.events.some(row=>['getTab','ax'].includes(row[0])&&row[1]===old.id));
  await closeChromeBridgeSession(env.api,base.session_id);assert.ok(env.tabs.has(unrelated.id));
  assert.ok(env.tabs.has(old.id));
});
test('same create request is consumed once even after failure immediately after blank cloud allocation',async () => {
  const env=environment({changeAccountOnCreate:true});const base=await verified(env);
  const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
  const first=await executeChromeBridgeRequest(env.api,req);assert.equal(first.error,'platform_browser_identity_mismatch');
  env.account='stable-id';const second=await executeChromeBridgeRequest(env.api,req);
  assert.equal(second.error,'platform_browser_operation_failed');assert.equal(env.creates,1);assert.equal(env.saves,0);
  assert.equal(env.events.filter(row=>['fill','pressSequentially'].includes(row[0])).length,0);
});
test('unknown unnumbered existing draft blocks find and virtualized listing blocks completeness',async () => {
  for(const kind of ['unnumbered','virtual']){
    const env=environment({virtual:kind==='virtual'});if(kind==='unnumbered')env.editors.set('22',{scope:'drafts',chapter:null,title:'',body:''});
    const base=await verified(env);const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(result.error,'platform_browser_'+(kind==='virtual'?'incomplete_list':'unnumbered_draft'));assert.equal(env.creates,0);
  }
});
test('deadlines, unverified operations and changed session binding refuse UI work',async () => {
  const env=environment();let req=request('create_draft',{chapter:12,title:'验收',body:'正文'});
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_target_unverified');
  req=request('verify_target');req.deadline='2000-01-01T00:00:00Z';
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
  const base=await verified(env);const events=env.events.length;
  assert.equal((await executeChromeBridgeRequest(env.api,{...next(base,'find_drafts',{chapter:12}),binding_sha256:'changed'})).error,'platform_browser_identity_mismatch');
  assert.equal(env.events.length,events);
});
test('observed read-only navigation acknowledgement timeouts accept an already arrived page without navigation replay',async()=>{
  const messages=[url=>'Timed out after 10000ms waiting for CDP command Page.navigate.',
    url=>'Timed out waiting for tab 123456 to navigate to '+url+'.'];
  for(const destination of [ORIGIN+'/book/10/chapters']){
    for(const message of messages){
      const env=environment(),base=await verified(env);let timeouts=0;env.events=[];
      env.afterNavigation=async(tab,kind,url)=>{
        if(url!==destination||timeouts)return;
        timeouts++;env.events.push(['navigationTimeout',tab.id]);throw Error(message(url));
      };
      const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
      assert.equal(result.ok,true,JSON.stringify(result));assert.deepEqual(result.result,[]);assert.equal(timeouts,1);
      const index=env.events.findIndex(row=>row[0]==='navigationTimeout');
      assert.deepEqual(env.events[index+1],['ax',env.events[index][1]],'recovery reads the existing page rather than navigating again');
      assert.equal(env.events.filter(row=>['goto','reload'].includes(row[0])&&row[1]===destination).length,1);
      assert.equal(env.creates,0);assert.equal(env.saves,0);
    }
  }
});
test('observed management tab switches use one exact frozen role click per direction and no goto reload or new page',async()=>{
  const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];
  const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(response.ok,true,JSON.stringify(response));assert.deepEqual(response.result,[]);
  assert.deepEqual(env.events.filter(row=>row[0]==='managementClick').map(row=>row[1]),['章节管理','草稿箱']);
  assert.deepEqual(env.events.filter(row=>row[0]==='getByRole').map(row=>row.slice(1)),[
    ['tab',{name:'章节管理',exact:true}],['tab',{name:'草稿箱',exact:true}]]);
  assert.ok(!env.events.some(row=>row[0]==='goto'||row[0]==='newTab'||row[0]==='reload'&&row[1].startsWith(MANAGEMENT)));
  assert.equal(env.creates,0);assert.equal(env.saves,0);assert.ok(env.accountReads>1);
});
test('management tab arrival waits for delayed URL and initially empty AX with a single click and no navigation fallback',async()=>{
  for(const mode of ['url','ax']){
    const {env,config}=managementFixture();let clicks=0,pending=0;
    env.afterManagementClick=async(tab,source,target)=>{
      if(clicks++)return;
      if(mode==='url'){const read=tab.url.bind(tab);tab.url=async()=>++pending<=2?source:read();}
      else{const read=tab.getAXState.bind(tab);tab.getAXState=async options=>++pending<=2?'':read(options);}
    };
    const base=await verified(env,config);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.ok(pending>=3);
    assert.equal(env.events.filter(row=>row[0]==='managementClick').length,2);
    assert.ok(!env.events.some(row=>row[0]==='goto'||row[0]==='newTab'));assert.equal(env.creates,0);
  }
});
test('management source and target coverage can become uniquely visible after absent hidden or empty loading states',async()=>{
  for(const stage of ['source','target'])for(const mode of ['absent','hidden','empty']){
    const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];let reads=0;
    env.countOverrides={};
    env.beforeLocatorCount=async(tab,selector)=>{
      if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith(stage==='source'?'?type=2':'?type=1'))return;
      const loading=++reads<=2;
      if(mode==='absent'&&loading)env.countOverrides[selector]=0;else delete env.countOverrides[selector];
      env.managementCoverageVisible=!(mode==='hidden'&&loading);
      env.managementCoverage=mode==='empty'&&loading?'':undefined;
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,true,stage+'/'+mode+': '+JSON.stringify(response));assert.ok(reads>=3);
    assert.equal(env.events.filter(row=>row[0]==='managementClick').length,2);
    assert.ok(!env.events.some(row=>['goto','newTab'].includes(row[0])||row[0]==='reload'&&row[1].startsWith(MANAGEMENT)));
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('the source recheck waits for a temporary rerender without clicking twice or replacing the directory page',async()=>{
  const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];let reads=0;
  env.countOverrides={};
  env.beforeLocatorCount=async(tab,selector)=>{
    if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith('?type=2'))return;
    if([2,3].includes(++reads))env.countOverrides[selector]=0;else delete env.countOverrides[selector];
  };
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,true);
  assert.ok(reads>=4);assert.equal(env.events.filter(row=>row[0]==='managementClick').length,2);
  assert.ok(!env.events.some(row=>['goto','newTab'].includes(row[0])||row[0]==='reload'&&row[1].startsWith(MANAGEMENT)));
});
test('create waits for source coverage after its real fresh catalog reload while allocation paste and save stay single',async()=>{
  const {env,config}=managementFixture();cacheManagementRows(env);const base=await verified(env,config);
  let loading=0,waits=0;env.countOverrides={};
  env.afterCatalogRefresh=async()=>{if(env.creates)loading=2;};
  env.beforeLocatorCount=async(tab,selector)=>{
    if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith('?type=2'))return;
    if(loading>0){loading--;waits++;env.countOverrides[selector]=0;}else delete env.countOverrides[selector];
  };
  const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文\n'}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.ok(waits>=2);
  assert.equal(env.creates,1);assert.equal(env.saves,1);assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
});
test('coverage waiting refuses duplicate nonempty wrong URL and SDK errors immediately before another action',async()=>{
  for(const stage of ['source','target'])for(const mode of ['duplicate','wrong','spaces','book','route','login','foreign','permission','disconnected','url_error']){
    const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];let reads=0,observed;
    const original=Object.assign(Error('PRIVATE coverage read refused'),{code:mode==='permission'?'permission_denied':'disconnected'});
    env.api.onDriverError=failure=>{observed=failure;};env.countOverrides={};
    env.beforeLocatorCount=async(tab,selector)=>{
      if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith(stage==='source'?'?type=2':'?type=1'))return;
      reads++;
      if(mode==='duplicate')env.countOverrides[selector]=2;
      if(mode==='wrong'||mode==='spaces')env.managementCoverage=mode==='spaces'?' ':'其它范围';
      if(mode==='book')tab.current=MANAGEMENT.replace('/10&','/11&')+'?type=2';
      if(mode==='route')tab.current=ORIGIN+'/different';
      if(mode==='login')tab.current=ORIGIN+'/login';
      if(mode==='foreign')tab.current='https://example.com/';
      if(['permission','disconnected'].includes(mode))throw original;
      if(mode==='url_error')tab.url=async()=>{throw original;};
    };
    const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(result.ok,false,stage+'/'+mode);assert.equal(reads,1,stage+'/'+mode);
    if(['permission','disconnected','url_error'].includes(mode))assert.equal(observed,original);
    assert.equal(env.events.filter(row=>row[0]==='managementClick').length,stage==='source'?0:1,stage+'/'+mode);
    assert.ok(!env.events.some(row=>['goto','reload','newTab'].includes(row[0])));assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('source and target loading retain one five-second budget and the original request deadline',async()=>{
  const now=Date.now;
  try{
    for(const stage of ['source','target'])for(const short of [false,true]){
      let clock=now();Date.now=()=>clock;
      const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];let reads=0;env.countOverrides={};
      env.beforeLocatorCount=async(tab,selector)=>{
        if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith(stage==='source'?'?type=2':'?type=1'))return;
        reads++;clock+=short?250:2500;env.countOverrides[selector]=0;
      };
      const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(clock+(short?250:60000)).toISOString();
      const result=await executeChromeBridgeRequest(env.api,req);
      assert.equal(result.error,'platform_browser_'+(short?'operation_failed':stage==='source'?'incomplete_scope':'navigation_timeout'));
      assert.equal(reads,short?1:2);assert.equal(env.events.filter(row=>row[0]==='managementClick').length,stage==='source'?0:1);
      assert.ok(!env.events.some(row=>['goto','reload','newTab'].includes(row[0])));
    }
  }finally{Date.now=now;}
});
test('the second pre-click source check cannot reset the first source readiness budget',async()=>{
  const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];env.countOverrides={};
  const now=Date.now;let clock=now(),reads=0;Date.now=()=>clock;
  const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(clock+60000).toISOString();
  env.beforeLocatorCount=async(tab,selector)=>{
    if(selector!==MANAGEMENT_COVERAGE||!tab.current.endsWith('?type=2'))return;
    reads++;
    if(reads===1){clock+=3000;env.countOverrides[selector]=0;}
    else if(reads===3){clock+=2000;env.countOverrides[selector]=0;}
    else delete env.countOverrides[selector];
  };
  try{
    const result=await executeChromeBridgeRequest(env.api,req);assert.equal(result.error,'platform_browser_incomplete_scope');
    assert.equal(reads,3);assert.equal(env.events.filter(row=>row[0]==='managementClick').length,0);
  }finally{Date.now=now;}
});
test('matching coverage returned after its awaited read cannot exceed the original request deadline',async()=>{
  const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];
  const now=Date.now;let clock=now();Date.now=()=>clock;
  const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(clock+500).toISOString();
  env.afterLocatorEvaluate=async(_tab,selector)=>{if(selector===MANAGEMENT_COVERAGE)clock+=500;};
  try{
    const result=await executeChromeBridgeRequest(env.api,req);assert.equal(result.error,'platform_browser_operation_failed');
    assert.equal(env.events.filter(row=>row[0]==='managementClick').length,0);
  }finally{Date.now=now;}
});
test('invalid frozen management mapping or selected source refuses before click and never falls back to goto',async()=>{
  for(const mode of ['duplicate','wrong_label','wrong_scope','attribute','source_selected']){
    const {env,config}=managementFixture();
    if(mode==='duplicate')config.chapter_lists.push(structuredClone(config.chapter_lists[0]));
    if(mode==='wrong_label')config.chapter_lists[0].coverage.expected='其它页面';
    if(mode==='wrong_scope')config.chapter_lists[0].scope='drafts';
    if(mode==='attribute')config.chapter_lists[0].coverage.attribute='title';
    if(mode==='source_selected')env.managementCoverage='章节管理';
    if(mode==='wrong_scope')config.chapter_lists.push({...structuredClone(config.chapter_lists[0]),url:MANAGEMENT+'?other=1',scope:'non_drafts'});
    const base=await verified(env,config);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,false);assert.ok(!env.events.some(row=>['managementClick','goto','newTab'].includes(row[0])));
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('missing duplicate hidden disabled selected or unavailable target role is terminal without click goto or new tab',async()=>{
  for(const options of [{managementRoleCount:0},{managementRoleCount:2},{managementHidden:true},{managementDisabled:true},
    {managementAlreadySelected:true},{missingRoleAPI:true}]){
    const {env,config}=managementFixture(options),base=await verified(env,config);env.events=[];
    if(options.missingRoleAPI)[...env.tabs.values()].find(tab=>tab.current.startsWith(MANAGEMENT)).playwright.getByRole=undefined;
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,false);assert.ok(!env.events.some(row=>['managementClick','goto','newTab'].includes(row[0])));
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('management click or post-click permission URL and DOM failures never click again or fall back to navigation',async()=>{
  for(const mode of ['click','permission','url','ax','other_route','coverage','nonstring_ax']){
    const original=Error(mode==='permission'?'PRIVATE origin approval gate did not grant access https://fanqienovel.com':'PRIVATE Debugger unattached');
    const {env,config}=managementFixture(mode==='click'||mode==='permission'?{managementClickFailure:original}:{});
    env.afterManagementClick=async tab=>{
      if(mode==='url')tab.url=async()=>{throw original;};
      if(mode==='ax')tab.getAXState=async()=>{throw original;};
      if(mode==='other_route')tab.current=ORIGIN+'/login';
      if(mode==='coverage')env.managementCoverage='草稿箱';
      if(mode==='nonstring_ax')tab.getAXState=async()=>({});
    };
    const base=await verified(env,config);env.events=[];let observed;env.api.onDriverError=failure=>{observed=failure;};
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,false);
    if(['click','permission','url','ax'].includes(mode))assert.equal(observed,original);
    assert.equal(env.events.filter(row=>row[0]==='managementClick').length,1);
    assert.ok(!env.events.some(row=>row[0]==='goto'||row[0]==='newTab'));assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('management arrival respects five-second and original-request budgets without a second click',async()=>{
  const now=Date.now;
  try{
    for(const short of [false,true]){
      let clock=now();Date.now=()=>clock;
      const {env,config}=managementFixture({managementStaySource:true}),base=await verified(env,config);env.events=[];
      env.afterManagementClick=async tab=>{const read=tab.url.bind(tab);tab.url=async()=>{clock+=short?100:1000;return read();};};
      const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(clock+(short?250:60000)).toISOString();
      const response=await executeChromeBridgeRequest(env.api,req);
      assert.equal(response.error,'platform_browser_'+(short?'operation_failed':'navigation_timeout'));
      assert.equal(env.events.filter(row=>row[0]==='managementClick').length,1);
      assert.ok(!env.events.some(row=>row[0]==='goto'||row[0]==='newTab'));assert.equal(env.creates,0);
    }
  }finally{Date.now=now;}
});
test('management switching keeps fresh identity filters and complete-catalog checks while create remains on its original navigation path',async()=>{
  for(const options of [{wrongDraftFilter:true},{virtual:true},{switchAccount:true}]){
    const {env,config}=managementFixture(options);if(options.switchAccount)env.afterManagementClick=async()=>{env.account='changed-account';};
    const base=await verified(env,config);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,false);assert.ok(env.events.some(row=>row[0]==='managementClick'));
    assert.ok(!env.events.some(row=>row[0]==='goto'||row[0]==='newTab'));assert.equal(env.creates,0);
  }
  const {env,config}=managementFixture(),base=await verified(env,config);env.events=[];
  const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  env.afterNavigation=async(tab,kind,url)=>{if(url===config.urls.create)throw original;};
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}))).ok,false);
  assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===config.urls.create).length,1);
  assert.ok(!env.events.some(row=>row[0]==='managementClick'));assert.equal(env.creates,0);
});
test('other management paths retain ordinary navigation and never infer a role click across routes or books',async()=>{
  for(const foreignBook of [false,true]){
    const {env,config}=managementFixture();
    config.chapter_lists[0].url=foreignBook?MANAGEMENT.replace('/10&','/11&')+'?type=1':MANAGEMENT.replace('&test','&other')+'?type=1';
    config.identity.book.templates.push(ORIGIN+'/main/writer/chapter-manage/{book_id}&other?type=1');
    const base=await verified(env,config);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,!foreignBook,JSON.stringify(response));
    if(foreignBook)assert.equal(response.error,'platform_browser_identity_mismatch');
    assert.ok(!env.events.some(row=>row[0]==='managementClick'));
    assert.ok(env.events.some(row=>row[0]==='goto'&&row[1]===config.chapter_lists[0].url));assert.equal(env.creates,0);
  }
});
test('changed missing or duplicate owned-list inventory refuses before the selected management click with no navigation fallback',async()=>{
  for(const mode of ['changed','missing','duplicate']){
    const {env,config}=managementFixture(),base=await verified(env,config),list=env.api.listTabs;env.events=[];
    env.api.listTabs=async()=>{
      const rows=await list(),current=rows.find(row=>row.url===MANAGEMENT+'?type=2');
      return mode==='missing'?rows.filter(row=>row!==current):mode==='duplicate'?[...rows,{...current}]:
        rows.map(row=>row===current?{...row,url:ORIGIN+'/login'}:row);
    };
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_identity_mismatch');
    assert.ok(!env.events.some(row=>['managementClick','goto','newTab'].includes(row[0])));assert.equal(env.creates,0);
  }
});
test('same-URL reload timeout is rejected even when the old URL and AX remain readable without replaying navigation',async()=>{
  for(const destination of [ORIGIN+'/account',ORIGIN+'/book/10/drafts']){
    const env=environment(),base=await verified(env);const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    let observed;env.api.onDriverError=failure=>{observed=failure;};env.events=[];
    env.afterNavigation=async(tab,kind,url)=>{
      if(url!==destination)return;
      assert.equal(kind,'reload');assert.equal(await tab.url(),destination);
      env.events.push(['navigationTimeout',tab.id]);throw original;
    };
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_operation_failed');
    assert.equal(observed,original);
    const index=env.events.findIndex(row=>row[0]==='navigationTimeout');
    assert.ok(index>=0);assert.ok(!env.events.slice(index+1).some(row=>row[0]==='ax'),'old DOM readability is not arrival evidence');
    assert.equal(env.events.filter(row=>['goto','reload'].includes(row[0])&&row[1]===destination).length,1);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('read-only timeout waits on the same tab for a late target URL or initially empty AX without another navigation',async()=>{
  for(const mode of ['late_url','empty_ax']){
    const env=environment(),base=await verified(env),destination=ORIGIN+'/book/10/chapters';let timeouts=0,reads=0;
    env.events=[];env.afterNavigation=async(tab,kind,url)=>{
      if(url!==destination||timeouts)return;
      timeouts++;if(mode==='late_url')tab.current=ORIGIN+'/book/10/drafts';
      const getAX=tab.getAXState.bind(tab);
      tab.getAXState=async options=>{
        reads++;
        if(reads<3){env.events.push(['pendingAX',tab.id]);if(reads===2)tab.current=destination;return ' \n ';}
        tab.getAXState=getAX;return getAX(options);
      };
      throw Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.deepEqual(response.result,[]);assert.equal(reads,3);
    assert.equal(timeouts,1);assert.equal(env.events.filter(row=>row[0]==='goto'&&row[1]===destination).length,1);
    assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===destination).length,0);
    assert.equal(env.events.filter(row=>row[0]==='newTab').length,0);assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('read-only arrival waiting stops at five seconds or the original request deadline and preserves the first timeout',async()=>{
  const originalNow=Date.now;
  try{
    for(const budget of ['arrival_cap','source_never_arrives','request_deadline']){
      let clock=originalNow();Date.now=()=>clock;
      const env=environment(),base=await verified(env),destination=ORIGIN+'/book/10/chapters';let reads=0,observed;
      const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
      env.api.onDriverError=failure=>{observed=failure;};env.events=[];
      env.afterNavigation=async(tab,kind,url)=>{
        if(url!==destination)return;
        if(budget==='source_never_arrives')tab.current=ORIGIN+'/book/10/drafts';
        tab.getAXState=async()=>{reads++;clock+=budget==='request_deadline'?100:1000;return reads===5&&budget==='arrival_cap'?'late visible page':'';};
        throw original;
      };
      const started=clock,req=next(base,'find_drafts',{chapter:12});
      req.deadline=new Date(clock+(budget==='request_deadline'?250:60000)).toISOString();
      const response=await executeChromeBridgeRequest(env.api,req);
      assert.equal(response.error,'platform_browser_operation_failed');assert.equal(observed,original);
      assert.equal(reads,budget==='request_deadline'?3:5);assert.equal(clock-started,budget==='request_deadline'?300:5000);
      assert.equal(env.events.filter(row=>row[0]==='goto'&&row[1]===destination).length,1);
      assert.equal(env.events.filter(row=>row[0]==='newTab').length,0);assert.equal(env.creates,0);assert.equal(env.saves,0);
    }
  }finally{Date.now=originalNow;}
});
test('arrival proof refuses other routes URL or AX exceptions and nonempty stale source immediately without polling or navigating again',async()=>{
  for(const mode of ['foreign_origin','login_route','editor_route','other_readonly_route','url_error','ax_error','after_url_error',
    'after_foreign_route','source_nonempty','nonstring_ax']){
    const env=environment(),base=await verified(env),destination=ORIGIN+'/book/10/chapters';let reads=0,urlReads=0,observed;
    const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    env.api.onDriverError=failure=>{observed=failure;};env.events=[];
    env.afterNavigation=async(tab,kind,url)=>{
      if(url!==destination)return;
      const route={foreign_origin:'https://example.com/',login_route:ORIGIN+'/login',editor_route:ORIGIN+'/book/10/edit/11?enter_from=modifydraft',
        other_readonly_route:ORIGIN+'/account',source_nonempty:ORIGIN+'/book/10/drafts'}[mode];
      if(route)tab.current=route;
      const readURL=tab.url.bind(tab);
      tab.url=async()=>{urlReads++;if(mode==='url_error'||mode==='after_url_error'&&urlReads===2)throw Error('PRIVATE URL disconnected');return readURL();};
      tab.getAXState=async()=>{reads++;if(mode==='ax_error')throw Error('PRIVATE Debugger unattached');
        if(mode==='after_foreign_route')tab.current='https://example.com/';return mode==='nonstring_ax'?{}:'visible page';};
      throw original;
    };
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_operation_failed');
    assert.equal(observed,original);assert.ok(reads<=1);assert.ok(urlReads<=2);
    assert.equal(env.events.filter(row=>row[0]==='goto'&&row[1]===destination).length,1);
    assert.equal(env.events.filter(row=>row[0]==='newTab').length,0);assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('read-draft completes its normal identity catalog and full preview gates after a read-only listing navigation timeout',async()=>{
  const env=environment(),base=await verified(env);let timeouts=0;
  env.afterNavigation=async(tab,kind,url)=>{
    if(url!==ORIGIN+'/book/10/chapters'||timeouts)return;
    timeouts++;throw Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  };
  const result=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.result.body,'原始正文');
  assert.equal(result.result.title,'原稿');assert.equal(timeouts,1);assert.equal(env.creates,0);assert.equal(env.saves,0);
});
test('an unarrived URL or unreadable AX page preserves the original navigation timeout and never retries',async()=>{
  for(const mode of ['not_arrived','ax_failed']){
    const env=environment(),base=await verified(env);const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    let observed;env.api.onDriverError=failure=>{observed=failure;};env.events=[];
    env.afterNavigation=async(tab,kind,url)=>{
      if(url!==ORIGIN+'/book/10/chapters')return;
      if(mode==='not_arrived')tab.current=ORIGIN+'/book/10/drafts';
      else tab.getAXState=async()=>{throw Error('PRIVATE probe permission denied');};
      throw original;
    };
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_operation_failed');
    assert.equal(observed,original);assert.equal(env.events.filter(row=>row[0]==='goto'&&row[1]===ORIGIN+'/book/10/chapters').length,1);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('navigation timeout recovery cannot bypass subsequent account identity checks',async()=>{
  const env=environment(),base=await verified(env);
  env.afterNavigation=async(tab,kind,url)=>{
    if(url!==ORIGIN+'/book/10/chapters')return;
    env.account='changed-account';for(const opened of env.tabs.values())if(opened.current===ORIGIN+'/account')opened.guardAccount=env.account;
    throw Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  };
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_identity_mismatch');
  assert.equal(env.creates,0);assert.equal(env.saves,0);
});
test('other errors mismatched navigation destinations and authorization or disconnect codes are never recovered',async()=>{
  const known='Timed out after 10000ms waiting for CDP command Page.navigate.';
  for(const [message,code,name] of [['Permission denied',undefined,'Error'],['Debugger unattached',undefined,'Error'],
      ['Timed out after 20000ms waiting for CDP command Page.navigate.',undefined,'Error'],
      ['Timed out waiting for tab 123456 to navigate to '+ORIGIN+'/book/other/chapters.',undefined,'Error'],
      [known,'platform_chrome_worker_origin_permission_required','Error'],[known,'disconnected','Error'],[known,undefined,'AbortError']]){
    const env=environment(),base=await verified(env);const original=Error(message);original.name=name;if(code)original.code=code;
    let observed;env.api.onDriverError=failure=>{observed=failure;};env.events=[];
    env.afterNavigation=async(tab,kind,url)=>{if(url===ORIGIN+'/book/10/chapters')throw original;};
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).error,'platform_browser_operation_failed');
    assert.equal(observed,original);assert.equal(env.events.filter(row=>row[0]==='goto'&&row[1]===ORIGIN+'/book/10/chapters').length,1);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('a create navigation is never recovered even when its landing URL is also a read-only listing',async()=>{
  const env=environment(),base=await verified(env);const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  let observed;env.api.onDriverError=failure=>{observed=failure;};env.events=[];
  env.afterNavigation=async(tab,kind,url)=>{if(url===base.profile.urls.create)throw original;};
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}))).error,'platform_browser_operation_failed');
  assert.equal(observed,original);assert.equal(env.creates,0);assert.equal(env.saves,0);
  assert.equal(env.events.filter(row=>['goto','reload'].includes(row[0])&&row[1]===base.profile.urls.create).length,1);
});
test('a failed new preview tab is not retried or treated as an arrived page and keeps its read diagnostic phase',async()=>{
  const env=environment(),base=await verified(env);const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  const newTab=env.api.newTab;let attempts=0,observed,phase;
  env.api.onProgress=event=>{if(event.phase!=='error')phase=event.phase;};
  env.api.onDriverError=failure=>{observed=failure;};
  env.api.newTab=async url=>{if(url.startsWith(ORIGIN+'/preview/')){attempts++;throw original;}return newTab(url);};
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}))).error,'platform_browser_operation_failed');
  assert.equal(attempts,1);assert.equal(observed,original);assert.equal(phase,'read');assert.equal(env.creates,0);assert.equal(env.saves,0);
});
test('only verified guard list and independent preview openings carry explicit read-only context while direct create remains false',async()=>{
  const env=environment(),calls=[],create=env.api.newTab;
  env.api.newTab=async(url,context)=>{calls.push({url,context});return create(url);};
  const base=await verified(env);assert.equal((await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}))).ok,true);
  assert.deepEqual(new Set(calls.map(item=>item.url)),new Set([ORIGIN+'/account',ORIGIN+'/book/10/drafts',ORIGIN+'/preview/10&11']));
  for(const call of calls)assert.deepEqual(call.context,{readOnly:true,target:base.binding.target});
  const direct=environment(),config=profile();delete config.create_action;
  const openings=[],open=direct.api.newTab;let created=false;
  direct.api.newTab=async(url,context)=>{openings.push({url,context});if(created&&url===config.urls.create)throw Error('stop before any direct creation');return open(url);};
  const directBase=await verified(direct,config);created=true;
  assert.equal((await executeChromeBridgeRequest(direct.api,next(directBase,'create_draft',{chapter:12,title:'验收',body:'正文'}))).error,'platform_browser_operation_failed');
  assert.deepEqual(openings.at(-1),{url:config.urls.create,context:{readOnly:false}});
  assert.equal(direct.creates,0);assert.equal(direct.saves,0);
});
test('all observed volumes are enumerated and matching later-volume draft is found',async () => {
  const env=environment({sections:['第一卷','第二卷']});env.editors.set('22',{scope:'drafts',chapter:12,title:'第二卷章',body:'保留',saved:true,section:1});
  const base=await verified(env);const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.result.length,1);assert.equal(result.result[0].remote_id,'22');
});
test('unsafe save label is never clicked and failure envelope contains no manuscript or exception text',async () => {
  const env=environment({saveLabel:'发布下一步'});const base=await verified(env);
  const config=structuredClone(base.profile);config.editor.ready.expected='发布下一步';
  const result=await executeChromeBridgeRequest(env.api,{...next(base,'create_draft',{chapter:12,title:'私密标题',body:'私密正文'}),profile:config});
  assert.equal(result.error,'platform_browser_unsafe_save');assert.equal(env.saves,0);assert.ok(!JSON.stringify(result).includes('私密'));
});
test('two new matching popup candidates are ambiguous and neither tab is owned or edited',async () => {
  const env=environment({concurrentTarget:true});const base=await verified(env);
  const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
  assert.equal(result.error,'platform_browser_ambiguous_dom');assert.equal(env.creates,1);assert.equal(env.saves,0);
  const added=[...env.tabs.values()].filter(tab=>tab.current.includes('enter_from=newdraft'));
  assert.equal(added.length,2);for(const tab of added)assert.ok(!env.events.some(row=>['getTab','ax','fill','pressSequentially'].includes(row[0])&&row[1]===tab.id));
  await closeChromeBridgeSession(env.api,base.session_id);for(const tab of added)assert.ok(env.tabs.has(tab.id));
});
test('unsafe origin and raw API errors fail without leaking underlying content',async () => {
  const env=environment();const config=profile();config.urls.drafts='https://example.com/unsafe';
  const result=await executeChromeBridgeRequest(env.api,request('verify_target',{},undefined,config));
  assert.equal(result.error,'platform_browser_unsafe_url');assert.equal(env.tabs.size,1);
  env.api.listTabs=async () => {throw new Error('PRIVATE DOM manuscript credentials');};
  const raw=await executeChromeBridgeRequest(env.api,request('verify_target'));
  assert.equal(raw.error,'platform_browser_operation_failed');assert.ok(!JSON.stringify(raw).includes('PRIVATE'));
});
test('observed paste-like type extra-paragraph counterexample never compares equal to intended text',() => {
  const expected='\nA\nB\n\nC\nD\n\n';
  const actual='\nA\n\nB\n\n\nC\n\nD\n\n';
  const packet=readParagraphs(bodyDOM(actual));
  assert.deepEqual(packet,{ok:true,value:actual});assert.notEqual(packet.value,expected);
  assert.equal(packet.value.split('\n').length,11);
});
test('platform removing tail newlines after save remains unverified and never changes intended body',async () => {
  const env=environment({dropTrailingNewlinesOnSave:true});const base=await verified(env);
  const body='\n中文甲\n\n中文乙\n\n';const req=next(base,'create_draft',{chapter:12,title:'验收',body});
  const result=await executeChromeBridgeRequest(env.api,req);
  assert.equal(result.error,'platform_browser_save_unverified');assert.equal(req.params.body,body);
  assert.equal(env.creates,1);assert.equal(env.saves,1);assert.equal(env.editors.get('99').body,body.slice(0,-2));
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
  assert.equal(env.creates,1);
});
test('explicit Fanqie terminal-LF policy accepts only trailing line breaks and preserves both actual texts',async()=>{
  for(const [body,actual] of [['\n甲\n\n乙\n\n','\n甲\n\n乙'],['正文','正文\n\n'],['甲\r\n乙\r\n','甲\n乙']]){
    const env=environment({previewBody:()=>actual});const base=request('verify_target');
    base.binding.comparison_policy='fanqie_terminal_lf_v1';
    assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);
    const req=next(base,'create_draft',{chapter:12,title:'验收',body});
    const created=await executeChromeBridgeRequest(env.api,req);
    assert.equal(created.ok,true,JSON.stringify(created));assert.equal(req.params.body,body);
    assert.equal(env.editors.get('99').body,body.replace(/\r\n/g,'\n').replace(/\r/g,'\n'),'typing still sends the complete frozen body');
    const observed=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'99'}));
    assert.equal(observed.result.body,actual,'readback must retain the actual platform body');
    assert.equal(env.creates,1);assert.equal(env.saves,1);
  }
});
test('terminal-LF policy refuses leading internal spaces text and title changes',async()=>{
  const body='\n甲\n\n乙  \n';
  for(const actual of ['甲\n\n乙  \n','\n甲\n乙  \n','\n甲\n\n乙\n','\n甲\n\n丙  \n']){
    const env=environment({previewBody:()=>actual});const base=request('verify_target');base.binding.comparison_policy='fanqie_terminal_lf_v1';
    assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);
    const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body}));
    assert.equal(result.error,'platform_browser_save_unverified');assert.equal(env.editors.get('99').body,body);
  }
  const env=environment({previewHeader:'第12章 验收 '});const base=request('verify_target');base.binding.comparison_policy='fanqie_terminal_lf_v1';
  assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body}))).error,'platform_browser_save_unverified');
});
test('terminal-LF readback policy cannot relax the exact editor input check',async()=>{
  const env=environment({editorBody:value=>value.replace(/\n+$/,'')});const base=request('verify_target');
  base.binding.comparison_policy='fanqie_terminal_lf_v1';
  assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);
  const body='甲\n\n';const req=next(base,'create_draft',{chapter:12,title:'验收',body});
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_editor_changed');
  assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal(req.params.body,body);
});
test('comparison policy is validated before UI and cannot change an existing session',async()=>{
  for(const policy of [null,true,'trim','fanqie_terminal_lf_v2']){
    const env=environment();const req=request('verify_target');req.binding.comparison_policy=policy;
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_invalid_profile');assert.deepEqual(env.events,[]);
  }
  const env=environment();const base=await verified(env);const before=env.events.length;
  const changed=next(base,'find_drafts',{chapter:12});changed.binding={...base.binding,comparison_policy:'fanqie_terminal_lf_v1'};
  assert.equal((await executeChromeBridgeRequest(env.api,changed)).error,'platform_browser_identity_mismatch');assert.equal(env.events.length,before);
  const foreign=request('verify_target');foreign.binding.comparison_policy='fanqie_terminal_lf_v1';foreign.binding.target.platform='qimao';
  assert.equal((await executeChromeBridgeRequest(environment().api,foreign)).error,'platform_browser_invalid_profile');
});
test('Markdown paragraph encoding consumes the already encoded body verbatim without another conversion',async()=>{
  for(const encoding of ['markdown_paragraphs_v1','markdown_paragraphs_v2']){
    const env=environment();const base=request('verify_target');base.binding.body_encoding=encoding;
    assert.equal((await executeChromeBridgeRequest(env.api,base)).ok,true);
    const body=(encoding==='markdown_paragraphs_v1'?'\n':'')+'首段\n第二段\n末段';const req=next(base,'create_draft',{chapter:12,title:'验收',body});
    assert.equal((await executeChromeBridgeRequest(env.api,req)).ok,true);
    assert.equal(req.params.body,body);assert.equal(env.editors.get('99').body,body);
    const observed=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'99'}));
    assert.equal(observed.result.body,body);assert.equal(env.creates,1);assert.equal(env.saves,1);
  }
});
test('body encoding is validated before UI and is isolated from comparison policy and session reuse',async()=>{
  for(const encoding of [null,true,'trim','markdown_paragraphs_v3']){
    const env=environment();const req=request('verify_target');req.binding.body_encoding=encoding;
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_invalid_profile');assert.deepEqual(env.events,[]);
  }
  const env=environment();const base=await verified(env);const before=env.events.length;
  const changed=next(base,'find_drafts',{chapter:12});changed.binding={...base.binding,body_encoding:'markdown_paragraphs_v1'};
  assert.equal((await executeChromeBridgeRequest(env.api,changed)).error,'platform_browser_identity_mismatch');assert.equal(env.events.length,before);
  const oldEnv=environment(),old=request('verify_target');old.binding.body_encoding='markdown_paragraphs_v1';
  assert.equal((await executeChromeBridgeRequest(oldEnv.api,old)).ok,true);const oldEvents=oldEnv.events.length;
  const upgrade=next(old,'find_drafts',{chapter:12});upgrade.binding={...old.binding,body_encoding:'markdown_paragraphs_v2'};
  assert.equal((await executeChromeBridgeRequest(oldEnv.api,upgrade)).error,'platform_browser_identity_mismatch');assert.equal(oldEnv.events.length,oldEvents);
  for(const encoding of ['markdown_paragraphs_v1','markdown_paragraphs_v2']){
    const foreign=request('verify_target');foreign.binding.body_encoding=encoding;foreign.binding.target.platform='qidian';
    assert.equal((await executeChromeBridgeRequest(environment().api,foreign)).error,'platform_browser_invalid_profile');
  }
});
test('catalog reuses this operation guard DOM while checking it throughout every scope and volume',async () => {
  const env=environment({sections:['第一卷','第二卷']});const base=await verified(env);
  env.events=[];env.accountReads=0;
  const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.deepEqual(response.result,[]);
  assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1);
  assert.ok(env.accountReads>10,'every identity still reads the loaded guard DOM');
  env.events=[];
  const found=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:11}));
  assert.equal(found.ok,true,JSON.stringify(found));
  assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1,
    'one fresh account page per read-only operation with repeated live DOM reads through readback');
});
test('each new operation refreshes guard rather than trusting prior operation DOM',async () => {
  const env=environment();const base=await verified(env);env.account='different-account';
  const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(response.error,'platform_browser_identity_mismatch');assert.equal(env.creates,0);
});
test('each independent read has one fresh guard but repeatedly checks actual account DOM throughout readback',async()=>{
  const env=environment({sections:['第一卷','第二卷']}),base=await verified(env);
  for(let index=0;index<2;index++){
    env.events=[];env.accountReads=0;
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result.body,'原始正文');
    assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1);
    assert.ok(env.accountReads>10,'live account DOM continues to be read without repeat navigation');
  }
});
test('read-only guard never substitutes an old value when live DOM changes becomes empty ambiguous or redirected',async()=>{
  for(const [mode,code] of [['changed','identity_mismatch'],['empty','login_required'],['multiple','ambiguous_dom'],['redirect','login_required']]){
    const env=environment(),base=await verified(env);env.events=[];
    env.onPreviewBody=()=>{
      const guard=[...env.tabs.values()].find(tab=>tab.current===ORIGIN+'/account');assert.ok(guard);
      if(mode==='changed')guard.guardAccount='different-account';
      if(mode==='empty')guard.guardAccount='';
      if(mode==='multiple')env.countOverrides={'#account':2};
      if(mode==='redirect')guard.current=ORIGIN+'/login';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(response.error,'platform_browser_'+code);assert.ok(!Object.hasOwn(response,'result'));
    assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('guard exact URL is required before and after the awaited live account DOM read',async()=>{
  for(const stage of ['before','after']){
    const env=environment(),base=await verified(env);env.events=[];let changed=false;
    if(stage==='before')env.afterNavigation=async(tab,kind,url)=>{if(url===ORIGIN+'/account'){changed=true;tab.current=ORIGIN+'/login';}};
    else env.afterLocatorEvaluate=async(tab,selector)=>{if(selector==='#account'){changed=true;tab.current=ORIGIN+'/login';}};
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(changed,true);assert.equal(response.error,'platform_browser_login_required');
    assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].startsWith(ORIGIN+'/preview/')),'redirect stops at the guard before opening preview');
    assert.equal(env.creates,0);
  }
});
test('book identity is rechecked after the guard await before reading a changed preview document',async()=>{
  const env=environment(),base=await verified(env);let changed=false,bodyReads=0;
  env.onPreviewBody=()=>{bodyReads++;};
  env.afterLocatorEvaluate=async(tab,selector)=>{
    if(selector!=='#account')return;
    const preview=[...env.tabs.values()].find(opened=>opened.current===ORIGIN+'/preview/10&11');
    if(preview){changed=true;preview.current=ORIGIN+'/preview/11&11';}
  };
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(changed,true);assert.equal(response.error,'platform_browser_identity_mismatch');assert.equal(bodyReads,0);
  assert.equal(env.creates,0);assert.equal(env.saves,0);
});
test('account switch before create click or each fill or final save is rejected using fresh guard',async () => {
  for(const options of [{changeAccountBeforeCreateClick:true},{changeAccountOnCreate:true},
    {changeAccountAfterFill:'#chapter'},{changeAccountAfterFill:'#title'},{changeAccountAfterBodyText:true}]) {
    const env=environment(options);const base=await verified(env);
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
    assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify(options));assert.equal(env.saves,0);
    const fields=env.events.filter(row=>row[0]==='fill').map(row=>row[1]);
    if(options.changeAccountBeforeCreateClick){assert.equal(env.creates,0);assert.deepEqual(fields,[]);}
    if(options.changeAccountOnCreate)assert.deepEqual(fields,[]);
    if(options.changeAccountAfterFill==='#chapter')assert.deepEqual(fields,['#chapter']);
    if(options.changeAccountAfterFill==='#title')assert.deepEqual(fields,['#chapter','#title']);
  }
});
test('a hidden server account change without loaded-DOM change is detected at the next read-only operation fresh guard',async () => {
  const env=environment({changeAccountOnPreviewBody:true});const base=await verified(env);env.events=[];
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result.body,'原始正文');
  assert.equal(env.account,'different-account');
  assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1);
  const later=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(later.error,'platform_browser_identity_mismatch');assert.ok(!Object.hasOwn(later,'result'));assert.equal(env.creates,0);
});
test('create retains forced return guard and rejects a hidden account change after saved-body preview',async()=>{
  const env=environment({changeAccountOnPreviewBody:true}),base=await verified(env);
  const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文\n'}));
  assert.equal(response.error,'platform_browser_identity_mismatch');assert.ok(!Object.hasOwn(response,'result'));
  assert.equal(env.creates,1);assert.equal(env.saves,1,'write remains a single attempt before the post-save guard refuses confirmation');
});
const evaluateTimeout=()=>Error('Timed out after 3000ms waiting for CDP command Runtime.evaluate.');
const focusSetupTimeout=()=>Error('Timed out after 3000ms waiting for CDP command Emulation.setFocusEmulationEnabled.');
test('an observed focus-emulation setup timeout can reread the same safe query once under the existing limits',async()=>{
  for(const name of ['Error','TimeoutError']){
    const env=environment(),base=await verified(env);let chosen,calls=0;
    env.beforeLocatorCount=async(tab,selector,locator)=>{
      if(selector!=='#account')return;chosen??=locator;if(locator!==chosen)return;
      if(!calls++){const failure=focusSetupTimeout();failure.name=name;throw failure;}
    };
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,true);
    assert.equal(calls,2);assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
test('one transient exact Runtime.evaluate timeout can recover each allowed SDK read without repeating the operation',async()=>{
  for(const kind of ['ax','count','value','paragraphs','preview']){
    const env=environment(),base=await verified(env);let calls=0,chosen=null;
    if(kind==='ax')env.beforeAX=async()=>{if(!calls++)throw evaluateTimeout();};
    if(kind==='count')env.beforeLocatorCount=async(tab,selector,locator)=>{if(selector!=='#account')return;
      chosen??=locator;if(locator===chosen&&!calls++)throw evaluateTimeout();};
    if(['value','paragraphs','preview'].includes(kind))env.beforeLocatorEvaluate=async(tab,selector,fn,locator)=>{
      if(kind==='value'&&selector!=='#account'||kind==='paragraphs'&&fn!==readParagraphs||kind==='preview'&&fn!==readPreviewParagraphs_v1)return;
      chosen??=locator;if(locator===chosen&&!calls++)throw evaluateTimeout();
    };
    const operation=kind==='paragraphs'?'create_draft':kind==='preview'?'read_draft':'find_drafts';
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,
      operation==='create_draft'?{chapter:12,title:'验收',body:'甲段\n乙段\n'}:operation==='read_draft'?{remote_id:'11'}:{chapter:12}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.ok(calls>=2);
    if(kind!=='ax')assert.equal(calls,2,'only the selected locator query is reread once');
    assert.equal(env.creates,kind==='paragraphs'?1:0);assert.equal(env.saves,kind==='paragraphs'?1:0);
  }
});
test('a successful first read is not repeated and a failed second SDK query never receives a third attempt',async()=>{
  for(const mode of ['success','second_exact','second_other']){
    const env=environment(),base=await verified(env);let chosen,calls=0,observed;
    const first=evaluateTimeout(),second=mode==='second_exact'?evaluateTimeout():Error('PRIVATE second read failure');
    env.api.onDriverError=failure=>{observed=failure;};
    env.beforeLocatorCount=async(tab,selector,locator)=>{if(selector!=='#account')return;chosen??=locator;if(locator!==chosen)return;
      calls++;if(mode==='success')return;throw calls===1?first:second;};
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(calls,mode==='success'?1:2);assert.equal(response.ok,mode==='success');
    if(mode!=='success')assert.equal(observed,second);assert.equal(env.creates,0);
  }
});
test('approximate Runtime.evaluate errors and permission disconnected cancellation or closed codes are never retried',async()=>{
  const message=evaluateTimeout().message;
  for(const [text,name,code] of [[message.replace('3000','3001'),'Error'],[message.slice(0,-1),'Error'],[message,'TypeError'],
    [message,'Error','permission_denied'],[message,'Error','auth_required'],[message,'Error','disconnected'],
    [message,'Error','Debugger_unattached'],[message,'Error','cancelled'],[message,'Error','closed'],[message,'AbortError'],
    [focusSetupTimeout().message.replace('3000','3001'),'Error'],[focusSetupTimeout().message.slice(0,-1),'Error'],
    [focusSetupTimeout().message,'Error','permission_denied'],
    ['Timed out after 3000ms waiting for CDP command Page.navigate.','Error'],
    ['Timed out after 3000ms waiting for CDP command Runtime.callFunctionOn.','Error']]){
    const env=environment(),base=await verified(env),original=Error(text);original.name=name;if(code)original.code=code;
    let calls=0,observed;env.api.onDriverError=failure=>{observed=failure;};
    env.beforeLocatorCount=async(tab,selector)=>{if(selector==='#account'){calls++;throw original;}};
    assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,false);
    assert.equal(calls,1);assert.equal(observed,original);assert.equal(env.creates,0);
  }
});
test('query retry refuses same-origin route remote login or foreign-origin changes before a second read',async()=>{
  for(const route of [ORIGIN+'/account?changed=1',ORIGIN+'/book/10/edit/99?enter_from=modifydraft',ORIGIN+'/login','https://example.com/']){
    const env=environment(),base=await verified(env);let calls=0;
    env.beforeLocatorCount=async(tab,selector)=>{if(selector==='#account'){calls++;tab.current=route;throw evaluateTimeout();}};
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.ok,false);assert.equal(calls,1);assert.equal(env.creates,0);
  }
  const env=environment(),base=await verified(env),fenceFailure=Error('PRIVATE URL check disconnected');let calls=0,observed;
  env.api.onDriverError=failure=>{observed=failure;};
  env.beforeLocatorCount=async(tab,selector)=>{if(selector==='#account'){calls++;tab.url=async()=>{throw fenceFailure;};throw evaluateTimeout();}};
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,false);
  assert.equal(calls,1);assert.equal(observed,fenceFailure,'URL-fence failure stays visible instead of the first query timeout');
});
test('retry and successful-query results cannot outlive the original deadline or a post-read URL change',async()=>{
  const now=Date.now;
  try{
    for(const mode of ['before_retry','after_retry','first_success']){
      let clock=now();Date.now=()=>clock;
      const env=environment(),base=await verified(env);let calls=0;
      env.beforeLocatorCount=async(tab,selector)=>{if(selector!=='#account')return;calls++;
        if(mode==='first_success'||mode==='before_retry'||calls===2)clock+=1000;
        if(mode!=='first_success'&&calls===1)throw evaluateTimeout();};
      const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(clock+500).toISOString();
      assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
      assert.equal(calls,mode==='after_retry'?2:1);assert.equal(env.creates,0);
    }
  }finally{Date.now=now;}
  const env=environment(),base=await verified(env);let calls=0;
  env.beforeLocatorCount=async(tab,selector)=>{if(selector!=='#account')return;calls++;
    if(calls===1)throw evaluateTimeout();tab.current=ORIGIN+'/login';};
  assert.equal((await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}))).ok,false);assert.equal(calls,2);
});
test('recovered reads still reject ambiguous counts wrong or blank accounts and unsupported paragraph structure',async()=>{
  for(const [mode,code] of [['count2','ambiguous_dom'],['empty','login_required'],['wrong','identity_mismatch'],['paragraphs','unsupported_editor']]){
    const env=environment(),base=await verified(env);let calls=0;
    if(mode==='count2')env.beforeLocatorCount=async(tab,selector)=>{if(selector==='#account'&&!calls++){
      env.countOverrides={'#account':2};throw evaluateTimeout();}};
    else env.beforeLocatorEvaluate=async(tab,selector,fn)=>{
      if(mode==='paragraphs'?fn!==readParagraphs:selector!=='#account')return;
      if(calls++)return;
      if(mode==='paragraphs')env.invalidEditorBody=true;else tab.guardAccount=mode==='empty'?'':'different-account';throw evaluateTimeout();
    };
    const req=mode==='paragraphs'?next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文'}):next(base,'find_drafts',{chapter:12});
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_'+code);assert.equal(env.saves,0);
  }
});
test('either exact read-setup timeout in a write action never repeats click fill paste press or navigation',async()=>{
  for(const timeout of [evaluateTimeout,focusSetupTimeout])for(const mode of ['click','fill','paste','press','save','navigation']){
    const original=timeout(),options={click:{createClickFailure:original},fill:{fillFailure:original},paste:{pasteFailure:original},
      press:{enterFailure:original},save:{saveFailure:original}}[mode]??{};
    const env=environment(options),base=await verified(env);env.events=[];let observed;env.api.onDriverError=failure=>{observed=failure;};
    if(mode==='navigation')env.afterNavigation=async(tab,kind,url)=>{if(url===base.profile.urls.create)throw original;};
    const req=next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文\n\n'});
    assert.equal((await executeChromeBridgeRequest(env.api,req)).ok,false);assert.equal(observed,original);
    assert.equal(env.creates,mode==='navigation'?0:1);
    assert.ok(env.events.filter(row=>row[0]==='click'&&row[1]==='#create').length<=1);
    assert.ok(env.events.filter(row=>row[0]==='pasteText').length<=1);
    if(mode==='press')assert.equal(env.events.filter(row=>row[0]==='press'&&row[2]==='Enter').length,1);
    if(mode==='fill')assert.equal(env.events.filter(row=>row[0]==='fill').length,1);
    assert.equal((await executeChromeBridgeRequest(env.api,req)).ok,false);assert.equal(env.creates,mode==='navigation'?0:1);
  }
});
test('post-action AX recovery rereads only the snapshot while create paste Enter and save each execute once',async()=>{
  for(const mode of ['create','paste','press','save']){
    const env=environment(),base=await verified(env);let started=false,complete=false,snapshots=0;
    env.beforeAX=async()=>{
      const reached=mode==='create'?env.creates===1:mode==='save'?env.saves===1:
        env.events.some(row=>mode==='paste'?row[0]==='pasteText':row[0]==='press'&&row[2]==='Enter');
      if(complete||!reached&&!started)return;started=true;snapshots++;
      if(snapshots===1)throw evaluateTimeout();complete=true;
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文\n'}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.equal(snapshots,2);assert.equal(env.creates,1);assert.equal(env.saves,1);
    assert.equal(env.events.filter(row=>row[0]==='pasteText').length,1);
    assert.equal(env.events.filter(row=>row[0]==='press'&&row[2]==='Enter').length,1);
  }
});
test('body focus failure after empty draft allocation stays failed and cannot retry create',async () => {
  const env=environment({failBodyFocus:true});const base=await verified(env);
  const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
  const response=await executeChromeBridgeRequest(env.api,req);
  assert.equal(response.error,'platform_browser_operation_failed');assert.ok(!JSON.stringify(response).includes('PRIVATE'));
  assert.equal(env.creates,1);assert.equal(env.saves,0);
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(env.creates,1);
});
test('unique locator polls transient zero counts using AX and never calls attached waitFor',async () => {
  const env=environment({delayCounts:{'#account':2}});const req=request('verify_target');
  const response=await executeChromeBridgeRequest(env.api,req);
  assert.equal(response.ok,true,JSON.stringify(response));assert.equal(env.delayCounts['#account'],0);
  assert.ok(env.events.filter(row=>row[0]==='ax').length>=5);
});
test('unique locator rejects duplicate immediately and missing locator obeys one total deadline',async () => {
  const duplicate=environment({duplicateSelector:'#account'});
  assert.equal((await executeChromeBridgeRequest(duplicate.api,request('verify_target'))).error,'platform_browser_ambiguous_dom');
  const missing=environment({alwaysMissing:'#account'});const req=request('verify_target');
  req.deadline=new Date(Date.now()+125).toISOString();const start=Date.now();
  assert.equal((await executeChromeBridgeRequest(missing.api,req)).error,'platform_browser_operation_failed');
  assert.ok(Date.now()-start<2000,'polling never resets or extends the request deadline');
});
test('diagnostic breadcrumbs contain only whitelisted indices metrics checks and module error location',async () => {
  const env=environment();const events=[];env.api.onProgress=event=>events.push(event);
  const base=await verified(env);await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  env.virtual=true;await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  const allowed=new Set(['phase','edge','scope','direction','catalog_index','listing_index','section_index','page','row_count',
    'guard_refreshes','line','column','ready_equal','rows_equal','count_equal','dimensions_equal','edge_reached','document_valid','document_edge_reached',
    'container_before','container_after','document_before','document_after','code']);
  for(const event of events){assert.ok(Object.isFrozen(event));for(const key of Object.keys(event))assert.ok(allowed.has(key),key);}
  assert.ok(events.some(event=>event.phase==='all_rows'&&event.container_before?.height===100));
  const error=events.find(event=>event.phase==='error');assert.equal(error.code,'platform_browser_incomplete_list');
  assert.ok(Number.isInteger(error.line)&&error.line>100);assert.ok(Number.isInteger(error.column));
  for(const secret of ['stable-id','原始正文','原稿','#account','fanqienovel.com'])assert.ok(!JSON.stringify(events).includes(secret));
});
test('throwing or rejected diagnostic callbacks cannot change a successful create or retry protection',async () => {
  for(const callback of [()=>{throw new Error('PRIVATE callback failure');},()=>Promise.reject(new Error('PRIVATE rejection'))]) {
    const env=environment();env.api.onProgress=callback;const base=await verified(env);
    const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
    const result=await executeChromeBridgeRequest(env.api,req);
    assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.result,'99');assert.equal(env.creates,1);assert.equal(env.saves,1);
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(env.creates,1);
  }
});
test('throwing or rejected original-error callbacks cannot change failure envelopes or retry a create',async () => {
  for(const callback of [()=>{throw new Error('PRIVATE diagnostic failure');},()=>Promise.reject(new Error('PRIVATE diagnostic rejection'))]){
    const env=environment({failBodyFocus:true});env.api.onDriverError=callback;const base=await verified(env);
    const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
    const result=await executeChromeBridgeRequest(env.api,req);
    assert.equal(result.error,'platform_browser_operation_failed');assert.equal(env.creates,1);assert.equal(env.saves,0);
    assert.ok(!JSON.stringify(result).includes('PRIVATE'));
    assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(env.creates,1);
  }
});
test('one dropdown click waits through zero and hidden animation until stable positive labels',async () => {
  const env=environment({delayCounts:{'.option':2},hiddenOptionReads:2});const base=await verified(env);env.events=[];
  const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.deepEqual(response.result,[]);
  assert.equal(env.events.filter(row=>row[0]==='click'&&row[1]==='#sections').length,6,
    'two scopes each open once for selection and once for final check then close; zero extra toggles');
  assert.equal([...env.tabs.values()].find(tab=>tab.current.endsWith('/chapters')).menu,false);
});
test('dropdown waiting preserves duplicate and maximum checks and one operation deadline',async () => {
  for(const options of [{sections:['重复卷','重复卷']},{countOverrides:{'.option':1001}}]) {
    const env=environment(options);const base=await verified(env);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
    assert.equal(response.error,'platform_browser_incomplete_list');
    assert.equal(env.events.filter(row=>row[0]==='click'&&row[1]==='#sections').length,1);
  }
  const env=environment({alwaysMissing:'.option'});const base=await verified(env);env.events=[];
  const req=next(base,'find_drafts',{chapter:12});req.deadline=new Date(Date.now()+125).toISOString();
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');
  assert.equal(env.events.filter(row=>row[0]==='click'&&row[1]==='#sections').length,1);
});
test('atomic DOM accessor preserves each original field kind whitespace and attribute behavior',() => {
  const node=(tagName,value,textContent,innerText,attrs={})=>({tagName,value,textContent,innerText,getAttribute:key=>attrs[key]??null});
  for(const tag of ['INPUT','TEXTAREA','SELECT'])assert.equal(readDOMValue(node(tag,'\n 字　\n','wrong','wrong')),'\n 字　\n');
  assert.equal(readDOMValue(node('SPAN',null,' textContent \n','different innerText')),' textContent \n');
  assert.equal(readDOMValue(node('DIV',null,'different textContent','\n正文\n\n',{contenteditable:'true'})),'\n正文\n\n');
  assert.equal(readDOMValue(node('INPUT','ignored','ignored','ignored',{href:' /literal '}),'href'),' /literal ');
  assert.equal(readDOMValue(node('A',null,'title','title'),'href'),null);
});
test('batch rows and explicit unsupported-native-CSS fallback have identical observations',async () => {
  const batched=environment();const base=await verified(batched);batched.events=[];
  const observed=await executeChromeBridgeRequest(batched.api,next(base,'find_drafts',{chapter:11}));
  assert.equal(observed.ok,true,JSON.stringify(observed));
  assert.ok(batched.events.some(row=>row[0]==='evaluateAll'));
  assert.equal(batched.events.filter(row=>row[0]==='fieldRead').length,0,'CSS rows use no per-field browser round trips');
  const fallback=environment({rowChapterSelector:'.chapter:visible',cssUnsupported:['.chapter:visible']});
  const config=profile();for(const listing of config.chapter_lists)listing.chapter.selector='.chapter:visible';
  const other=await verified(fallback,config);fallback.events=[];
  const repeated=await executeChromeBridgeRequest(fallback.api,next(other,'find_drafts',{chapter:11}));
  assert.deepEqual(repeated.result,observed.result);assert.ok(fallback.events.some(row=>row[0]==='fieldRead'));
});
test('batch duplicate attribute and DOM errors never fall back or hide invalid chapter state or URL',async () => {
  const cases=[
    [{duplicateRowField:'.chapter',cssUnsupported:['.link']},'ambiguous_dom'],
    [{missingLinkAttribute:true},'incomplete_dom'],[{cssErrors:['.chapter']},'incomplete_dom'],
    [{rowChapterRaw:'无法识别的章号'},'invalid_chapter'],[{rowState:'  '},'incomplete_list'],
    [{rowURL:'https://example.com/foreign'},'unsafe_url']
  ];
  for(const [options,code] of cases){
    const env=environment(options);const base=await verified(env);env.events=[];
    const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:11}));
    assert.equal(response.error,'platform_browser_'+code,JSON.stringify(options));
    assert.equal(env.events.filter(row=>row[0]==='fieldRead').length,0,'failure cannot select locator fallback');
  }
});
test('row field sources other than DOM and explicit scope constants are rejected',async () => {
  const env=environment();const config=profile();config.chapter_lists[0].chapter.source='page_url';
  const result=await executeChromeBridgeRequest(env.api,request('verify_target',{},undefined,config));
  assert.equal(result.error,'platform_browser_invalid_profile');assert.deepEqual(env.events,[]);
});
test('strict preview paragraphs preserve whitespace and reject attributes BR non-P or missing sentinel',()=>{
  for(const value of ['','正文','\n甲\n\n乙\n\n','  文字　\n末段  '])assert.deepEqual(readPreviewParagraphs_v1(previewDOM(value)),{ok:true,value});
  const invalid=[element('DIV',{},[element('P',{},[textNode('no sentinel')])]),
    element('DIV',{},[element('P',{class:'x'},[textNode('正文')]),element('P')]),
    element('DIV',{},[element('P',{},[br()]),element('P')]),
    element('DIV',{},[element('SPAN',{},[textNode('正文')]),element('P')]),
    element('DIV',{},[textNode('outside'),element('P')])];
  for(const node of invalid)assert.deepEqual(readPreviewParagraphs_v1(node),{ok:false});
  assert.deepEqual(readPreviewParagraphs_v1(parse('<p><br></p><p>甲</p><p>乙</p><p><br></p><p>丙</p><p>丁</p><p></p>')),
    {ok:true,value:'\n甲\n乙\n\n丙\n丁'});
  for(const html of ['<p>正文</p><p><br></p>','<p>正文<br></p><p></p>',
    '<p><br class="unknown"></p><p></p>','<p data-x="true"><br></p><p></p>'])assert.deepEqual(readPreviewParagraphs_v1(parse(html)),{ok:false});
});
test('readback mode closes own saved editor before opening preview and never reopens modifydraft',async()=>{
  const env=environment();const base=await verified(env,previewProfile());env.events=[];
  const body='\n中文甲\n\n中文乙\n\n';const title=' 标题　 ';
  const req=next(base,'create_draft',{chapter:12,title,body});
  const created=await executeChromeBridgeRequest(env.api,req);
  assert.equal(created.ok,true,JSON.stringify(created));assert.equal(created.result,'99');
  assert.ok(![...env.tabs.values()].some(tab=>tab.current.includes('/edit/99')));
  assert.ok([...env.tabs.values()].some(tab=>tab.current===ORIGIN+'/preview/10&99'));
  assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/edit/')));
  const observed=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'99'}));
  assert.equal(observed.result.body,body);assert.equal(observed.result.title,title);assert.equal(env.creates,1);
  assert.equal(observed.result.url,ORIGIN+'/preview/10&99');
});
test('unowned matching editor with another query blocks preview without touching it or other documents',async()=>{
  const env=environment();const same=env.add(ORIGIN+'/book/10/edit/11?another=entry#caret');
  const other=env.add(ORIGIN+'/book/10/edit/1?enter_from=modifydraft');
  const base=await verified(env,previewProfile());env.events=[];
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(response.error,'platform_browser_editor_in_use');
  assert.ok(env.tabs.has(same.id)&&env.tabs.has(other.id));assert.deepEqual(env.closed,[]);
  assert.ok(!env.events.some(row=>['getTab','ax'].includes(row[0])&&[same.id,other.id].includes(row[1])));
  assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/preview/')));
});
test('preview readiness polls header and empty body but refuses unknown structure without editor fallback',async()=>{
  const env=environment({previewHeaderPending:2,previewBodyPending:2});
  const base=await verified(env,previewProfile());env.events=[];
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result.body,'原始正文');
  env.invalidPreviewBody=true;
  const failed=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(failed.error,'platform_browser_unsupported_editor');
  assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/edit/')));
});
test('initial preview loading still verifies a newly saved draft without repeating creation or saving',async()=>{
  const env=environment({previewHeaderPending:2,previewBodyPending:2}),base=await verified(env);
  const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新草稿',body:'完整正文'}));
  assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result,'99');
  assert.equal(env.creates,1);assert.equal(env.saves,1);assert.equal(env.editors.get('99').body,'完整正文');
});
test('final preview body disappearance refuses without waiting for the old text to return',async()=>{
  for(const operation of ['read_draft','create_draft'])for(const change of ['pending','missing','book','remote']){
    const env=environment(),base=await verified(env);let reads=0,timer;
    env.beforeLocatorEvaluate=async(tab,selector)=>{
      if(selector!=='#preview-body')return;
      if(++reads===1&&change==='missing')env.delayCounts={'#preview-body':1};
      if(reads===2&&change!=='missing'){
        env.previewBodyPending=1;
        if(change==='book'||change==='remote')timer=setTimeout(()=>{
          env.editors.set('22',{scope:'drafts',chapter:22,title:'另一稿标题',body:tab.data.body,saved:true});
          tab.load(ORIGIN+'/preview/'+(change==='book'?'20':'10')+'&22');
        },10);
      }
    };
    try {
      const params=operation==='read_draft'?{remote_id:'11'}:{chapter:12,title:'新草稿',body:'相同正文'};
      const response=await executeChromeBridgeRequest(env.api,next(base,operation,params));
      assert.equal(response.error,'platform_browser_editor_changed',JSON.stringify({operation,change,response}));
      assert.ok(!Object.hasOwn(response,'result'));
      assert.equal(reads,change==='missing'?1:2,'final verification never polls the body back into existence');
      assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    } finally { clearTimeout(timer); }
  }
});
test('final preview body read is followed by live account and complete chapter header verification',async()=>{
  for(const operation of ['read_draft','create_draft'])for(const change of ['account','chapter','title']){
    const env=environment(),base=await verified(env);let reads=0;
    env.onPreviewBody=()=>{
      if(++reads!==2)return;
      if(change==='account'){
        const guard=[...env.tabs.values()].find(tab=>tab.current===ORIGIN+'/account');
        guard.guardAccount='different-account';
      } else {
        const original=operation==='read_draft'?{chapter:11,title:'原稿'}:{chapter:12,title:'新草稿'};
        env.previewHeader='第'+(change==='chapter'?22:original.chapter)+'章 '+(change==='title'?'已改变标题':original.title);
      }
    };
    const params=operation==='read_draft'?{remote_id:'11'}:{chapter:12,title:'新草稿',body:'相同正文'};
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,params));
    assert.equal(response.error,'platform_browser_'+(change==='account'?'identity_mismatch':'editor_changed'),JSON.stringify({operation,change,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(reads,2);
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
  }
});
test('final preview header disappearance cannot start a new readiness wait after the body was verified',async()=>{
  for(const change of ['missing','duplicate']){
    const env=environment(),base=await verified(env);let reads=0;
    env.onPreviewBody=()=>{
      if(++reads!==2)return;
      if(change==='missing')env.delayCounts={'#preview-title':1};
      else env.countOverrides={'#preview-title':2};
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(response.error,'platform_browser_'+(change==='missing'?'editor_changed':'ambiguous_dom'),JSON.stringify(response));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(reads,2);assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});
for(const operation of ['read_draft','find_drafts','create_draft'])test('final preview account cannot restart readiness for '+operation,async()=>{
  for(const change of ['missing','empty','duplicate']){
    const env=environment(),base=await verified(env);let reads=0,finalCounts=0,waited=false;
    const remote=operation==='create_draft'?'99':'11';
    env.onPreviewBody=()=>{
      if(++reads!==2)return;
      if(change==='missing')env.delayCounts={'#account':1};
      if(change==='empty')[...env.tabs.values()].find(tab=>tab.current===ORIGIN+'/account').guardAccount='';
      if(change==='duplicate')env.countOverrides={'#account':2};
    };
    env.beforeLocatorCount=async(tab,selector)=>{if(selector==='#account'&&reads===2)finalCounts++;};
    env.beforeAX=async tab=>{
      if(reads===2&&tab.current===ORIGIN+'/account'){
        waited=true;env.editors.get(remote).body='等待期间已经改变的正文';
      }
    };
    const params=operation==='create_draft'?{chapter:12,title:'新草稿',body:'相同正文'}:
      operation==='find_drafts'?{chapter:11}:{remote_id:'11'};
    const command=next(base,operation,params);
    const response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(response.error,'platform_browser_'+(change==='duplicate'?'ambiguous_dom':'login_required'),JSON.stringify({operation,change,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(reads,2);assert.equal(finalCounts,1);
    assert.equal(waited,false,'an observed missing final account must not be polled back into existence');
    if(operation==='create_draft'){
      const replay=await executeChromeBridgeRequest(env.api,command);
      assert.equal(replay.ok,false,'an uncertain create remains consumed');
    }
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
    assert.deepEqual([...env.tabs.values()].map(tab=>tab.current),['https://example.com/already-open']);
  }
});
test('initial account readiness remains available before read and create final checks',async()=>{
  for(const operation of ['read_draft','create_draft']){
    const env=environment(),base=await verified(env);env.events=[];env.delayCounts={'#account':1};
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,operation==='read_draft'?
      {remote_id:'11'}:{chapter:12,title:'新草稿',body:'新正文'}));
    assert.equal(response.ok,true,JSON.stringify(response));
    if(operation==='read_draft')assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,1);
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});
test('final preview account and header queries never recover a timeout by retrying after the body',async()=>{
  for(const operation of ['read_draft','create_draft'])for(const selector of ['#account','#preview-title'])for(const query of ['count','value']){
    const env=environment(),base=await verified(env);let reads=0,calls=0;
    const failure=evaluateTimeout();let observed;
    env.api.onDriverError=error=>{observed=error;};env.onPreviewBody=()=>{reads++;};
    const timeout=async(tab,actual)=>{
      if(reads!==2||actual!==selector)return;
      if(++calls===1){env.editors.get(operation==='create_draft'?'99':'11').body='超时期间已改变正文';throw failure;}
    };
    if(query==='count')env.beforeLocatorCount=timeout;else env.beforeLocatorEvaluate=timeout;
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,operation==='read_draft'?
      {remote_id:'11'}:{chapter:12,title:'新草稿',body:'原正文'}));
    assert.equal(response.ok,false,JSON.stringify({operation,selector,query,response}));
    assert.equal(calls,1);assert.equal(observed,failure);assert.equal(reads,2);
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});
test('preview identity remains pinned across initial body loading and the final editor inventory await',async()=>{
  for(const stage of ['initial_header','initial_pending','final_inventory'])for(const change of ['book','remote']){
    const env=environment(),base=await verified(env);let reads=0,timer,changed=false;
    const move=tab=>{
      env.editors.set('22',{scope:'drafts',chapter:11,title:'原稿',body:'原始正文',saved:true});
      tab.load(ORIGIN+'/preview/'+(change==='book'?'20':'10')+'&22');changed=true;
    };
    env.beforeLocatorEvaluate=async(tab,selector)=>{
      if(selector==='#preview-title'&&stage==='initial_header'&&!timer){
        env.previewHeaderPending=1;timer=setTimeout(()=>move(tab),10);
      }
      if(selector!=='#preview-body')return;
      if(++reads===1&&stage==='initial_pending'){
        env.previewBodyPending=1;timer=setTimeout(()=>move(tab),10);
      }
    };
    const list=env.api.listTabs;
    env.api.listTabs=async()=>{
      if(stage==='final_inventory'&&reads===2&&!changed)move([...env.tabs.values()].find(tab=>tab.preview));
      return list();
    };
    try {
      const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
      assert.equal(changed,true);assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({stage,change,response}));
      assert.ok(!Object.hasOwn(response,'result'));assert.equal(reads,stage==='initial_header'?0:stage==='initial_pending'?1:2);
      assert.equal(env.creates,0);assert.equal(env.saves,0);
    } finally { clearTimeout(timer); }
  }
});
test('preview arrival is pinned before inventory or account checks can conceal an early redirect',async()=>{
  for(const stage of ['inventory_restore','account_restore','after_inventory']){
    const env=environment(),base=await verified(env);let preview,inventoryChecked=false,bodyReads=0;
    const expected=ORIGIN+'/preview/10&11',other=ORIGIN+'/preview/10&22';
    env.editors.set('22',{scope:'drafts',chapter:22,title:'另一稿',body:'另一稿正文',saved:true});
    const newTab=env.api.newTab,list=env.api.listTabs;
    env.api.newTab=async(url,...args)=>{
      const tab=await newTab(url,...args);
      if(url===expected){preview=tab;if(stage!=='after_inventory')tab.load(other);}
      return tab;
    };
    env.api.listTabs=async()=>{
      if(preview&&!inventoryChecked){
        inventoryChecked=true;
        if(stage==='inventory_restore')preview.load(expected);
        if(stage==='after_inventory')preview.load(other);
      }
      return list();
    };
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(selector==='#account'&&preview?.current===other)preview.load(expected);
    };
    env.onPreviewBody=()=>{bodyReads++;};
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({stage,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(bodyReads,0);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
    assert.ok(env.closed.includes(preview.id),'a refused new preview is still owned for cleanup');
  }
});
test('every observed preview URL rejects a different remote before a later guard can restore it',async()=>{
  // Arrival and AX use the first three URL reads; exercise the new-tab tail
  // check and both identity reads immediately before the account guard await.
  for(const redirectOnRead of [4,5,6]){
    const env=environment(),base=await verified(env);let preview,reads=0,bodyReads=0,redirected=false;
    const expected=ORIGIN+'/preview/10&11',other=ORIGIN+'/preview/10&22',newTab=env.api.newTab;
    env.editors.set('22',{scope:'drafts',chapter:22,title:'另一稿',body:'另一稿正文',saved:true});
    env.api.newTab=async(url,...args)=>{
      const tab=await newTab(url,...args);
      if(url===expected){
        preview=tab;const readURL=tab.url.bind(tab);
        tab.url=async()=>{if(++reads===redirectOnRead){tab.load(other);redirected=true;}return readURL();};
      }
      return tab;
    };
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(selector==='#account'&&preview?.current===other)preview.load(expected);
    };
    env.onPreviewBody=()=>{bodyReads++;};
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(redirected,true);assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({redirectOnRead,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(bodyReads,0);assert.equal(env.creates,0);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
    assert.ok(env.closed.includes(preview.id));
  }
});
test('owned editor inventory may lag one successful close before independent preview readback',async()=>{
  const env=environment();const base=await verified(env,previewProfile());env.events=[];
  const get=env.api.getTab,list=env.api.listTabs;let stale,polls=0,closeCalls=0;
  env.api.getTab=async id=>{
    const tab=await get(id);
    if(tab.current.includes('/edit/')&&!tab.delayedClose){
      tab.delayedClose=true;const close=tab.close.bind(tab);
      tab.close=async()=>{closeCalls++;stale={id:tab.id,url:tab.current};await close();};
    }
    return tab;
  };
  env.api.listTabs=async()=>{const rows=await list();if(stale&&++polls<=3)rows.push(stale);return rows;};
  const body='\n中文甲\n\n中文乙\n\n';
  const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body}));
  assert.equal(result.ok,true,JSON.stringify(result));assert.ok(polls>=4);assert.equal(closeCalls,1);
  assert.equal(env.creates,1);assert.equal(env.saves,1);
  assert.ok(!env.tabs.has(stale.id));assert.ok([...env.tabs.values()].some(tab=>tab.current===ORIGIN+'/preview/10&99'));
  const readback=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'99'}));
  assert.equal(readback.result.body,body);assert.equal(readback.result.title,'验收');assert.equal(closeCalls,1);
});
test('owned editor close waits at most five seconds or the original deadline without retrying create or close',async()=>{
  const originalNow=Date.now;
  try{
    for(const budget of ['close-cap','request-deadline']){
      let clock=originalNow();Date.now=()=>clock;
      const env=environment({closeEditorFailure:true});const base=await verified(env,previewProfile());env.events=[];
      const list=env.api.listTabs;let closingId,polls=0;
      env.api.listTabs=async()=>{
        const rows=await list();closingId ||= env.closed.find(id=>env.tabs.get(id)?.current.includes('/edit/'));
        if(closingId){polls++;clock+=budget==='close-cap'?1000:100;}
        return rows;
      };
      const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
      req.deadline=new Date(clock+(budget==='close-cap'?60000:250)).toISOString();
      const result=await executeChromeBridgeRequest(env.api,req);
      assert.equal(result.error,'platform_browser_editor_in_use');assert.equal(env.creates,1);assert.equal(env.saves,1);
      assert.equal(env.closed.filter(id=>id===closingId).length,1);assert.equal(polls,budget==='close-cap'?5:3);
      assert.ok(env.tabs.has(closingId),'unconfirmed closure retains the owned tab for existing cleanup');
      assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/preview/')));
      assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(env.creates,1);
    }
  }finally{Date.now=originalNow;}
});
test('a new unowned matching editor during close propagation blocks preview and is never touched',async()=>{
  const env=environment();const base=await verified(env,previewProfile());env.events=[];
  const get=env.api.getTab,list=env.api.listTabs;let stale,polls=0,user,closeCalls=0;
  env.api.getTab=async id=>{
    const tab=await get(id);
    if(tab.current.includes('/edit/')&&!tab.delayedClose){
      tab.delayedClose=true;const close=tab.close.bind(tab);
      tab.close=async()=>{closeCalls++;stale={id:tab.id,url:tab.current};await close();};
    }
    return tab;
  };
  env.api.listTabs=async()=>{
    if(stale&&++polls===2)user=env.add(ORIGIN+'/book/10/edit/99?another=user-entry#caret');
    const rows=await list();if(stale)rows.push(stale);return rows;
  };
  const result=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
  assert.equal(result.error,'platform_browser_editor_in_use');assert.equal(polls,2);assert.equal(closeCalls,1);
  assert.equal(env.creates,1);assert.equal(env.saves,1);assert.ok(env.tabs.has(user.id));
  assert.ok(!env.closed.includes(user.id));
  assert.ok(!env.events.some(row=>['getTab','ax'].includes(row[0])&&row[1]===user.id));
  assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/preview/')));
});
test('Fanqie host rejects missing or editor-backed readback before any UI operation',async()=>{
  for(const operation of ['verify_target','find_drafts','read_draft','create_draft']){
    const env=environment();const req=request(operation,{chapter:12,title:'验收',body:'正文',remote_id:'11'},undefined,legacyProfile());
    const response=await executeChromeBridgeRequest(env.api,req);
    assert.equal(response.error,'platform_browser_readback_required');assert.deepEqual(env.events,[]);assert.equal(env.creates,0);
  }
  const env=environment();const config=profile();config.readback.url=config.urls.edit;
  config.readback.remote_id=structuredClone(config.editor.remote_id);
  const response=await executeChromeBridgeRequest(env.api,request('verify_target',{},undefined,config));
  assert.equal(response.error,'platform_browser_invalid_profile');assert.deepEqual(env.events,[]);
});

test('host identity-source subset rejects standalone DOM modes before any UI operation',async()=>{
  for(const location of ['account','book','remote_id']){
    const env=environment(),config=profile();
    (location==='remote_id'?config.editor:config.identity)[location]=field('#'+location,{source:'dom',attribute:'data-id'});
    const response=await executeChromeBridgeRequest(env.api,request('verify_target',{},undefined,config));
    assert.equal(response.error,'platform_browser_invalid_profile',location);assert.deepEqual(env.events,[]);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});

test('host requires explicit editor readiness before any UI work even with a DOM draft state',async()=>{
  for(const missing of [undefined,null]){
    const env=environment(),config=profile();config.editor.state=field('#state',{expected:'草稿'});
    config.editor.ready=missing;
    const response=await executeChromeBridgeRequest(env.api,request('verify_target',{},undefined,config));
    assert.equal(response.error,'platform_browser_invalid_profile');assert.deepEqual(env.events,[]);
    assert.equal(env.creates,0);assert.equal(env.saves,0);
  }
});

test('editor DOM marker sources are rejected before verification or creation touches UI',async()=>{
  for(const marker of ['saved','ready','new'])for(const source of ['page_url','guard_dom','listing','prosemirror_paragraphs','',false,[]]){
    const config=profile();config.editor.new=field('#new',{expected:'新建草稿'});config.editor[marker].source=source;
    for(const operation of ['verify_target','create_draft']){
      const env=environment();const req=request(operation,{chapter:12,title:'验收',body:'正文'},undefined,config);
      const response=await executeChromeBridgeRequest(env.api,req);
      assert.equal(response.error,'platform_browser_invalid_profile',JSON.stringify({marker,source,operation}));
      assert.deepEqual(env.events,[]);assert.equal(env.creates,0);assert.equal(env.saves,0);
    }
  }
});

test('editor DOM marker sources preserve omitted, null and explicit DOM creation',async()=>{
  for(const source of [undefined,null,'dom']){
    const config=profile();config.editor.new=field('#new',{expected:'新建草稿'});
    for(const marker of ['saved','ready','new'])if(source!==undefined)config.editor[marker].source=source;
    const env=environment();const base=await verified(env,config);
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
    assert.equal(response.ok,true,JSON.stringify(response));assert.equal(env.creates,1);assert.equal(env.saves,1);
  }
});

// Reuse the public-CUA fixture with each ordinary editor's official origin.
// URL translation stays in the fake host; the driver sees only that platform.
async function ordinaryFixture(platform,stateSource='dom',{extended=false,bodySource='dom'}={}) {
  const origin={qimao:'https://zuozhe.qimao.com',qidian:'https://write.qq.com'}[platform];
  const config=JSON.parse(JSON.stringify(legacyProfile()).replaceAll(ORIGIN,origin));config.platform=platform;
  if(stateSource==='dom')config.editor.state=field('#state',{expected:'草稿'});
  config.editor.body=field('#body',{source:bodySource});
  if(extended)for(const name of ['chapter','title','body'])config.editor[name].selector+=':visible';
  const env=environment(),add=env.add;
  const internal=value=>value.replace(origin,ORIGIN),external=value=>value.replace(ORIGIN,origin);
  env.add=url=>{
    const tab=add(internal(url)),lookup=tab.lookup.bind(tab),goto=tab.goto.bind(tab);
    tab.url=async()=>external(tab.current);tab.goto=url=>goto(internal(url));
    tab.documentURL=()=>external(tab.current);
    tab.playwright.locator=selector=>new Locator(tab,()=>tab.lookup(selector),selector.replace(/:visible$/,''));
    tab.lookup=selector=>{
      selector=selector.replace(/:visible$/,'');
      if(tab.data&&selector==='#body'&&bodySource==='dom')return [{tag:'TEXTAREA',value:()=>tab.data.body,set:value=>{tab.data.body=value;}}];
      if(tab.data&&selector==='#state')return [{text:env.editorState??'草稿'}];
      const nodes=lookup(selector);
      for(const node of nodes)if(node.attrs?.href)node.attrs.href=external(node.attrs.href);
      if(selector==='.row')for(const node of nodes)for(const link of node.children['.link'])link.attrs.href=external(link.attrs.href);
      return nodes;
    };
    return tab;
  };
  env.api.listTabs=async()=>[...env.tabs.values()].map(tab=>({id:tab.id,url:external(tab.current)}));
  const base=request('verify_target',{},undefined,config);base.binding.target.platform=platform;
  const response=await executeChromeBridgeRequest(env.api,base);assert.equal(response.ok,true,JSON.stringify(response));
  return {env,base,origin};
}
function ordinaryParams(operation) {
  return operation==='create_draft'?{chapter:12,title:'新草稿',body:'相同正文'}:
    operation==='find_drafts'?{chapter:11}:{remote_id:'11'};
}

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' editor supports read find create and existing selector/body modes',async()=>{
  for(const stateSource of ['dom','listing'])for(const bodySource of ['dom','prosemirror_paragraphs'])for(const operation of ['read_draft','find_drafts','create_draft']){
    const {env,base,origin}=await ordinaryFixture(platform,stateSource,{extended:true,bodySource});
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,ordinaryParams(operation)));
    assert.equal(response.ok,true,JSON.stringify({stateSource,bodySource,operation,response}));
    if(operation==='read_draft')assert.deepEqual(response.result,{remote_id:'11',title:'原稿',body:'原始正文',state:'draft',url:origin+'/book/10/edit/11?enter_from=modifydraft'});
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' editor rejects another remote during read find and create readback',async()=>{
  for(const stateSource of ['dom','listing'])for(const operation of ['read_draft','find_drafts','create_draft']){
    const {env,base}=await ordinaryFixture(platform,stateSource);let changed=false,armed=false,urlReads=0;
    env.editors.set('22',{scope:'drafts',chapter:22,title:'另一草稿',body:'另一草稿正文',saved:true});
    env.afterLocatorEvaluate=async(tab,selector)=>{if(selector==='#title'&&tab.current.includes('enter_from=modifydraft')&&!changed)armed=true;};
    const add=env.add;env.add=url=>{const tab=add(url),readURL=tab.url.bind(tab);tab.url=async()=>{
      if(armed&&!changed&&tab.current.includes('/edit/')&&++urlReads===2){changed=true;tab.load(ORIGIN+'/book/10/edit/22?enter_from=modifydraft');}
      return readURL();
    };return tab;};
    const command=next(base,operation,ordinaryParams(operation)),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({stateSource,operation,response}));
    assert.ok(!Object.hasOwn(response,'result'));
    if(operation==='create_draft')assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' editor refuses changed current chapter title body and DOM state after body observation',async()=>{
  for(const operation of ['read_draft','find_drafts','create_draft'])for(const change of ['chapter','title','body','state','account']){
    const {env,base}=await ordinaryFixture(platform);let changed=false;
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(selector!=='#body'||!tab.current.includes('enter_from=modifydraft')||changed)return;changed=true;
      if(change==='state')env.editorState='已发布';
      else if(change==='account')[...env.tabs.values()].find(opened=>opened.current===ORIGIN+'/account').guardAccount='different-account';
      else tab.data[change]=change==='chapter'?22:'已改变的文本';
    };
    const command=next(base,operation,ordinaryParams(operation)),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({operation,change,response}));
    assert.ok(!Object.hasOwn(response,'result'));
    if(operation==='create_draft')assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' final body and guard awaits cannot conceal changed identities or headers',async()=>{
  for(const operation of ['read_draft','find_drafts','create_draft'])for(const stage of ['body','guard','title'])for(const change of stage==='title'?['remote']:stage==='body'?['remote','chapter','title','state','account']:['remote','chapter','title','state']){
    const {env,base}=await ordinaryFixture(platform);let changed=false,finalBody=false;
    env.afterLocatorEvaluateAll=async(tab,selector)=>{
      if(selector==='#body')finalBody=true;
      if(changed||!finalBody||selector!=={body:'#body',guard:'#account',title:'#title'}[stage])return;changed=true;
      const editor=[...env.tabs.values()].find(opened=>opened.current.includes('enter_from=modifydraft'));
      if(change==='remote'){env.editors.set('22',{...editor.data});editor.load(ORIGIN+'/book/10/edit/22?enter_from=modifydraft');}
      else if(change==='account')[...env.tabs.values()].find(opened=>opened.current===ORIGIN+'/account').guardAccount='different-account';
      else if(change==='state')env.editorState='已发布';
      else editor.data[change]=change==='chapter'?22:'已改变标题';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,ordinaryParams(operation)));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({operation,stage,change,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' final fields fail once for missing duplicate empty and timeout without readiness restart',async()=>{
  for(const selector of ['#chapter','#title','#body','#state','#account'])for(const change of ['missing','duplicate','empty','timeout']){
    const {env,base}=await ordinaryFixture(platform);let calls=0,armed=false,waited=false;const failure=evaluateTimeout();
    env.afterLocatorEvaluate=async(tab,field)=>{if(field==='#body'&&tab.current.includes('enter_from=modifydraft'))armed=true;};
    env.beforeAX=async()=>{if(calls)waited=true;};
    env.beforeLocatorEvaluateAll=async(tab,field)=>{
      if(!armed||field!==selector)return;calls++;
      if(change==='missing'||change==='duplicate')env.currentFieldCounts={[selector]:change==='missing'?0:2};
      if(change==='timeout')throw failure;
      if(change==='empty'){
        if(selector==='#account')tab.guardAccount='';
        else if(selector==='#state')env.editorState='';
        else tab.data[selector.slice(1)]='';
      }
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(response.ok,false,JSON.stringify({selector,change,response}));assert.equal(calls,1);
    assert.equal(waited,false);assert.equal(env.creates,0);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('ordinary initial field readiness still waits before strict final reads',async()=>{
  for(const platform of ['qimao','qidian'])for(const operation of ['read_draft','create_draft']){
    const {env,base}=await ordinaryFixture(platform);env.delayCounts={'#chapter':1,'#account':1};
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,ordinaryParams(operation)));
    assert.equal(response.ok,true,JSON.stringify({platform,operation,response}));
    assert.equal(env.delayCounts['#chapter'],0);assert.equal(env.delayCounts['#account'],0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('current editor evaluateAll callback is self-contained and preserves the original paragraph contract',()=>{
  const isolated=Function('return ('+readParagraphs.toString()+')')();
  assert.deepEqual(isolated(bodyDOM('甲\n\n乙\n')),{ok:true,value:'甲\n\n乙\n'});
  assert.deepEqual(isolated([bodyDOM('甲\n\n乙\n')],{source:'prosemirror_paragraphs'}),{ok:true,count:1,value:'甲\n\n乙\n'});
  const input={tagName:'TEXTAREA',value:'正文',getAttribute:()=>null};
  assert.deepEqual(isolated([input],{source:'dom'}),{ok:true,count:1,value:'正文'});
  assert.deepEqual(isolated([],{source:'dom'}),{ok:false,count:0});
  assert.deepEqual(isolated([input,input],{source:'dom'}),{ok:false,count:2});
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' pre-final guard refresh detects server account changes and editor changes during reload',async()=>{
  for(const operation of ['read_draft','find_drafts','create_draft'])for(const change of ['server_account','remote','chapter','title','body','state']){
    const {env,base}=await ordinaryFixture(platform,'listing');let armed=false,changed=false;
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(selector==='#body'&&tab.current.includes('enter_from=modifydraft')){
        armed=true;if(change==='server_account'){env.account='different-account';changed=true;}
      }
    };
    env.afterNavigation=async(tab,kind,url)=>{
      if(!armed||changed||kind!=='reload'||url!==ORIGIN+'/account')return;changed=true;
      const editor=[...env.tabs.values()].find(opened=>opened.current.includes('enter_from=modifydraft'));
      if(change==='remote'){env.editors.set('22',{...editor.data});editor.load(ORIGIN+'/book/10/edit/22?enter_from=modifydraft');}
      else if(change==='state'){editor.data.scope='non_drafts';editor.data.state='已发布';}
      else editor.data[change]=change==='chapter'?22:'已经改变文本';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,ordinaryParams(operation)));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({operation,change,response}));
    assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' creation keeps its confirmed remote through saving and readback',async()=>{
  for(const stage of ['unchanged','before_fill','after_save','saved_marker']){
    const {env,base}=await ordinaryFixture(platform);let changed=false,armed=false,urlReads=0;
    const move=tab=>{
      if(changed)return;changed=true;
      env.editors.set('22',{...tab.data});
      tab.load(ORIGIN+'/book/10/edit/22?enter_from=modifydraft');
    };
    const add=env.add;env.add=url=>{const tab=add(url),readURL=tab.url.bind(tab);tab.url=async()=>{
      if(armed&&!changed&&tab.current.includes('enter_from=newdraft')&&++urlReads===2)move(tab);
      return readURL();
    };return tab;};
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(!tab.current.includes('enter_from=newdraft'))return;
      if(stage==='before_fill'&&selector==='#state'&&!env.saves)armed=true;
      if(stage==='saved_marker'&&selector==='#saved')armed=true;
    };
    env.beforeAX=async tab=>{if(stage==='after_save'&&env.saves&&tab.current.includes('enter_from=newdraft'))armed=true;};
    const command=next(base,'create_draft',ordinaryParams('create_draft')),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,stage!=='unchanged');
    if(stage==='unchanged'){assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result,'99');}
    else {assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({platform,stage,response}));assert.ok(!Object.hasOwn(response,'result'));}
    assert.equal(env.creates,1);assert.equal(env.saves,stage==='before_fill'?0:1);
    if(stage==='before_fill')assert.equal(env.events.filter(event=>event[0]==='fill').length,0);
    assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.equal(env.creates,1);assert.equal(env.saves,stage==='before_fill'?0:1);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' creation stops before another fill or save after a confirmed editor switch',async()=>{
  for(const selector of ['#chapter','#title','#body']){
    const {env,base}=await ordinaryFixture(platform);const foreign={scope:'drafts',chapter:22,title:'既有稿标题',body:'既有稿正文',saved:true};
    env.editors.set('22',{...foreign});let changed=false;
    env.afterFill=async(tab,field)=>{if(field===selector&&!changed){changed=true;tab.load(ORIGIN+'/book/10/edit/22?enter_from=modifydraft');}};
    const command=next(base,'create_draft',ordinaryParams('create_draft')),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({platform,selector,response}));
    assert.deepEqual(env.editors.get('22'),foreign,'the other draft remains untouched');
    assert.deepEqual(env.events.filter(event=>event[0]==='fill').map(event=>event[1]),['#chapter','#title','#body'].slice(0,['#chapter','#title','#body'].indexOf(selector)+1));
    assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.deepEqual(env.editors.get('22'),foreign);assert.equal(env.creates,1);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('ordinary creation allows initial ID allocation before binding and same-remote saved URL transitions',async()=>{
  for(const platform of ['qimao','qidian'])for(const lateAllocation of [false,true]){
    const {env,base,origin}=await ordinaryFixture(platform);let allocated=!lateAllocation,transitioned=false,armed=false,urlReads=0;
    const originalAdd=env.add;env.add=url=>{const tab=originalAdd(url),readURL=tab.url.bind(tab);tab.url=async()=>{
      if(armed&&!transitioned&&tab.current.includes('enter_from=newdraft')&&++urlReads===2){transitioned=true;tab.load(ORIGIN+'/book/10/edit/99?enter_from=modifydraft');}
      return readURL();
    };return tab;};
    if(lateAllocation){
      delete base.profile.create_action;base.profile.urls.create=origin+'/book/10/new';
      base.profile.identity.book.templates.push(origin+'/book/{book_id}/new');
      const add=env.add;env.add=url=>{const tab=add(url);if(url===base.profile.urls.create){
        env.creates++;env.editors.set('99',{scope:'drafts',chapter:null,title:'',body:'',saved:false});tab.data=env.editors.get('99');
      }return tab;};
      env.beforeLocatorEvaluate=async(tab,selector)=>{
        const creating=[...env.tabs.values()].find(opened=>opened.current.endsWith('/new'));
        if(!allocated&&selector==='#account'&&creating){allocated=true;creating.load(ORIGIN+'/book/10/edit/99?enter_from=newdraft');}
      };
    }
    env.beforeAX=async tab=>{if(env.saves&&tab.current.includes('enter_from=newdraft'))armed=true;};
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',ordinaryParams('create_draft')));
    assert.equal(response.ok,true,JSON.stringify({platform,lateAllocation,response}));assert.equal(response.result,'99');
    assert.equal(allocated,true);assert.equal(transitioned,true);assert.equal(env.creates,1);assert.equal(env.saves,1);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' final editor snapshot rejects body changes during later guard or header reads',async()=>{
  for(const operation of ['read_draft','find_drafts','create_draft'])for(const stage of ['#account','#chapter','#title','#state']){
    const {env,base}=await ordinaryFixture(platform);let finalBody=false,changed=false;
    env.afterLocatorEvaluateAll=async(tab,selector)=>{
      if(selector==='#body')finalBody=true;
      if(!finalBody||changed||selector!==stage)return;
      changed=true;const editor=[...env.tabs.values()].find(opened=>opened.current.includes('enter_from=modifydraft'));
      editor.data.body='已变更的正文';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,operation,ordinaryParams(operation)));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({operation,stage,response}));
    assert.ok(!Object.hasOwn(response,'result'));assert.equal(env.creates,operation==='create_draft'?1:0);assert.equal(env.saves,operation==='create_draft'?1:0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('preview final snapshot rejects body changes during later account and header reads',async()=>{
  for(const stage of ['#account','#preview-title']){
    const env=environment(),base=await verified(env);let bodyReads=0,changed=false;
    env.afterLocatorEvaluate=async(tab,selector)=>{
      if(selector==='#preview-body')bodyReads++;
      if(bodyReads<2||changed||selector!==stage)return;changed=true;env.editors.get('11').body='稍后改变正文';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({stage,response}));
    assert.equal(env.creates,0);assert.equal(env.saves,0);await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' readback accepts configured same-ID initial redirects and pins the arrival',async()=>{
  for(const mode of ['same_id','different_id','later_return']){
    const {env,base,origin}=await ordinaryFixture(platform);let changed=false;
    const redirected=origin+'/book/10/edit/11?enter_from=canonical';
    base.profile.identity.book.templates.push(origin+'/book/{book_id}/edit/{remote_id}?enter_from=canonical');
    base.profile.editor.remote_id.templates.push(origin+'/book/10/edit/{remote_id}?enter_from=canonical');
    const add=env.add;env.add=url=>{
      const tab=add(url);if(url===base.profile.urls.edit.replace('{remote_id}','11')){
        tab.load(ORIGIN+'/book/10/edit/'+(mode==='different_id'?'22':'11')+'?enter_from=canonical');changed=true;
      }return tab;
    };
    env.afterLocatorEvaluate=async(tab,selector)=>{if(mode==='later_return'&&selector==='#title'&&tab.current.includes('enter_from=canonical'))tab.load(ORIGIN+'/book/10/edit/11?enter_from=modifydraft');};
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(changed,true);
    if(mode==='same_id'){assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result.url,redirected);assert.equal(response.result.body,'原始正文');}
    else assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify({mode,response}));
    assert.equal(env.creates,0);assert.equal(env.saves,0);await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('paragraph paste rechecks focus after the final action URL await without writing another field',async()=>{
  const env=environment(),base=await verified(env);let checked=false,changed=false,pasted=0;
  env.afterLocatorEvaluate=async(tab,selector,fn)=>{if(selector==='#body'&&String(fn).includes('activeElement'))checked=true;};
  const add=env.add;env.add=url=>{const tab=add(url),getURL=tab.url.bind(tab),paste=tab.pasteText.bind(tab);tab.url=async()=>{
    if(checked&&!changed&&tab.current.includes('enter_from=newdraft')){changed=true;tab.focusedBody=false;}
    return getURL();
  };tab.pasteText=async value=>{pasted++;if(!tab.focusedBody)tab.data.title+=value;else await paste(value);};return tab;};
  const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文'}));
  assert.equal(changed,true);assert.equal(response.ok,false);assert.equal(pasted,0);
  assert.equal(env.editors.get('99').title,'验收');assert.equal(env.creates,1);assert.equal(env.saves,0);
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
});

test('create and save action labels cannot turn into publish during a fresh account guard',async()=>{
  for(const stage of ['create','save']){
    const env=environment(),base=await verified(env);let armed=false,changed=false,unsafeClicks=0;
    const add=env.add;env.add=url=>{const tab=add(url),lookup=tab.lookup.bind(tab);tab.lookup=selector=>{
      const nodes=lookup(selector);if(selector===(stage==='create'?'#create':'#save'))for(const node of nodes){
        const click=node.click;node.click=async()=>{if(changed)unsafeClicks++;await click?.();};
        if(changed)node.text='发布';
      }return nodes;
    };return tab;};
    // The pre-existing list tab also uses the same observable label contract.
    for(const tab of env.tabs.values())if(tab.current.endsWith('/drafts')){
      const lookup=tab.lookup.bind(tab);tab.lookup=selector=>{const nodes=lookup(selector);if(stage==='create'&&selector==='#create')for(const node of nodes){const click=node.click;node.click=async()=>{if(changed)unsafeClicks++;await click?.();};if(changed)node.text='发布';}return nodes;};
    }
    env.afterLocatorInnerText=async(tab,selector)=>{if(selector===(stage==='create'?'#create':'#save'))armed=true;};
    env.afterNavigation=async(tab,kind,url)=>{if(url===ORIGIN+'/account'&&armed){changed=true;}};
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文'}));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({stage,response}));assert.equal(unsafeClicks,0);
    assert.equal(env.creates,stage==='create'?0:1);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('snapshot callbacks are self-contained and preserve attributes paragraphs and open shadow roots',()=>{
  const path=Function('return ('+readDOMPath.toString()+')')(),snapshot=Function('return ('+readParagraphs.toString()+')')();
  const env=environment(),tab=env.add(ORIGIN+'/book/10/edit/11?enter_from=modifydraft'),doc=tab.liveDocument();
  const fields=Object.entries(doc.fields).map(([selector,node])=>({name:selector,...path([node]),spec:{source:selector==='#body'?'prosemirror_paragraphs':'dom'}}));
  const packet=snapshot([doc.documentElement],{source:'editor_snapshot',fields});
  assert.equal(packet.ok,true);assert.equal(packet.url,tab.current);assert.deepEqual(packet.fields,{'#chapter':'11','#title':'原稿','#body':'原始正文','#state':'草稿'});
  assert.deepEqual(path([]),{count:0});assert.deepEqual(path([doc.fields['#body'],doc.fields['#body']]),{count:2});
  const body=doc.fields['#body'];body.isConnected=false;assert.equal(path([body]).connected,false);body.isConnected=true;
  // Move the real node behind an open shadow root; paths traverse its host.
  const host=element('DIV'),shadow={host,childNodes:[body],ownerDocument:doc};host.shadowRoot=shadow;
  host.isConnected=true;host.ownerDocument=doc;host.parentNode=doc.documentElement;
  doc.documentElement.childNodes[2]=host;body.parentNode=shadow;
  const shadowPath=path([body]);assert.ok(shadowPath.path.includes('shadow'));
  const shadowPacket=snapshot([doc.documentElement],{source:'editor_snapshot',fields:[{name:'body',...shadowPath,spec:{source:'prosemirror_paragraphs'}}]});
  assert.equal(shadowPacket.fields.body,'原始正文');host.shadowRoot=null;
  assert.equal(snapshot([doc.documentElement],{source:'editor_snapshot',fields:[{name:'body',...shadowPath,spec:{source:'prosemirror_paragraphs'}}]}).ok,false);
  const attr={...doc.fields['#title'],getAttribute:key=>key==='data-title'?'属性标题':null};doc.documentElement.childNodes[1]=attr;
  assert.equal(snapshot([doc.documentElement],{source:'editor_snapshot',fields:[{name:'title',...path([attr]),spec:{source:'dom',attribute:'data-title'}}]}).fields.title,'属性标题');
});

test('final snapshot preview serialization matches the standalone strict preview callback',()=>{
  const snapshot=Function('return ('+readParagraphs.toString()+')')();
  for(const value of ['', '甲', '\n甲\n\n乙\n', '甲  \n　乙']){
    const node=previewDOM(value),expected=readPreviewParagraphs_v1(node),actual=snapshot([node],{source:'fanqie_preview_paragraphs_v1'});
    assert.equal(actual.ok,expected.ok);assert.equal(actual.value,expected.value);
  }
  for(const node of [element('DIV'),element('DIV',{},[element('P',{class:'unknown'},[textNode('正文')]),element('P')]),
    element('DIV',{},[element('P',{},[element('STRONG',{},[textNode('正文')])]),element('P')]),
    element('DIV',{},[element('P',{},[textNode('无末尾空段')])]),
    element('DIV',{},[element('P',{},[element('BR')]),element('P')])]){
    const expected=readPreviewParagraphs_v1(node),actual=snapshot([node],{source:'fanqie_preview_paragraphs_v1'});
    assert.equal(actual.ok,expected.ok);assert.equal(actual.value,expected.value);
  }
});

test('final snapshot failures never restart readiness or retry timed-out field paths',async()=>{
  for(const preview of [false,true])for(const stage of ['path','snapshot']){
    const {env,base}=preview?{env:environment()}:await ordinaryFixture('qimao');
    const requestBase=preview?await verified(env):base;let calls=0;
    env.beforeLocatorEvaluateAll=async(tab,selector,fn)=>{
      if(stage==='path'?fn!==readDOMPath:selector!==':root')return;calls++;throw evaluateTimeout();
    };
    const response=await executeChromeBridgeRequest(env.api,next(requestBase,'read_draft',{remote_id:'11'}));
    assert.equal(response.ok,false);assert.equal(calls,1);assert.equal(env.creates,0);assert.equal(env.saves,0);
    await closeChromeBridgeSession(env.api,requestBase.session_id,{keepReadback:false});
  }
});

test('button state callback is self-contained and checks accessible labels href disabled and focus',()=>{
  const read=Function('return ('+readActionState.toString()+')')(),attrs={'aria-label':'发布','title':'送审','href':'/create','aria-disabled':'true'};
  const el={innerText:' 存草稿 ',getAttribute:key=>attrs[key]??null,ownerDocument:{location:{href:ORIGIN+'/book/10/drafts'}},contains:()=>false};
  el.ownerDocument.activeElement=el;
  assert.deepEqual(read([el]),{count:1,url:ORIGIN+'/book/10/drafts',text:'存草稿',aria:'发布',title:'送审',href:'/create',disabled:true,focused:true,editableFocus:false});
  assert.deepEqual(read([]),{count:0});assert.deepEqual(read([el,el]),{count:2});
});

test('snapshot rechecks duplicate and retargeted selectors and changes during that recheck',async()=>{
  for(const mode of ['duplicate','missing','retarget','body_change']){
    const {env,base}=await ordinaryFixture('qimao','dom',{extended:true});let changed=false,snapshots=0;
    env.afterLocatorEvaluateAll=async(tab,selector,fn)=>{
      if(selector===':root')snapshots++;
      if(changed||fn!==readDOMPath||selector!=='#title'||(mode==='body_change'&&snapshots===0))return;changed=true;
      if(mode==='duplicate'||mode==='missing')env.currentFieldCounts={'#chapter':mode==='duplicate'?2:0};
      if(mode==='retarget')env.retargetChapter=true;
      if(mode==='body_change')tab.data.body='selector复核期间变更';
    };
    const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({mode,response}));
    assert.equal(env.creates,0);assert.equal(env.saves,0);await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('paragraph paste never writes a nested control or noneditable tool after focus moves inside the editor',async()=>{
  for(const control of ['INPUT','TEXTAREA','SELECT','BUTTON','IFRAME','noneditable']){
    const env=environment(),base=await verified(env);let changed=false,pasted=0,controlText='原控件文本';
    env.beforeLocatorEvaluate=async(tab,selector,fn)=>{
      if(selector==='#body'&&String(fn).includes('activeElement')&&!changed){changed=true;env.focusBodyControl=control;}
    };
    const add=env.add;env.add=url=>{const tab=add(url),paste=tab.pasteText.bind(tab);tab.pasteText=async value=>{
      pasted++;if(env.focusBodyControl)controlText+=value;else await paste(value);
    };return tab;};
    const command=next(base,'create_draft',{chapter:12,title:'验收',body:'完整正文'}),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({control,response}));assert.equal(pasted,0);
    assert.equal(controlText,'原控件文本');assert.equal(env.creates,1);assert.equal(env.saves,0);
    assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);assert.equal(env.creates,1);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

// Bind every observed draft action link through the account refresh. Popup
// creation already had this fence; same-page creation and saving need it too.
async function actionHrefFixture(stage, {changedHref, initialHref=null, opens='popup'}={}) {
  const env=environment(),config=profile();config.create_action.opens=opens;
  const base=await verified(env,config);let armed=false,changed=false,unsafeClicks=0;
  const selector=stage==='create'?'#create':'#save';
  const decorate=tab=>{
    const lookup=tab.lookup.bind(tab);tab.lookup=name=>{
      const nodes=lookup(name);
      if(name==='#create'&&opens==='same_page')for(const node of nodes)node.click=async()=>{
        env.creates++;env.editors.set('99',{scope:'drafts',chapter:null,title:'',body:'',saved:false});
        tab.load(ORIGIN+'/book/10/edit/99?enter_from=newdraft');
      };
      if(name!==selector)return nodes;
      for(const node of nodes){
        node.attrs={...(node.attrs||{})};
        const href=changed?changedHref:initialHref;
        if(href==null)delete node.attrs.href;else node.attrs.href=href;
        const click=node.click;node.click=async()=>{
          if(changed)unsafeClicks++;
          await click?.();
        };
      }return nodes;
    };return tab;
  };
  for(const tab of env.tabs.values())decorate(tab);
  const add=env.add;env.add=url=>decorate(add(url));
  env.afterLocatorInnerText=async(tab,name)=>{if(name===selector)armed=true;};
  env.afterNavigation=async(tab,kind,url)=>{
    if(changedHref!==undefined&&armed&&url===ORIGIN+'/account'&&kind==='reload')changed=true;
  };
  return {env,base,state:()=>({changed,unsafeClicks})};
}

for(const stage of ['create','save'])test('draft action href changes fail before clicking '+stage,async()=>{
  for(const initialHref of [null,'/book/10/save-draft']){
    const {env,base,state}=await actionHrefFixture(stage,{initialHref,changedHref:'/book/10/publish',opens:stage==='create'?'same_page':'popup'});
    const command=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(state().changed,true);assert.equal(state().unsafeClicks,0,JSON.stringify({stage,initialHref,response}));
    assert.equal(response.error,'platform_browser_unsafe_'+stage);assert.equal(env.creates,stage==='create'?0:1);assert.equal(env.saves,0);
    assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);assert.equal(state().unsafeClicks,0);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('stable same-page create and save action href forms remain supported',async()=>{
  for(const stage of ['create','save'])for(const initialHref of [null,'','/book/10/save-draft',ORIGIN+'/book/10/save-draft']){
    const {env,base}=await actionHrefFixture(stage,{initialHref,opens:'same_page'});
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'验收',body:'正文'}));
    assert.equal(response.ok,true,JSON.stringify({stage,initialHref,response}));assert.equal(env.creates,1);assert.equal(env.saves,1);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

for(const platform of ['qimao','qidian'])test('ordinary '+platform+' stops subsequent field and save actions when loaded draft state changes',async()=>{
  for(const stage of ['#chapter','#title','#save'])for(const change of ['published','missing','duplicate']){
    const {env,base}=await ordinaryFixture(platform);let changed=false;
    const mutate=()=>{changed=true;if(change==='published')env.editorState='已发布';else env.currentFieldCounts={'#state':change==='duplicate'?2:0};};
    env.afterFill=async(tab,selector)=>{if(selector===stage&&!changed)mutate();};
    env.afterLocatorInnerText=async(tab,selector)=>{if(stage==='#save'&&selector==='#save'&&!changed)mutate();};
    const command=next(base,'create_draft',ordinaryParams('create_draft')),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);assert.equal(response.ok,false,JSON.stringify({platform,stage,change,response}));
    assert.deepEqual(env.events.filter(event=>event[0]==='fill').map(event=>event[1]),stage==='#save'?['#chapter','#title','#body']:['#chapter','#title'].slice(0,stage==='#chapter'?1:2));
    assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.equal(env.creates,1);assert.equal(env.saves,0);await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('current draft action state supports attribute fields and all ordinary DOM source forms',async()=>{
  for(const source of [undefined,null,'dom'])for(const bodySource of ['dom','prosemirror_paragraphs']){
    const {env,base}=await ordinaryFixture('qimao','dom',{bodySource});
    base.profile.editor.state.attribute='data-state';
    if(source===undefined)delete base.profile.editor.state.source;else base.profile.editor.state.source=source;
    const add=env.add;env.add=url=>{const tab=add(url),lookup=tab.lookup.bind(tab);tab.lookup=selector=>{
      const nodes=lookup(selector);if(selector==='#state')for(const node of nodes){node.text='非状态展示';node.attrs={'data-state':'草稿'};}return nodes;
    };return tab;};
    const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',ordinaryParams('create_draft')));
    assert.equal(response.ok,true,JSON.stringify({source,bodySource,response}));assert.equal(env.creates,1);assert.equal(env.saves,1);
    await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});

test('current draft action state timeout cannot retry or continue a later write',async()=>{
  const {env,base}=await ordinaryFixture('qidian');let armed=false,calls=0;
  env.afterFill=async(tab,selector)=>{if(selector==='#chapter')armed=true;};
  env.beforeLocatorEvaluateAll=async(tab,selector)=>{if(armed&&selector==='#state'){calls++;throw evaluateTimeout();}};
  const command=next(base,'create_draft',ordinaryParams('create_draft')),response=await executeChromeBridgeRequest(env.api,command);
  assert.equal(response.ok,false);assert.equal(calls,1);assert.deepEqual(env.events.filter(event=>event[0]==='fill').map(event=>event[1]),['#chapter']);
  assert.equal(env.creates,1);assert.equal(env.saves,0);assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);assert.equal(calls,1);
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
});

test('current draft state snapshot preserves same-ID configured creation URL transitions only',async()=>{
  for(const mode of ['same_id','other_id','other_book','unknown_url']){
    const {env,base}=await ordinaryFixture('qimao');let changed=false;
    env.editors.set('22',{scope:'drafts',chapter:22,title:'既有稿',body:'旧正文',saved:true});
    env.beforeLocatorEvaluateAll=async(tab,selector)=>{
      if(selector==='#state'&&!changed&&tab.current.includes('enter_from=newdraft')){
        changed=true;tab.load(ORIGIN+'/book/'+(mode==='other_book'?'20':'10')+'/edit/'+(mode==='other_id'?'22':'99')+'?enter_from='+(mode==='unknown_url'?'unexpected':'modifydraft'));
      }
    };
    const command=next(base,'create_draft',ordinaryParams('create_draft')),response=await executeChromeBridgeRequest(env.api,command);
    assert.equal(changed,true);
    if(mode==='same_id'){assert.equal(response.ok,true,JSON.stringify(response));assert.equal(response.result,'99');assert.equal(env.saves,1);}
    else{assert.equal(response.ok,false,JSON.stringify({mode,response}));assert.equal(env.saves,0);assert.equal(env.events.filter(event=>event[0]==='fill').length,0);}
    assert.equal(env.creates,1);assert.equal((await executeChromeBridgeRequest(env.api,command)).ok,false);
    assert.equal(env.editors.get('22').body,'旧正文');await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  }
});
