/* Native-host admission contracts only. No host SDK, browser or child process. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ChromeWorker,validateWorkerConfig,runChromeWorker} from '../skills/story-skill/scripts/story_chrome_worker.mjs';

const SCRIPTS=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../skills/story-skill/scripts');
const QODER_SDK='/Applications/Qoder CN.app/Contents/Resources/node-repl/node_modules/@qoder-space/browser-use/index.js';
const CODEX_SDK='/synthetic/.codex/plugins/cache/openai-bundled/chrome/1.0.0/scripts/browser-client.mjs';
function config(directory,changes={}) {
  return {schema:1,book_root:path.join(directory,'book'),book_id:'synthetic-book',plan_id:'plan',profile_id:'profile.json',
    target:{platform:'fanqie',account_id:'123',remote_book_id:'10'},
    handle_path:path.join(directory,'handle.json'),binding_sha256:'2'.repeat(64),worker:{pid:process.pid,instance:'3'.repeat(32)},
    python_executable:path.join(directory,'python'),story_cli:path.join(SCRIPTS,'story.py'),
    driver_path:path.join(SCRIPTS,'story_chrome_bridge.mjs'),sdk_path:CODEX_SDK,
    health_interval_seconds:15,idle_timeout_seconds:120,checkpoint_ms:20000,poll_interval_ms:250,
    browser_id:null,stop_path:path.join(directory,'stop.json'),...changes};
}
const native={execution_host:'qoder-cn',native_model_authorized:true,sdk_path:QODER_SDK};
async function fixture(changes,callback) {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'story-native-host-'));
  await fs.chmod(directory,0o700);
  try {
    const value=config(directory,changes),filename=path.join(directory,'config.json');
    await fs.writeFile(filename,JSON.stringify(value),{mode:0o600,flag:'wx'});
    await callback({directory,value,filename});
  } finally { await fs.rm(directory,{recursive:true,force:true}); }
}
async function syntheticCheckpoints(callback) {
  const original=ChromeWorker.prototype.checkpoint,seen=[];
  // Exercise the real runChromeWorker configuration/caching admission without
  // entering bootstrap, reading credentials or launching the queue CLI.
  ChromeWorker.prototype.checkpoint=async function() {
    seen.push(this);
    return {done:Boolean(this.fatalCode),state:this.fatalCode?'failed':'synthetic_checkpoint',
      code:this.fatalCode??null,processed:this.processed};
  };
  try { await callback(seen); }
  finally { ChromeWorker.prototype.checkpoint=original; }
}

test('legacy and explicit Codex configurations keep their original SDK and do not gain native authorization',()=>{
  const base=config('/synthetic');
  assert.equal(validateWorkerConfig(base),base);
  assert.equal(validateWorkerConfig({...base,execution_host:'codex'}).execution_host,'codex');
  assert.equal(validateWorkerConfig({...base,native_model_authorized:false}).native_model_authorized,false);
  for(const change of [{native_model_authorized:true},{sdk_path:QODER_SDK},{execution_host:'codex',native_model_authorized:true}]){
    assert.throws(()=>validateWorkerConfig({...base,...change}),{code:'platform_chrome_worker_invalid_config'});
  }
});

test('Qoder native host requires exact explicit authorization and its own public SDK path',()=>{
  const base=config('/synthetic',native);assert.equal(validateWorkerConfig(base),base);
  for(const authorized of [undefined,false,'true',1,null]){
    const changed={...base,native_model_authorized:authorized};if(authorized===undefined)delete changed.native_model_authorized;
    assert.throws(()=>validateWorkerConfig(changed),{code:'platform_chrome_worker_native_model_not_authorized'});
  }
  for(const sdk_path of [CODEX_SDK,'/Applications/Qoder.app/Contents/Resources/node-repl/node_modules/@qoder-space/browser-use/index.js',
    '/tmp/browser-use/index.js',QODER_SDK.replace('index.js','private-launcher.js')]){
    assert.throws(()=>validateWorkerConfig({...base,sdk_path}),{code:'platform_chrome_worker_invalid_config'});
  }
});

test('unsupported native host or fabricated model and context flags do not bypass admission',()=>{
  const base=config('/synthetic',native);
  assert.throws(()=>validateWorkerConfig({...base,execution_host:'workbuddy'}),{code:'platform_chrome_worker_invalid_config'});
  assert.throws(()=>validateWorkerConfig({...base,execution_host:'auto'}),{code:'platform_chrome_worker_invalid_config'});
  for(const extra of [{model_verified:true},{host_context:{sessionId:'fake'}},{execution_origin:'assistant_session'}]){
    assert.throws(()=>validateWorkerConfig({...base,...extra}),{code:'platform_chrome_worker_invalid_config'});
  }
});

test('Qoder entrypoint refuses missing factory without starting a worker checkpoint',async()=>{
  await syntheticCheckpoints(async seen=>{
    await fixture(native,async({filename})=>{
      for(const options of [{},{sdkFactory:'factory'},{sdkFactory:null}]){
        const result=await runChromeWorker(filename,options);
        assert.equal(result.code,'platform_chrome_worker_native_runtime_required');assert.equal(result.done,true);
      }
    });assert.equal(seen.length,0);
  });
});

test('Codex public entrypoint rejects injected native factories but accepts unchanged ordinary calls',async()=>{
  await syntheticCheckpoints(async seen=>{
    let calls=0;const factory=async()=>{calls++;return {};};
    await fixture({},async({filename})=>{
      const denied=await runChromeWorker(filename,{sdkFactory:factory});
      assert.equal(denied.code,'platform_chrome_worker_native_runtime_required');assert.equal(seen.length,0);
      const result=await runChromeWorker(filename);assert.equal(result.state,'synthetic_checkpoint');
      assert.equal(seen.length,1);assert.equal(seen[0].sdkFactory,null);assert.equal(calls,0);
    });
  });
});

test('native entrypoint accepts only a factory reference and cannot receive transport or driver overrides',async()=>{
  await syntheticCheckpoints(async seen=>{
    const factory=async()=>({});
    await fixture(native,async({filename})=>{
      for(const extra of [{command:()=>{}},{transport:{}},{driver:{}},{model_verified:true},{signal:{}}]){
        const result=await runChromeWorker(filename,{sdkFactory:factory,...extra});
        assert.equal(result.code,'platform_chrome_worker_invalid_config');assert.equal(seen.length,0);
      }
      const result=await runChromeWorker(filename,{sdkFactory:factory});assert.equal(result.state,'synthetic_checkpoint');
      assert.equal(seen.length,1);assert.equal(seen[0].sdkFactory,factory);
      assert.equal(seen[0].config.execution_host,'qoder-cn');assert.equal(seen[0].processed,0);
      assert.equal(seen[0].attached,false,'admission is not authenticated host readiness');
    });
  });
});

test('same native factory resumes one worker while a changed factory fails without calling the replacement',async()=>{
  await syntheticCheckpoints(async seen=>{
    let calls=0,replacementCalls=0;const factory=async()=>{calls++;return {};};
    await fixture(native,async({filename})=>{
      const first=await runChromeWorker(filename,{sdkFactory:factory});
      const second=await runChromeWorker(filename,{sdkFactory:factory});
      assert.equal(first.state,'synthetic_checkpoint');assert.equal(second.state,'synthetic_checkpoint');
      assert.equal(seen[0],seen[1]);assert.equal(calls,0,'bootstrap stays in the official host checkpoint');
      const result=await runChromeWorker(filename,{sdkFactory:async()=>{replacementCalls++;return {};}});
      assert.equal(result.code,'platform_chrome_worker_config_changed');assert.equal(result.done,true);
      assert.equal(seen[2],seen[0]);assert.equal(seen[0].stopRequested,true);
      assert.equal(seen[0].sdkFactory,factory);assert.equal(replacementCalls,0);
    });
  });
});

test('native target or host change cannot reuse a previously admitted worker',async()=>{
  for(const change of [{target:{platform:'fanqie',account_id:'123',remote_book_id:'20'}},
    {execution_host:'codex',native_model_authorized:false,sdk_path:CODEX_SDK}]){
    await syntheticCheckpoints(async seen=>{
      const factory=async()=>({});
      await fixture(native,async({filename,value})=>{
        await runChromeWorker(filename,{sdkFactory:factory});
        await fs.writeFile(filename,JSON.stringify({...value,...change}));
        const result=await runChromeWorker(filename,change.execution_host==='codex'?{}:{sdkFactory:factory});
        assert.equal(result.code,'platform_chrome_worker_config_changed');assert.equal(seen[1],seen[0]);
        assert.equal(seen[0].stopRequested,true);
      });
    });
  }
});
