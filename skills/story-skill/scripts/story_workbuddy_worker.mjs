/* Explicit WorkBuddy-native runner. Importing this module never opens a browser.
 * Model execution remains the caller's responsibility; no CLI flag attests it.
 */
import * as fs from 'node:fs/promises';
import {constants} from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {runChromeWorker,validateWorkerConfig} from './story_chrome_worker.mjs';
import {prepareWorkBuddyBrowserCLI,setupWorkBuddyBrowserRuntime} from './story_workbuddy_browser.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const fail=code=>{throw Object.assign(new Error(code),{code});};
const requireValue=(condition,code='platform_chrome_worker_invalid_config')=>{if(!condition)fail(code);};
const canonicalState=session=>path.join(os.homedir(),'.story-skill/workbuddy-browser-sessions',session);

export async function readWorkBuddyConfig(filename){
  requireValue(path.isAbsolute(filename));
  requireValue(await fs.realpath(filename)===filename,'platform_chrome_worker_unsafe_file');
  const handle=await fs.open(filename,constants.O_RDONLY|(constants.O_NOFOLLOW||0));
  let config;
  try{
    const stat=await handle.stat();
    requireValue(stat.isFile()&&stat.size<=65536&&(process.platform==='win32'||
      (stat.uid===process.getuid()&&(stat.mode&0o777)===0o600)),'platform_chrome_worker_unsafe_file');
    config=validateWorkerConfig(JSON.parse(await handle.readFile('utf8')));
  }finally{await handle.close();}
  requireValue(config.execution_host==='workbuddy'&&config.native_model_authorized===true);
  requireValue(path.dirname(filename)===path.dirname(config.handle_path));
  requireValue(config.workbuddy_state_directory===canonicalState(config.workbuddy_session));
  const identity=[config.book_id,config.target.platform,config.target.account_id,config.target.remote_book_id];
  const session='story-wb-'+createHash('sha256').update(JSON.stringify(identity)).digest('hex').slice(0,24);
  requireValue(session===config.workbuddy_session,'platform_chrome_worker_binding_mismatch');
  const digest=createHash('sha256').update(await fs.readFile(config.workbuddy_cli_path)).digest('hex');
  requireValue(digest===config.workbuddy_cli_sha256,'platform_chrome_worker_unsafe_runtime');
  return config;
}

export async function runWorkBuddyWorker(filename,{emit=value=>process.stdout.write(JSON.stringify(value)+'\n')}={}){
  const config=await readWorkBuddyConfig(filename);
  const options={executablePath:config.workbuddy_cli_path,sessionName:config.workbuddy_session,
    stateDirectory:config.workbuddy_state_directory,target:config.target};
  // Creates only local isolation config. It does not open, log in, or recover
  // another session. Those steps precede the waiting queue controller.
  await prepareWorkBuddyBrowserCLI({executablePath:options.executablePath,sessionName:options.sessionName,
    stateDirectory:options.stateDirectory});
  const lockPath=path.join(options.stateDirectory,'worker.lock');
  let lock;
  try{lock=await fs.open(lockPath,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|(constants.O_NOFOLLOW||0),0o600);}
  catch(e){if(e.code==='EEXIST')fail('platform_chrome_worker_busy');throw e;}
  const identity=await lock.stat();
  await lock.writeFile(JSON.stringify({pid:process.pid,config_path:filename,worker:config.worker})+'\n');
  await lock.close();
  const factory=()=>setupWorkBuddyBrowserRuntime(options);
  let completed=false;
  try{
    while(true){
      const checkpoint=await runChromeWorker(filename,{sdkFactory:factory});
      emit(checkpoint);
      if(checkpoint.done){
        try{
          const final=JSON.parse(await fs.readFile(path.join(path.dirname(filename),'worker-final.json'),'utf8'));
          completed=final.done===true&&final.cleanup_complete===true&&final.owned_tabs_remaining===0&&
            final.binding_sha256===config.binding_sha256&&final.worker?.pid===config.worker.pid&&
            final.worker?.instance===config.worker.instance;
        }catch{/* Missing cleanup proof keeps the exclusive session lock. */}
        return checkpoint;
      }
    }
  }finally{
    // A crashed/unfinished runner keeps its lock; another invocation must not
    // silently inherit an unconfirmed operation or delete stale ownership.
    if(completed){
      const current=await fs.lstat(lockPath);
      requireValue(!current.isSymbolicLink()&&current.dev===identity.dev&&current.ino===identity.ino,
        'platform_chrome_worker_unsafe_file');
      await fs.unlink(lockPath);
    }
  }
}

export async function prepareWorkBuddyLogin({executablePath,sessionName,stateDirectory,url}){
  requireValue(stateDirectory===canonicalState(sessionName));
  requireValue(typeof url==='string'&&/^https:\/\/fanqienovel\.com\/main\/writer\/chapter-manage\/[0-9]{1,200}&(?:[A-Za-z0-9_.!~*'()-]|%[0-9A-Fa-f]{2})+\?type=[12]$/.test(url));
  try{await fs.lstat(path.join(stateDirectory,'worker.lock'));fail('platform_chrome_worker_busy');}
  catch(error){if(error.code!=='ENOENT')throw error;}
  const cli=await prepareWorkBuddyBrowserCLI({executablePath,sessionName,stateDirectory});
  // Explicit login preparation in the user's selected native host, with its
  // own temporary profile. Do not attach to or restart personal Chrome.
  return await new Promise((resolve,reject)=>{
    const child=spawn(cli.executablePath,[...cli.baseArgs,'open',url],{
      cwd:cli.stateDirectory,env:cli.env,stdio:['ignore','inherit','inherit'],windowsHide:true});
    child.once('error',reject);
    child.once('close',code=>code===0?resolve({opened:true,session:sessionName,platform_verified:false}):
      reject(Object.assign(new Error('platform_chrome_worker_cli_failed'),{code:'platform_chrome_worker_cli_failed'})));
  });
}

async function main(args){
  if(args.length===2&&args[0]==='--config')return runWorkBuddyWorker(args[1]);
  if(args.length===9&&args[0]==='--prepare-login'){
    const keys=['--cli','--session','--state','--url'];
    requireValue(keys.every((key,i)=>args[1+i*2]===key));
    return prepareWorkBuddyLogin({executablePath:args[2],sessionName:args[4],stateDirectory:args[6],url:args[8]});
  }
  fail('platform_chrome_worker_invalid_config');
}
if(process.argv[1]&&path.resolve(process.argv[1])===path.join(HERE,'story_workbuddy_worker.mjs')){
  main(process.argv.slice(2)).catch(error=>{
    process.stderr.write(JSON.stringify({ok:false,code:typeof error?.code==='string'?error.code:'platform_chrome_worker_failed'})+'\n');
    process.exitCode=1;
  });
}
