/* Qoder public collection contract, including real driver with offline fake DOM. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setupQoderBrowserRuntime,chromeDriverAPI} from '../skills/story-skill/scripts/story_chrome_worker.mjs';
import {executeChromeBridgeRequest,readRowFields,readParagraphs,readDOMPath,readActionState} from '../skills/story-skill/scripts/story_chrome_bridge.mjs';

const missing=selector=>Object.assign(Error('No element matched locator: '+selector),{name:'BrowserAgentRuntimeError',code:'LOCATOR_NOT_FOUND'});
async function wrap(rawTab) {
  const browser={browserId:'chrome',documentation:async()=>'',nameSession:async()=>{},
    tabs:{get:async()=>rawTab,new:async()=>rawTab,list:async()=>[]},user:{openTabs:async()=>[],claimTab:async()=>rawTab}};
  const sdk={createBrowserAgent:async()=>({browsers:{list:async()=>[{id:'chrome',type:'extension',family:'chrome'}],get:async()=>browser}})};
  const agent=await setupQoderBrowserRuntime(sdk),wrapped=await agent.browsers.get('chrome');
  return {tab:await wrapped.tabs.get(rawTab.id),browser:wrapped};
}
async function fixture() {
  const state={nodes:[],failure:null,calls:[],afterCount:null};
  class Locator {
    constructor(selector,resolve=()=>state.nodes){this.selector=selector;this.resolve=resolve;}
    nodes(method){state.calls.push([method,this.selector]);if(state.failure)throw state.failure;const nodes=this.resolve();if(!nodes.length)throw missing(this.selector);return nodes;}
    async count(){const n=this.nodes('count').length;state.afterCount?.();return Object.hasOwn(state,'reportedCount')?state.reportedCount:n;}
    async all(){return Array.from({length:await this.count()},(_,i)=>this.nth(i));}
    nth(index){return new Locator(this.selector+' >> nth='+index,()=>this.resolve().slice(index,index+1));}
    locator(selector){return new Locator(this.selector+' >> '+selector,()=>this.resolve().flatMap(n=>n.children?.[selector]||[]));}
    filter({has}={}){if(has)assert.ok(has instanceof Locator);return new Locator(this.selector+' >> filtered',()=>this.resolve().filter(n=>!has||has.resolve().length));}
    async evaluateAll(fn,arg){return fn(this.nodes('evaluateAll'),arg);}
    async evaluate(fn,arg){const nodes=this.nodes('evaluate');if(nodes.length!==1)throw Error('ambiguous locator');return fn(nodes[0],arg);}
    async innerText(){return this.evaluate(n=>n.innerText);}
    async textContent(){return this.evaluate(n=>n.textContent);}
    async getAttribute(name){return this.evaluate(n=>n.getAttribute(name));}
    async isVisible(){return this.evaluate(()=>true);}
    async isEnabled(){return this.evaluate(()=>true);}
    async click(){return this.evaluate(()=>state.calls.push(['write']));}
  }
  const rawTab={id:'tab',url:async()=> 'https://example.test/',playwright:{locator:s=>new Locator(s),getByRole:r=>new Locator('role='+r),domSnapshot:async()=> 'synthetic DOM',evaluate:()=>{throw Error('No page-eval fallback permitted');}}};
  return {...await wrap(rawTab),state,rawTab};
}

test('zero/one/multiple counts and all preserve collection semantics with live nth locators',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');assert.equal(await loc.count(),0);assert.deepEqual(await loc.all(),[]);
  f.state.nodes=[{innerText:'one'},{innerText:'two'}];assert.equal(await loc.count(),2);
  const all=await loc.all();assert.equal(all.length,2);assert.equal(await all[1].innerText(),'two');
  f.state.nodes=[{innerText:'new'}];assert.equal(await loc.count(),1);assert.equal(await all[0].innerText(),'new');
  await assert.rejects(()=>all[1].innerText(),/No element matched/);
});

test('only exact shared read callbacks obtain their actual empty-array result',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');
  assert.deepEqual(await loc.evaluateAll(readRowFields,[]),{ok:true,rows:[]});
  assert.deepEqual(await loc.evaluateAll(readDOMPath),{count:0});
  assert.deepEqual(await loc.evaluateAll(readActionState),{count:0});
  assert.deepEqual(await loc.evaluateAll(readParagraphs,{source:'dom'}),{ok:false,count:0});
  let called=false;await assert.rejects(()=>loc.evaluateAll(()=>{called=true;return [];}),/No element matched/);assert.equal(called,false);
  const imposter=function readRowFields(){called=true;return {ok:true,rows:[]};};
  await assert.rejects(()=>loc.evaluateAll(imposter,[]),/No element matched/);assert.equal(called,false);
  await assert.rejects(()=>loc.evaluateAll('nodes=>[]',[]),/No element matched/);
});

test('nonempty evaluateAll retains every row and exact text/attribute values',async()=>{
  const f=await fixture(),value=text=>({textContent:text,tagName:'SPAN',getAttribute:()=>null});
  f.state.nodes=['first','second'].map(text=>({querySelectorAll:s=>s==='.title'?[value(text)]:[]}));
  assert.deepEqual(await f.tab.playwright.locator('.row').evaluateAll(readRowFields,[{selector:'.title'}]),{ok:true,rows:[['first'],['second']]});
  f.state.nodes=[{innerText:'line\nend ',textContent:'line\nend ',getAttribute:key=>key==='href'?'/exact':null}];
  const loc=f.tab.playwright.locator('.row');assert.equal(await loc.innerText(),'line\nend ');assert.equal(await loc.textContent(),'line\nend ');assert.equal(await loc.getAttribute('href'),'/exact');
});

test('no missing single-element read or write is converted into an empty successful result',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');
  for(const call of [()=>loc.evaluate(()=>1),()=>loc.innerText(),()=>loc.textContent(),()=>loc.getAttribute('href'),()=>loc.isVisible(),()=>loc.isEnabled(),()=>loc.click()])await assert.rejects(call,/No element matched/);
  assert.equal(f.state.calls.some(c=>c[0]==='write'),false);
});

test('exact selector and error classification reject neighboring selectors, ambiguity, invalid CSS, permission and timeout',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');
  const failures=[missing('.row-other'),Error('No element matched locator: .row\n'),Error('ambiguous locator'),new SyntaxError('Invalid selector'),Error('Permission denied'),Error('Operation timed out'),
    ...['PERMISSION_DENIED','ORIGIN_PERMISSION_REQUIRED','TIMEOUT','TURN_CONTEXT_REQUIRED','OUTCOME_UNKNOWN','HOST_UNREACHABLE','TURN_CANCELLED'].map(code=>Object.assign(missing('.row'),{code}))];
  for(const failure of failures){f.state.failure=failure;for(const call of [()=>loc.count(),()=>loc.all(),()=>loc.evaluateAll(readRowFields,[])])await assert.rejects(call,e=>e===failure);}
});

test('real SDK error requires its exact code; ordinary legacy error remains supported',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');
  for(const code of [undefined,'BROWSER_AGENT_FAILED','PERMISSION_DENIED','TIMEOUT','OTHER']){
    const failure=Object.assign(missing('.row'),{code});f.state.failure=failure;
    for(const call of [()=>loc.count(),()=>loc.all(),()=>loc.evaluateAll(readRowFields,[])])await assert.rejects(call,e=>e===failure);
  }
  f.state.failure=Object.assign(Error('No element matched locator: .row'),{code:'BROWSER_AGENT_FAILED'});
  assert.equal(await loc.count(),0);assert.deepEqual(await loc.evaluateAll(readRowFields,[]),{ok:true,rows:[]});
  f.state.failure=Object.assign(missing('.row'),{name:'PermissionError'});
  await assert.rejects(()=>loc.evaluateAll(readRowFields,[]),e=>e===f.state.failure);
});

test('child and nth recipes normalize only their own fresh collection, with no stale-match cache',async()=>{
  const f=await fixture(),loc=f.tab.playwright.locator('.row');
  f.state.nodes=[{children:{span:[{innerText:'child'}]}}];const child=loc.locator('span');assert.equal(await child.count(),1);
  f.state.nodes=[];assert.equal(await child.count(),0);assert.deepEqual(await child.evaluateAll(readDOMPath),{count:0});
  f.state.nodes=[{innerText:'new'}];assert.equal(await loc.nth(0).count(),1);
  f.state.afterCount=()=>{f.state.nodes=[];};assert.equal(await loc.count(),1);await assert.rejects(()=>loc.innerText(),/No element matched/);
});

test('SDK dispatcher preserves empty collection recovery while retaining current-turn leaf execution',async()=>{
  const f=await fixture(),api=chromeDriverAPI(f.browser),tab=await api.getTab('tab'),loc=tab.playwright.locator('.row');
  assert.equal(await loc.count(),0);assert.deepEqual(await loc.evaluateAll(readRowFields,[]),{ok:true,rows:[]});
  f.state.failure=Error('Qoder turn disconnected');await assert.rejects(()=>loc.count(),e=>e===f.state.failure);
});

test('malformed native counts never become false zero, and public role builders receive the same collection rules',async()=>{
  const f=await fixture(),role=f.tab.playwright.getByRole('button');assert.equal(await role.count(),0);
  f.state.nodes=[{}];
  for(const reportedCount of [null,undefined,'0',-1,0.5,NaN,Infinity]){
    f.state.reportedCount=reportedCount;await assert.rejects(()=>role.count(),{code:'platform_chrome_worker_unsupported_sdk'});
  }
});

// Reuse only existing offline fixture declarations, never their test cases or
// production browser code. A data module preserves the exact shared callback
// imports, so the adapter's function-identity allowlist is exercised end-to-end.
const source=await readFile(new URL('./test_chrome_bridge_driver.mjs',import.meta.url),'utf8');
const bridgeURL=new URL('../skills/story-skill/scripts/story_chrome_bridge.mjs',import.meta.url).href;
const prefix=source.slice(0,source.indexOf("test('Fanqie scans every page"));
const emptyStart=source.indexOf('function emptyChapterFixture('),emptyEnd=source.indexOf("test('batch catalog proves empty book",emptyStart);
assert.ok(emptyStart>0&&emptyEnd>emptyStart);
const fixtureModule=(prefix+'\n'+source.slice(emptyStart,emptyEnd)+'\nexport {emptyChapterFixture,verified,next};')
  .replace("'../skills/story-skill/scripts/story_chrome_bridge.mjs'",JSON.stringify(bridgeURL));
const {emptyChapterFixture,verified,next}=await import('data:text/javascript;base64,'+Buffer.from(fixtureModule).toString('base64'));
async function driverFixture(options={}) {
  const {env,config}=emptyChapterFixture(options),get=env.api.getTab,create=env.api.newTab;
  const wrappedTabs=new WeakMap();let zeroErrors=0;
  const asQoder=async tab=>{
    if(wrappedTabs.has(tab))return wrappedTabs.get(tab);
    const raw=tab.playwright,adapt=locator=>new Proxy(locator,{get(target,name){
      if(['count','evaluateAll'].includes(name))return async(...args)=>{
        if((await target.count())===0){zeroErrors++;throw missing(target.selector);}
        return target[name](...args);
      };
      if(['locator','nth'].includes(name))return (...args)=>adapt(target[name](...args));
      const v=target[name];return typeof v==='function'?v.bind(target):v;
    }});
    tab.playwright=new Proxy(raw,{get(target,name){if(name==='locator')return (...args)=>adapt(target.locator(...args));const v=target[name];return typeof v==='function'?v.bind(target):v;}});
    const wrapped=(await wrap(tab)).tab;wrappedTabs.set(tab,wrapped);return wrapped;
  };
  env.api.getTab=async id=>asQoder(await get(id));env.api.newTab=async url=>asQoder(await create(url));
  return {env,config,get zeroErrors(){return zeroErrors;}};
}

test('complete empty-volume catalog succeeds through Qoder zero errors and keeps all-volume proof',async()=>{
  const f=await driverFixture({sections:['第一卷','第二卷']}),base=await verified(f.env,f.config);
  const result=await executeChromeBridgeRequest(f.env.api,next(base,'inspect_catalog'));
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.result.complete,true);assert.deepEqual(result.result.entries,[]);
  assert.ok(f.zeroErrors>10,'rows, table, paging and rejected virtual controls exercised');assert.ok(f.env.chapterEmptyReads>=6);
  assert.equal(f.env.creates,0);assert.equal(f.env.saves,0);
});

test('zero rows without the explicit empty marker still cannot authorize a new draft',async()=>{
  for(const options of [{missingChapterEmpty:true},{hiddenChapterEmpty:true},{missingEmptyMarker:true}]){
    const f=await driverFixture(options),base=await verified(f.env,f.config),req=next(base,'inspect_catalog');
    req.deadline=new Date(Date.now()+150).toISOString();const result=await executeChromeBridgeRequest(f.env.api,req);
    assert.equal(result.ok,false,JSON.stringify(options));assert.equal(f.env.creates,0);assert.equal(f.env.saves,0);
  }
});

test('later-volume published row and contradictory empty evidence remain blocking through Qoder',async()=>{
  const f=await driverFixture({sections:['第一卷','第二卷']});
  f.env.editors.set('published',{scope:'non_drafts',chapter:1,title:'已有章',body:'保留',saved:true,section:1});
  const base=await verified(f.env,f.config),result=await executeChromeBridgeRequest(f.env.api,next(base,'find_drafts',{chapter:1}));
  assert.equal(result.error,'platform_browser_chapter_exists_not_draft');assert.equal(f.env.creates,0);assert.equal(f.env.saves,0);
  for(const options of [{emptyChapterPagination:true},{virtual:true},{duplicateChapterEmpty:true}]){
    const bad=await driverFixture(options),verifiedBase=await verified(bad.env,bad.config);
    assert.equal((await executeChromeBridgeRequest(bad.env.api,next(verifiedBase,'inspect_catalog'))).ok,false);
    assert.equal(bad.env.creates,0);assert.equal(bad.env.saves,0);
  }
});
