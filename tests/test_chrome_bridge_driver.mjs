/* Offline public-CUA contract tests. No Chrome, network, account or credentials. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executeChromeBridgeRequest, closeChromeBridgeSession, readParagraphs, readDOMValue, readRowFields, readPreviewParagraphs_v1} from '../skills/story-skill/scripts/story_chrome_bridge.mjs';

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
    const env=this.tab.env;
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
  async innerText() { return this.node().text ?? ''; }
  async textContent() { return this.node().text ?? ''; }
  async isVisible() {
    if(this.selector==='.option'&&(this.tab.env.hiddenOptionReads||0)>0){this.tab.env.hiddenOptionReads--;return false;}
    return this.node().visible !== false;
  }
  async isEnabled() { return this.node().enabled !== false; }
  async evaluate(fn,arg) {
    const node=this.node();
    if(['.chapter','.state','.link','.chapter:visible'].includes(this.selector))this.tab.env.events.push(['fieldRead',this.selector]);
    if (node.dom) return fn(node.dom());
    if (node.metrics) return node.metrics();
    return fn(this.dom(node),arg);
  }
  dom(node) {
    return {tagName:node.tag || 'DIV',value:node.value?.() ?? '',innerText:node.text ?? '',textContent:node.text ?? '',
      getAttribute:key => node.attrs?.[key] ?? null,
      querySelectorAll:selector => {
        if((this.tab.env.cssUnsupported || []).includes(selector))throw new SyntaxError('PRIVATE unsupported selector');
        if((this.tab.env.cssErrors || []).includes(selector))throw new Error('PRIVATE DOM proxy error');
        return (node.children?.[selector] || []).map(child=>this.dom(child));
      }};
  }
  async evaluateAll(fn,arg) {
    this.tab.env.events.push(['evaluateAll',this.selector]);
    return fn(this.lookup().map(node=>this.dom(node)),arg);
  }
  async click() {
    this.tab.env.events.push(['click',this.selector]);
    if(this.selector==='#body'&&this.tab.env.failBodyFocus)throw new Error('PRIVATE manuscript focus failure');
    await this.node().click?.();
  }
  async fill(value) {
    this.tab.env.events.push(['fill',this.selector]); this.node().set(value);
    if(this.tab.env.changeAccountAfterFill===this.selector)this.tab.env.account='different-account';
  }
  async press(value) {
    this.tab.env.events.push(['press',this.selector,value]);
    assert.equal(this.selector,'#body');
    if (value === 'Backspace') this.tab.data.body='';
    else if (value === 'Enter') this.tab.data.body+='\n';
    else assert.ok(['Meta+A','Control+A'].includes(value));
  }
  async type() { throw new Error('Paste-like CUA type must not write paragraph bodies'); }
  async pressSequentially(value, options) {
    this.tab.env.events.push(['pressSequentially',this.selector,value]); assert.equal(this.selector,'#body');
    assert.equal(options.delay,0); assert.ok(options.timeoutMs>0);
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
  async goto(url) { assert.notEqual(url,this.current,'same URL must explicitly reload'); this.env.events.push(['goto',url]);this.load(url); }
  async reload() { this.env.events.push(['reload',this.current]);this.load(this.current); }
  async getAXState(options) { assert.equal(options.emit,false);this.env.events.push(['ax',this.id]);return {}; }
  async scroll(coords,direction,pages) { assert.ok(Array.isArray(coords) && coords.length===2);assert.ok(['up','down'].includes(direction));assert.ok(pages>=1);this.env.events.push(['scroll',coords]); }
  async close() {
    this.env.closed.push(this.id);
    if(this.env.closeEditorFailure&&this.current.includes('/edit/'))return;
    this.env.tabs.delete(this.id);
  }
  rows() { return [...this.env.editors.entries()].filter(([,item]) => item.scope===this.scope() && (item.section || 0)===this.section); }
  scope() { return this.current.endsWith('/chapters')?'non_drafts':'drafts'; }
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
        if(env.changeAccountOnReadBody)env.account='different-account';
        return previewDOM(this.data.body);
      }}];
      return [];
    }
    if(this.data) {
      if(selector==='#body')return [{dom:() => {
        const result=bodyDOM(this.data.body);
        if(env.changeAccountOnReadBody)env.account='different-account';
        return result;
      }}];
      if(['#chapter','#title'].includes(selector)) {const key=selector.slice(1);return [{tag:'INPUT',value:() => String(this.data[key] ?? ''),set:value => {this.data[key]=key==='chapter'?Number(value):value;}}];}
      if(selector==='#save')return [{text:env.saveLabel || '存草稿',click:() => {
        env.saves++;this.data.saved=true;
        if(env.dropTrailingNewlinesOnSave)this.data.body=this.data.body.replace(/\n\n$/,'');
      }}];
      if(selector==='#saved')return plain(this.data.saved?'已保存到云端':'尚未保存');
      return [];
    }
    if(selector==='#scope')return plain(this.scope());
    if(selector==='#filter')return plain('全部');
    if(selector==='#ready')return plain('ready');
    if(selector==='#empty')return this.rows().length?[]:plain('暂无草稿');
    if(selector==='#count')return plain('共'+this.rows().length+'篇草稿');
    if(selector==='#scroll')return [{metrics:() => ({height:100,total:100,width:100,wide:100,top:0,x:50,y:50})}];
    if(selector==='.virtual')return env.virtual?[{}]:[];
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
      env.editors.set('99',{scope:'drafts',chapter:null,title:'',body:'',saved:false});
      env.add(ORIGIN+'/book/10/edit/99?enter_from=newdraft');
      if(env.concurrentTarget)env.add(ORIGIN+'/book/10/edit/98?enter_from=newdraft');
      if(env.changeAccountOnCreate)env.account='different-account';
    }}];
    }
    return [];
  }
}
function environment(options = {}) {
  const env={events:[],closed:[],tabs:new Map(),editors:new Map([['11',{scope:'drafts',chapter:11,title:'原稿',body:'原始正文',saved:true}],['1',{scope:'non_drafts',chapter:1,title:'正式章',body:'正式',saved:true}]]),
    account:'stable-id',sections:['第一卷'],creates:0,saves:0,...options};
  let next=0;env.add=url => {const tab=new Tab(env,String(++next),url);env.tabs.set(tab.id,tab);return tab;};
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
  assert.equal(env.events.filter(row => row[0]==='pressSequentially').map(row => row[2]).join('|'),'中文甲|中文乙');
  assert.ok(env.events.some(row=>row[0]==='reload'));assert.ok(env.events.some(row=>row[0]==='scroll'));
  await closeChromeBridgeSession(env.api,base.session_id);assert.ok(env.tabs.has('1'),'pre-existing user tab survives');
  assert.equal(env.tabs.size,2,'latest readback remains');
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
  assert.equal(env.events.filter(row=>row[0]==='reload'&&row[1]===ORIGIN+'/account').length,3,
    'one operation entry, one read start and one read return refresh regardless of catalog loops');
});
test('each new operation refreshes guard rather than trusting prior operation DOM',async () => {
  const env=environment();const base=await verified(env);env.account='different-account';
  const response=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(response.error,'platform_browser_identity_mismatch');assert.equal(env.creates,0);
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
test('account switch after body read is blocked by fresh return guard and never returns stale observation',async () => {
  const env=environment({changeAccountOnReadBody:true});const base=await verified(env);
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(response.error,'platform_browser_identity_mismatch');assert.ok(!Object.hasOwn(response,'result'));
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
  const base=await verified(env,config);env.events=[];
  const result=await executeChromeBridgeRequest(env.api,next(base,'find_drafts',{chapter:12}));
  assert.equal(result.error,'platform_browser_invalid_profile');assert.ok(!env.events.some(row=>row[0]==='evaluateAll'));
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
test('failure to close owned editor blocks fresh preview and consumes its single create request',async()=>{
  const env=environment({closeEditorFailure:true});const base=await verified(env,previewProfile());env.events=[];
  const req=next(base,'create_draft',{chapter:12,title:'验收',body:'正文'});
  const response=await executeChromeBridgeRequest(env.api,req);
  assert.equal(response.error,'platform_browser_editor_in_use');assert.equal(env.creates,1);assert.equal(env.saves,1);
  assert.ok(!env.events.some(row=>row[0]==='newTab'&&row[1].includes('/preview/')));
  assert.equal((await executeChromeBridgeRequest(env.api,req)).error,'platform_browser_operation_failed');assert.equal(env.creates,1);
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
