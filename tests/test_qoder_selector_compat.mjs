/* Public Locator filter/has/getByText contract in memory; no SDK/browser. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import {setupQoderBrowserRuntime,chromeDriverAPI} from '../skills/story-skill/scripts/story_chrome_worker.mjs';

const ACCOUNT='.user-info-line:has(> label:text-is("番茄小说网ID")) > div > span';
const EMPTY_CHAPTERS='.arco-tabs-content-item-active .author-empty.author-empty-default.empty > span.author-empty-description.font-4:text-is("暂无章节内容")';
const EMPTY_DRAFTS='.arco-tabs-content-item-active :text-is("暂无草稿")';
const CHAPTER_SPAN=EMPTY_CHAPTERS.slice(0,EMPTY_CHAPTERS.indexOf(':text-is('));
const node=text=>({textContent:text});
const row=(label,value)=>({labels:[node(label)],values:[node(value)],nestedLabels:[]});
const panel=(active,chapterTexts=[],draftTexts=[])=>({active,chapterSpans:chapterTexts.map(node),draftLeaves:draftTexts.map(node)});

async function fixture(){
  const calls=[],state={rows:[row('番茄小说网ID','12345')],panels:[panel(true,['暂无章节内容'],['暂无草稿'])]};
  const query=(selector,scope)=>{
    if(selector.includes(':text-is('))throw Error('Native qSA does not implement :text-is');
    if(selector==='.user-info-line')return state.rows;
    if(selector===':scope > label')return scope?.labels||[];
    if(selector===':scope > div > span')return scope?.values||[];
    if(selector==='.arco-tabs-content-item-active')return state.panels.filter(p=>p.active);
    if(selector===CHAPTER_SPAN)return state.panels.filter(p=>p.active).flatMap(p=>p.chapterSpans);
    if(selector==='span')return scope?.values||[];
    if(selector==='input')return [node('ordinary')];
    throw Error('Unknown synthetic CSS selector '+selector);
  };
  class Locator {
    #resolve;
    constructor(resolve){this.#resolve=resolve;}
    resolve(scope){return this.#resolve(scope);}
    filter(options){
      calls.push(['filter',options]);assert.deepEqual(Object.keys(options),options.has?['has']:['hasText']);
      if(options.has){assert.ok(options.has instanceof Locator);return new Locator(scope=>this.resolve(scope).filter(n=>options.has.resolve(n).length>0));}
      assert.ok(options.hasText instanceof RegExp);
      return new Locator(scope=>this.resolve(scope).filter(n=>options.hasText.test(n.textContent)));
    }
    locator(selector,options){calls.push(['child',selector,options]);return new Locator(scope=>this.resolve(scope).flatMap(n=>query(selector,n)));}
    getByText(text,options){calls.push(['getByText',text,options]);assert.deepEqual(options,{exact:true});
      return new Locator(scope=>this.resolve(scope).flatMap(p=>p.draftLeaves).filter(n=>n.textContent.replace(/\s+/g,' ').trim()===text));}
    nth(index){return new Locator(scope=>this.resolve(scope).slice(index,index+1));}
    async count(){calls.push(['count']);return this.resolve().length;}
    async evaluate(fn,arg){calls.push(['evaluate']);const rows=this.resolve();
      if(rows.length!==1)throw Object.assign(new Error('Public Locator must resolve unambiguously'),{code:'platform_browser_ambiguous_dom'});
      return fn(rows[0],arg);}
    async evaluateAll(fn,arg){calls.push(['evaluateAll']);return fn(this.resolve(),arg);}
    async innerText(){return this.evaluate(n=>n.textContent);}
    async isVisible(){return this.evaluate(()=>true);}
  }
  const playwright={locator(selector){calls.push(['locator',selector]);return new Locator(scope=>query(selector,scope));},
    evaluate(){throw Error('Page evaluate must not be used for selector compatibility');},domSnapshot:async()=> 'synthetic'};
  const rawTab={id:'tab',playwright,url:async()=> 'https://example.test/account'};
  const browser={browserId:'chrome',documentation:async()=> 'public',nameSession:async()=>{},
    tabs:{get:async()=>rawTab,new:async()=>rawTab,list:async()=>[]},user:{openTabs:async()=>[],claimTab:async()=>rawTab}};
  const sdk={createBrowserAgent:async()=>({browsers:{list:async()=>[{id:'chrome',type:'extension',family:'chrome'}],get:async()=>browser}})};
  const agent=await setupQoderBrowserRuntime(sdk),wrapped=await agent.browsers.get('chrome'),tab=await wrapped.tabs.get('tab');
  return {calls,state,tab,rawTab,browser:wrapped,Locator};
}

test('all three production Fanqie text-is forms use only documented native locator composition',async()=>{
  const source=await fs.readFile(new URL('../skills/story-skill/scripts/story_platform_browser.py',import.meta.url),'utf8');
  assert.equal([...source.matchAll(/:text-is\(/g)].length,3,'new profile extensions require explicit compatibility coverage');
  const f=await fixture();
  for(const [selector,text] of [[ACCOUNT,'12345'],[EMPTY_CHAPTERS,'暂无章节内容'],[EMPTY_DRAFTS,'暂无草稿']]){
    const locator=f.tab.playwright.locator(selector);assert.equal(await locator.count(),1);assert.equal(await locator.innerText(),text);
  }
  assert.ok(f.calls.filter(c=>c[0]==='locator').every(c=>!c[1].includes(':text-is(')));
  const has=f.calls.find(c=>c[0]==='filter'&&c[1].has);assert.ok(has);
  assert.deepEqual(f.calls.find(c=>c[0]==='child'),['child',':scope > div > span',{}]);
  assert.deepEqual(f.calls.find(c=>c[0]==='getByText'),['getByText','暂无草稿',{exact:true}]);
});

test('account label is exact and direct-child scoped, never an adjacent or nested label',async()=>{
  const f=await fixture();f.state.rows=[row('番茄小说网ID副本','wrong-a'),row('其他ID','wrong-b')];
  f.state.rows[1].nestedLabels=[node('番茄小说网ID')];
  const locator=f.tab.playwright.locator(ACCOUNT);assert.equal(await locator.count(),0);
  await assert.rejects(()=>locator.innerText(),{code:'platform_browser_ambiguous_dom'});
  f.state.rows.push(row('  番茄小说网ID\n','correct'));assert.equal(await locator.count(),1);
  assert.equal(await locator.innerText(),'correct');
});

test('duplicate identity rows or duplicate value spans remain ambiguous rather than choosing the first',async()=>{
  const f=await fixture(),locator=f.tab.playwright.locator(ACCOUNT);
  f.state.rows.push(row('番茄小说网ID','other'));assert.equal(await locator.count(),2);
  await assert.rejects(()=>locator.evaluate(n=>n.textContent),{code:'platform_browser_ambiguous_dom'});
  f.state.rows.splice(1);f.state.rows[0].values.push(node('other'));
  assert.equal(await locator.count(),2);await assert.rejects(()=>locator.innerText(),{code:'platform_browser_ambiguous_dom'});
});

test('empty markers remain exact and restricted to the currently active panel',async()=>{
  const f=await fixture();f.state.panels=[panel(false,['暂无章节内容'],['暂无草稿']),
    panel(true,['暂无章节内容，请重试'],['暂无草稿，但待审核'])];
  for(const selector of [EMPTY_CHAPTERS,EMPTY_DRAFTS]){
    const locator=f.tab.playwright.locator(selector);assert.equal(await locator.count(),0);
    await assert.rejects(()=>locator.innerText(),{code:'platform_browser_ambiguous_dom'});
  }
  f.state.panels[1]=panel(true,['暂无章节内容'],['暂无草稿']);
  for(const selector of [EMPTY_CHAPTERS,EMPTY_DRAFTS])assert.equal(await f.tab.playwright.locator(selector).count(),1);
  f.state.panels[1]=panel(true,['暂无章节内容','暂无章节内容'],['暂无草稿','暂无草稿']);
  for(const selector of [EMPTY_CHAPTERS,EMPTY_DRAFTS]){
    const locator=f.tab.playwright.locator(selector);assert.equal(await locator.count(),2);
    await assert.rejects(()=>locator.innerText(),{code:'platform_browser_ambiguous_dom'});
  }
});

test('every read resolves current DOM after count and never keeps a stale identity or empty marker',async()=>{
  const f=await fixture(),locator=f.tab.playwright.locator(ACCOUNT);assert.equal(await locator.count(),1);
  f.state.rows[0].labels[0].textContent='其他ID';
  await assert.rejects(()=>locator.innerText(),{code:'platform_browser_ambiguous_dom'});
  f.state.rows=[row('番茄小说网ID','new-current-id')];assert.equal(await locator.innerText(),'new-current-id');
  const empty=f.tab.playwright.locator(EMPTY_DRAFTS);assert.equal(await empty.count(),1);
  f.state.panels[0].active=false;await assert.rejects(()=>empty.innerText(),{code:'platform_browser_ambiguous_dom'});
});

test('production SDK gate reconstructs translated selectors for each locator leaf',async()=>{
  const f=await fixture(),api=chromeDriverAPI(f.browser),tab=await api.getTab('tab'),locator=tab.playwright.locator(ACCOUNT);
  assert.equal(await locator.count(),1);f.state.rows=[row('番茄小说网ID','current')];
  assert.equal(await locator.evaluate(n=>n.textContent),'current');
  assert.equal(f.calls.filter(c=>c[0]==='locator'&&c[1]==='.user-info-line').length,2);
  f.state.rows.push(row('番茄小说网ID','ambiguous'));
  await assert.rejects(()=>locator.evaluate(n=>n.textContent),{code:'platform_browser_ambiguous_dom'});
});

test('unknown text-is forms or options are rejected before native dispatch and plain CSS is unchanged',async()=>{
  const f=await fixture();
  for(const selector of ['label:text-is("番茄小说网ID")',ACCOUNT.replace('label','div'),EMPTY_DRAFTS.replace('暂无草稿','其他'),
    ACCOUNT+' button',EMPTY_CHAPTERS+', input']){
    assert.throws(()=>f.tab.playwright.locator(selector),{code:'platform_chrome_worker_unsupported_sdk'});
  }
  assert.throws(()=>f.tab.playwright.locator(ACCOUNT,{}),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.deepEqual(f.calls,[]);assert.equal(await f.tab.playwright.locator('input').innerText(),'ordinary');
  assert.equal(f.calls[0][1],'input');
});

test('missing native filter capability never falls back to DOM evaluation or broad text selection',async()=>{
  const f=await fixture();f.Locator.prototype.filter=undefined;
  assert.throws(()=>f.tab.playwright.locator(ACCOUNT),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.throws(()=>f.tab.playwright.locator(EMPTY_CHAPTERS),{code:'platform_chrome_worker_unsupported_sdk'});
  f.Locator.prototype.getByText=undefined;
  assert.throws(()=>f.tab.playwright.locator(EMPTY_DRAFTS),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.equal(f.calls.some(c=>['count','evaluate','evaluateAll'].includes(c[0])),false);
});
