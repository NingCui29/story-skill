/* Synthetic public Qoder SDK contract; no SDK import, browser, files or network. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {setupQoderBrowserRuntime} from '../skills/story-skill/scripts/story_chrome_worker.mjs';
import {chromeDriverAPI} from '../skills/story-skill/scripts/story_chrome_worker.mjs';

const FOCUSED_BODY='textarea:focus, [contenteditable="true"]:focus';

function fixture() {
  const calls = [], state = {turn:'first', denied:false, failure:null, failureAt:null, inserted:null, matches:1, afterCount:null, focusLost:false};
  const invoke = (name, args=[]) => {
    calls.push({name,args,turn:state.turn});
    if (state.denied) throw Object.assign(new Error('Current Qoder turn is unavailable'),{code:'TURN_CONTEXT_REQUIRED'});
    if (state.failure && (!state.failureAt || state.failureAt===name)) throw state.failure;
  };
  class Clipboard {
    #text='unchanged';
    async writeText(text) { invoke('clipboard.writeText',[text]);this.#text=text; }
    async readText() { invoke('clipboard.readText');return this.#text; }
  }
  class Cua {
    #bound='draft-tab';
    async type(value) { assert.equal(this.#bound,'draft-tab');invoke('cua.type',[value]);state.inserted=value.text.replaceAll('\n','');return 'typed'; }
    async scroll(value) { invoke('cua.scroll',[value]); }
  }
  class Ax {
    #bound='draft-tab';
    async typeText(text) { assert.equal(this.#bound,'draft-tab');invoke('ax.typeText',[text]);state.inserted=text.replaceAll('\n','');return 'typed'; }
    async scroll(...args) { assert.equal(this.#bound,'draft-tab');invoke('ax.scroll',args); }
  }
  class Locator {
    #selector;
    constructor(selector) { this.#selector=selector; }
    locator(value) { return new Locator(this.#selector+' '+value); }
    nth(index) { return new Locator(this.#selector+':'+index); }
    async count() { invoke('locator.count',[this.#selector]);const count=state.matches;state.afterCount?.();return count; }
    async fill(text,options) {
      assert.equal(this.#selector,FOCUSED_BODY);invoke('locator.fill',[this.#selector,text,options]);
      if(state.focusLost)throw Object.assign(new Error('Focused body no longer matches'),{code:'LOCATOR_NOT_FOUND'});
      state.inserted=text;return 'filled';
    }
    async evaluateAll(fn,arg) { invoke('locator.evaluateAll',[this.#selector]);return fn([{value:'plain'}],arg); }
    async pressSequentially(text, options) { invoke('locator.pressSequentially',[text,options]); }
  }
  class Playwright {
    #bound='draft-tab';
    locator(selector) { assert.equal(this.#bound,'draft-tab');return new Locator(selector); }
    getByRole(role,options) { return new Locator(role+options.name); }
    async domSnapshot() { invoke('domSnapshot');return 'public DOM'; }
    async evaluate(fn,arg) { invoke('evaluate');return fn(arg); }
  }
  class Tab {
    #url='https://example.test/draft';
    id='tab';browserId='chrome';playwright=new Playwright();cua=new Cua();clipboard=new Clipboard();
    ax=new Ax();
    async url() { invoke('url');return this.#url; }
    async goto(url) { invoke('goto',[url]);this.#url=url; }
    async reload() { invoke('reload'); }
    async close() { invoke('close'); }
    async markDeliverable() { invoke('markDeliverable'); }
  }
  const tab = new Tab(), descriptors=[{id:'chrome',family:'chrome',type:'extension'}], tabList=[{id:'tab',url:'https://example.test/draft'}];
  class Tabs {
    #tab=tab;
    async list() { invoke('tabs.list');return tabList; }
    async get(id) { assert.equal(id,this.#tab.id);invoke('tabs.get',[id]);return this.#tab; }
    async new() { invoke('tabs.new');return this.#tab; }
  }
  const userTab={id:'opaque-once',providerTabId:'7',title:'draft',url:'https://example.test/draft'};
  class User {
    #tab=tab;
    async openTabs() { invoke('user.openTabs');return [userTab]; }
    async claimTab(value) { invoke('user.claimTab',[value]);assert.equal(value,userTab);return this.#tab; }
  }
  class Browser {
    #name='Chrome';browserId='chrome';tabs=new Tabs();user=new User();
    async documentation() { invoke('documentation');return this.#name; }
    async nameSession(name) { invoke('nameSession',[name]);this.#name=name; }
  }
  const browser=new Browser();
  class Browsers {
    #browser=browser;
    async list() { invoke('browsers.list');return descriptors; }
    async get(id) { assert.equal(id,this.#browser.browserId);invoke('browsers.get',[id]);return this.#browser; }
  }
  const agent={browsers:new Browsers(),documentation:{get:async()=> 'SDK reference'}};
  const sdk={async createBrowserAgent(...args){assert.equal(this,sdk);assert.deepEqual(args,[]);invoke('createBrowserAgent');return agent;}};
  return {sdk,agent,browser,tab,descriptors,tabList,userTab,calls,state};
}

test('Qoder bootstrap uses only the public no-options factory and does no browser inventory',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk);
  assert.deepEqual(f.calls.map(row=>row.name),['createBrowserAgent']);
  assert.equal(agent.documentation,f.agent.documentation);
  assert.equal(await agent.browsers.list(),f.descriptors);
  assert.equal(Object.hasOwn(agent,'model_verified'),false);
});

test('Qoder browser tab user claim and public APIs preserve native receivers and exact descriptors',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),browser=await agent.browsers.get('chrome');
  assert.equal(await browser.documentation(),'Chrome');await browser.nameSession('Draft task');
  assert.equal(await browser.documentation(),'Draft task');assert.equal(await browser.tabs.list(),f.tabList);
  const tab=await browser.tabs.get('tab');assert.equal(tab,await browser.tabs.new());
  const listed=await browser.user.openTabs();assert.equal(listed[0],f.userTab);
  assert.equal(await browser.user.claimTab(listed[0]),tab);assert.equal(tab,await browser.tabs.get('tab'));
  await tab.goto('https://example.test/readback');assert.equal(await tab.url(),'https://example.test/readback');
  assert.notEqual(tab.playwright,f.tab.playwright,'Qoder locator syntax has a public-API facade');assert.equal(tab.clipboard,f.tab.clipboard);assert.equal(tab.cua,f.tab.cua);
  assert.equal(await tab.playwright.locator('p').nth(0).count(),1);
  assert.equal(await tab.playwright.locator('p').evaluateAll((nodes,suffix)=>nodes[0].value+suffix,'!'),'plain!');
  await tab.playwright.locator('input').pressSequentially('标题',{timeoutMs:400});
  await tab.clipboard.writeText('plain');assert.equal(await tab.clipboard.readText(),'plain');
  await tab.ax.scroll([1,2],'down',1);await tab.cua.scroll({x:1,y:2,scrollX:0,scrollY:50});
  await tab.reload();await tab.markDeliverable();await tab.close();
  assert.equal(f.calls.filter(row=>row.name==='close').length,1);
});

test('Qoder paste fills only the focused body once and never invokes LF-dropping AX or CUA',async()=>{
  for(const text of ['', '第一段\n第二段\n', '\n甲\n\n乙\n\n', '  空格　\r\n正文']){
    const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),tab=await (await agent.browsers.get('chrome')).tabs.get('tab');
    const result=await tab.ax.paste(null,text,{format:'text'});
    assert.equal(result,'filled');assert.deepEqual(f.calls.filter(row=>row.name==='locator.count').map(row=>row.args),[[FOCUSED_BODY]]);
    assert.deepEqual(f.calls.filter(row=>row.name==='locator.fill').map(row=>row.args),[[FOCUSED_BODY,text,{timeoutMs:10000}]]);
    assert.equal(f.state.inserted,text);assert.equal(f.calls.filter(row=>['cua.type','ax.typeText'].includes(row.name)).length,0);
    assert.equal(f.calls.some(row=>row.name.startsWith('clipboard.')),false);
    assert.equal(Object.hasOwn(f.tab.ax,'paste'),false,'native SDK instance remains unchanged');
  }
});

test('existing production worker facade consumes Qoder tab locator and single-call paste contracts',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk);
  const api=chromeDriverAPI(await agent.browsers.get('chrome'));
  const tab=await api.getTab('tab');
  assert.equal(await tab.getAXState(),'public DOM');
  assert.equal(await tab.playwright.locator('p').nth(0).count(),1);
  const body='第一段\n\n第二段\n';await tab.pasteText(body);
  assert.deepEqual(f.calls.filter(row=>row.name==='locator.fill').map(row=>row.args),[[FOCUSED_BODY,body,{timeoutMs:10000}]]);
  assert.equal(f.state.inserted,body);assert.equal(f.calls.filter(row=>['cua.type','ax.typeText'].includes(row.name)).length,0);
  assert.equal(f.calls.some(row=>row.name.startsWith('clipboard.')),false);
});

test('Qoder paste refuses non-plain targets formats extra fields and missing capability without dispatch',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),tab=await (await agent.browsers.get('chrome')).tabs.get('tab');
  for(const args of [[1,'body',{format:'text'}],[null,'body',{format:'html'}],[null,'body',{format:'text',retry:true}],
    [null,1,{format:'text'}],[null,'body'],[null,'body',{format:'text'},{}]]){
    await assert.rejects(()=>tab.ax.paste(...args),{code:'platform_chrome_worker_invalid_config'});
  }
  for(const broken of ['locator','count','fill']){
    const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),tab=await(await agent.browsers.get('chrome')).tabs.get('tab');
    if(broken==='locator')f.tab.playwright.locator=null;
    else f.tab.playwright.locator=()=>({[broken==='count'?'fill':'count']:async()=>1});
    await assert.rejects(()=>tab.ax.paste(null,'body',{format:'text'}),{code:'platform_chrome_worker_unsupported_sdk'});
    assert.equal(f.calls.some(row=>row.name==='locator.fill'||row.name==='locator.count'||row.name==='ax.typeText'||row.name==='cua.type'||row.name.startsWith('clipboard.')),false);
  }
  for(const matches of [0,2]){
    f.state.matches=matches;await assert.rejects(()=>tab.ax.paste(null,'body',{format:'text'}),{code:'platform_browser_ambiguous_dom'});
  }
  assert.equal(f.calls.filter(row=>row.name==='locator.fill'||row.name==='ax.typeText'||row.name==='cua.type'||row.name.startsWith('clipboard.')).length,0);
  assert.equal(f.state.inserted,null);
  await tab.close();assert.equal(f.calls.filter(row=>row.name==='close').length,1,'cleanup remains accessible');
});

test('Qoder transport failure remains the same unknown failure with no paste retry or fallback',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),tab=await (await agent.browsers.get('chrome')).tabs.get('tab');
  const failure=Object.assign(new Error('response lost'),{code:'BROWSER_HOST_STALE'});f.state.failure=failure;f.state.failureAt='locator.fill';
  await assert.rejects(()=>tab.ax.paste(null,'body',{format:'text'}),error=>error===failure);
  assert.equal(f.calls.filter(row=>row.name==='locator.fill').length,1);
  assert.equal(f.calls.filter(row=>['cua.type','ax.typeText'].includes(row.name)).length,0);assert.equal(f.state.inserted,null);
  assert.equal(f.calls.some(row=>row.name.startsWith('clipboard.')),false);
  const moved=fixture(),movedAgent=await setupQoderBrowserRuntime(moved.sdk),movedTab=await(await movedAgent.browsers.get('chrome')).tabs.get('tab');
  moved.state.afterCount=()=>{moved.state.focusLost=true;};
  await assert.rejects(()=>movedTab.ax.paste(null,'body',{format:'text'}),{code:'LOCATOR_NOT_FOUND'});
  assert.equal(moved.calls.filter(row=>row.name==='locator.fill').length,1);assert.equal(moved.state.inserted,null);
  assert.equal(moved.calls.some(row=>['cua.type','ax.typeText'].includes(row.name)||row.name.startsWith('clipboard.')),false);
});

test('Qoder SDK resolves current turn per call and refuses missing current host context',async()=>{
  const f=fixture(),agent=await setupQoderBrowserRuntime(f.sdk),tab=await (await agent.browsers.get('chrome')).tabs.get('tab');
  f.state.turn='second';await tab.ax.paste(null,'one',{format:'text'});
  f.state.turn='third';await tab.playwright.locator('p').count();
  assert.equal(f.calls.find(row=>row.name==='locator.fill').turn,'second');
  assert.equal(f.calls.find(row=>row.name==='locator.count'&&row.args[0]==='p').turn,'third');
  f.state.denied=true;await assert.rejects(()=>agent.browsers.list(),{code:'TURN_CONTEXT_REQUIRED'});
  assert.equal(f.calls.filter(row=>row.name==='browsers.list').length,1);
});

test('Qoder factory errors and incomplete public browser APIs fail without synthetic readiness',async()=>{
  await assert.rejects(()=>setupQoderBrowserRuntime({host:'qoder',model_verified:true}),{code:'platform_chrome_worker_unsupported_sdk'});
  const f=fixture();await assert.rejects(()=>setupQoderBrowserRuntime(f.sdk,{signal:{hostContext:{}}}),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.equal(f.calls.length,0);
  const error=Object.assign(new Error('connector unavailable'),{code:'BROWSER_CONNECTOR_NOT_READY'});
  f.state.failure=error;await assert.rejects(()=>setupQoderBrowserRuntime(f.sdk),failure=>failure===error);
  assert.equal(f.calls.length,1);
  for(const broken of ['agent','browser','tabs','user']){
    const item=fixture();
    if(broken==='agent')item.agent.browsers={};
    else if(broken==='browser')item.browser.documentation=null;
    else if(broken==='tabs')item.browser.tabs.new=null;
    else item.browser.user.claimTab=null;
    if(broken==='agent')await assert.rejects(()=>setupQoderBrowserRuntime(item.sdk),{code:'platform_chrome_worker_unsupported_sdk'});
    else {const agent=await setupQoderBrowserRuntime(item.sdk);await assert.rejects(()=>agent.browsers.get('chrome'),{code:'platform_chrome_worker_unsupported_sdk'});}
    assert.ok(!item.calls.some(row=>['tabs.new','user.claimTab','cua.type','ax.typeText','locator.fill'].includes(row.name)));
  }
});
