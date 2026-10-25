/* Offline Chrome SDK and local-queue contracts. No browser or model starts. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {execFile} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ChromeWorker,chromeDriverAPI,readPrivateJSON,validateWorkerConfig,runChromeWorker} from '../skills/story-skill/scripts/story_chrome_worker.mjs';
import {executeChromeBridgeRequest,closeChromeBridgeSession} from '../skills/story-skill/scripts/story_chrome_bridge.mjs';

const SCRIPTS=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../skills/story-skill/scripts');
const SESSION='1'.repeat(32),BINDING='2'.repeat(64),INSTANCE='3'.repeat(32);
const BOOK_URL='https://fanqienovel.com/main/writer/chapter-manage/10&test?type=2';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const privateJSON=(filename,value)=>fs.writeFile(filename,JSON.stringify(value),{mode:0o600,flag:'wx'});
async function fixture(options={}){
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'story-chrome-worker-'));await fs.chmod(directory,0o700);
  const book=path.join(directory,'book');await fs.mkdir(book,{mode:0o700});
  const run=path.join(directory,'run');await fs.mkdir(run,{mode:0o700});
  const queue=path.join(book,'.story','platform-chrome-bridge',SESSION);await fs.mkdir(queue,{recursive:true,mode:0o700});
  const config={schema:1,book_root:book,book_id:'book',plan_id:'plan',profile_id:'profile.json',
    target:{platform:'fanqie',account_id:'123',remote_book_id:'10'},
    handle_path:path.join(run,'handle.json'),binding_sha256:BINDING,worker:{pid:process.pid,instance:INSTANCE},
    python_executable:path.join(directory,process.platform==='win32'?'python.exe':'python3'),story_cli:path.join(SCRIPTS,'story.py'),driver_path:path.join(SCRIPTS,'story_chrome_bridge.mjs'),
    sdk_path:path.join(directory,'.codex','plugins','cache','openai-bundled','chrome','1','scripts','browser-client.mjs'),
    health_interval_seconds:15,idle_timeout_seconds:120,checkpoint_ms:5,poll_interval_ms:1,browser_id:null,stop_path:path.join(run,'stop')};
  const events=[],nativeTabs=new Map();let id=0;
  const makeTab=(url,tabId=String(++id))=>{
    let current=url;
    const tab={id:tabId,
      url:async()=>current,goto:async value=>{events.push(['goto',tabId,value]);current=value;},
      reload:async()=>events.push(['reload',tabId]),close:async()=>{events.push(['close',tabId]);nativeTabs.delete(tabId);},
      markDeliverable:async()=>events.push(['deliverable',tabId]),screenshot:async options=>{events.push(['screenshot',tabId,options]);return Uint8Array.from([137,80,78,71]);},
      playwright:{domSnapshot:async()=>{events.push(['snapshot',tabId]);if(options.debuggerUnattached)throw Error(options.debuggerMessage??'PRIVATE Debugger unattached');return 'visible management page';},
        evaluate:async()=>({height:800}),locator:selector=>({selector,press:async(value,options)=>events.push(['press',selector,value,options]),
          pressSequentially:async(value,options)=>events.push(['pressSequentially',selector,value,options])})},
      ax:{scroll:async(target,direction,pages)=>events.push(['scroll',tabId,target,direction,pages]),
        paste:async(element,text,options)=>events.push(['paste',tabId,element,text,options])}};
    nativeTabs.set(tabId,tab);return tab;
  };
  const original=makeTab(BOOK_URL,'original');const userObject={id:'user-opaque',providerTabId:'user-id',url:BOOK_URL,title:'test'};
  const browser={browserId:'chrome-one',documentation:async()=>{events.push(['documentation']);return 'Official public Chrome API';},
    nameSession:async name=>events.push(['nameSession',name]),tabs:{
    list:async()=>{events.push(['list']);return options.noAgentTabs?[...nativeTabs].filter(([key])=>key!=='original').map(([id,tab])=>({id,url:tab.currentURL || BOOK_URL})):
      await Promise.all([...nativeTabs].map(async([id,tab])=>({id,url:await tab.url()})));},
    get:async tabId=>{events.push(['get',tabId]);const tab=nativeTabs.get(tabId);if(!tab)throw Error('PRIVATE missing tab');return tab;},
    new:async()=>{events.push(['new']);return makeTab('about:blank');}},
    user:{openTabs:async()=>{events.push(['openTabs']);return options.userTabs??[userObject];},
      claimTab:async object=>{events.push(['claim',object]);assert.ok((options.userTabs??[userObject]).includes(object),'claim exact documented user object');return original;}}};
  const inventory=options.inventory??[{id:'chrome-one',family:'chrome',type:'extension'}];
  const agent={browsers:{list:async()=>{events.push(['browsers']);return inventory;},get:async id=>{events.push(['browser',id]);return browser;}}};
  const requests=[];let health=0,requestSequence=0;const responses=[];
  const command=async(_config,action,extra=[])=>{
    events.push(['cli',action,...extra]);
    if(action==='attach'){
      await privateJSON(config.handle_path,{schema:1,session_id:SESSION,book_root:book,book_id:'book',binding_sha256:BINDING,token:'PRIVATE TOKEN'});
      return {ok:true,session_id:SESSION};
    }
    if(action==='health'){
      if(!extra.length)return {ok:true,challenge:{schema:1,session_id:SESSION,binding_sha256:BINDING,nonce:String(++health).padStart(32,'0'),deadline_epoch:Date.now()/1000+30}};
      const packet=await readPrivateJSON(extra[1]);
      assert.deepEqual(packet.capabilities,{list_tabs:true,get_tab:true,dom_snapshot:true});
      assert.deepEqual(packet.browser,{kind:'chrome',id:'chrome-one'});assert.deepEqual(packet.worker,config.worker);
      assert.ok(events.some(row=>row[0]==='snapshot'),'real DOM read precedes any health capability assertion');
      return {ok:true,ready:true};
    }
    if(action==='next')return requests.shift()||{ok:true,request_id:null,request_path:null,claimed_once:false};
    if(action==='respond'){
      if(options.respondFails)throw Error('PRIVATE unknown receipt failure');
      responses.push(await readPrivateJSON(extra[3]));return {ok:true,response_recorded:true};
    }
    if(action==='detach')return {ok:true,detached:true};
    throw Error('unexpected CLI action '+action);
  };
  const enqueue=async(operation='verify_target',params={})=>{
    const requestId=String(++requestSequence).padStart(32,'a');
    const request={schema:1,id:requestId,session_id:SESSION,binding_sha256:BINDING,nonce:'nonce',
      binding:{target:{...config.target}},operation,params,deadline:new Date(Date.now()+300000).toISOString(),profile:{readback:{url:'https://fanqienovel.com/preview/10&{remote_id}'},urls:{edit:'https://fanqienovel.com/edit/{remote_id}'}}};
    const request_path=path.join(queue,'claimed-'+requestId+'.json');await privateJSON(request_path,request);
    const claim={ok:true,claimed_once:true,request_id:requestId,request_path};requests.push(claim);return claim;
  };
  const driver={executeChromeBridgeRequest:async(_api,request)=>({schema:1,id:request.id,session_id:request.session_id,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:{...config.target}}),
    closeChromeBridgeSession:async()=>events.push(['closeSession'])};
  const worker=new ChromeWorker(config,{sdkFactory:async()=>agent,command,driver});
  return {directory,run,book,config,events,agent,browser,original,makeTab,worker,driver,enqueue,requests,responses,
    cleanup:async()=>{await worker.stop();await fs.rm(directory,{recursive:true,force:true});}};
}
async function until(predicate){const deadline=Date.now()+2000;while(!predicate()){if(Date.now()>deadline)throw Error('condition timed out');await sleep(2);}}

const noChromeInventory=[{id:'iab-one',type:'iab'},{id:'apps-one',type:'mcpapps'}];
function bootstrapClock(f,budget=120000){
  const state={now:Date.now(),start:Date.now(),delays:[]};state.start=state.now;
  if(budget!=null)f.config.bootstrap_deadline_ms=state.start+budget;
  f.worker.clock=()=>state.now;
  f.worker.sleep=async ms=>{state.delays.push(ms);state.now+=ms;};
  return state;
}

test('first Chrome absence waits with bounded backoff then verifies the same binding before attach and ready',async()=>{
  const f=await fixture();const time=bootstrapClock(f),hintPath=path.join(f.run,'bootstrap-state.json');let inventories=0,hints=0;
  const list=f.agent.browsers.list,sleep=f.worker.sleep;
  f.agent.browsers.list=async()=>++inventories<=3?noChromeInventory:list();
  f.worker.sleep=async ms=>{
    if(f.worker.phase==='waiting_for_chrome'){
      hints++;assert.equal(f.worker.state,'connecting');assert.equal(f.worker.attached,false);assert.equal(f.worker.consumed.size,0);
      assert.ok(!f.events.some(row=>row[0]==='cli'||row[0]==='new'||row[0]==='claim'));
      assert.deepEqual(await readPrivateJSON(hintPath),{schema:1,worker_instance:INSTANCE,binding_sha256:BINDING,
        phase:'waiting_for_chrome',observed_at_ms:time.start,bootstrap_deadline_ms:time.start+120000});
      if(process.platform!=='win32')assert.equal((await fs.stat(hintPath)).mode&0o777,0o600);
    }
    await sleep(ms);
  };
  const get=f.agent.browsers.get;
  f.agent.browsers.get=async id=>{await assert.rejects(fs.lstat(hintPath),{code:'ENOENT'});return get(id);};
  try{
    const result=await f.worker.checkpoint();assert.equal(result.state,'ready');assert.equal(result.done,false);
    assert.deepEqual(time.delays.filter(ms=>ms>=1000),[1000,2000,4000]);assert.equal(hints,3);
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1);
    assert.ok(f.events.some(row=>row[0]==='snapshot'));assert.equal(f.worker.processed,0);assert.equal(f.worker.browser.browserId,'chrome-one');
  }finally{await f.cleanup();}
});

test('persistent Chrome absence stops after twenty seconds including old config compatibility without starting the queue',async()=>{
  for(const budget of [120000,null]){
    const f=await fixture();const time=bootstrapClock(f,budget);let inventories=0;
    f.agent.browsers.list=async()=>{inventories++;return noChromeInventory;};
    try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,'failed');
      assert.equal(result.code,'platform_chrome_worker_browser_wait_timeout');assert.equal(result.failure_phase,'waiting_for_chrome');
      assert.equal(time.now-time.start,20000);assert.deepEqual(time.delays,[1000,2000,4000,4000,4000,4000,1000]);assert.equal(inventories,7);
      assert.equal(f.worker.processed,0);assert.equal(f.worker.consumed.size,0);
      assert.ok(!f.events.some(row=>['cli','browser','get','claim','new','goto'].includes(row[0])));
      await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('Chrome waiting consumes only remaining bootstrap time while reserving fifteen seconds for verification',async()=>{
  for(const budget of [23000,15000,10000]){
    const f=await fixture();const time=bootstrapClock(f,budget);
    f.agent.browsers.list=async()=>noChromeInventory;
    try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);
      assert.equal(result.code,budget>15000?'platform_chrome_worker_browser_wait_timeout':'platform_chrome_worker_connect_timeout');
      assert.equal(time.now-time.start,Math.max(0,budget-15000));
      assert.equal(f.config.bootstrap_deadline_ms,time.start+budget,'waiting cannot reset the total deadline');
      assert.ok(!f.events.some(row=>row[0]==='cli'));await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('a late Chrome result cannot outlive the waiting window or the absolute bootstrap deadline',async()=>{
  for(const mode of ['window','total']){
    const f=await fixture();const time=bootstrapClock(f);let calls=0;
    f.agent.browsers.list=async()=>{
      if(++calls===1)return noChromeInventory;
      time.now=time.start+(mode==='window'?20000:120000);
      return [{id:'chrome-one',family:'chrome',type:'extension'}];
    };
    try{
      const result=await f.worker.checkpoint();assert.equal(result.code,mode==='window'?'platform_chrome_worker_browser_wait_timeout':'platform_chrome_worker_connect_timeout');
      assert.equal(calls,2);assert.ok(!f.events.some(row=>row[0]==='browser'||row[0]==='cli'));
      await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('invalid multiple wrong-type and changed pinned browser inventories fail immediately without waiting or switching',async()=>{
  const cases=[
    [null,'platform_chrome_worker_unavailable'],[[null],'platform_chrome_worker_unavailable'],
    [[{id:'unknown',type:'private'}],'platform_chrome_worker_unavailable'],
    [[{id:'unknown',type:'extension',family:{name:'chrome'}}],'platform_chrome_worker_unavailable'],
    [[{id:'cdp',family:'chrome',type:'cdp'}],'platform_chrome_worker_unsupported_sdk'],
    [[{id:'one',family:'chrome',type:'extension'},{id:'two',family:'chrome',type:'extension'}],'platform_chrome_worker_ambiguous_browser'],
    [[{id:'different',family:'chrome',type:'extension'}],'platform_chrome_worker_browser_changed']];
  for(const [inventory,code] of cases){
    const f=await fixture();bootstrapClock(f);f.config.browser_id='chrome-one';let calls=0;
    f.agent.browsers.list=async()=>{calls++;return inventory;};
    try{
      const result=await f.worker.checkpoint();assert.equal(result.code,code);assert.equal(calls,1);
      assert.ok(!f.events.some(row=>row[0]==='browser'||row[0]==='cli'));await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('inventory SDK errors during initial wait stop without another list call and preserve the actual error classification',async()=>{
  for(const failure of [Error('Wrapped Debugger unattached'),Error('The user declined permission for https://fanqienovel.com'),new TypeError('PRIVATE inventory error')]){
    const f=await fixture();const time=bootstrapClock(f);let calls=0;
    f.agent.browsers.list=async()=>{if(++calls===1)return noChromeInventory;throw failure;};
    try{
      const result=await f.worker.checkpoint();assert.equal(calls,2);assert.deepEqual(time.delays,[1000]);
      assert.equal(result.failure_phase,'waiting_for_chrome');assert.equal(result.code,
        failure.message.includes('Debugger')?'platform_chrome_worker_browser_unattached':failure.message.includes('declined')?'platform_chrome_worker_origin_permission_required':'platform_chrome_worker_failed');
      assert.ok(!f.events.some(row=>row[0]==='cli'));await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('an initial inventory error never starts a wait and an immediately present Chrome does not require waiting reserve',async()=>{
  for(const mode of ['error','visible']){
    const f=await fixture();const time=bootstrapClock(f,10000);let calls=0;
    const list=f.agent.browsers.list;
    f.agent.browsers.list=async()=>{calls++;if(mode==='error')throw Error('Debugger unattached');return list();};
    try{
      const result=await f.worker.checkpoint();assert.equal(result.state,mode==='error'?'failed':'ready');
      if(mode==='error'){assert.equal(result.code,'platform_chrome_worker_browser_unattached');assert.equal(calls,1);assert.equal(time.delays.length,0);}
      else assert.ok(f.events.some(row=>row[0]==='cli'&&row[1]==='attach'));
      assert.ok(!time.delays.some(ms=>ms>=1000));await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('initial Chrome waiting honors cancellation and binding changes before another SDK call and clears its hint',async()=>{
  for(const mode of ['stop','binding']){
    const f=await fixture();const time=bootstrapClock(f);let calls=0;
    f.agent.browsers.list=async()=>{calls++;return noChromeInventory;};
    f.worker.sleep=async ms=>{
      time.now+=ms;
      if(mode==='stop')await privateJSON(f.config.stop_path,{schema:1,worker_instance:INSTANCE,keep_readback:false});
      else f.config.target={...f.config.target,remote_book_id:'999'};
    };
    try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(calls,1);
      assert.equal(result.state,mode==='stop'?'stopped':'failed');
      if(mode==='binding')assert.equal(result.code,'platform_chrome_worker_binding_mismatch');
      else assert.equal(result.code,undefined);
      assert.ok(!f.events.some(row=>row[0]==='cli'));await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('an attached or already claimed worker never waits for Chrome or replays its consumed request',async()=>{
  for(const claimed of [false,true]){
    const f=await fixture();const time=bootstrapClock(f);let calls=0;
    try{
      if(claimed)f.worker.consumed.add('a'.repeat(32));else{await f.worker.initialize();f.worker.initialized=true;}
      f.agent.browsers.list=async()=>{calls++;return noChromeInventory;};
      if(claimed){const result=await f.worker.checkpoint();assert.equal(result.code,'platform_chrome_worker_unavailable');}
      else await assert.rejects(f.worker.refreshHealth(),{code:'platform_chrome_worker_unavailable'});
      assert.equal(calls,1);assert.equal(time.delays.length,0);assert.equal(f.worker.processed,0);
      await assert.rejects(fs.lstat(path.join(f.run,'bootstrap-state.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});

test('bootstrap hints are exclusive and an existing foreign file or link is never overwritten or removed',async()=>{
  for(const link of [false,true]){
    const f=await fixture();bootstrapClock(f);const filename=path.join(f.run,'bootstrap-state.json'),target=path.join(f.run,'foreign.json');
    const foreign={schema:1,worker_instance:'9'.repeat(32),phase:'waiting_for_chrome'};
    await privateJSON(link?target:filename,foreign);if(link)await fs.symlink(target,filename);
    f.agent.browsers.list=async()=>noChromeInventory;
    try{
      const result=await f.worker.checkpoint();assert.equal(result.code,'platform_chrome_worker_unsafe_file');
      assert.deepEqual(JSON.parse(await fs.readFile(link?target:filename,'utf8')),foreign);assert.equal((await fs.lstat(filename)).isSymbolicLink(),link);
      assert.ok(!f.events.some(row=>row[0]==='cli'));
    }finally{await f.cleanup();}
  }
});

test('wait cleanup removes only the hint inode created by this worker',async()=>{
  const f=await fixture();const time=bootstrapClock(f),filename=path.join(f.run,'bootstrap-state.json');
  const foreign={schema:1,worker_instance:'9'.repeat(32),phase:'waiting_for_chrome'};
  f.agent.browsers.list=async()=>noChromeInventory;
  f.worker.sleep=async ms=>{
    time.now+=ms;await fs.unlink(filename);await privateJSON(filename,foreign);
    await privateJSON(f.config.stop_path,{schema:1,worker_instance:INSTANCE,keep_readback:false});
  };
  try{
    const result=await f.worker.checkpoint();assert.equal(result.state,'stopped');
    assert.deepEqual(await readPrivateJSON(filename),foreign);assert.ok(!f.events.some(row=>row[0]==='cli'));
  }finally{await f.cleanup();}
});

test('health and driver public SDK leaves share one FIFO including synchronous Locator chains',async()=>{
  const f=await fixture();let active=0,maximum=0,driverFinished=false,healthDuringDriver=false;
  const order=[];
  const observed=(name,fn)=>async(...args)=>{
    active++;maximum=Math.max(maximum,active);order.push(name);
    try{await sleep(1);return await fn(...args);}finally{active--;}
  };
  const nativeLocator=()=>{
    const locator={locator:()=>nativeLocator(),nth:()=>nativeLocator(),privateBypass:async()=>{throw Error('must not expose');}};
    for(const name of ['count','evaluate','evaluateAll','isVisible','isEnabled','getAttribute','innerText','click','fill','press','pressSequentially']){
      locator[name]=observed('driver.'+name,async()=>name==='count'?1:name==='evaluateAll'?[]:true);
    }
    return locator;
  };
  f.original.playwright.locator=()=>nativeLocator();f.original.playwright.getByRole=()=>nativeLocator();
  try{
    await f.worker.initialize();f.worker.initialized=true;
    f.agent.browsers.list=observed('health.inventory',f.agent.browsers.list);
    f.browser.tabs.list=observed('tabs.list',async()=>[{id:'original',url:BOOK_URL}]);
    f.browser.tabs.get=observed('tabs.get',f.browser.tabs.get);
    f.original.url=observed('tab.url',f.original.url);
    f.original.playwright.domSnapshot=observed('snapshot',f.original.playwright.domSnapshot);
    f.original.playwright.evaluate=observed('evaluate',f.original.playwright.evaluate);
    f.original.ax.paste=observed('paste',f.original.ax.paste);f.original.ax.scroll=observed('scroll',f.original.ax.scroll);
    const command=f.worker.command;
    f.worker.command=async(action,args)=>{
      const result=await command(action,args);
      if(action==='health'&&args?.length&&!driverFinished)healthDuringDriver=true;
      return result;
    };
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      const tab=await api.getTab('original');
      const locator=tab.playwright.locator('#body').nth(0).locator('p');
      assert.equal(locator.then,undefined,'builders remain synchronous');assert.equal(locator.privateBypass,undefined);
      assert.equal(tab.playwright.privateBypass,undefined);
      for(const name of ['count','evaluate','evaluateAll','isVisible','isEnabled','getAttribute','innerText','click','fill','press','pressSequentially'])await locator[name]('value');
      await tab.playwright.getByRole('tab',{name:'草稿箱'}).count();
      await tab.playwright.evaluate(()=>1);await tab.getAXState();await tab.pasteText('正文');await tab.scroll([1,2],'down',1);
      await until(()=>healthDuringDriver);
      driverFinished=true;
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:[]};
    };
    const claim=await f.enqueue('find_drafts');f.requests.shift();
    const operation=f.worker.executeClaim(claim);await until(()=>order.includes('driver.count'));
    await Promise.all([operation,f.worker.refreshHealth()]);
    assert.equal(maximum,1,'focus/setup/read calls cannot overlap between health and driver');
    assert.equal(healthDuringDriver,true,'health runs between leaves instead of waiting for the entire operation');
    assert.equal(order.filter(name=>name==='paste').length,1);assert.equal(order.filter(name=>name==='driver.click').length,1);
    assert.equal(f.responses.length,1);assert.equal(f.worker.requestScope,null);
  }finally{await f.cleanup();}
});

test('SDK FIFO releases after refused writes and never replays paste click or press',async()=>{
  const f=await fixture();const counts={paste:0,click:0,press:0};const denied=Error('PRIVATE SDK refusal');
  f.original.ax.paste=async()=>{counts.paste++;throw denied;};
  f.original.playwright.locator=()=>({click:async()=>{counts.click++;throw denied;},press:async()=>{counts.press++;throw denied;}});
  try{
    const api=chromeDriverAPI(f.browser,{sdkGate:f.worker.sdkGate});const tab=await api.getTab('original');
    const outcomes=await Promise.allSettled([tab.pasteText('正文'),tab.playwright.locator('#body').click(),tab.playwright.locator('#body').press('Enter'),tab.getAXState()]);
    for(const result of outcomes.slice(0,3))assert.equal(result.reason,denied);
    assert.equal(outcomes[3].value,'visible management page');assert.deepEqual(counts,{paste:1,click:1,press:1});
    await f.worker.sdkGate.drain();
  }finally{await f.cleanup();}
});

test('queued writes recheck the original deadline before SDK entry while owned cleanup remains possible',async()=>{
  const f=await fixture();let now=1000,release,entered=false,writes=0,closed=0;
  f.worker.clock=()=>now;
  try{
    const api=chromeDriverAPI(f.browser,{sdkGate:f.worker.sdkGate,sdkScope:()=>({deadline:2000})});
    const tab=await api.newTab(BOOK_URL),native=await f.browser.tabs.get(tab.id);
    native.ax.paste=async()=>{writes++;};native.playwright.locator=()=>({click:async()=>{writes++;}});
    native.close=async()=>{closed++;};
    const held=f.worker.sdkGate.run(async()=>{entered=true;await new Promise(resolve=>{release=resolve;});});
    await until(()=>entered);
    const click=tab.playwright.locator('#body').click(),paste=tab.pasteText('正文'),close=tab.close();
    now=2000;release();await held;
    const outcomes=await Promise.allSettled([click,paste,close]);
    assert.equal(outcomes[0].reason.code,'platform_browser_operation_failed');assert.equal(outcomes[1].reason.code,'platform_browser_operation_failed');
    assert.equal(outcomes[2].status,'fulfilled');assert.equal(writes,0);assert.equal(closed,1);
    await f.worker.sdkGate.drain();
  }finally{release?.();await f.cleanup();}
});

test('request deadline and invalid deadline are checked by the worker even when the driver is queued',async()=>{
  for(const invalid of [false,true]){
    const f=await fixture();let reads=0;
    f.original.playwright.locator=()=>({click:async()=>{reads++;}});
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      await (await api.getTab('original')).playwright.locator('#button').click();
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:[]};
    };
    try{
      const claim=await f.enqueue('create_draft'),request=await readPrivateJSON(claim.request_path);
      request.deadline=invalid?'not a deadline':new Date(Date.now()-1).toISOString();await fs.writeFile(claim.request_path,JSON.stringify(request));
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,'failed');assert.equal(reads,0);
      assert.equal(result.code,invalid?'platform_chrome_worker_invalid_packet':'platform_browser_operation_failed');
    }finally{await f.cleanup();}
  }
});

test('an expired health challenge cannot enter the SDK after waiting in the shared queue',async()=>{
  const f=await fixture();let release,entered=false;
  try{
    await f.worker.initialize();f.worker.initialized=true;
    const before=f.events.filter(row=>row[0]==='browsers').length;
    const held=f.worker.sdkGate.run(async()=>{entered=true;await new Promise(resolve=>{release=resolve;});});
    await until(()=>entered);
    const health=f.worker.refreshHealth();const rejection=assert.rejects(health,{code:'platform_chrome_worker_invalid_challenge'});
    await until(()=>f.events.filter(row=>row[0]==='cli'&&row[1]==='health').length>=3);
    f.worker.clock=()=>Date.now()+31000;release();await held;await rejection;
    assert.equal(f.events.filter(row=>row[0]==='browsers').length,before);
    assert.equal(f.worker.healthPromise,null);await f.worker.sdkGate.drain();
  }finally{release?.();await f.cleanup();}
});

test('failed owned close is reported after the gate drains rather than returning a clean stop',async()=>{
  const f=await fixture();let closes=0;
  try{
    await f.worker.initialize();f.worker.initialized=true;
    const tab=await f.worker.api.newTab(BOOK_URL);
    const native=await f.browser.tabs.get(tab.id);native.close=async()=>{closes++;throw Error('PRIVATE close failed');};
    let clock=Date.now();f.worker.clock=()=>clock;f.worker.sleep=async ms=>{clock+=ms;};
    await f.worker.stop();
    assert.equal(f.worker.done,true);assert.equal(f.worker.state,'failed');assert.equal(f.worker.fatalCode,'platform_chrome_worker_cleanup_failed');assert.equal(closes,1);
  }finally{await f.cleanup();}
});

function cleanupAPI(f,operation='create_draft',time={now:Date.now()}){
  f.worker.clock=()=>time.now;
  return chromeDriverAPI(f.browser,{target:f.config.target,sdkGate:f.worker.sdkGate,sdkScope:()=>({operation}),cleanupSleep:async ms=>{time.now+=ms;}});
}
test('retiring a former readback releases its preservation before the one owned close',async()=>{
  const f=await fixture();try{
    const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL);
    assert.equal(await api.rememberReadback(BOOK_URL),tab.id);
    await tab.close();assert.ok((await f.browser.tabs.list()).some(item=>item.id===tab.id),'ordinary cleanup preserves the selected proof');
    assert.throws(()=>api.releaseReadback('original'),{code:'platform_chrome_worker_unsafe_user_tab'});
    api.releaseReadback(tab.id);assert.equal(await api.readbackTab(),null);
    await tab.close();const remaining=await f.browser.tabs.list();
    assert.ok(!remaining.some(item=>item.id===tab.id));assert.ok(remaining.some(item=>item.id==='original'));
    await api.closeCreated();assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===tab.id).length,1);
  }finally{await f.cleanup();}
});
test('successful driver close tolerates delayed open-tab inventory without a second get or close',async()=>{
  const f=await fixture();try{
    const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL),native=await f.browser.tabs.get(tab.id);
    const close=native.close,list=f.browser.tabs.list;let lag=0,calls=0;
    native.close=async()=>{calls++;await close();lag=3;};
    f.browser.tabs.list=async()=>{const rows=await list();return lag-->0?[...rows,{id:tab.id,url:BOOK_URL}]:rows;};
    await tab.close();const gets=f.events.filter(row=>row[0]==='get').length;
    await api.closeCreated();await api.closeCreated();
    assert.equal(calls,1);assert.equal(f.events.filter(row=>row[0]==='get').length,gets,'cleanup uses its recorded handle rather than getting a closed tab');
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{await f.cleanup();}
});
test('throw-after-close and already absent owned pages are accepted only with an actual absence observation',async()=>{
  for(const mode of ['throws_after_close','already_absent']){
    const f=await fixture();try{
      const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL),native=await f.browser.tabs.get(tab.id),close=native.close;let calls=0;
      native.close=async()=>{calls++;await close();throw Error('PRIVATE close response lost');};
      if(mode==='already_absent')await close();else await tab.close();
      await api.closeCreated();await api.closeCreated();assert.equal(calls,mode==='already_absent'?0:1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('a refused close with a still-open target is a real failure and is never resent by repeated cleanup',async()=>{
  const f=await fixture();try{
    const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL),native=await f.browser.tabs.get(tab.id);let calls=0;
    const refusal=Object.assign(Error('PRIVATE close denied'),{code:'permission_denied'});
    native.close=async()=>{calls++;throw refusal;};
    await assert.rejects(api.closeCreated(),failure=>failure===refusal);
    await assert.rejects(api.closeCreated(),failure=>failure===refusal);assert.equal(calls,1);
    assert.ok((await f.browser.tabs.list()).some(item=>item.id===tab.id));
  }finally{await f.cleanup();}
});
test('successful close is not reported as verified cleanup when public inventory is unavailable',async()=>{
  const f=await fixture();try{
    const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL);await tab.close();
    const failure=Error('PRIVATE inventory unavailable');f.browser.tabs.list=async()=>{throw failure;};
    await assert.rejects(api.closeCreated(),error=>error===failure);
    assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===tab.id).length,1);
  }finally{await f.cleanup();}
});
test('cleanup confirmation checks one deadline before and after each awaited inventory without an expiry-time query',async()=>{
  for(const mode of ['late_first_list','late_confirmation','never_disappears']){
    const f=await fixture();try{
      const time={now:Date.now()},api=cleanupAPI(f,'create_draft',time),tab=await api.newTab(BOOK_URL);
      await tab.close();const list=f.browser.tabs.list,start=time.now,observed=[];let reads=0;
      f.browser.tabs.list=async()=>{
        reads++;observed.push(time.now-start);const rows=await list();
        if(mode==='late_first_list'||mode==='late_confirmation'&&reads===2){time.now=start+5001;return rows;}
        return [...rows,{id:tab.id,url:BOOK_URL}];
      };
      await assert.rejects(api.closeCreated(),{code:'platform_chrome_worker_cleanup_failed'});
      assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===tab.id).length,1);
      assert.equal(reads,mode==='late_first_list'?1:mode==='late_confirmation'?2:51);
      assert.ok(observed.every(elapsed=>elapsed<5000),'no new inventory starts at or after expiry');
    }finally{await f.cleanup();}
  }
});
test('classified close failures remain failures even if the owned page has actually disappeared',async()=>{
  const failures=[Object.assign(Error('PRIVATE close permission'),{code:'permission_denied'}),
    Object.assign(Error('PRIVATE unsupported'),{code:'platform_chrome_worker_unsupported_sdk'}),
    Error('The user declined permission for https://fanqienovel.com'),Error('PRIVATE Debugger unattached'),
    Error('PRIVATE operation is not supported'),Object.assign(Error('PRIVATE timeout'),{name:'TimeoutError'})];
  for(const failure of failures){
    const f=await fixture();try{
      const api=cleanupAPI(f),tab=await api.newTab(BOOK_URL),native=await f.browser.tabs.get(tab.id),close=native.close;let calls=0;
      native.close=async()=>{calls++;await close();throw failure;};
      await assert.rejects(tab.close(),error=>error===failure);
      await assert.rejects(api.closeCreated(),error=>error===failure);
      await assert.rejects(tab.close(),error=>error===failure);assert.equal(calls,1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('slow owned closes each execute once before a separate five-second disappearance confirmation window',async()=>{
  const f=await fixture();try{
    const time={now:Date.now()},api=cleanupAPI(f,'create_draft',time),first=await api.newTab(BOOK_URL),second=await api.newTab(BOOK_URL);
    const ids=[first.id,second.id],calls=new Map(ids.map(id=>[id,0])),list=f.browser.tabs.list;let remaining=0;
    for(const id of ids){
      const native=await f.browser.tabs.get(id),close=native.close;
      native.close=async()=>{calls.set(id,calls.get(id)+1);time.now+=6000;await close();remaining=3;};
    }
    f.browser.tabs.list=async()=>{const rows=await list();return remaining-->0?[...rows,...ids.map(id=>({id,url:BOOK_URL}))]:rows;};
    const started=time.now;await api.closeCreated();await api.closeCreated();
    assert.equal(time.now-started,12300,'both slow closes complete before the bounded read-only confirmation polls');
    assert.deepEqual([...calls.values()],[1,1]);assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{await f.cleanup();}
});
test('new raw or claimed popup ownership is physical-ID based and cannot include an initial user page with the same URL',async()=>{
  const url='https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft';
  for(const kind of ['raw','claimed']){
    const f=await fixture();try{
      await f.original.goto(url);const api=cleanupAPI(f);let opened=false,claimed=false;
      const list=f.browser.tabs.list,user={id:'new-user-popup',providerTabId:'new-provider-popup',url,title:'new'};
      f.browser.tabs.list=async()=>(await list()).filter(tab=>kind==='raw'||tab.id!=='new-sdk-popup'||claimed);
      f.browser.user.openTabs=async()=>opened&&kind==='claimed'?[user]:[];
      await api.listTabs();const popup=f.makeTab(url,'new-sdk-popup');opened=true;
      f.browser.user.claimTab=async descriptor=>{assert.equal(descriptor,user);claimed=true;return popup;};
      const listed=await api.listTabs(),candidate=listed.find(item=>kind==='raw'?item.id===popup.id:item.providerTabId===user.providerTabId);
      const owned=await api.getTab(candidate.id);assert.ok(owned);
      const old=await api.getTab('original');await assert.rejects(old.close(),{code:'platform_chrome_worker_unsafe_user_tab'});
      await api.closeCreated();assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===popup.id).length,1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('an initial user claim with different alias and native ID stays protected beside a newly claimed same-URL popup',async()=>{
  const f=await fixture();try{
    const url='https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft';await f.original.goto(url);
    const original={id:'old-user-id',providerTabId:'old-provider-id',url,title:'original'},added={id:'new-user-id',providerTabId:'new-provider-id',url,title:'new'};
    const api=cleanupAPI(f),list=f.browser.tabs.list;let newVisible=false,oldClaimed=false,newClaimed=false,popup;
    f.browser.user.openTabs=async()=>newVisible?[original,added]:[original];
    f.browser.tabs.list=async()=>(await list()).filter(row=>row.id==='original'?oldClaimed:row.id==='new-native-id'?newClaimed:true);
    f.browser.user.claimTab=async descriptor=>{if(descriptor===original){oldClaimed=true;return f.original;}assert.equal(descriptor,added);newClaimed=true;return popup;};
    const initial=await api.listTabs(),old=await api.getTab(initial[0].id);
    assert.notEqual(old.id,'original');await assert.rejects(old.close(),{code:'platform_chrome_worker_unsafe_user_tab'});
    popup=f.makeTab(url,'new-native-id');newVisible=true;
    const candidate=(await api.listTabs()).find(row=>row.providerTabId===added.providerTabId);
    const fresh=await api.getTab(candidate.id);assert.notEqual(fresh.id,popup.id);
    await fresh.close();await api.closeCreated();
    assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===popup.id).length,1);
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{await f.cleanup();}
});
test('popup handles remain in final cleanup even when driver get or close errors are swallowed',async()=>{
  for(const mode of ['get_failed','close_failed']){
    const f=await fixture();try{
      const api=cleanupAPI(f);await api.listTabs();
      const url='https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft',popup=f.makeTab(url,'new-popup');
      await api.listTabs();const owned=await api.getTab(popup.id);const get=f.browser.tabs.get;let closes=0;
      const close=popup.close;popup.close=async()=>{closes++;if(mode==='close_failed')throw Error('PRIVATE true close failure');await close();};
      if(mode==='get_failed')f.browser.tabs.get=async id=>{if(id===popup.id)throw Error('PRIVATE stale get');return get(id);};
      try{await (await api.getTab(owned.id)).close();}catch{/* Mirrors the driver's best-effort closeSession. */}
      if(mode==='close_failed')await assert.rejects(api.closeCreated(),/PRIVATE true close failure/);else await api.closeCreated();
      assert.equal(closes,1);assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('a user popup present before the baseline or outside a create request never becomes cleanup-owned',async()=>{
  const url='https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft';
  for(const initial of [false,true]){
    const f=await fixture();try{
      const api=cleanupAPI(f,initial?'create_draft':'read_draft');let popup;
      if(initial)popup=f.makeTab(url,'user-popup');await api.listTabs();if(!initial)popup=f.makeTab(url,'user-popup');await api.listTabs();
      const tab=await api.getTab(popup.id);await assert.rejects(tab.close(),{code:'platform_chrome_worker_unsafe_user_tab'});
      await api.closeCreated();assert.equal(f.events.filter(row=>row[0]==='close').length,0);
    }finally{await f.cleanup();}
  }
});
test('the independently owned fallback probe also confirms disappearance and accepts a throw-after-close',async()=>{
  for(const mode of ['lag','throw_after_close','still_open']){
    const f=await fixture();let fresh,closes=0,lag=0,clock=Date.now();
    f.worker.clock=()=>clock;f.worker.sleep=async ms=>{clock+=ms;};
    f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};
    const create=f.browser.tabs.new,list=f.browser.tabs.list;
    f.browser.tabs.new=async()=>{
      fresh=await create();const close=fresh.close;
      fresh.close=async()=>{closes++;if(mode==='still_open')throw Error('PRIVATE fallback still open');await close();lag=mode==='lag'?2:0;
        if(mode==='throw_after_close')throw Error('PRIVATE lost close receipt');};return fresh;
    };
    f.browser.tabs.list=async()=>{const rows=await list();return lag-->0?[...rows,{id:fresh.id,url:BOOK_URL}]:rows;};
    try{
      await f.worker.checkpoint();await f.worker.stop();assert.equal(closes,1);
      assert.equal(f.worker.state,mode==='still_open'?'failed':'stopped');
      assert.equal(f.worker.fatalCode,mode==='still_open'?'platform_chrome_worker_cleanup_failed':undefined);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});

test('a driver new-tab allocation crossing its deadline or fatal boundary is owned and closed before refusal',async()=>{
  for(const mode of ['deadline','fatal']){
    const f=await fixture();let allocated,allocatedReads=0;
    const create=f.browser.tabs.new;
    f.browser.tabs.new=async()=>{
      allocated=await create();
      const read=allocated.url;allocated.url=async()=>{allocatedReads++;return read();};
      if(mode==='deadline')f.worker.clock=()=>Date.now()+400000;
      else f.worker.recordFailure(Error('PRIVATE concurrent health refusal'));
      return allocated;
    };
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      await api.newTab(BOOK_URL,{readOnly:true,target:f.config.target});
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:[]};
    };
    try{
      await f.enqueue('find_drafts');const result=await f.worker.checkpoint();
      assert.equal(result.done,true);assert.equal(result.state,'failed');assert.ok(allocated);
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);
      assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===allocated.id).length,1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      assert.equal(f.events.filter(row=>row[0]==='goto').length,0,'expired allocation never navigates');
      assert.equal(allocatedReads,0,'scope is refused before the first real URL read of the allocated tab');
      assert.equal(f.responses.length,0);assert.equal(f.worker.requestScope,null);
    }finally{await f.cleanup();}
  }
});

