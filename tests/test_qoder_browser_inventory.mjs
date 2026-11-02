/* Offline public Qoder inventories and production-worker bootstrap; no SDK/network. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ChromeWorker,readPrivateJSON,setupQoderBrowserRuntime} from '../skills/story-skill/scripts/story_chrome_worker.mjs';

const SCRIPTS=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../skills/story-skill/scripts');
const SESSION='1'.repeat(32),BINDING='2'.repeat(64),INSTANCE='3'.repeat(32);
const BOOK_URL='https://fanqienovel.com/main/writer/chapter-manage/10&test?type=2';
const descriptor=(id='chrome-one')=>({id,name:'Chrome d3bd00',type:'extension',provider:'qoder-browser-connector'});
const publicBrowser=(id='chrome-one')=>({id,browserType:'chrome',browserName:'Chrome',status:'ready'});
function fixture(t) {
  const original=Object.getOwnPropertyDescriptor(globalThis,'nodeRepl');
  // Synthetic turn signals only; production gets the unmodified official object.
  const signal={testTurn:1};globalThis.nodeRepl={signal};
  t.after(()=>{if(original)Object.defineProperty(globalThis,'nodeRepl',original);else delete globalThis.nodeRepl;});
  const calls=[],data={listed:[descriptor()],state:{enabled:true,phase:'ready',browsers:[publicBrowser()]},failure:null};
  class Inventory {
    #receiver=true;
    async getState(options){assert.ok(this.#receiver);calls.push(['getState',options]);if(data.failure)throw data.failure;return data.state;}
  }
  class Browsers {
    #receiver=true;
    async list(){assert.ok(this.#receiver);calls.push(['list']);return data.listed;}
    async get(){throw Error('No browser should be opened by inventory normalization');}
  }
  const agent={browsers:new Browsers()},client=new Inventory();
  const sdk={async createBrowserAgent(...args){assert.equal(this,sdk);assert.deepEqual(args,[]);calls.push(['agent']);return agent;},
    BrowserUse:{async create(options){assert.equal(this,sdk.BrowserUse);calls.push(['create',options]);return client;}}};
  return {sdk,agent,data,calls,signal,client};
}

test('missing Agent family is normalized only through matching public ready Chrome metadata',async t=>{
  const f=fixture(t),raw=f.data.listed,agent=await setupQoderBrowserRuntime(f.sdk);
  assert.deepEqual(f.calls.map(row=>row[0]),['agent'],'bootstrap itself does no inventory');
  const result=await agent.browsers.list();
  assert.deepEqual(result,[{...raw[0],family:'chrome'}]);assert.notEqual(result,raw);
  assert.equal(Object.hasOwn(raw[0],'family'),false,'official descriptor is not mutated');
  assert.equal(f.calls.find(row=>row[0]==='create')[1].signal,f.signal);
  assert.equal(f.calls.find(row=>row[0]==='getState')[1].signal,f.signal);
});

test('public inventory client is reused while each getState receives the current official turn signal',async t=>{
  const f=fixture(t),agent=await setupQoderBrowserRuntime(f.sdk);
  await agent.browsers.list();const second={testTurn:2};globalThis.nodeRepl.signal=second;
  await agent.browsers.list();
  assert.equal(f.calls.filter(row=>row[0]==='create').length,1);
  assert.deepEqual(f.calls.filter(row=>row[0]==='getState').map(row=>row[1].signal),[f.signal,second]);
});

test('explicit family and non-extension descriptors preserve the original list without extra API calls',async t=>{
  const f=fixture(t),agent=await setupQoderBrowserRuntime(f.sdk);
  for(const item of [{...descriptor(),family:'chrome'},{...descriptor(),family:'edge'},
    {...descriptor(),family:''},{...descriptor(),family:null},{...descriptor(),type:'cdp'},
    {...descriptor(),type:'iab'}]) {
    f.data.listed=[item];assert.equal(await agent.browsers.list(),f.data.listed);
  }
  assert.equal(f.calls.some(row=>['create','getState'].includes(row[0])),false);
});

test('Chrome names and provider strings never override disabled missing mismatched or non-ready type evidence',async t=>{
  const f=fixture(t),agent=await setupQoderBrowserRuntime(f.sdk);
  for(const state of [
    {enabled:false,phase:'ready',browsers:[publicBrowser()]},
    {enabled:true,phase:'ready',browsers:[publicBrowser('other-id')]},
    {enabled:true,phase:'ready',browsers:[]},
    ...['edge','chromium',null,undefined].map(browserType=>({enabled:true,phase:'ready',browsers:[{...publicBrowser(),browserType}]})),
    ...['waiting','unavailable','discovering','error','incompatible'].map(status=>({enabled:true,phase:'ready',browsers:[{...publicBrowser(),status}]}))
  ]) {
    f.data.state=state;assert.equal(Object.hasOwn((await agent.browsers.list())[0],'family'),false);
  }
});

test('missing official turn or public BrowserUse contract fails without constructing a replacement context',async t=>{
  const f=fixture(t),agent=await setupQoderBrowserRuntime(f.sdk);
  delete globalThis.nodeRepl;
  await assert.rejects(()=>agent.browsers.list(),{code:'platform_chrome_worker_native_runtime_required'});
  assert.equal(f.calls.some(row=>['create','getState'].includes(row[0])),false);
  globalThis.nodeRepl={signal:f.signal};delete f.sdk.BrowserUse;
  await assert.rejects(()=>agent.browsers.list(),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.equal(f.calls.some(row=>['create','getState'].includes(row[0])),false);
});

test('ambiguous or malformed public inventory is rejected and SDK failures propagate without retry',async t=>{
  const f=fixture(t),agent=await setupQoderBrowserRuntime(f.sdk);
  for(const state of [null,{enabled:1,phase:'ready',browsers:[]},
    {enabled:true,phase:'ready',browsers:[publicBrowser(),publicBrowser()]},
    {enabled:true,phase:'ready',browsers:[{...publicBrowser(),browserType:1}]},
    {enabled:true,phase:'ready',browsers:[{...publicBrowser(),id:''}]},
    {enabled:true,phase:'ready',browsers:[{...publicBrowser(),status:null}]}]) {
    f.data.state=state;await assert.rejects(()=>agent.browsers.list(),{code:'platform_chrome_worker_unsupported_sdk'});
  }
  const error=Object.assign(new Error('official permission denial'),{code:'TURN_CONTEXT_REQUIRED'});f.data.failure=error;
  const before=f.calls.length;await assert.rejects(()=>agent.browsers.list(),failure=>failure===error);
  assert.deepEqual(f.calls.slice(before).map(row=>row[0]),['list','getState']);
});

async function workerFixture(t,change=()=>{}) {
  const f=fixture(t),directory=await fs.mkdtemp(path.join(os.tmpdir(),'story-qoder-inventory-'));
  await fs.chmod(directory,0o700);const book=path.join(directory,'book'),run=path.join(directory,'run');
  await fs.mkdir(book,{mode:0o700});await fs.mkdir(run,{mode:0o700});
  const config={schema:1,book_root:book,book_id:'book',plan_id:'plan',profile_id:'profile.json',
    target:{platform:'fanqie',account_id:'123',remote_book_id:'10'},handle_path:path.join(run,'handle.json'),
    binding_sha256:BINDING,worker:{pid:process.pid,instance:INSTANCE},python_executable:path.join(directory,'python3'),
    story_cli:path.join(SCRIPTS,'story.py'),driver_path:path.join(SCRIPTS,'story_chrome_bridge.mjs'),
    sdk_path:'/Applications/Qoder CN.app/Contents/Resources/node-repl/node_modules/@qoder-space/browser-use/index.js',
    execution_host:'qoder-cn',native_model_authorized:true,health_interval_seconds:15,idle_timeout_seconds:120,
    checkpoint_ms:1,poll_interval_ms:1,browser_id:null,stop_path:path.join(run,'stop')};
  const events=[],tabs=new Map();let seq=0,now=Date.now();
  const makeTab=(url,id=String(++seq))=>{
    let current=url;const tab={id,url:async()=>current,goto:async value=>{current=value;events.push(['goto',id]);},
      close:async()=>{tabs.delete(id);events.push(['close',id]);},
      playwright:{domSnapshot:async()=>{events.push(['snapshot',id]);return 'synthetic book management';}}};
    tabs.set(id,tab);return tab;
  };
  makeTab(BOOK_URL,'original');
  const browser={browserId:'chrome-one',documentation:async()=> 'Official public reference',nameSession:async()=>{},
    tabs:{list:async()=>await Promise.all([...tabs].map(async([id,tab])=>({id,url:await tab.url()}))),
      get:async id=>{assert.ok(tabs.has(id));return tabs.get(id);},new:async()=>{events.push(['new']);return makeTab('about:blank');}},
    user:{openTabs:async()=>[],claimTab:async()=>{throw Error('unexpected claim');}}};
  f.agent.browsers.get=async id=>{assert.equal(id,'chrome-one');events.push(['getBrowser',id]);return browser;};
  const command=async(_config,action,extra=[])=>{
    events.push(['command',action]);
    if(action==='attach'){
      await fs.writeFile(config.handle_path,JSON.stringify({schema:1,session_id:SESSION,book_root:book,book_id:'book',binding_sha256:BINDING,token:'synthetic'}),{mode:0o600,flag:'wx'});
      return {ok:true,session_id:SESSION};
    }
    if(action==='health'){
      if(!extra.length)return {ok:true,challenge:{schema:1,session_id:SESSION,binding_sha256:BINDING,nonce:'4'.repeat(32),deadline_epoch:now/1000+30}};
      const packet=await readPrivateJSON(extra[1]);assert.deepEqual(packet.browser,{kind:'chrome',id:'chrome-one'});
      assert.ok(events.some(row=>row[0]==='snapshot'));return {ok:true,ready:true};
    }
    if(action==='next')return {ok:true,request_id:null,request_path:null,claimed_once:false};
    if(action==='detach')return {ok:true,detached:true};
    throw Error('unexpected command '+action);
  };
  config.bootstrap_deadline_ms=now+120000;change(f,config);
  const driver={executeChromeBridgeRequest:async()=>{throw Error('no queue operations authorized in bootstrap fixture');},closeChromeBridgeSession:async()=>{}};
  const worker=new ChromeWorker(config,{sdkFactory:()=>setupQoderBrowserRuntime(f.sdk),command,driver,clock:()=>now,sleep:async ms=>{now+=ms;}});
  t.after(async()=>{await worker.stop();await fs.rm(directory,{recursive:true,force:true});});
  return {...f,worker,events,tabs,run,config};
}

test('real-shaped Qoder descriptor reaches production attach and health then cleans only its owned probe',async t=>{
  const f=await workerFixture(t),result=await f.worker.checkpoint();
  assert.equal(result.done,false,JSON.stringify(result));assert.equal(result.state,'ready');assert.equal(result.processed,0);
  assert.equal(f.events.filter(row=>row[0]==='command'&&row[1]==='attach').length,1);
  assert.ok(f.events.findIndex(row=>row[0]==='snapshot')<f.events.findIndex(row=>row[0]==='command'&&row[1]==='attach'));
  assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.tabs.size,2);
  await f.worker.stop();const final=await readPrivateJSON(path.join(f.run,'worker-final.json'));
  assert.equal(final.cleanup_complete,true);assert.equal(final.bridge_detached,true);assert.equal(final.owned_tabs_remaining,0);
  assert.equal(f.tabs.size,1);assert.ok(f.tabs.has('original'));
});

test('production bootstrap does not treat a Chrome label with Edge type as Chrome or attach a queue',async t=>{
  const f=await workerFixture(t,item=>{item.data.state.browsers[0].browserType='edge';});
  const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.code,'platform_chrome_worker_browser_wait_timeout');
  assert.equal(result.processed,0);assert.equal(f.events.some(row=>['command','new','getBrowser'].includes(row[0])),false);
});

test('two public Chrome IDs still fail the production ambiguity guard before selecting a browser',async t=>{
  const f=await workerFixture(t,item=>{item.data.listed.push(descriptor('second'));item.data.state.browsers.push(publicBrowser('second'));});
  const result=await f.worker.checkpoint();assert.equal(result.code,'platform_chrome_worker_ambiguous_browser');
  assert.equal(result.processed,0);assert.equal(f.events.some(row=>['command','new','getBrowser'].includes(row[0])),false);
});
