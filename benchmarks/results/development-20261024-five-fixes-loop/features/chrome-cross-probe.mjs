/* Offline public-CUA contract tests. No Chrome, network, account or credentials. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executeChromeBridgeRequest, closeChromeBridgeSession, readParagraphs, readDOMValue, readDOMPath, readActionState, readRowFields, readPreviewParagraphs_v1} from 'file:///Users/cuining/Documents/GitHub/story-skill/skills/story-skill/scripts/story_chrome_bridge.mjs';

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


const rows=[];
for(const platform of ['qimao','qidian'])for(const timing of ['before','after'])for(const change of ['same','remote','book','unknown']){
 const {env,base}=await ordinaryFixture(platform);let changed=false;
 env.editors.set('88',{scope:'drafts',chapter:88,title:'OLD TITLE',body:'OLD BODY',saved:true});
 const mutate=async(tab,selector)=>{if(selector==='#state'&&!changed&&tab.current.includes('enter_from=newdraft')){
  changed=true;
  const route=change==='same'?'/book/10/edit/99?enter_from=modifydraft':change==='remote'?'/book/10/edit/88?enter_from=modifydraft':change==='book'?'/book/11/edit/99?enter_from=modifydraft':'/unexpected';
  tab.load(ORIGIN+route);
 }};
 env[timing==='before'?'beforeLocatorEvaluateAll':'afterLocatorEvaluateAll']=mutate;
 const response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',ordinaryParams('create_draft')));
 rows.push({platform,timing,change,changed,response,writes:env.events.filter(e=>['fill','click','paste'].includes(e[0])),old:env.editors.get('88')});
 await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
}
console.log(JSON.stringify(rows,null,2));
