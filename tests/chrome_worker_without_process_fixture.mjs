/* Subprocess fixture: run the actual module in a realm without `process`. */
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import vm from 'node:vm';
import {fileURLToPath,pathToFileURL} from 'node:url';

const filename=process.argv[2];
const workerURL=new URL('../skills/story-skill/scripts/story_chrome_worker.mjs',import.meta.url).href;
const source=await fs.readFile(fileURLToPath(workerURL),'utf8');
assert.ok(!/\bprocess\s*\.|['"]node:process['"]/.test(source));
const context=vm.createContext({URL,TextDecoder,Buffer,setTimeout,clearTimeout,setInterval,clearInterval});
assert.equal(vm.runInContext('typeof process',context),'undefined');
const module=new vm.SourceTextModule(source,{context,identifier:workerURL,initializeImportMeta:meta=>{meta.url=workerURL;}});
await module.link(async specifier=>{
  assert.ok(specifier.startsWith('node:')&&specifier!=='node:process');
  const namespace=await import(specifier);
  return new vm.SyntheticModule(Object.keys(namespace),function(){for(const key of Object.keys(namespace))this.setExport(key,namespace[key]);},{context});
});
await module.evaluate();
const config=await module.namespace.readPrivateJSON(filename);
assert.equal(module.namespace.validateWorkerConfig(config),config);
const managementURL='https://fanqienovel.com/main/writer/chapter-manage/10&test?type=2';
let userClosed=false,freshClosed=false,created=0,freshURL='about:blank',freshReads=0;
const tab={id:'target-existing',url:async()=>managementURL,close:async()=>{userClosed=true;},
  playwright:{domSnapshot:async()=>{throw Error('Debugger unattached');}}};
const fresh={id:'fresh-owned',url:async()=>freshURL,goto:async url=>{assert.equal(url,managementURL);freshURL=url;},
  close:async()=>{freshClosed=true;},playwright:{domSnapshot:async()=>++freshReads<=2?'':'Visible target management page'}};
const browser={browserId:'chrome-existing',documentation:async()=>'Official browser public API',nameSession:async()=>{},
  tabs:{list:async()=>[{id:tab.id,url:await tab.url()},...(created&&!freshClosed?[{id:fresh.id,url:await fresh.url()}]:[])],
    get:async id=>{assert.ok([tab.id,fresh.id].includes(id));return id===tab.id?tab:fresh;},
    new:async()=>{assert.equal(created++,0);return fresh;}},user:{openTabs:async()=>[]}};
const agent={browsers:{list:async()=>[{id:browser.browserId,type:'extension',family:'chrome'}],get:async id=>{assert.equal(id,browser.browserId);return browser;}}};
const session='1'.repeat(32);let detached=false;
const command=async(_config,action,extra=[])=>{
  if(action==='attach'){
    await fs.writeFile(config.handle_path,JSON.stringify({schema:1,session_id:session,book_root:config.book_root,
      book_id:config.book_id,binding_sha256:config.binding_sha256,token:'PRIVATE FIXTURE TOKEN'}),{flag:'wx',mode:0o600});
    return {ok:true,session_id:session};
  }
  if(action==='health'){
    if(!extra.length)return {ok:true,challenge:{schema:1,session_id:session,binding_sha256:config.binding_sha256,
      nonce:'7'.repeat(32),deadline_epoch:Date.now()/1000+30}};
    const packet=JSON.parse(await fs.readFile(extra[1],'utf8'));
    assert.deepEqual(packet.capabilities,{list_tabs:true,get_tab:true,dom_snapshot:true});return {ok:true,ready:true};
  }
  if(action==='next')return {ok:true,request_id:null,request_path:null,claimed_once:false};
  if(action==='detach'){detached=true;return {ok:true,detached:true};}
  throw Error('unexpected fixture command');
};
const worker=new module.namespace.ChromeWorker(config,{sdkFactory:async()=>agent,command,
  driver:{executeChromeBridgeRequest:async()=>{throw Error('no queued operations');},closeChromeBridgeSession:async()=>{}}});
try{
  await worker.checkpoint();const deadline=Date.now()+2000;
  while(worker.state!=='ready'&&!worker.done&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,2));
  assert.equal(worker.state,'ready',worker.fatalCode);assert.equal(worker.fatalCode,undefined);
}finally{await worker.stop();}
assert.equal(worker.state,'stopped');assert.equal(worker.fatalCode,undefined);
assert.equal(detached,true);assert.equal(vm.runInContext('typeof process',context),'undefined');
assert.equal(created,1);assert.equal(freshClosed,true);assert.equal(userClosed,false);assert.ok(freshReads>=3);
console.log(JSON.stringify({ok:true,process_global:'undefined',private_read:true,initialized:true,fallback_closed:true,user_closed:false}));