test('a fresh-probe allocation crossing its scope is registered before failure cleanup without touching an existing ID',async()=>{
  for(const mode of ['deadline','fatal','duplicate']){
    const f=await fixture();let allocated,allocatedReads=0;
    f.worker.agent=f.agent;f.original.playwright.domSnapshot=async()=>'';
    const deadline=Date.now()+30000,create=f.browser.tabs.new;
    f.browser.tabs.new=async()=>{
      allocated=mode==='duplicate'?f.original:await create();
      const read=allocated.url;allocated.url=async()=>{allocatedReads++;return read();};
      if(mode==='fatal')f.worker.recordFailure(Error('PRIVATE fatal while allocating'));
      else f.worker.clock=()=>deadline+1;
      return allocated;
    };
    try{
      await assert.rejects(f.worker.probe({deadline}),mode==='duplicate'?{code:'platform_chrome_worker_ambiguous_tab'}:
        mode==='deadline'?{code:'platform_chrome_worker_invalid_challenge'}:{code:'platform_chrome_worker_unavailable'});
      if(mode==='duplicate')assert.equal(f.worker.fallbackProbeTab,undefined);
      else assert.equal(f.worker.fallbackProbeTab.id,allocated.id);
      await f.worker.finish();
      assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===allocated.id).length,mode==='duplicate'?0:1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      assert.equal(f.events.filter(row=>row[0]==='goto').length,0);
      assert.equal(allocatedReads,0);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,0);
    }finally{await f.cleanup();}
  }
});

