/* Synthetic runner lifecycle. All browser/process edges are replaced in a
 * temporary module copy; the production runner body is otherwise unchanged. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import {validateWorkerConfig} from '../skills/story-skill/scripts/story_chrome_worker.mjs';

const SCRIPTS=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../skills/story-skill/scripts');
const digest=value=>createHash('sha256').update(value).digest('hex');
async function fixture(t){
  const directory=await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'story-workbuddy-runner-test-')));
  const home=path.join(directory,'home'),context=path.join(directory,'context');
  await fs.mkdir(home);await fs.mkdir(context,{mode:0o700});
  const cli=path.join(directory,'synthetic-cli');await fs.writeFile(cli,'Never executed',{mode:0o700});
  const target={platform:'fanqie',account_id:'123',remote_book_id:'456'};
  const session='story-wb-'+digest(JSON.stringify(['book',target.platform,target.account_id,target.remote_book_id])).slice(0,24);
  const config={schema:1,book_root:path.join(directory,'book'),book_id:'book',plan_id:'plan',profile_id:'profile.json',
    handle_path:path.join(context,'handle.json'),stop_path:path.join(context,'stop.json'),binding_sha256:'2'.repeat(64),target,
    worker:{pid:process.pid,instance:'3'.repeat(32)},python_executable:path.join(directory,'python3'),
    story_cli:path.join(SCRIPTS,'story.py'),driver_path:path.join(SCRIPTS,'story_chrome_bridge.mjs'),
    sdk_path:path.join(SCRIPTS,'story_workbuddy_browser.mjs'),execution_host:'workbuddy',native_model_authorized:true,
    workbuddy_cli_path:cli,workbuddy_cli_sha256:digest(await fs.readFile(cli)),workbuddy_session:session,
    workbuddy_state_directory:path.join(home,'.story-skill/workbuddy-browser-sessions',session),
    health_interval_seconds:15,idle_timeout_seconds:120,checkpoint_ms:1,poll_interval_ms:1,browser_id:null};
  const filename=path.join(context,'config.json');
  const save=()=>fs.writeFile(filename,JSON.stringify(config),{mode:0o600});await save();
  const calls=[],emitted=[],state={sequence:[{done:false,state:'ready'},{done:true,state:'stopped'}],failure:null,finalChange:{},writeFinal:true};
  const dependencies={os:{homedir:()=>home},validateWorkerConfig,
    spawn:()=>{throw Error('No subprocess/browser may launch in this suite');},
    prepareWorkBuddyBrowserCLI:async options=>{calls.push(['prepare',options]);await fs.mkdir(options.stateDirectory,{recursive:true,mode:0o700});return options;},
    setupWorkBuddyBrowserRuntime:async options=>{calls.push(['setup',options]);return {synthetic:true};},
    runChromeWorker:async(file,options)=>{
      assert.equal(file,filename);calls.push(['checkpoint',options.sdkFactory]);
      if(state.failure)throw state.failure;
      if(calls.filter(row=>row[0]==='checkpoint').length===1)await options.sdkFactory();
      const result=state.sequence.shift();assert.ok(result,'must stop after terminal checkpoint');
      if(result.done&&state.writeFinal)await fs.writeFile(path.join(context,'worker-final.json'),JSON.stringify({
        schema:1,done:true,worker:config.worker,binding_sha256:config.binding_sha256,
        cleanup_complete:true,owned_tabs_remaining:0,...state.finalChange}),{mode:0o600});
      return result;
    }};
  const key='workbuddy-runner-test-'+randomUUID();globalThis[key]=dependencies;
  let source=await fs.readFile(path.join(SCRIPTS,'story_workbuddy_worker.mjs'),'utf8');
  const replacements=[
    ["import os from 'node:os';",`const os=globalThis[${JSON.stringify(key)}].os;`],
    ["import {spawn} from 'node:child_process';",`const spawn=globalThis[${JSON.stringify(key)}].spawn;`],
    ["import {runChromeWorker,validateWorkerConfig} from './story_chrome_worker.mjs';",`const {runChromeWorker,validateWorkerConfig}=globalThis[${JSON.stringify(key)}];`],
    ["import {prepareWorkBuddyBrowserCLI,setupWorkBuddyBrowserRuntime} from './story_workbuddy_browser.mjs';",`const {prepareWorkBuddyBrowserCLI,setupWorkBuddyBrowserRuntime}=globalThis[${JSON.stringify(key)}];`]
  ];
  for(const [before,after] of replacements){assert.equal(source.split(before).length,2,'exact explicit test edge');source=source.replace(before,after);}
  const testModule=path.join(directory,'injected-runner.mjs');await fs.writeFile(testModule,source);
  const runner=await import(pathToFileURL(testModule).href);
  t.after(async()=>{delete globalThis[key];await fs.rm(directory,{recursive:true,force:true});});
  return {directory,context,config,filename,cli,save,calls,state,emitted,runner,
    lock:path.join(config.workbuddy_state_directory,'worker.lock'),run:()=>runner.runWorkBuddyWorker(filename,{emit:value=>emitted.push(value)})};
}

test('WorkBuddy config binds canonical session target and exact CLI bytes without starting a process',async t=>{
  const f=await fixture(t);assert.deepEqual(await f.runner.readWorkBuddyConfig(f.filename),f.config);
  assert.deepEqual(f.calls,[]);
  await fs.writeFile(f.cli,'changed');
  await assert.rejects(()=>f.runner.readWorkBuddyConfig(f.filename),{code:'platform_chrome_worker_unsafe_runtime'});
  assert.deepEqual(f.calls,[]);
});

test('runner rejects foreign host authorization session and state before any CLI preparation',async t=>{
  const f=await fixture(t),original={...f.config};
  for(const change of [{execution_host:'codex'},{execution_host:'qoder-cn'},{native_model_authorized:false},
    {target:{...f.config.target,remote_book_id:'999'}},{workbuddy_state_directory:path.join(f.directory,f.config.workbuddy_session)}]){
    Object.assign(f.config,original,change);await f.save();
    await assert.rejects(f.run);assert.deepEqual(f.calls,[]);
  }
});

test('config file must be private regular and canonical before any runner activity',async t=>{
  const f=await fixture(t);
  if(process.platform!=='win32'){
    await fs.chmod(f.filename,0o644);await assert.rejects(f.run,{code:'platform_chrome_worker_unsafe_file'});
    await fs.chmod(f.filename,0o600);
  }
  const alias=path.join(f.context,'alias.json');await fs.symlink(f.filename,alias);
  await assert.rejects(()=>f.runner.readWorkBuddyConfig(alias),{code:'platform_chrome_worker_unsafe_file'});
  assert.deepEqual(f.calls,[]);
});

test('runner checkpoints reuse one factory and release exclusive lock only after matching clean final',async t=>{
  const f=await fixture(t),result=await f.run();assert.equal(result.done,true);
  const checkpoints=f.calls.filter(row=>row[0]==='checkpoint');assert.equal(checkpoints.length,2);
  assert.equal(checkpoints[0][1],checkpoints[1][1]);assert.equal(f.calls.filter(row=>row[0]==='setup').length,1);
  assert.deepEqual(f.calls.find(row=>row[0]==='setup')[1],{executablePath:f.cli,
    sessionName:f.config.workbuddy_session,stateDirectory:f.config.workbuddy_state_directory,target:f.config.target});
  assert.equal(f.emitted.length,2);await assert.rejects(fs.lstat(f.lock),{code:'ENOENT'});
});

test('unsettled checkpoint failure retains lock and a second runner cannot replay it',async t=>{
  const f=await fixture(t),failure=Object.assign(new Error('synthetic transport loss'),{code:'UNKNOWN'});f.state.failure=failure;
  await assert.rejects(f.run,error=>error===failure);assert.ok((await fs.lstat(f.lock)).isFile());
  const before=f.calls.filter(row=>row[0]==='checkpoint').length;
  await assert.rejects(f.run,{code:'platform_chrome_worker_busy'});
  assert.equal(f.calls.filter(row=>row[0]==='checkpoint').length,before);assert.equal(f.emitted.length,0);
});

test('terminal checkpoint with absent incomplete or foreign cleanup proof keeps the lock',async t=>{
  const f=await fixture(t);
  for(const change of [null,{cleanup_complete:false},{owned_tabs_remaining:1},{done:false},
    {binding_sha256:'4'.repeat(64)},{worker:{...f.config.worker,instance:'5'.repeat(32)}}]){
    f.state.sequence=[{done:true,state:'failed'}];f.state.writeFinal=change!==null;f.state.finalChange=change||{};
    await fs.rm(path.join(f.context,'worker-final.json'),{force:true});
    const result=await f.run();assert.equal(result.done,true);assert.ok((await fs.lstat(f.lock)).isFile());
    await fs.unlink(f.lock); // Synthetic fixture cleanup only, never production recovery.
  }
});

test('WorkBuddy-only config fields cannot be injected into the unchanged Codex or Qoder branch',async t=>{
  const f=await fixture(t);
  for(const host of ['codex','qoder-cn']){
    const config={...f.config,execution_host:host};
    if(host==='codex'){config.native_model_authorized=false;config.sdk_path='/mock/openai-bundled/chrome/1/scripts/browser-client.mjs';}
    else config.sdk_path='/Applications/Qoder CN.app/Contents/Resources/node-repl/node_modules/@qoder-space/browser-use/index.js';
    assert.throws(()=>validateWorkerConfig(config),{code:'platform_chrome_worker_invalid_config'});
    for(const key of ['workbuddy_cli_path','workbuddy_cli_sha256','workbuddy_session','workbuddy_state_directory'])delete config[key];
    assert.equal(validateWorkerConfig(config),config);
  }
});