test('public Chrome adapter maps snapshots creation and AX-only scrolling without CUA or hidden APIs',async()=>{
  const f=await fixture();try{
    assert.equal(f.original.cua,undefined,'native SDK shape exposes AX rather than a legacy CUA object');
    f.original.playwright.evaluate=async()=>{throw Error('AX scrolling must not convert pages to a viewport delta');};
    const api=chromeDriverAPI(f.browser,{selectAllKey:'Meta+A'});const existing=await api.getTab('original');
    assert.equal(await existing.getAXState({emit:false}),'visible management page');
    await existing.scroll([30,40],'down',3);await existing.scroll([30,40],'up',1);
    assert.deepEqual(f.events.filter(row=>row[0]==='scroll').map(row=>row.slice(2)),[
      [[30,40],'down',3],[[30,40],'up',1]]);
    for(const args of [[[NaN,40],'up',1],[[30,40],'left',1],[[30,40],'down',0],[[30,40],'up',100001]]){
      await assert.rejects(existing.scroll(...args),{code:'platform_chrome_worker_invalid_scroll'});
    }
    assert.equal(f.events.filter(row=>row[0]==='scroll').length,2);
    await existing.playwright.locator('#body').press('Enter',{timeoutMs:1000});
    await existing.playwright.locator('#body').pressSequentially('正式正文',{timeoutMs:1000});
    const added=await api.newTab('https://fanqienovel.com/preview/10&99');
    assert.notEqual(added.id,'original');assert.equal(f.events.filter(row=>row[0]==='new').length,1);
    assert.ok(f.events.some(row=>row[0]==='goto'&&row[1]===added.id));
    await api.closeCreated();assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{await f.cleanup();}
});
test('effective CUA-only scrolling uses documented scrollX and scrollY with bounded actual viewport pixels',async()=>{
  const f=await fixture();delete f.original.ax;const calls=[];
  f.original.cua={scroll:async options=>calls.push(options)};
  try{
    const tab=await chromeDriverAPI(f.browser).getTab('original');
    await tab.scroll([30,40],'down',3);await tab.scroll([30,40],'up',1);
    assert.deepEqual(calls,[{x:30,y:40,scrollX:0,scrollY:2400},{x:30,y:40,scrollX:0,scrollY:-800}]);
    for(const height of [0,-1,NaN,1.2,Infinity,100000001]){
      f.original.playwright.evaluate=async()=>({height});
      await assert.rejects(tab.scroll([30,40],'down',1),{code:'platform_chrome_worker_invalid_scroll'});
    }
    f.original.playwright.evaluate=async()=>({height:2000});
    await assert.rejects(tab.scroll([30,40],'down',100000),{code:'platform_chrome_worker_invalid_scroll'});
    assert.equal(calls.length,2,'invalid viewport or excessive delta never scrolls');
  }finally{await f.cleanup();}
});
test('plain-text paste uses the public AX virtual clipboard exactly once with fixed null focus and text format',async()=>{
  const f=await fixture();try{
    const tab=await chromeDriverAPI(f.browser).getTab('original'),body='\n  中文甲　\n中文乙\n\n';
    await tab.pasteText(body,{format:'html'});
    assert.deepEqual(f.events.filter(row=>row[0]==='paste'),[['paste','original',null,body,{format:'text'}]]);
    for(const value of [null,{},['正文'],12])await assert.rejects(tab.pasteText(value),{code:'platform_chrome_worker_invalid_config'});
    assert.equal(f.events.filter(row=>row[0]==='paste').length,1);
    assert.ok(!f.events.some(row=>['press','pressSequentially'].includes(row[0])));
  }finally{await f.cleanup();}
});
test('plain-text paste failure or missing AX support never retries or falls back to another input API',async()=>{
  for(const mode of ['missing','throws']){
    const f=await fixture();let calls=0,fallback=0;const original=Error('PRIVATE paste refusal');
    if(mode==='missing')delete f.original.ax.paste;
    else f.original.ax.paste=async()=>{calls++;throw original;};
    f.original.cua={paste:async()=>{fallback++;}};
    try{
      const tab=await chromeDriverAPI(f.browser).getTab('original');
      await assert.rejects(tab.pasteText('完整正文'),mode==='missing'?{code:'platform_chrome_worker_unsupported_sdk'}:failure=>failure===original);
      assert.equal(calls,mode==='throws'?1:0);assert.equal(fallback,0);
      assert.ok(!f.events.some(row=>['press','pressSequentially','new'].includes(row[0])));
    }finally{await f.cleanup();}
  }
});
test('new owned read-only pages recover only the two exact navigation timeouts after URL DOM URL arrival proof',async()=>{
  for(const url of [BOOK_URL,'https://fanqienovel.com/main/writer/user-info?enter_from=avatar',
    'https://fanqienovel.com/main/writer/preview/10&99']){
    for(const message of ['Timed out after 10000ms waiting for CDP command Page.navigate.',
      'Timed out waiting for tab 123456 to navigate to '+url+'.']){
      const f=await fixture();const create=f.browser.tabs.new;let owned;const proof=[];
      f.browser.tabs.new=async()=>{
        owned=await create();let current='about:blank';
        owned.url=async()=>{proof.push('url');return current;};
        owned.goto=async value=>{f.events.push(['goto',owned.id,value]);current=value;proof.push('goto');throw Error(message);};
        owned.playwright.domSnapshot=async()=>{proof.push('dom');return 'actual arrived page';};return owned;
      };
      const api=chromeDriverAPI(f.browser,{target:f.config.target});
      try{
        assert.equal((await api.newTab(url,{readOnly:true,target:{...f.config.target}})).id,owned.id);
        assert.deepEqual(proof,['url','goto','url','dom','url']);
        assert.equal(f.events.filter(row=>row[0]==='new').length,1);
        assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
        await api.closeCreated();assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      }finally{await f.cleanup();}
    }
  }
});
test('new read-only arrival failures preserve the original error and close only the one owned tab without retry',async()=>{
  for(const mode of ['not_arrived','empty','dom_failed','changed_after_dom','same_initial_url','initial_login']){
    const f=await fixture();const create=f.browser.tabs.new;let owned,snapshots=0;
    const now=Date.now;let clock=now();Date.now=()=>clock;
    const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    f.browser.tabs.new=async()=>{
      owned=await create();let current=mode==='same_initial_url'?BOOK_URL:mode==='initial_login'?'https://fanqienovel.com/login':'about:blank';
      owned.url=async()=>{if(mode==='not_arrived')clock+=1000;return current;};
      owned.goto=async value=>{f.events.push(['goto',owned.id,value]);if(mode!=='not_arrived')current=value;throw original;};
      owned.playwright.domSnapshot=async()=>{snapshots++;if(mode==='dom_failed')throw Error('PRIVATE snapshot failure');
        if(mode==='empty')clock+=1000;
        if(mode==='changed_after_dom')current='https://fanqienovel.com/main/writer/home';return mode==='empty'?'  \n':'actual page';};return owned;
    };
    try{
      const api=chromeDriverAPI(f.browser,{target:f.config.target});
      await assert.rejects(api.newTab(BOOK_URL,{readOnly:true,target:{...f.config.target}}),failure=>failure===original);
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
      assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===owned.id).length,1);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      if(['not_arrived','same_initial_url','initial_login'].includes(mode))assert.equal(snapshots,0);
    }finally{Date.now=now;await f.cleanup();}
  }
});
test('new read-only preview waits for a late target URL or initially empty DOM on that same tab without another goto',async()=>{
  const url='https://fanqienovel.com/main/writer/preview/10&99';
  for(const mode of ['late_url','empty_dom']){
    const f=await fixture();const create=f.browser.tabs.new;let owned,attempted=false,urls=0,snapshots=0;
    f.browser.tabs.new=async()=>{
      owned=await create();const read=owned.url.bind(owned),goto=owned.goto.bind(owned);
      owned.url=async()=>attempted&&mode==='late_url'&&++urls<=2?'about:blank':read();
      owned.goto=async value=>{await goto(value);attempted=true;throw Error('Timed out waiting for tab 123456 to navigate to '+url+'.');};
      owned.playwright.domSnapshot=async()=>++snapshots<=2&&mode==='empty_dom'?' \n ':'actual loaded preview';return owned;
    };
    const api=chromeDriverAPI(f.browser,{target:f.config.target});
    try{
      const tab=await api.newTab(url,{readOnly:true,target:f.config.target});assert.equal(tab.id,owned.id);
      if(mode==='late_url')assert.ok(urls>=3);else assert.equal(snapshots,3);
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
      assert.ok(!f.events.some(row=>row[0]==='reload'));await api.closeCreated();
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('new read-only arrival polls for at most five seconds and rejects even DOM becoming nonempty at the deadline',async()=>{
  const f=await fixture(),now=Date.now;let clock=now(),snapshots=0;const create=f.browser.tabs.new;
  const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
  Date.now=()=>clock;
  f.browser.tabs.new=async()=>{const tab=await create(),goto=tab.goto.bind(tab);
    tab.goto=async url=>{await goto(url);throw original;};
    tab.playwright.domSnapshot=async()=>{snapshots++;clock+=1000;return snapshots===5?'late arrived page':'';};return tab;};
  try{
    const started=clock;
    await assert.rejects(chromeDriverAPI(f.browser,{target:f.config.target}).newTab(BOOK_URL,{readOnly:true,target:f.config.target}),failure=>failure===original);
    assert.equal(snapshots,5);assert.equal(clock-started,5000);
    assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
  }finally{Date.now=now;await f.cleanup();}
});
test('new read-only waiting rejects a third route login URL exception or DOM permission failure immediately with the original timeout',async()=>{
  for(const mode of ['third_route','login','url_error','dom_permission','after_url_error','after_third_route','nonstring']){
    const f=await fixture(),create=f.browser.tabs.new;let reads=0,snapshots=0,attempted=false;
    const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    f.browser.tabs.new=async()=>{const tab=await create(),read=tab.url.bind(tab),goto=tab.goto.bind(tab);
      tab.goto=async url=>{await goto(url);attempted=true;throw original;};
      tab.url=async()=>{if(!attempted)return read();reads++;
        if(mode==='url_error'||mode==='after_url_error'&&reads===2)throw Error('PRIVATE disconnected');
        if(mode==='third_route'||mode==='after_third_route'&&reads===2)return 'https://example.com/';
        if(mode==='login')return 'https://fanqienovel.com/login';return read();};
      tab.playwright.domSnapshot=async()=>{snapshots++;if(mode==='dom_permission')throw Error('PRIVATE origin approval gate did not grant access https://fanqienovel.com');
        return mode==='nonstring'?{}:'actual preview';};return tab;};
    try{
      await assert.rejects(chromeDriverAPI(f.browser,{target:f.config.target}).newTab(BOOK_URL,{readOnly:true,target:f.config.target}),failure=>failure===original);
      assert.ok(reads<=2);assert.ok(snapshots<=1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);
    }finally{await f.cleanup();}
  }
});
test('new-tab recovery rejects unsafe routes and foreign bindings before opening and never treats create context as read-only',async()=>{
  const f=await fixture();const api=chromeDriverAPI(f.browser,{target:f.config.target});
  try{
    for(const url of ['https://example.com/main/writer/preview/10&99',BOOK_URL.replace('/10&','/11&'),
      BOOK_URL+'#fragment',BOOK_URL+'\n','https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft',
      'https://fanqienovel.com/main/writer/10/publish/99/?enter_from=modifydraft',
      'https://fanqienovel.com/main/writer/preview/11&99']){
      await assert.rejects(api.newTab(url,{readOnly:true,target:{...f.config.target}}),{code:'platform_chrome_worker_invalid_config'});
    }
    for(const target of [{...f.config.target,account_id:'999'},{...f.config.target,remote_book_id:'11'},
      {...f.config.target,platform:'qimao'},{...f.config.target,extra:true}]){
      await assert.rejects(api.newTab(BOOK_URL,{readOnly:true,target}),{code:'platform_chrome_worker_binding_mismatch'});
    }
    assert.equal(f.events.filter(row=>row[0]==='new').length,0);
    const create=f.browser.tabs.new;let snapshots=0;const original=Error('Timed out after 10000ms waiting for CDP command Page.navigate.');
    f.browser.tabs.new=async()=>{const tab=await create(),goto=tab.goto;
      tab.goto=async url=>{await goto(url);throw original;};tab.playwright.domSnapshot=async()=>{snapshots++;return 'arrived';};return tab;};
    for(const context of [undefined,{readOnly:false}])await assert.rejects(api.newTab(BOOK_URL,context),failure=>failure===original);
    assert.equal(snapshots,0);assert.equal(f.events.filter(row=>row[0]==='new').length,2);
    assert.equal(f.events.filter(row=>row[0]==='goto').length,2);
  }finally{await f.cleanup();}
});
test('new owned tab never recovers authorization disconnected wrong-destination or other errors and rejects duplicate IDs without touching the user tab',async()=>{
  for(const [message,code,name] of [['Permission denied',undefined,'Error'],['Debugger unattached',undefined,'Error'],
    ['Timed out after 20000ms waiting for CDP command Page.navigate.',undefined,'Error'],
    ['Timed out waiting for tab 123 to navigate to https://fanqienovel.com/main/writer/home.',undefined,'Error'],
    ['Timed out after 10000ms waiting for CDP command Page.navigate.','disconnected','Error'],
    ['Timed out after 10000ms waiting for CDP command Page.navigate.','permission_denied','Error'],
    ['Timed out after 10000ms waiting for CDP command Page.navigate.',undefined,'AbortError']]){
    const f=await fixture();const create=f.browser.tabs.new;let snapshots=0;const original=Error(message);original.name=name;if(code)original.code=code;
    f.browser.tabs.new=async()=>{const tab=await create(),goto=tab.goto;tab.goto=async url=>{await goto(url);throw original;};
      tab.playwright.domSnapshot=async()=>{snapshots++;return 'arrived';};return tab;};
    try{await assert.rejects(chromeDriverAPI(f.browser,{target:f.config.target}).newTab(BOOK_URL,{readOnly:true,target:f.config.target}),failure=>failure===original);
      assert.equal(snapshots,0);assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
    }finally{await f.cleanup();}
  }
  const f=await fixture();f.browser.tabs.new=async()=>{f.events.push(['new']);return f.original;};
  try{const api=chromeDriverAPI(f.browser,{target:f.config.target});
    await assert.rejects(api.newTab(BOOK_URL,{readOnly:true,target:f.config.target}),{code:'platform_chrome_worker_ambiguous_tab'});
    await api.closeCreated();assert.ok(!f.events.some(row=>row[0]==='goto'||row[0]==='close'));
  }finally{await f.cleanup();}
});
test('scroll chooses AX when both APIs exist and never falls back after a selected API refuses',async()=>{
  const f=await fixture();let ax=0,cua=0;const denied=Error('PRIVATE permission refusal');
  f.original.ax.scroll=async()=>{ax++;throw denied;};f.original.cua={scroll:async()=>{cua++;}};
  try{
    const tab=await chromeDriverAPI(f.browser).getTab('original');
    await assert.rejects(tab.scroll([30,40],'down',1),failure=>failure===denied);
    assert.equal(ax,1);assert.equal(cua,0,'AX refusal cannot switch to CUA');
    delete f.original.ax;
    f.original.cua.scroll=async()=>{cua++;f.original.ax={scroll:async()=>{ax++;}};throw denied;};
    await assert.rejects(tab.scroll([30,40],'up',1),failure=>failure===denied);
    assert.equal(ax,1);assert.equal(cua,1,'CUA refusal cannot switch to AX or retry');
    delete f.original.ax;delete f.original.cua;
    f.original.playwright.evaluate=async()=>{throw Error('must reject unsupported API before a viewport query');};
    await assert.rejects(tab.scroll([30,40],'down',1),{code:'platform_chrome_worker_unsupported_sdk'});
  }finally{await f.cleanup();}
});
test('merged public inventory distinguishes provider identities and never deduplicates equal editor URLs',async()=>{
  const url='https://fanqienovel.com/main/writer/10/publish/11/?enter_from=modifydraft';
  const f=await fixture();try{
    const controlled=[{id:'same-opaque',providerTabId:'provider-owned',url}];
    const user=[{id:'same-opaque',providerTabId:'provider-other',url},
      {id:'different-opaque',providerTabId:'provider-owned',url},
      {id:'unclaimed',providerTabId:'provider-third',url}];
    f.browser.tabs.list=async()=>controlled;f.browser.user.openTabs=async()=>user;
    const api=chromeDriverAPI(f.browser,{target:f.config.target});const listed=await api.listTabs();
    assert.equal(listed.length,3,'provider-owned is one tab; different providers remain distinct even with equal IDs/URLs');
    assert.equal(new Set(listed.map(tab=>tab.id)).size,3);
    assert.equal(listed.filter(tab=>tab.id==='same-opaque').length,1);
    const other=listed.find(tab=>tab.providerTabId==='provider-other');
    await assert.rejects(api.getTab(other.id),{code:'platform_chrome_worker_unsafe_user_tab'});
    assert.ok(!f.events.some(row=>['claim','close'].includes(row[0])));
  }finally{await f.cleanup();}
});
test('only a newly selected target popup is claimed with its fresh public object and stable owned alias',async()=>{
  const f=await fixture();try{
    const user={id:'user-popup',providerTabId:'provider-popup',url:'https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft',title:'new'};
    let claimed=false,opened=false;const popup=f.makeTab(user.url,'sdk-popup');
    f.browser.tabs.list=async()=>claimed?[{id:'sdk-popup',url:user.url}]:[];
    f.browser.user.openTabs=async()=>opened?[{...user}]:[];
    f.browser.user.claimTab=async item=>{assert.deepEqual(item,user);claimed=true;f.events.push(['claim',item]);return popup;};
    const api=chromeDriverAPI(f.browser,{target:f.config.target,sdkScope:()=>({operation:'create_draft'})});
    assert.deepEqual(await api.listTabs(),[]);opened=true;const before=(await api.listTabs())[0];
    const tab=await api.getTab(before.id);assert.equal(tab.id,before.id);
    assert.equal((await api.listTabs())[0].id,before.id,'claim does not change the driver ownership key');
    assert.equal((await api.listTabs()).length,1);assert.equal(f.events.filter(row=>row[0]==='claim').length,1);
    await tab.close();assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]==='sdk-popup').length,1);
  }finally{await f.cleanup();}
});
test('real driver rejects an unclaimed same-book user editor before preview without claiming or closing it',async()=>{
  const origin='https://fanqienovel.com';
  const editorURL=origin+'/main/writer/10/publish/11/?enter_from=modifydraft';
  const f=await fixture({userTabs:[{id:'unclaimed-editor',providerTabId:'provider-editor',url:editorURL,title:'unsaved user editor'}]});
  const newTab=f.browser.tabs.new;
  f.browser.tabs.new=async()=>{const tab=await newTab();tab.playwright.locator=selector=>({count:async()=>selector==='#account'?1:0,
    evaluate:async()=> '123'});return tab;};
  const config={platform:'fanqie',origin,execution_mode:'host_assisted_cua',urls:{drafts:BOOK_URL,edit:origin+'/main/writer/10/publish/{remote_id}/?enter_from=modifydraft'},
    identity:{account:{source:'guard_dom',selector:'#account',guard_url:origin+'/main/writer/user-info?enter_from=avatar'},
      book:{source:'page_url',templates:[origin+'/main/writer/chapter-manage/{book_id}&test?type=2',origin+'/main/writer/{book_id}/publish/{remote_id}/?enter_from=modifydraft',origin+'/preview/{book_id}&{remote_id}'],capture:'book_id',id_kind:'digits'}},
    chapter_lists:[{scope:'drafts'},{scope:'non_drafts'}],editor:{ready:{selector:'#editor-ready',expected:'可编辑'},remote_id:{source:'page_url',templates:[origin+'/main/writer/10/publish/{remote_id}/?enter_from=modifydraft'],capture:'remote_id',id_kind:'digits'}},
    readback:{url:origin+'/preview/10&{remote_id}',remote_id:{source:'page_url',templates:[origin+'/preview/10&{remote_id}'],capture:'remote_id',id_kind:'digits'},
      header:{source:'dom',selector:'#header',format:'chapter_title',title_separator:' '},body:{source:'fanqie_preview_paragraphs_v1',selector:'#body'}}};
  const request={schema:1,id:'read-editor-safety',session_id:'user-editor-safety',binding_sha256:BINDING,binding:{profile_sha256:'profile',target:f.config.target},
    profile:config,operation:'verify_target',params:{},deadline:new Date(Date.now()+5000).toISOString()};
  const api=chromeDriverAPI(f.browser,{target:f.config.target});
  try{
    assert.equal((await executeChromeBridgeRequest(api,request)).ok,true);
    const result=await executeChromeBridgeRequest(api,{...request,operation:'read_draft',params:{remote_id:'11'}});
    assert.equal(result.error,'platform_browser_editor_in_use');
    assert.ok(!f.events.some(row=>row[0]==='claim'||row[0]==='get'&&row[1]==='unclaimed-editor'||row[0]==='close'&&row[1]==='unclaimed-editor'));
    assert.ok(!f.events.some(row=>row[0]==='goto'&&row[2].includes('/preview/')));
  }finally{await closeChromeBridgeSession(api,request.session_id,{keepReadback:false});await f.cleanup();}
});
test('health requires target management page get and successful DOM snapshot before attach and ready',async()=>{
  const f=await fixture();try{
    const state=await f.worker.checkpoint();await until(()=>f.worker.state==='ready');
    assert.equal(state.done,false);assert.ok(f.events.findIndex(row=>row[0]==='snapshot')<f.events.findIndex(row=>row[0]==='cli'&&row[1]==='attach'));
    assert.equal(f.events.filter(row=>row[0]==='documentation').length,1);
    assert.deepEqual(f.events.filter(row=>row[0]==='nameSession'),[['nameSession','📝 番茄草稿']]);
    assert.ok(f.events.filter(row=>row[0]==='snapshot').length>=2);assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1);
    assert.equal(f.events.filter(row=>row[0]==='new').length,0);
    await f.worker.stop();assert.equal(f.worker.state,'stopped');assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
  }finally{await f.cleanup();}
});
test('first health claims the exact existing target management tab when no agent tab is available',async()=>{
  const f=await fixture({noAgentTabs:true});try{
    await f.worker.checkpoint();await until(()=>f.worker.state==='ready');
    assert.equal(f.events.filter(row=>row[0]==='claim').length,1);assert.equal(f.events.filter(row=>row[0]==='new'||row[0]==='goto').length,0);
    assert.ok(f.events.findIndex(row=>row[0]==='nameSession')<f.events.findIndex(row=>row[0]==='claim'));
    await f.worker.refreshHealth();assert.equal(f.events.filter(row=>row[0]==='claim').length,1,'subsequent probes reuse the checked tab');
  }finally{await f.cleanup();}
});
test('initial stale-tab get claim URL snapshot or empty DOM uses at most one owned read-only fallback',async()=>{
  for(const stage of ['get','claim','url','snapshot','empty']){
    const f=await fixture({noAgentTabs:stage==='claim'});let fresh;
    const stale=()=>{throw Error('Wrapped Debugger unattached');};
    if(stage==='get'){const get=f.browser.tabs.get;f.browser.tabs.get=async id=>id==='original'?stale():get(id);}
    if(stage==='claim')f.browser.user.claimTab=async()=>stale();
    if(stage==='url'){const get=f.browser.tabs.get;f.browser.tabs.get=async id=>id==='original'?{...f.original,url:async()=>stale()}:get(id);}
    if(stage==='snapshot')f.original.playwright.domSnapshot=async()=>stale();
    if(stage==='empty')f.original.playwright.domSnapshot=async()=>'';
    const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{fresh=await create();return fresh;};
    try{
      await f.worker.checkpoint();await until(()=>f.worker.state==='ready');
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);
      assert.deepEqual(f.events.filter(row=>row[0]==='goto').map(row=>row[2]),[BOOK_URL]);
      assert.equal(f.worker.probeTabId,fresh.id);assert.equal(f.worker.probeFallbackUsed,true);
      await f.worker.refreshHealth();assert.equal(f.events.filter(row=>row[0]==='new').length,1);
      await f.worker.stop();assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===fresh.id));
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('fresh fallback waits for initial empty DOM and cannot open another tab after attachment',async()=>{
  const f=await fixture();let fresh,reads=0;
  f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};
  const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{
    fresh=await create();const snapshot=fresh.playwright.domSnapshot;
    fresh.playwright.domSnapshot=async()=>++reads<=2?'':snapshot();return fresh;
  };
  try{
    await f.worker.checkpoint();await until(()=>f.worker.state==='ready');assert.ok(reads>=3);
    assert.equal(f.events.filter(row=>row[0]==='new').length,1);
    fresh.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};
    await assert.rejects(f.worker.refreshHealth());await f.worker.checkpoint();assert.equal(f.worker.done,true);
    assert.equal(f.events.filter(row=>row[0]==='new').length,1);
    assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===fresh.id));assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{await f.cleanup();}
});
test('initial fresh probe can become healthy after an exact timeout only when its same new tab has arrived',async()=>{
  for(const message of ['Timed out after 10000ms waiting for CDP command Page.navigate.',
    'Timed out waiting for tab 123456 to navigate to '+BOOK_URL+'.']){
    const f=await fixture();f.original.playwright.domSnapshot=async()=>'';const create=f.browser.tabs.new;let fresh;
    f.browser.tabs.new=async()=>{fresh=await create();const goto=fresh.goto;
      fresh.goto=async url=>{await goto(url);throw Error(message);};return fresh;};
    try{const result=await f.worker.checkpoint();assert.equal(result.state,'ready');assert.equal(result.done,false);
      assert.equal(f.worker.probeTabId,fresh.id);assert.equal(f.events.filter(row=>row[0]==='new').length,1);
      assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1);
      assert.ok(f.events.some(row=>row[0]==='cli'&&row[1]==='health'));
    }finally{await f.cleanup();}
  }
});
test('fresh probe timeout without arrival or DOM proof keeps the original private diagnostic and never attaches',async()=>{
  for(const mode of ['not_arrived','empty','dom_failed']){
    const f=await fixture();f.original.playwright.domSnapshot=async()=>'';const create=f.browser.tabs.new;let fresh;
    const now=Date.now;let clock=now();Date.now=()=>clock;
    const original=Error('Timed out waiting for tab 123456 to navigate to '+BOOK_URL+'.');
    f.browser.tabs.new=async()=>{fresh=await create();const goto=fresh.goto,read=fresh.url;
      fresh.url=async()=>{if(mode==='not_arrived')clock+=1000;return read();};
      fresh.goto=async url=>{if(mode!=='not_arrived')await goto(url);else f.events.push(['goto',fresh.id,url]);throw original;};
      fresh.playwright.domSnapshot=async()=>{if(mode==='dom_failed')throw Error('PRIVATE DOM unavailable');clock+=1000;return '';};return fresh;};
    try{const result=await f.worker.checkpoint();assert.equal(result.state,'failed');assert.equal(result.done,true);
      assert.equal((await readPrivateJSON(path.join(f.run,'worker-bootstrap-error.private.json'))).message,original.message);
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.equal(f.events.filter(row=>row[0]==='goto').length,1);
      assert.ok(!f.events.some(row=>row[0]==='cli'));assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===fresh.id));
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{Date.now=now;await f.cleanup();}
  }
});
test('a failed fallback closes its known owned tab before API initialization or after attach failure',async()=>{
  for(const failure of ['goto','empty','attach']){
    const f=await fixture();let fresh;const started=Date.now();let clock=started;
    f.original.playwright.domSnapshot=async()=>'';
    f.worker.clock=()=>clock;f.worker.sleep=async ms=>{clock+=ms;await sleep(0);};
    const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{
      fresh=await create();if(failure==='goto')fresh.goto=async()=>{throw Error('PRIVATE failed readonly navigation');};
      else if(failure==='empty')fresh.playwright.domSnapshot=async()=>'';return fresh;
    };
    if(failure==='attach'){
      const command=f.worker.command;f.worker.command=async(action,extra)=>{
        if(action==='attach'){f.events.push(['cli','attach']);throw Error('PRIVATE attach unavailable');}
        return command(action,extra);
      };
    }
    try{
      await f.worker.checkpoint();await until(()=>f.worker.done);assert.equal(f.worker.state,'failed');
      if(failure==='attach'){assert.ok(f.worker.api);assert.deepEqual(f.events.filter(row=>row[0]==='cli').map(row=>row[1]),['attach']);}
      else{assert.equal(f.worker.api,undefined);assert.equal(f.events.filter(row=>row[0]==='cli').length,0);}
      assert.equal(f.events.filter(row=>row[0]==='new').length,1);assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===fresh.id));
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      if(failure==='empty')assert.equal(clock-started,15000,'empty fallback spends exactly its one bounded 15 second readiness budget');
    }finally{await f.cleanup();}
  }
});
test('official origin permission denial during fresh navigation never attaches retries or leaks its URL',async()=>{
  for(const phrase of ['The user declined permission','could not acquire authority to trigger network egress','origin approval gate did not grant access']){
    const f=await fixture();let fresh;
    f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};
    const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{
      fresh=await create();fresh.goto=async()=>{throw Error('Wrapped '+phrase+' for origin "https://fanqienovel.com" PRIVATE_DOM_AND_IDS');};return fresh;
    };
    try{
      await f.worker.checkpoint();await until(()=>f.worker.done);const result=await f.worker.checkpoint();
      assert.equal(result.code,'platform_chrome_worker_origin_permission_required');assert.equal(result.error_marker,'origin_permission_required');
      assert.equal(result.failure_phase,'existing_tab_probe');assert.equal(result.error_name,'Error');assert.equal(result.processed,0);
      assert.equal(f.events.filter(row=>row[0]==='cli').length,0);assert.equal(f.events.filter(row=>row[0]==='new').length,1);
      assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===fresh.id));assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
      assert.ok(!JSON.stringify(result).includes('fanqienovel'));assert.ok(!JSON.stringify(result).includes('PRIVATE'));
    }finally{await f.cleanup();}
  }
});
test('permission denial on an existing target page is terminal without a fresh-page fallback',async()=>{
  const f=await fixture();f.original.playwright.domSnapshot=async()=>{
    throw Error('Debugger unattached wrapper: The user declined permission for https://fanqienovel.com/main/writer/chapter-manage/10&test?type=2');
  };
  try{
    await f.worker.checkpoint();await until(()=>f.worker.done);const result=await f.worker.checkpoint();
    assert.equal(result.code,'platform_chrome_worker_origin_permission_required');assert.equal(result.error_marker,'origin_permission_required');
    assert.equal(result.failure_phase,'existing_tab_probe');assert.ok(!f.events.some(row=>['new','goto','cli','close'].includes(row[0])));
  }finally{await f.cleanup();}
});
test('URL-free and structured permission refusals stop every existing-page probe stage without another route',async()=>{
  const refusals = [
    ...['The user declined permission','could not acquire authority to trigger network egress',
      'origin approval gate did not grant access'].map(message=>({message})),
    ...['permission_denied','authentication_required','forbidden','cancelled'].map(code=>({message:'Debugger unattached PRIVATE_REFUSAL',code}))
  ];
  for(const stage of ['get','claim','url','snapshot'])for(const refusal of refusals){
    const f=await fixture({noAgentTabs:stage==='claim'});
    const deny=()=>{const failure=Error(refusal.message);if(refusal.code)failure.code=refusal.code;throw failure;};
    if(stage==='get'){const get=f.browser.tabs.get;f.browser.tabs.get=async id=>id==='original'?deny():get(id);}
    if(stage==='claim')f.browser.user.claimTab=async()=>deny();
    if(stage==='url'){const get=f.browser.tabs.get;f.browser.tabs.get=async id=>id==='original'?{...f.original,url:async()=>deny()}:get(id);}
    if(stage==='snapshot')f.original.playwright.domSnapshot=async()=>deny();
    try{
      const result=await f.worker.checkpoint();
      assert.equal(result.done,true);assert.equal(result.state,'failed');assert.equal(result.processed,0);
      assert.equal(result.code,'platform_chrome_worker_origin_permission_required');
      assert.equal(result.error_marker,'origin_permission_required');assert.equal(result.failure_phase,'existing_tab_probe');
      assert.ok(!f.events.some(row=>['new','goto','cli','close'].includes(row[0])));
      assert.ok(!JSON.stringify(result).includes('PRIVATE_REFUSAL'));
    }finally{await f.cleanup();}
  }
});
test('unknown probe failures and malformed snapshots fail closed while denials for another origin never reopen a tab',async()=>{
  for(const kind of ['unknown','invalid_snapshot','foreign_denial']){
    const f=await fixture();
    f.original.playwright.domSnapshot=async()=>{
      if(kind==='invalid_snapshot')return {text:'unvalidated snapshot'};
      throw Error(kind==='foreign_denial'?'Debugger unattached: The user declined permission for https://example.com':'PRIVATE_UNKNOWN_ERROR');
    };
    try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,'failed');
      assert.ok(!f.events.some(row=>['new','goto','cli','close'].includes(row[0])));
      assert.notEqual(result.error_marker,'origin_permission_required');
      assert.ok(!JSON.stringify(result).includes('PRIVATE_UNKNOWN_ERROR'));
    }finally{await f.cleanup();}
  }
});
test('permission classification never attributes an explicit foreign origin or an incomplete phrase to the bound origin',async()=>{
  for(const message of ['The user declined permission for https://fanqienovel.com.evil.example',
    'The user declined permission for https://fanqienovel.com@evil.example',
    'The user declined permission for https://example.com/?redirect=https://fanqienovel.com',
    'The user declined permission for http://fanqienovel.com',
    'The user declined permission for https://example.com',
    'Permission denied for https://fanqienovel.com']){
    const f=await fixture();f.worker.sdkFactory=async()=>{throw Error(message);};
    try{
      await f.worker.checkpoint();await until(()=>f.worker.done);const result=await f.worker.checkpoint();
      assert.equal(result.code,'platform_chrome_worker_failed');assert.equal(result.error_marker,'none');
      assert.ok(!f.events.some(row=>['new','goto','cli','close'].includes(row[0])));
    }finally{await f.cleanup();}
  }
});
test('fallback never invents a binding URL or navigates to an editor foreign book or malformed management route',async()=>{
  for(const url of [BOOK_URL.replace('/10&','/11&'),BOOK_URL.replace('fanqienovel.com','example.com'),
    'https://fanqienovel.com/main/writer/10/publish/99?enter_from=newdraft',BOOK_URL+'&submit=1',
    BOOK_URL.replace('&test','&test/subpath'),BOOK_URL.replace('&test','&bad%2Ftitle')]){
    const f=await fixture();f.browser.tabs.list=async()=>[{id:'wrong',url}];f.browser.user.openTabs=async()=>[{id:'wrong-user',url}];
    try{
      await f.worker.checkpoint();await until(()=>f.worker.done);assert.equal(f.worker.fatalCode,'platform_chrome_worker_existing_tab_required');
      assert.ok(!f.events.some(row=>['new','goto','claim'].includes(row[0])));
    }finally{await f.cleanup();}
  }
});
test('visible inventory with debugger failure never attaches grants health or claims a queued command',async()=>{
  const f=await fixture({debuggerUnattached:true});try{
    const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,'failed');
    assert.equal(result.code,'platform_chrome_worker_browser_unattached');
    assert.equal(result.failure_phase,'existing_tab_probe');assert.equal(result.phase,'cleanup');
    assert.ok(!f.events.some(row=>row[0]==='cli'));assert.ok(!JSON.stringify(result).includes('PRIVATE'));
  }finally{await f.cleanup();}
});
test('wrapped SDK debugger errors map to a static recovery code without returning their text',async()=>{
  for(const message of ['Debugger unattached','PRIVATE Debugger unattached','Debugger unattached.']){
    const f=await fixture({debuggerUnattached:true,debuggerMessage:message});try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);
      assert.equal(result.code,'platform_chrome_worker_browser_unattached');
      assert.equal(result.error_marker,'browser_unattached');assert.equal(result.error_name,'Error');
      assert.ok(!JSON.stringify(result).includes('Debugger'));assert.ok(!f.events.some(row=>row[0]==='cli'));
    }finally{await f.cleanup();}
  }
});
test('first failure phase and bounded error diagnostics survive cleanup without raw SDK text',async()=>{
  const cases=[
    ['sdk_setup','ReferenceError','none','platform_chrome_worker_failed',new ReferenceError('PRIVATE unexpected symbol')],
    ['sdk_setup','Error','missing_turn_metadata','platform_chrome_worker_turn_metadata_missing',Error('Wrapped Missing required Codex turn metadata: PRIVATE_ID')],
    ['sdk_setup','Error','missing_browser_service','platform_chrome_worker_runtime_unavailable',Error('Browser use requires a trusted Node REPL browser service PRIVATE')],
    ['sdk_setup','ReferenceError','missing_runtime_global','platform_chrome_worker_failed',new ReferenceError('Buffer is not defined')],
    ['browser_inventory','RangeError','none','platform_chrome_worker_failed',new RangeError('PRIVATE browser list problem')],
    ['browser_documentation','TypeError','none','platform_chrome_worker_failed',new TypeError('PRIVATE documentation problem')],
    ['existing_tab_probe','Error','browser_unattached','platform_chrome_worker_browser_unattached',Error('PRIVATE wrapped: Debugger unattached')]
  ];
  for(const [phase,name,marker,code,failure] of cases){
    const f=await fixture();const reject=async()=>{throw failure;};
    if(phase==='sdk_setup')f.worker.sdkFactory=reject;
    if(phase==='browser_inventory')f.agent.browsers.list=reject;
    if(phase==='browser_documentation')f.browser.documentation=reject;
    if(phase==='existing_tab_probe'){
      f.original.playwright.domSnapshot=reject;
      const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{const tab=await create();tab.playwright.domSnapshot=reject;return tab;};
    }
    try{
      await f.worker.checkpoint();await until(()=>f.worker.done);const result=await f.worker.checkpoint();
      assert.equal(result.phase,'cleanup');assert.equal(result.failure_phase,phase);
      assert.equal(result.error_name,name);assert.equal(result.error_marker,marker);assert.equal(result.code,code);
      assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.ok(!JSON.stringify(result).includes(failure.message));
      assert.equal(f.events.filter(row=>row[0]==='cli').length,0,'early diagnosis never attaches or claims a command');
    }finally{await f.cleanup();}
  }
});
test('bootstrap SDK and probe errors are bounded private and synced before cleanup without reading other error fields',async()=>{
  for(const phase of ['sdk_setup','browser_inventory','browser_documentation','existing_tab_probe']){
    const f=await fixture();const message='PRIVATE bootstrap failure '+ '甲'.repeat(5000),failure=Error(message);
    for(const key of ['stack','args','headers','dom'])Object.defineProperty(failure,key,{get(){throw Error('must not inspect '+key);}});
    const reject=async()=>{throw failure;};
    if(phase==='sdk_setup')f.worker.sdkFactory=reject;
    if(phase==='browser_inventory')f.agent.browsers.list=reject;
    if(phase==='browser_documentation')f.browser.documentation=reject;
    if(phase==='existing_tab_probe')f.browser.tabs.list=reject;
    const filename=path.join(f.run,'worker-bootstrap-error.private.json'),finish=f.worker.finish.bind(f.worker);let persisted=false;
    f.worker.finish=async()=>{
      assert.deepEqual(await readPrivateJSON(filename),{schema:1,phase,message:message.slice(0,4096)});
      if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
      persisted=true;return finish();
    };
    try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.code,'platform_chrome_worker_failed');
      assert.equal(result.failure_phase,phase);assert.equal(persisted,true);
      assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.equal(f.events.filter(row=>row[0]==='cli').length,0);
      assert.equal(f.worker.processed,0);
    }finally{await f.cleanup();}
  }
});
test('bootstrap diagnostic refusal never overwrites an existing file or link or replaces the original failure',async()=>{
  for(const linked of [false,true]){
    const f=await fixture(),filename=path.join(f.run,'worker-bootstrap-error.private.json'),target=path.join(f.run,'other-bootstrap-private.json');
    const original={schema:1,retained:true};
    if(linked){await privateJSON(target,original);await fs.symlink(target,filename);}else await privateJSON(filename,original);
    f.worker.sdkFactory=async()=>{throw new ReferenceError('PRIVATE original bootstrap error');};
    try{
      const result=await f.worker.checkpoint();assert.equal(result.code,'platform_chrome_worker_failed');
      assert.equal(result.error_name,'ReferenceError');assert.equal(result.failure_phase,'sdk_setup');
      assert.deepEqual(JSON.parse(await fs.readFile(linked?target:filename,'utf8')),original);
      assert.equal((await fs.lstat(filename)).isSymbolicLink(),linked);assert.equal(f.events.filter(row=>row[0]==='cli').length,0);
    }finally{await f.cleanup();}
  }
});
test('checkpoints keep SDK setup documentation and target DOM awaited until they complete',async()=>{
  for(const phase of ['sdk_setup','browser_documentation','existing_tab_probe']){
    const f=await fixture();let release;let first=true;
    const hold=async value=>{if(first){first=false;await new Promise(resolve=>{release=resolve;});}return value;};
    if(phase==='sdk_setup')f.worker.sdkFactory=()=>hold(f.agent);
    if(phase==='browser_documentation')f.browser.documentation=()=>hold('Official API docs');
    if(phase==='existing_tab_probe')f.original.playwright.domSnapshot=()=>{f.events.push(['snapshot','original']);return hold('Visible target page');};
    try{
      let returned=false;const firstCheckpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});
      await until(()=>typeof release==='function');await sleep(15);assert.equal(returned,false);
      assert.equal(f.worker.phase,phase);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,0);
      release();const result=await firstCheckpoint;assert.equal(result.done,false);assert.equal(result.state,'ready');
      assert.deepEqual(Object.keys(result).sort(),['done','phase','processed','state']);
    }finally{release?.();await f.cleanup();}
  }
});
test('driver failure keeps only bounded phase and module location through cleanup',async()=>{
  const f=await fixture();f.driver.executeChromeBridgeRequest=async(api,request)=>{
    api.onProgress({phase:'all_rows',selector:'PRIVATE selector',body:'PRIVATE 正文'});
    api.onProgress({phase:'error',line:442,column:37,path:'/PRIVATE/book',stack:'PRIVATE stack',token:'PRIVATE TOKEN'});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,
      ok:false,error:'platform_browser_operation_failed'};
  };
  try{
    await f.enqueue('find_drafts',{chapter:12});await f.worker.checkpoint();await until(()=>f.worker.done);
    const result=await f.worker.checkpoint();assert.equal(result.code,'platform_browser_operation_failed');
    assert.equal(result.phase,'cleanup');assert.equal(result.failure_phase,'queue_operation');
    assert.equal(result.driver_phase,'all_rows');assert.equal(result.driver_line,442);assert.equal(result.driver_column,37);
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
    assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.ok(!JSON.stringify(result).includes('正文'));
    assert.deepEqual(Object.keys(result).filter(key=>key.startsWith('driver_')).sort(),['driver_column','driver_line','driver_phase']);
  }finally{await f.cleanup();}
});
test('a checkpoint awaits detach and cleanup before returning its terminal failure diagnostics',async()=>{
  const f=await fixture();let release,detaching=false,durableBeforeRespond=false;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    api.onProgress({phase:'all_rows'});api.onProgress({phase:'error',line:442,column:37});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,
      ok:false,error:'platform_browser_operation_failed'};
  };
  const command=f.worker.command;f.worker.command=async(action,extra)=>{
    if(action==='respond'){
      const filename=path.join(f.run,'worker-driver-error.json');
      assert.deepEqual(await readPrivateJSON(filename),{schema:1,code:'platform_browser_operation_failed',
        error_name:'Error',error_marker:'none',worker_code:'none',driver_phase:'all_rows',driver_line:442,driver_column:37});
      if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
      durableBeforeRespond=true;
    }
    if(action==='detach'){detaching=true;await new Promise(resolve=>{release=resolve;});}
    return command(action,extra);
  };
  try{
    await f.enqueue('find_drafts');let returned=false;
    const checkpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});await until(()=>detaching);
    assert.equal(durableBeforeRespond,true,'private diagnostic was synced before the platform outcome reached the controller');
    await sleep(15);assert.equal(returned,false);assert.equal(f.worker.phase,'cleanup');
    assert.equal(f.worker.fatalCode,'platform_browser_operation_failed','the observed failure remains recorded while cleanup awaits');
    assert.equal((await readPrivateJSON(path.join(f.run,'worker-driver-error.json'))).driver_line,442);
    release();const result=await checkpoint;
    assert.equal(result.done,true);assert.equal(result.state,'failed');assert.equal(result.code,'platform_browser_operation_failed');
    assert.equal(result.failure_phase,'queue_operation');assert.equal(result.driver_phase,'all_rows');
    assert.equal(result.driver_line,442);assert.equal(result.driver_column,37);
  }finally{release?.();await f.cleanup();}
});
test('durable driver error records only fixed enums and location even when the SDK error contains secrets',async()=>{
  for(const known of [true,false]){
    const f=await fixture();f.driver.executeChromeBridgeRequest=async(api,request)=>{
      api.onProgress({phase:'listing',path:'/PRIVATE/book',body:'PRIVATE 正文'});
      const failure=new TypeError('The user declined permission for https://fanqienovel.com/PRIVATE?token=PRIVATE_TOKEN 正文');
      if(!known)failure.name='PRIVATE_ERROR_NAME';
      api.onDriverError(failure);
      api.onProgress({phase:'error',line:449,column:37,stack:failure.stack,selector:'PRIVATE selector'});
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,
        ok:false,error:known?'platform_browser_operation_failed':'platform_browser_private_secret'};
    };
    try{
      await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.done);
      const packet=await readPrivateJSON(path.join(f.run,'worker-driver-error.json'));
      assert.deepEqual(packet,{schema:1,code:'platform_browser_operation_failed',error_name:known?'TypeError':'Error',
        error_marker:'origin_permission_required',worker_code:'none',driver_phase:'listing',driver_line:449,driver_column:37});
      const serialized=JSON.stringify(packet);assert.ok(serialized.length<512);
      for(const value of ['PRIVATE','正文','fanqienovel','token','stack','selector',f.directory])assert.ok(!serialized.includes(value));
    }finally{await f.cleanup();}
  }
});
test('durable driver diagnostics retain only explicit adapter error codes',async()=>{
  for(const code of ['platform_chrome_worker_unsupported_sdk','platform_chrome_worker_invalid_scroll','platform_chrome_worker_unsafe_user_tab',
    'platform_chrome_worker_private_token','PRIVATE TOKEN']){
    const f=await fixture();f.driver.executeChromeBridgeRequest=async(api,request)=>{
      api.onProgress({phase:'all_rows'});api.onDriverError(Object.assign(Error('PRIVATE body /path TOKEN'),{code}));
      api.onProgress({phase:'error',line:449,column:37});
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:'platform_browser_operation_failed'};
    };
    try{
      await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.done);
      const packet=await readPrivateJSON(path.join(f.run,'worker-driver-error.json'));
      assert.equal(packet.worker_code,code.includes('private')||code.startsWith('PRIVATE')?'none':code);
      assert.ok(!JSON.stringify(packet).includes('PRIVATE'));assert.ok(!JSON.stringify(packet).includes('private_token'));
      assert.ok(!Object.hasOwn(await f.worker.checkpoint(),'worker_code'),'private diagnostic does not expand checkpoint schema');
    }finally{await f.cleanup();}
  }
});
test('an existing or linked diagnostic cannot be overwritten and never obscures the failed outcome',async()=>{
  for(const linked of [false,true]){
    const f=await fixture();const filename=path.join(f.run,'worker-driver-error.json');const target=path.join(f.run,'other-private.json');
    const original={schema:1,code:'platform_browser_identity_mismatch'};
    if(linked){await privateJSON(target,original);await fs.symlink(target,filename);}
    else await privateJSON(filename,original);
    let calls=0;f.driver.executeChromeBridgeRequest=async(_api,request)=>{
      calls++;return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:'platform_browser_operation_failed'};
    };
    try{
      await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.done);
      assert.equal((await f.worker.checkpoint()).code,'platform_browser_operation_failed');assert.equal(calls,1);assert.equal(f.responses.length,1);
      assert.deepEqual(JSON.parse(await fs.readFile(linked?target:filename,'utf8')),original);
      assert.equal((await fs.lstat(filename)).isSymbolicLink(),linked);
    }finally{await f.cleanup();}
  }
});
test('private read diagnostics include generic verify failures with unknown phase and sync before respond',async()=>{
  const cases=[['find_drafts','all_rows',false,'platform_browser_operation_failed',true],
    ['find_drafts','listing',false,'platform_browser_operation_failed',true],
    ['read_draft','listing',false,'platform_browser_operation_failed',true],
    ['read_draft','read',false,'platform_browser_operation_failed',true],
    ['find_drafts','all_rows',false,'platform_browser_incomplete_list',false],
    ['find_drafts','all_rows',true,null,false],
    ['create_draft','all_rows',false,'platform_browser_operation_failed',false],
    ['read_draft','all_rows',false,'platform_browser_operation_failed',true],
    ['read_draft','guard',false,'platform_browser_operation_failed',true],
    ['verify_target','all_rows',false,'platform_browser_operation_failed',true],
    ['verify_target',null,false,'platform_browser_operation_failed',true],
    ['verify_target','guard',false,'platform_browser_operation_failed',true],
    ['verify_target',null,false,'platform_browser_identity_mismatch',false]];
  for(const [operation,phase,ok,code,expected] of cases){
    const f=await fixture();const filename=path.join(f.run,'worker-catalog-error.private.json');
    const message='PRIVATE local SDK failure '+ '甲'.repeat(5000);let checked=false;
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      api.onProgress({phase});const failure=Error(message);
      for(const key of ['stack','args','headers','dom'])Object.defineProperty(failure,key,{get(){throw Error('must not inspect '+key);}});
      api.onDriverError(failure);api.onProgress({phase:'error',line:449,column:37});
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok,
        ...(ok?{result:[]}:{error:code})};
    };
    const command=f.worker.command;f.worker.command=async(action,extra)=>{
      if(action==='respond'){
        if(expected){
          assert.deepEqual(await readPrivateJSON(filename),{schema:1,operation,phase,message:message.slice(0,4096)});
          if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
        }else await assert.rejects(fs.lstat(filename),{code:'ENOENT'});
        checked=true;
      }
      return command(action,extra);
    };
    try{
      await f.enqueue(operation);await f.worker.checkpoint();await until(()=>ok?f.worker.processed===1:f.worker.done);
      assert.equal(checked,true);assert.equal(f.worker.activeCatalogMessage,null);assert.equal(f.worker.activeOperation,null);
      await assert.rejects(fs.lstat(path.join(f.run,'worker-bootstrap-error.private.json')),{code:'ENOENT'});
      assert.ok(!JSON.stringify(await f.worker.checkpoint()).includes('PRIVATE'));
      if(expected)assert.equal((await readPrivateJSON(filename)).message.length,4096);
    }finally{await f.cleanup();}
  }
});
test('failed draft mutations preserve bounded private diagnostics without public leakage or replay',async()=>{
  for(const operation of ['create_draft','update_draft'])for(const code of [
    'platform_browser_operation_failed','platform_browser_navigation_timeout','platform_browser_read_timeout']){
    const f=await fixture();const filename=path.join(f.run,'worker-mutation-error.private.json');let calls=0;
    const message='PRIVATE SDK failure '+ '甲'.repeat(5000);
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      calls++;api.onProgress({phase:'guard'});const failure=Error(message);
      for(const key of ['stack','args','headers','dom'])Object.defineProperty(failure,key,{get(){throw Error('must not inspect '+key);}});
      api.onDriverError(failure);api.onProgress({phase:'error'});
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:code};
    };
    try{
      await f.enqueue(operation);await f.worker.checkpoint();await until(()=>f.worker.done);
      assert.deepEqual(await readPrivateJSON(filename),{schema:1,operation,phase:'guard',code,message:message.slice(0,4096)});
      if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
      assert.equal(calls,1);assert.equal(f.responses.length,1);
      assert.equal(f.worker.activeCatalogMessage,null);assert.equal(f.worker.activeOperation,null);
      assert.ok(!JSON.stringify(await f.worker.checkpoint()).includes('PRIVATE'));
      assert.ok(!JSON.stringify(f.responses).includes('PRIVATE'));
      await assert.rejects(fs.lstat(path.join(f.run,'worker-catalog-error.private.json')),{code:'ENOENT'});
    }finally{await f.cleanup();}
  }
});
test('successful catalog work clears captured messages before a later failure without an original error',async()=>{
  const f=await fixture();let calls=0;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    calls++;api.onProgress({phase:'all_rows'});
    if(calls===1)api.onDriverError(Error('PRIVATE stale successful diagnostic'));
    api.onProgress({phase:'error',line:449,column:37});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,
      ...(calls===1?{ok:true,result:[]}:{ok:false,error:'platform_browser_operation_failed'})};
  };
  try{
    await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.processed===1&&f.worker.state==='ready');
    assert.equal(f.worker.activeCatalogMessage,null);
    await f.enqueue('find_drafts');await f.worker.checkpoint();assert.equal(f.worker.done,true);
    await assert.rejects(fs.lstat(path.join(f.run,'worker-catalog-error.private.json')),{code:'ENOENT'});
    assert.equal(calls,2);assert.equal((await f.worker.checkpoint()).code,'platform_browser_operation_failed');
  }finally{await f.cleanup();}
});
test('private catalog diagnostic write refusal preserves the original failure without overwriting or retrying',async()=>{
  const f=await fixture();const filename=path.join(f.run,'worker-catalog-error.private.json');
  const original={schema:1,retained:true};await privateJSON(filename,original);let calls=0;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    calls++;api.onProgress({phase:'all_rows'});api.onDriverError(Error('PRIVATE new SDK diagnostic'));
    api.onProgress({phase:'error',line:449,column:37});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:'platform_browser_operation_failed'};
  };
  try{
    await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.done);
    assert.equal((await f.worker.checkpoint()).code,'platform_browser_operation_failed');assert.equal(calls,1);
    assert.equal(f.responses.length,1);assert.deepEqual(await readPrivateJSON(filename),original);
    assert.equal(f.worker.activeCatalogMessage,null);assert.ok(!JSON.stringify(await f.worker.checkpoint()).includes('PRIVATE'));
  }finally{await f.cleanup();}
});
test('driver diagnostic fields are independently bounded and successful work cannot leave stale errors',async()=>{
  for(const diagnostic of [{phase:'error',line:0,column:-1},{phase:'error',line:100001,column:true},
    {phase:'error',line:'22',column:1.2},{phase:'error',line:22,column:undefined}]){
    const f=await fixture();f.driver.executeChromeBridgeRequest=async(api,request)=>{
      api.onProgress({phase:'PRIVATE phase'});api.onProgress(diagnostic);
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:'platform_browser_operation_failed'};
    };
    try{
      await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.done);const result=await f.worker.checkpoint();
      assert.equal(Object.hasOwn(result,'driver_line'),diagnostic.line===22);
      assert.ok(!Object.hasOwn(result,'driver_column'));assert.ok(!Object.hasOwn(result,'driver_phase'));
      assert.ok(!JSON.stringify(result).includes('PRIVATE'));
    }finally{await f.cleanup();}
  }
  const f=await fixture();f.driver.executeChromeBridgeRequest=async(api,request)=>{
    api.onProgress({phase:'catalog'});api.onProgress({phase:'error',line:500,column:20});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:[]};
  };
  try{
    await f.enqueue('find_drafts');await f.worker.checkpoint();await until(()=>f.worker.processed===1&&f.worker.state==='ready');
    assert.ok(!Object.keys(await f.worker.checkpoint()).some(key=>key.startsWith('driver_')));
    await assert.rejects(fs.lstat(path.join(f.run,'worker-driver-error.json')),{code:'ENOENT'});
    f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};f.worker.clock=()=>Date.now()+20000;
    await f.worker.checkpoint();assert.equal(f.worker.done,true);assert.ok(!Object.keys(await f.worker.checkpoint()).some(key=>key.startsWith('driver_')));
  }finally{await f.cleanup();}
});
test('CLI failures retain only bounded exit known-error and errno diagnostics at attach',async()=>{
  const packet=(code,message='PRIVATE TOKEN /private/book 正文')=>JSON.stringify({ok:false,error:code,message});
  const cases=[
    [{code:2,stderr:packet('io_or_input_error','[Errno '+os.constants.errno.EPERM+"] Operation not permitted: '/PRIVATE/book/.story/operation.lock' TOKEN 正文")},2,'io_or_input_error','EPERM'],
    [{code:2,stderr:packet('io_or_input_error','[Errno '+os.constants.errno.EROFS+'] PRIVATE')},2,'io_or_input_error','EROFS'],
    [{code:2,stderr:packet('io_or_input_error','attempt to write a readonly database PRIVATE')},2,'io_or_input_error','none'],
    [{code:2,stderr:packet('io_or_input_error','PRIVATE prefix [Errno '+os.constants.errno.EPERM+']')},2,'io_or_input_error','none'],
    [{code:2,stderr:packet('book_missing')},2,'book_missing','none'],
    [{code:2,stderr:packet('schema_mismatch')},2,'schema_mismatch','none'],
    [{code:2,stderr:packet('platform_draft_source_changed')},2,'platform_draft_source_changed','none'],
    [{code:2,stderr:packet('platform_chrome_unsafe_file')},2,'platform_chrome_unsafe_file','none'],
    [{code:2,stderr:packet('platform_chrome_private_token')},2,'other','none'],
    [{code:'EACCES',stderr:'PRIVATE unreadable executable'},null,'other','EACCES'],
    [{code:'EINVENTED',errno:'PRIVATE',stderr:'PRIVATE not JSON'},null,'other','none'],
    [{code:256,stderr:packet('invalid_input')},null,'invalid_input','none'],
    [{code:-1,stderr:packet('io_or_input_error','[Errno 999] PRIVATE')},null,'io_or_input_error','none'],
    [{code:'2',signal:'SIGTERM',stderr:packet('io_or_input_error','[Errno 01] PRIVATE')},null,'io_or_input_error','none'],
    [{code:1,stderr:JSON.stringify([{error:'book_missing',message:'PRIVATE'}])},1,'other','none'],
    [{code:1,stderr:packet('PRIVATE unknown','[Errno '+os.constants.errno.EACCES+'] PRIVATE')},1,'other','none']
  ];
  for(const [failure,exit,known,errno] of cases){
    const f=await fixture();const calls=[];
    const worker=new ChromeWorker(f.config,{sdkFactory:async()=>f.agent,driver:f.driver,commandRunner:async(executable,args,options)=>{
      calls.push(args[3]);assert.equal(executable,f.config.python_executable);
      assert.deepEqual(args.slice(0,4),['-I','-B',f.config.story_cli,'publish-chrome-attach']);
      assert.equal(options.timeout,20000);assert.equal(options.encoding,'utf8');assert.ok(!Object.hasOwn(options,'shell'));
      throw Object.assign(Error('PRIVATE child process error'),failure,{stdout:'PRIVATE stdout 正文'});
    }});
    try{
      await worker.checkpoint();await until(()=>worker.done);const result=await worker.checkpoint();
      assert.equal(result.code,known==='platform_chrome_unsafe_file'?known:'platform_chrome_worker_cli_failed');
      assert.equal(result.cli_exit_code,exit);assert.equal(result.cli_known_error,known);assert.equal(result.cli_errno,errno);
      assert.equal(result.failure_phase,'bridge_attach');assert.equal(result.phase,'cleanup');assert.equal(result.processed,0);
      assert.deepEqual(calls,['publish-chrome-attach'],'a failed local attach never claims or retries a request');
      await assert.rejects(fs.lstat(f.config.handle_path),{code:'ENOENT'});
      const serialized=JSON.stringify(result);
      for(const secret of ['PRIVATE','TOKEN','stdout','stderr','正文',f.directory,'SIGTERM'])assert.ok(!serialized.includes(secret));
      assert.deepEqual(Object.keys(result).sort(),['cli_errno','cli_exit_code','cli_known_error','code','done','error_marker','error_name','failure_phase','phase','processed','state']);
    }finally{await worker.stop();await f.cleanup();}
  }
});
test('successful CLI exit with invalid protocol remains exit zero and never exposes stdout',async()=>{
  for(const stdout of ['PRIVATE stdout body',JSON.stringify({ok:false,error:'book_missing',message:'PRIVATE TOKEN'})]){
    const f=await fixture();const worker=new ChromeWorker(f.config,{sdkFactory:async()=>f.agent,driver:f.driver,
      commandRunner:async()=>({stdout,stderr:'PRIVATE ignored stderr'})});
    try{
      await worker.checkpoint();await until(()=>worker.done);const result=await worker.checkpoint();
      assert.equal(result.code,'platform_chrome_worker_cli_failed');assert.equal(result.cli_exit_code,0);
      assert.equal(result.cli_known_error,'other');assert.equal(result.cli_errno,'none');
      assert.ok(!JSON.stringify(result).includes('PRIVATE'));
    }finally{await worker.stop();await f.cleanup();}
  }
});
test('real local executable absence is reported without leaking its path or starting a CLI',async()=>{
  const f=await fixture();const worker=new ChromeWorker({...f.config,python_executable:path.join(f.directory,'PRIVATE-no-executable')},
    {sdkFactory:async()=>f.agent,driver:f.driver});
  try{
    await worker.checkpoint();await until(()=>worker.done);const result=await worker.checkpoint();
    assert.equal(result.code,'platform_chrome_worker_cli_failed');assert.equal(result.cli_exit_code,null);
    assert.equal(result.cli_known_error,'other');assert.equal(result.cli_errno,'ENOENT');
    assert.equal(result.failure_phase,'bridge_attach');assert.equal(result.processed,0);
    assert.ok(!JSON.stringify(result).includes(f.directory));assert.ok(!JSON.stringify(result).includes('PRIVATE'));
  }finally{await worker.stop();await f.cleanup();}
});
test('first CLI failure diagnostics survive a different detach failure without exposing either packet',async()=>{
  const f=await fixture();const calls=[];
  const worker=new ChromeWorker(f.config,{sdkFactory:async()=>f.agent,driver:f.driver,commandRunner:async(_executable,args)=>{
    const action=args[3];calls.push(action);
    if(action==='publish-chrome-attach'){
      await privateJSON(f.config.handle_path,{schema:1,session_id:SESSION,book_root:f.book,book_id:'book',binding_sha256:BINDING,token:'PRIVATE TOKEN'});
      return {stdout:JSON.stringify({ok:true,session_id:SESSION})};
    }
    throw Object.assign(Error('PRIVATE'),{code:action==='publish-chrome-health'?2:3,
      stderr:JSON.stringify({ok:false,error:'io_or_input_error',message:'[Errno '+os.constants.errno[action==='publish-chrome-health'?'EPERM':'EACCES']+'] PRIVATE body'})});
  }});
  try{
    await worker.checkpoint();await until(()=>worker.done);const result=await worker.checkpoint();
    assert.deepEqual(calls,['publish-chrome-attach','publish-chrome-health','publish-chrome-detach']);
    assert.equal(result.code,'platform_chrome_worker_cli_failed');assert.equal(result.failure_phase,'bridge_health');
    assert.equal(result.cli_exit_code,2);assert.equal(result.cli_known_error,'io_or_input_error');assert.equal(result.cli_errno,'EPERM');
    assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.equal(result.processed,0);
  }finally{await worker.stop();await f.cleanup();}
});
test('health loss after ready closes owned tabs detaches and refuses to silently reuse the old handle',async()=>{
  const f=await fixture();let owned;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    owned=await api.newTab(BOOK_URL);
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:{...f.config.target}};
  };
  try{
    await f.enqueue();await f.worker.checkpoint();await until(()=>f.worker.processed===1);
    f.original.playwright.domSnapshot=async()=>{throw Error('PRIVATE debugger lost after ready');};
    f.worker.clock=()=>Date.now()+20000;
    await f.worker.checkpoint();assert.equal(f.worker.done,true);assert.equal(f.worker.state,'failed');
    assert.equal(f.worker.probeFallbackUsed,undefined,'an already attached worker never opens a replacement health tab');
    assert.equal(f.events.filter(row=>row[0]==='new').length,1,'the only new tab belongs to the previously queued operation');
    assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===owned.id));
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
    const next=new ChromeWorker(f.config,{sdkFactory:async()=>f.agent});
    const result=await next.checkpoint();assert.equal(result.done,true);assert.equal(result.code,'platform_chrome_worker_handle_exists');
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1,'a fresh worker must receive a fresh controller run directory');
  }finally{await f.cleanup();}
});
test('a changed health tab records bounded private evidence and never grants another health receipt',async()=>{
  for(const mismatch of ['url','handle']){
    const f=await fixture();try{
      await f.worker.checkpoint();const health=f.events.filter(row=>row[0]==='cli'&&row[1]==='health'&&row.length>2).length;
      if(mismatch==='url')await f.original.goto('https://fanqienovel.com/main/writer/preview/10&99');
      else {const get=f.browser.tabs.get;f.browser.tabs.get=async id=>({...await get(id),id:'changed'});}
      await assert.rejects(f.worker.refreshHealth(),{code:mismatch==='url'?'platform_chrome_worker_target_tab_changed':'platform_chrome_worker_target_tab_closed'});
      const filename=path.join(f.run,'worker-probe-error.private.json'),proof=await readPrivateJSON(filename);
      assert.equal(proof.expected_tab_id,'original');assert.equal(proof.expected_url,BOOK_URL);
      assert.equal(proof.observed_tab_id,mismatch==='url'?'original':'changed');
      assert.equal(proof.observed_url,mismatch==='url'?'https://fanqienovel.com/main/writer/preview/10&99':null);
      assert.equal(proof.attached,true);assert.equal(proof.listed,true);
      if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='health'&&row.length>2).length,health);
      const result=await f.worker.checkpoint();assert.equal(result.state,'failed');
      assert.ok(!JSON.stringify(result).includes('https:'));assert.equal(f.events.filter(row=>row[0]==='new').length,0);
    }finally{await f.cleanup();}
  }
});
test('health retries only an exact transient snapshot timeout while pinning the same URL',async()=>{
  for(const mode of ['recover','repeat','denied','changed']){
    const f=await fixture();try{
      await f.worker.checkpoint();let calls=0;const snapshot=f.original.playwright.domSnapshot;
      f.original.playwright.domSnapshot=async()=>{
        calls++;
        if(mode==='recover'&&calls===2)return snapshot();
        if(mode==='changed')await f.original.goto('https://fanqienovel.com/main/writer/preview/10&99');
        throw Object.assign(Error('Timed out after 3000ms waiting for CDP command Runtime.evaluate.'),
          mode==='denied'?{code:'permission_denied'}:{});
      };
      if(mode==='recover')await f.worker.refreshHealth();
      else{
        await assert.rejects(f.worker.refreshHealth());
        const filename=path.join(f.run,'worker-health-error.private.json'),proof=await readPrivateJSON(filename);
        assert.equal(proof.phase,'existing_tab_probe');
        assert.ok(['dom_snapshot','retry_dom_snapshot','retry_url'].includes(proof.step));
        if(process.platform!=='win32')assert.equal((await fs.stat(filename)).mode&0o777,0o600);
      }
      assert.equal(calls,['recover','repeat'].includes(mode)?2:1);
      assert.equal(f.events.filter(row=>row[0]==='new').length,0);
    }finally{await f.cleanup();}
  }
});
test('non-Chrome multiple Chrome and unrelated tab inventories refuse health without new tabs',async()=>{
  for(const options of [{inventory:[{id:'cdp',family:'chrome',type:'cdp'}]},
    {inventory:[{id:'one',family:'chrome',type:'extension'},{id:'two',family:'chrome',type:'extension'}]},
    {noAgentTabs:true,userTabs:[{id:'other',providerTabId:'other',url:'https://example.com',title:'unrelated'}]}]){
    const f=await fixture(options);try{
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,'failed');
      assert.ok(!f.events.some(row=>row[0]==='cli'||row[0]==='new'||row[0]==='goto'||row[0]==='claim'));
    }finally{await f.cleanup();}
  }
});
test('a checkpoint awaits a complete create beyond its yield budget without replaying the claim',async()=>{
  const f=await fixture();let release;let executions=0;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    executions++;await new Promise(resolve=>{release=resolve;});
    await api.newTab('https://fanqienovel.com/preview/10&99');
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:'99'};
  };
  try{
    await f.enqueue('create_draft',{chapter:12,title:'标题',body:'PRIVATE 正文'});
    let returned=false;const checkpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});
    await until(()=>executions===1);await sleep(15);assert.equal(returned,false);assert.equal(f.worker.state,'busy');
    assert.equal(executions,1);release();const result=await checkpoint;
    assert.equal(result.done,false);assert.equal(result.state,'ready');assert.ok(!JSON.stringify(result).includes('PRIVATE'));
    await f.worker.stop();
    assert.equal(executions,1);assert.equal(f.responses.length,1);assert.equal(f.events.filter(row=>row[0]==='screenshot').length,1);
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));assert.equal(f.events.filter(row=>row[0]==='deliverable').length,0);
    assert.equal(f.events.find(row=>row[0]==='screenshot')[2].fullPage,false);
  }finally{release?.();await f.cleanup();}
});
test('each checkpoint owns its SDK work and health timer and proactively refreshes the next call',async()=>{
  const f=await fixture();let active=false;const intervals=new Map(),callbacks=[];
  f.worker.interval=(fn,delay)=>{assert.equal(active,true);assert.equal(delay,15000);callbacks.push(fn);intervals.set(fn,delay);return fn;};
  f.worker.cancelInterval=id=>intervals.delete(id);
  const list=f.agent.browsers.list,snapshot=f.original.playwright.domSnapshot;
  f.agent.browsers.list=async()=>{assert.equal(active,true,'SDK inventory requires an active tool invocation');return list();};
  f.original.playwright.domSnapshot=async()=>{assert.equal(active,true,'SDK DOM requires an active tool invocation');return snapshot();};
  const invoke=async()=>{active=true;try{return await f.worker.checkpoint();}finally{active=false;}};
  try{
    await invoke();assert.equal(intervals.size,0);assert.equal(f.worker.healthPromise,null);
    const events=f.events.length,reads=f.events.filter(row=>row[0]==='snapshot').length;
    for(const fn of callbacks)fn();await sleep(15);
    assert.equal(f.events.length,events,'expired interval callbacks cannot start SDK work after tool return');
    await f.enqueue();assert.equal(f.worker.processed,0,'the queue is not serviced between tool calls');
    await invoke();assert.equal(f.worker.processed,1);assert.equal(intervals.size,0);assert.equal(f.worker.healthPromise,null);
    assert.ok(f.events.filter(row=>row[0]==='snapshot').length>reads,'each resumed call proves current browser health');
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1);
  }finally{active=true;await f.cleanup();active=false;}
});
test('long operations renew health within the active call and drain an in-flight probe before returning',async()=>{
  const f=await fixture();let timer,releaseOperation,releaseHealth,holdHealth=false,returned=false,active=true,cancelled=false,executions=0;
  f.worker.interval=fn=>{timer=fn;return fn;};f.worker.cancelInterval=()=>{cancelled=true;};
  const snapshot=f.original.playwright.domSnapshot;
  f.original.playwright.domSnapshot=async()=>{
    assert.equal(active,true,'all browser work stays in the active call');
    if(holdHealth)await new Promise(resolve=>{releaseHealth=resolve;});
    return snapshot();
  };
  f.driver.executeChromeBridgeRequest=async(_api,request)=>{
    executions++;await new Promise(resolve=>{releaseOperation=resolve;});
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:[]};
  };
  try{
    await f.enqueue('find_drafts');const checkpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});
    await until(()=>typeof releaseOperation==='function');await sleep(15);assert.equal(returned,false);
    const before=f.events.filter(row=>row[0]==='cli'&&row[1]==='health').length;
    timer();await until(()=>f.worker.healthPromise===null);
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='health').length,before+2,'long operation keeps its actual health fresh');
    holdHealth=true;timer();await until(()=>typeof releaseHealth==='function');
    releaseOperation();await until(()=>cancelled);assert.equal(returned,false);assert.ok(f.worker.healthPromise);
    holdHealth=false;releaseHealth();const result=await checkpoint;active=false;
    assert.equal(result.done,false);assert.equal(result.state,'ready');assert.equal(f.worker.healthPromise,null);
    assert.equal(f.worker.checkpointPromise,null);assert.equal(executions,1);assert.equal(f.responses.length,1);
    const events=f.events.length;timer();await sleep(15);assert.equal(f.events.length,events);
  }finally{releaseOperation?.();releaseHealth?.();active=true;await f.cleanup();}
});
test('a fallback DOM call is awaited before its deadline failure cleans the owned tab',async()=>{
  const f=await fixture();let clock=Date.now(),release,fresh,returned=false;
  f.worker.clock=()=>clock;
  f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};
  const create=f.browser.tabs.new;f.browser.tabs.new=async()=>{
    fresh=await create();fresh.playwright.domSnapshot=()=>new Promise(resolve=>{release=resolve;});return fresh;
  };
  try{
    const checkpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});
    await until(()=>typeof release==='function');clock+=16000;await sleep(15);
    assert.equal(returned,false,'no timeout race can orphan the target DOM await');
    release('Visible page');const result=await checkpoint;
    assert.equal(result.done,true);assert.equal(result.code,'platform_chrome_worker_existing_tab_required');
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,0);
    assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===fresh.id).length,1);
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{release?.('');await f.cleanup();}
});
test('idle expiry and an instance-bound stop finish at the next call before another health probe or claim',async()=>{
  for(const reason of ['idle','stop']){
    const f=await fixture();try{
      await f.worker.checkpoint();const health=f.events.filter(row=>row[0]==='cli'&&row[1]==='health').length;
      const claims=f.events.filter(row=>row[0]==='cli'&&row[1]==='next').length;
      await f.enqueue('create_draft');
      if(reason==='idle')f.worker.lastActivity=Date.now()-121000;
      else await privateJSON(f.config.stop_path,{schema:1,worker_instance:INSTANCE,keep_readback:false});
      const result=await f.worker.checkpoint();assert.equal(result.done,true);assert.equal(result.state,reason==='idle'?'idle_timeout':'stopped');
      assert.equal(f.worker.processed,0);assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='health').length,health);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='next').length,claims);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
    }finally{await f.cleanup();}
  }
});
test('stop drains the one active create before cleanup and never starts another checkpoint loop',async()=>{
  const f=await fixture();let release,returned=false,stopped=false,executions=0,owned;
  f.driver.executeChromeBridgeRequest=async(api,request)=>{
    executions++;await new Promise(resolve=>{release=resolve;});
    owned=await api.newTab('https://fanqienovel.com/preview/10&99');
    return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:'99'};
  };
  try{
    await f.enqueue('create_draft');const checkpoint=f.worker.checkpoint().then(result=>{returned=true;return result;});
    await until(()=>typeof release==='function');const stopping=f.worker.stop().then(()=>{stopped=true;});
    await sleep(15);assert.equal(returned,false);assert.equal(stopped,false);assert.equal(executions,1);
    release();const [result]=await Promise.all([checkpoint,stopping]);
    assert.equal(result.done,true);assert.equal(result.state,'stopped');assert.equal(executions,1);assert.equal(f.responses.length,1);
    assert.equal(f.worker.healthPromise,null);assert.equal(f.worker.checkpointPromise,null);
    assert.equal(f.events.filter(row=>row[0]==='close'&&row[1]===owned.id).length,1);
    assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
    assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
  }finally{release?.();await f.cleanup();}
});
test('duplicate claimed create IDs and unknown respond failures never execute a create again',async()=>{
  for(const respondFails of [false,true]){
    const f=await fixture({respondFails});let executions=0;
    f.driver.executeChromeBridgeRequest=async(_api,request)=>{executions++;return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,result:'99'};};
    try{
      const claim=await f.enqueue('create_draft');if(!respondFails)f.requests.push({...claim});
      await f.worker.checkpoint();if(!f.worker.done)await f.worker.checkpoint();assert.equal(f.worker.done,true);assert.equal(executions,1);assert.equal(f.worker.state,'failed');
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='attach').length,1);
      assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='detach').length,1);
    }finally{await f.cleanup();}
  }
});
test('a previously verified readback is never marked or kept after health loss failed operation or unknown receipt',async()=>{
  for(const failure of ['health','respond','driver']){
    const options={respondFails:false};const f=await fixture(options);const owned=[];
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      const remote=String(99+owned.length);const url='https://fanqienovel.com/preview/10&'+remote;
      owned.push(await api.newTab(url));
      if(failure==='driver'&&owned.length>1)return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:false,error:'platform_browser_operation_failed'};
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,
        result:request.operation==='create_draft'?remote:{remote_id:remote,title:'title',body:'PRIVATE body',url,state:'draft'}};
    };
    try{
      await f.enqueue('create_draft',{chapter:12,title:'title',body:'PRIVATE body'});
      await f.worker.checkpoint();await until(()=>f.worker.processed===1&&f.worker.state==='ready');
      assert.equal(f.events.filter(row=>row[0]==='screenshot').length,1);
      assert.equal(f.events.filter(row=>row[0]==='deliverable').length,0,'per-chapter success is not terminal batch approval');
      if(failure==='health'){
        f.original.playwright.domSnapshot=async()=>{throw Error('Debugger unattached');};f.worker.clock=()=>Date.now()+20000;
      }else{options.respondFails=failure==='respond';await f.enqueue('read_draft',{remote_id:'100'});}
      await f.worker.checkpoint();assert.equal(f.worker.done,true);assert.equal(f.worker.state,'failed');
      for(const tab of owned)assert.ok(f.events.some(row=>row[0]==='close'&&row[1]===tab.id));
      assert.equal(f.events.filter(row=>row[0]==='deliverable').length,0);
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
});
test('disconnect stop packet is private instance-bound and can close the verified readback',async()=>{
  for(const keep of [false,true]){
    const f=await fixture();let readback;
    f.driver.executeChromeBridgeRequest=async(api,request)=>{
      readback=await api.newTab('https://fanqienovel.com/preview/10&99');
      return {schema:1,id:request.id,session_id:SESSION,binding_sha256:BINDING,nonce:request.nonce,ok:true,
        result:{remote_id:'99',title:'title',body:'body',url:'https://fanqienovel.com/preview/10&99',state:'draft'}};
    };
    try{
      await f.enqueue('read_draft',{remote_id:'99'});await f.worker.checkpoint();await until(()=>f.worker.processed===1);
      await privateJSON(f.config.stop_path,{schema:1,worker_instance:INSTANCE,keep_readback:keep});
      await f.worker.checkpoint();assert.equal(f.worker.done,true);assert.equal(f.worker.state,'stopped');
      assert.equal(f.events.some(row=>row[0]==='close'&&row[1]===readback.id),!keep);
      assert.equal(f.events.filter(row=>row[0]==='deliverable').length,Number(keep));
      assert.ok(!f.events.some(row=>row[0]==='close'&&row[1]==='original'));
    }finally{await f.cleanup();}
  }
  const f=await fixture();try{
    await privateJSON(f.config.stop_path,{schema:1,worker_instance:'9'.repeat(32),keep_readback:false});
    await f.worker.checkpoint();await until(()=>f.worker.done);assert.equal(f.worker.fatalCode,'platform_chrome_worker_invalid_stop');
  }finally{await f.cleanup();}
});
test('wrong queue path binding target comparison policy or encoding is refused before browser execution',async()=>{
  for(const change of ['path','binding','target','policy','invalid_policy','encoding','invalid_encoding','cross_encoding']){
    const f=await fixture();let executions=0;f.driver.executeChromeBridgeRequest=async()=>{executions++;};
    if(change==='cross_encoding')f.config.body_encoding='markdown_paragraphs_v1';
    try{
      const claim=await f.enqueue();
      if(change==='path')claim.request_path=path.join(f.directory,'unrelated.json');
      else{const packet=await readPrivateJSON(claim.request_path);if(change==='binding')packet.binding_sha256='9'.repeat(64);else if(change==='target')packet.binding.target.remote_book_id='999';else if(change.includes('encoding'))packet.binding.body_encoding=change==='encoding'?'markdown_paragraphs_v1':change==='cross_encoding'?'markdown_paragraphs_v2':null;else packet.binding.comparison_policy=change==='policy'?'fanqie_terminal_lf_v1':null;await fs.writeFile(claim.request_path,JSON.stringify(packet));}
      await f.worker.checkpoint();await until(()=>f.worker.done);assert.equal(executions,0);assert.equal(f.worker.fatalCode,'platform_chrome_worker_binding_mismatch');
    }finally{await f.cleanup();}
  }
});
test('explicit comparison policy reaches CLI attach and must match each claimed request',async()=>{
  const f=await fixture();f.config.comparison_policy='fanqie_terminal_lf_v1';
  try{
    const claim=await f.enqueue();const packet=await readPrivateJSON(claim.request_path);
    packet.binding.comparison_policy='fanqie_terminal_lf_v1';await fs.writeFile(claim.request_path,JSON.stringify(packet));
    await f.worker.checkpoint();await until(()=>f.worker.processed===1);
    const attached=f.events.find(row=>row[0]==='cli'&&row[1]==='attach');
    assert.deepEqual(attached.slice(-2),['--comparison-policy','fanqie_terminal_lf_v1']);
    assert.ok(!f.worker.fatalCode);assert.equal(f.responses.length,1);
  }finally{await f.cleanup();}
});
test('explicit body encoding reaches CLI attach and matches the queue independently of comparison policy',async()=>{
  for(const encoding of ['markdown_paragraphs_v1','markdown_paragraphs_v2','plain_paragraphs_v1']){
    const f=await fixture();f.config.body_encoding=encoding;
    try{
      const claim=await f.enqueue();const packet=await readPrivateJSON(claim.request_path);
      packet.binding.body_encoding=encoding;await fs.writeFile(claim.request_path,JSON.stringify(packet));
      await f.worker.checkpoint();await until(()=>f.worker.processed===1);
      const attached=f.events.find(row=>row[0]==='cli'&&row[1]==='attach');
      assert.deepEqual(attached.slice(-2),['--body-encoding',encoding]);
      assert.ok(!attached.includes('--comparison-policy'));assert.ok(!f.worker.fatalCode);assert.equal(f.responses.length,1);
    }finally{await f.cleanup();}
  }
});
test('private config rejects readable-by-others files symlinks broad directories or executable substitution',async()=>{
  const f=await fixture();try{
    assert.equal(validateWorkerConfig(f.config),f.config);
    for(const change of [{driver_path:path.join(f.directory,'other.mjs')},{story_cli:path.join(f.directory,'other.py')},{sdk_path:path.join(f.directory,'transport.mjs')},{worker:{pid:0,instance:INSTANCE}},{target:{platform:'fanqie',account_id:'123',remote_book_id:'10',extra:true}},
      {comparison_policy:null},{comparison_policy:true},{comparison_policy:'trim'},
      {body_encoding:null},{body_encoding:true},{body_encoding:'trim'},
      {bootstrap_deadline_ms:null},{bootstrap_deadline_ms:true},{bootstrap_deadline_ms:0},{bootstrap_deadline_ms:-1},
      {bootstrap_deadline_ms:1.5},{bootstrap_deadline_ms:Number.MAX_SAFE_INTEGER+1}]){
      assert.throws(()=>validateWorkerConfig({...f.config,...change}),{code:'platform_chrome_worker_invalid_config'});
    }
    const filename=path.join(f.run,'config.json');await privateJSON(filename,f.config);assert.deepEqual(await readPrivateJSON(filename),f.config);
    const alias=path.join(f.run,'alias.json');await fs.symlink(filename,alias);
    assert.equal((await runChromeWorker(alias)).code,'platform_chrome_worker_unsafe_file');
    if(process.platform!=='win32'){
      await fs.chmod(filename,0o644);assert.equal((await runChromeWorker(filename)).code,'platform_chrome_worker_unsafe_file');await fs.chmod(filename,0o600);
      await fs.chmod(f.run,0o755);assert.equal((await runChromeWorker(filename)).code,'platform_chrome_worker_unsafe_file');await fs.chmod(f.run,0o700);
    }
  }finally{await f.cleanup();}
});
test('actual worker private JSON read and initialization succeed in a module realm without process',async()=>{
  const f=await fixture();try{
    const filename=path.join(f.run,'config.json');await privateJSON(filename,f.config);
    const fixturePath=fileURLToPath(new URL('./chrome_worker_without_process_fixture.mjs',import.meta.url));
    const result=await new Promise((resolve,reject)=>execFile(process.execPath,
      ['--experimental-vm-modules',fixturePath,filename],{encoding:'utf8',timeout:10000},(error,stdout,stderr)=>error?
        reject(Error('Process-free VM fixture failed: '+stdout+stderr)):resolve(JSON.parse(stdout))));
    assert.deepEqual(result,{ok:true,process_global:'undefined',private_read:true,initialized:true,fallback_closed:true,user_closed:false});
  }finally{await f.cleanup();}
});

test('closed target health tab has a specific stop cause without navigating or retrying',async()=>{
 const f=await fixture();try{
  await f.worker.checkpoint();const count=f.events.filter(row=>row[0]==='cli'&&row[1]==='health'&&row.length>2).length;
  f.browser.tabs.list=async()=>[];f.browser.tabs.get=async()=>{throw Error('No tab');};
  await assert.rejects(f.worker.refreshHealth(),{code:'platform_chrome_worker_target_tab_closed'});
  const result=await f.worker.checkpoint();assert.equal(result.code,'platform_chrome_worker_target_tab_closed');
  assert.equal(f.events.filter(row=>row[0]==='cli'&&row[1]==='health'&&row.length>2).length,count);
  assert.ok(!f.events.some(row=>['new','goto','close'].includes(row[0])));
 }finally{await f.cleanup();}
});
