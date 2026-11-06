/* WorkBuddy's official agent-browser CLI, in an isolated, user-logged-in
 * Chromium session. This is a CLI adapter, not an SDK or model attestation.
 * It never launches a browser on import/factory, attaches personal Chrome,
 * imports authentication, or retries an interaction with an unknown outcome. */
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readDOMValue,readDocumentScrollPoint,readDOMPath,readActionState,readRowFields,readParagraphs,
  readPreviewParagraphs_v1} from './story_chrome_bridge.mjs';

export const WORKBUDDY_CLI_VERSION='0.41.0';
export const WORKBUDDY_CLI_HASHES=Object.freeze({
  'darwin-x64':'f108d4aa02f41eee05d6b1b05817475fd5d576d802826783ef80800da16f33e2',
  'darwin-arm64':'a402fef2489c221b17522aa21f103ca5dc47f00ff5879d4a72c39881544730ab',
  'win32-x64':'dee86b78071cbe7217f71ec1c9df6a50efa73cd44d64a18877bb36556fc8e270'
});
const NAMESPACE='story-workbuddy',MAX_OUTPUT=16*1024*1024;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const fail=(code='unsupported_sdk')=>{const e=new Error('platform_chrome_worker_'+code);e.code=e.message;throw e;};
const requireValue=(v,code)=>{if(!v)fail(code);};
const targetID=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(v)&&!/^\d+$/.test(v);
const sessionID=v=>typeof v==='string'&&/^story-wb-[0-9a-f]{24,64}$/.test(v);
const compact=fn=>Function.prototype.toString.call(fn).replace(/\s+/g,'');
// These are the existing driver's fixed, read-only callbacks. Matching source
// is necessary for its inline callbacks; arbitrary caller scripts fail closed.
const READ_FUNCTIONS=new Set([readDOMValue,readDocumentScrollPoint,readDOMPath,readActionState,readRowFields,readParagraphs,readPreviewParagraphs_v1,
  el=>el.getAttribute('contenteditable'),
  el=>({tag:el.tagName,type:el.getAttribute('type')||'text',readOnly:el.readOnly===true,disabled:el.disabled===true}),
  el=>{const focused=el.ownerDocument?.activeElement;return !!focused&&(focused===el||el.contains(focused));},
  el=>{const r=el.getBoundingClientRect();return {height:el.clientHeight,total:el.scrollHeight,width:el.clientWidth,wide:el.scrollWidth,top:el.scrollTop,x:r.left+r.width/2,y:r.top+r.height/2};},
  el=>({height:el.clientHeight}),
  el=>{const scroller=el.ownerDocument?.scrollingElement||el;return {height:scroller.clientHeight,total:scroller.scrollHeight,top:scroller.scrollTop,width:el.clientWidth};},
  ()=>{const el=document.scrollingElement;return {height:el.clientHeight,total:el.scrollHeight,top:el.scrollTop};},
  ()=>{const el=document.scrollingElement;return {height:el.clientHeight,total:el.scrollHeight,top:el.scrollTop,width:document.documentElement.clientWidth};},
  ()=>({height:document.documentElement.clientHeight})
].map(compact));
const json=value=>JSON.stringify(value===undefined?null:value);
const selector=value=>{requireValue(typeof value==='string'&&value.length>0&&value.length<=8192&&!value.includes('\0'));return value;};

// Only DOM reads: no page globals, properties, attributes or events are written.
function resolveNodes(recipe){
  const exact=(el,text)=>(el.textContent??'').replace(/\s+/g,' ').trim()===text.replace(/\s+/g,' ').trim();
  const query=(root,css)=>{
    // Fixed enhanced selectors used by the production Fanqie profile. Their
    // text is JSON data, never script. Unknown text engines are not guessed.
    const has=/^(.*):has\(>\s*([A-Za-z][\w-]*):text-is\(("(?:[^"\\]|\\.)*")\)\)(.*)$/.exec(css);
    if(has){
      if(!has[1]||!/^\s*(?:>|$)/.test(has[4]))throw Error('unsupported selector');
      const parents=Array.from(root.querySelectorAll(has[1])).filter(el=>Array.from(el.children).some(child=>child.tagName.toLowerCase()===has[2].toLowerCase()&&exact(child,JSON.parse(has[3]))));
      return has[4].trim()?parents.flatMap(el=>Array.from(el.querySelectorAll(':scope'+has[4]))):parents;
    }
    const text=/^(.*):text-is\(("(?:[^"\\]|\\.)*")\)$/.exec(css);
    if(text){
      const descendant=/\s$/.test(text[1]),base=descendant?text[1]+'*':text[1]||'*',expected=JSON.parse(text[2]);
      return Array.from(root.querySelectorAll(base)).filter(el=>exact(el,expected)&&
        (!descendant||!Array.from(el.querySelectorAll('*')).some(child=>exact(child,expected))));
    }
    if(/:text(?:-is)?\(|:has-text\(|:visible\b/.test(css))throw Error('unsupported selector');
    return Array.from(root.querySelectorAll(css));
  };
  let nodes=[document];
  for(const step of recipe){
    if(step.kind==='css')nodes=[...new Set(nodes.flatMap(n=>query(n,step.value)))];
    else if(step.kind==='nth')nodes=nodes[step.index]?[nodes[step.index]]:[];
    else if(step.kind==='role'){
      nodes=Array.from(document.querySelectorAll('[role="tab"]')).filter(el=>{
        // The driver only uses explicit, exact management tabs. Complex
        // accessible-name inference is deliberately unsupported here.
        if(el.hasAttribute('aria-labelledby'))throw Error('unsupported accessible name');
        return (el.getAttribute('aria-label')??el.textContent??'').trim()===step.name;
      });
    }else throw Error('unsupported locator');
  }
  return nodes;
}
function nodePath(nodes){
  if(nodes.length!==1)return {count:nodes.length};
  const el=nodes[0],parts=[];let n=el;
  if(el.getRootNode()!==document||!el.isConnected)return {count:1,connected:false};
  while(n&&n.nodeType===1){
    const parent=n.parentElement;
    const index=parent?Array.prototype.indexOf.call(parent.children,n)+1:1;
    parts.unshift(n.tagName.toLowerCase()+':nth-child('+index+')');n=parent;
  }
  return {count:1,connected:true,selector:parts.join(' > '),url:location.href,
    focused:document.activeElement===el||el.contains(document.activeElement),
    tag:el.tagName,text:(el.innerText??el.textContent??'').trim(),editable:el.getAttribute('contenteditable')==='true'};
}
function nodeProperty(nodes,property){
  if(property==='count')return nodes.length;
  if(nodes.length!==1)throw Error('ambiguous element');
  const el=nodes[0];
  if(property==='text')return el.innerText;
  if(property==='enabled')return !el.disabled&&el.getAttribute('aria-disabled')!=='true';
  if(property==='visible'){const s=getComputedStyle(el);return s.visibility!=='hidden'&&s.display!=='none'&&el.getClientRects().length>0;}
  if(property.startsWith('attr:'))return el.getAttribute(property.slice(5));
  throw Error('unsupported read');
}

/** Public CLI invocation; no shell and no manuscript strings in argv/logs. */
async function runProcess(executable,args,{input='',env,cwd}={}){
  return new Promise((resolve,reject)=>{
    const child=spawn(executable,args,{cwd,env,stdio:['pipe','pipe','pipe'],windowsHide:true});
    let stdout='',stderr='',overflow=false;
    // Do not kill a still-running CLI on a timer: the daemon may already have
    // dispatched its action. The caller awaits settlement and never replays it.
    const collect=which=>chunk=>{
      if(overflow)return;
      if(which==='stdout')stdout+=chunk;else stderr+=chunk;
      if(Buffer.byteLength(stdout)+Buffer.byteLength(stderr)>MAX_OUTPUT){overflow=true;stdout='';stderr='';}
    };
    child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
    child.stdout.on('data',collect('stdout'));child.stderr.on('data',collect('stderr'));
    child.once('error',()=>reject(Object.assign(new Error('platform_chrome_worker_cli_failed'),{code:'platform_chrome_worker_cli_failed'})));
    child.once('close',code=>resolve({code,stdout:overflow?'':stdout,stderr:overflow?'':stderr,overflow}));
    child.stdin.on('error',()=>{});child.stdin.end(input);
  });
}

/** Prepares explicit isolated CLI configuration, without a browser call.
 * The WorkBuddy runner may use baseArgs + ['open', managementURL] once for
 * login, then call setupWorkBuddyBrowserRuntime after the user is ready. */
export async function prepareWorkBuddyBrowserCLI(options){
  requireValue(object(options)&&Object.keys(options).every(k=>['executablePath','sessionName','stateDirectory','target'].includes(k)),'invalid_config');
  const {executablePath,sessionName,stateDirectory}=options;
  requireValue(sessionID(sessionName)&&path.isAbsolute(executablePath||'')&&path.isAbsolute(stateDirectory||'')&&
    path.resolve(executablePath)===executablePath&&path.resolve(stateDirectory)===stateDirectory,'invalid_config');
  const stat=await fs.lstat(executablePath);requireValue(stat.isFile()&&!stat.isSymbolicLink(),'unsafe_file');
  requireValue(await fs.realpath(executablePath)===executablePath,'unsafe_file');
  const expected=WORKBUDDY_CLI_HASHES[process.platform+'-'+process.arch];
  requireValue(expected&&createHash('sha256').update(await fs.readFile(executablePath)).digest('hex')===expected,'native_sdk_unavailable');
  await fs.mkdir(stateDirectory,{recursive:true,mode:0o700});
  const directory=await fs.lstat(stateDirectory);
  requireValue(await fs.realpath(stateDirectory)===stateDirectory&&directory.isDirectory()&&!directory.isSymbolicLink()&&
    (process.platform==='win32'||((directory.mode&0o777)===0o700&&directory.uid===process.getuid())),'unsafe_file');
  const configPath=path.join(stateDirectory,'agent-browser.config.json');
  const content=JSON.stringify({engine:'chrome',headed:true,pinTab:true,noAutoDialog:true,plugins:[]})+'\n';
  try{await fs.writeFile(configPath,content,{flag:'wx',mode:0o600});}
  catch(e){if(e.code!=='EEXIST')throw e;const s=await fs.lstat(configPath);
    requireValue(s.isFile()&&!s.isSymbolicLink()&&(process.platform==='win32'||((s.mode&0o777)===0o600&&s.uid===process.getuid()))&&await fs.readFile(configPath,'utf8')===content,'unsafe_file');}
  // Do not inherit profile, CDP, provider, init-script, restore or plugin env.
  // HOME is preserved, never repurposed; --config bypasses personal defaults.
  const env={};for(const k of ['PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR','TEMP','TMP','SystemRoot','WINDIR','LOCALAPPDATA','APPDATA'])if(process.env[k])env[k]=process.env[k];
  env.AGENT_BROWSER_DEFAULT_TIMEOUT='10000';
  const baseArgs=['--config',configPath,'--namespace',NAMESPACE,'--session',sessionName,'--pin-tab','--json'];
  return Object.freeze({executablePath,sessionName,stateDirectory,configPath,baseArgs:Object.freeze(baseArgs),env:Object.freeze(env)});
}

/** Pure facade constructor, with an injected public-CLI command function for
 * synthetic contract tests. command(kind,args) returns parsed public JSON. */
export async function createWorkBuddyBrowserFacade({sessionName,target=null},command){
  requireValue(sessionID(sessionName)&&typeof command==='function','invalid_config');
  if(target!==null)requireValue(object(target)&&Object.keys(target).length===3&&target.platform==='fanqie'&&
    ['account_id','remote_book_id'].every(k=>typeof target[k]==='string'&&/^[0-9]{1,200}$/.test(target[k])),'invalid_config');
  let tail=Promise.resolve(),identity=null,fatal=null,popupIntent=null,uncertainMutation=false;
  const owned=new Set(),closed=new Set(),closing=new Set(),retained=new Set(),tabs=new Map();
  const enqueue=body=>{const result=tail.then(body);tail=result.catch(()=>{});return result;};
  const checkIdentity=async()=>{
    const packet=await command('session',['session','info']);const s=packet?.data,r=s?.runtime;
    requireValue(packet?.success===true&&s?.session===sessionName&&s.namespace===NAMESPACE&&s.active===true&&
      s.version===WORKBUDDY_CLI_VERSION&&Number.isSafeInteger(s.pid)&&s.pid>0&&r?.backgroundPid===s.pid&&
      r.session===sessionName&&r.namespace===NAMESPACE&&r.browserLaunched===true&&r.engine==='chrome'&&
      typeof r.launchHash==='string'&&/^[0-9]+$/.test(r.launchHash)&&!r.restoreKey&&!r.restoreLoadedPath,'unavailable');
    const current=JSON.stringify([s.pid,r.launchHash]);
    if(identity===null)identity=current;else requireValue(identity===current,'browser_changed');
  };
  const call=async args=>{
    try{
      const rows=await command('batch',[args]);
      requireValue(Array.isArray(rows)&&rows.length===1&&rows[0]?.index===0,'invalid_packet');
      if(rows[0].success!==true)fail(rows[0].code==='tab_gone'?'target_tab_closed':'cli_failed');
      requireValue(object(rows[0].result),'invalid_packet');return rows[0].result;
    }catch(e){
      if(['click','fill','type','press','open','reload'].includes(args[0])||args[0]==='tab'&&['new','close'].includes(args[1]))uncertainMutation=true;
      throw e;
    }
  };
  const list=async()=>{
    const p=await call(['tab','list']);requireValue(Array.isArray(p.tabs),'invalid_packet');
    const ids=new Set();const result=p.tabs.map(t=>{
      requireValue(object(t)&&targetID(t.targetId)&&!ids.has(t.targetId)&&typeof t.url==='string'&&typeof t.title==='string'&&typeof t.active==='boolean'&&t.type==='page','ambiguous_tab');
      ids.add(t.targetId);return {id:t.targetId,providerTabId:t.targetId,url:t.url,title:t.title,active:t.active};
    });
    if(popupIntent&&Date.now()<=popupIntent.expires&&target){
      const found=result.filter(t=>!popupIntent.before.has(t.id)&&new RegExp('^https://fanqienovel\\.com/main/writer/'+target.remote_book_id+'/publish/[0-9]{1,200}/?\\?enter_from=newdraft$').test(t.url));
      requireValue(found.length<=1,'ambiguous_tab');if(found.length){owned.add(found[0].id);popupIntent=null;}
    }
    return result;
  };
  const guarded=(body,{cleanup=false}={})=>enqueue(async()=>{
    if(fatal&&!cleanup)throw fatal;
    try{await checkIdentity();const value=await body();await checkIdentity();return value;}
    catch(e){fatal=e;throw e;}
  });
  const select=async id=>{
    requireValue(targetID(id)&&!closed.has(id),'target_tab_closed');
    const before=await list();requireValue(before.some(t=>t.id===id),'target_tab_closed');
    const response=await call(['tab',id]);requireValue(response.targetId===id&&!response.revived&&!response.dialogBlocked,'target_tab_changed');
    const current=await list();requireValue(current.filter(t=>t.active).length===1&&current.find(t=>t.active).id===id,'target_tab_changed');
  };
  const pinned=(id,body)=>guarded(async()=>{
    await select(id);const value=await body();const after=await list();
    requireValue(after.filter(t=>t.active).length===1&&after.find(t=>t.active).id===id,'target_tab_changed');return value;
  });
  const evalRead=async source=>{const p=await call(['eval',source]);requireValue(Object.hasOwn(p,'result'),'invalid_packet');return p.result;};
  const nodes=recipe=>'('+resolveNodes.toString()+')('+json(recipe)+')';
  const query=(id,recipe,fn,arg,all=false)=>{
    requireValue(typeof fn==='function'&&READ_FUNCTIONS.has(compact(fn)),'unsupported_sdk');
    return pinned(id,()=>evalRead('(()=>{const n='+nodes(recipe)+';'+(all?'':'if(n.length!==1)throw Error("ambiguous element");')+
      'return ('+Function.prototype.toString.call(fn)+')('+(all?'n':'n[0]')+','+json(arg)+');})()'));
  };
  const property=(id,recipe,name)=>pinned(id,()=>evalRead('('+nodeProperty.toString()+')('+nodes(recipe)+','+json(name)+')'));
  const optionsCheck=options=>{if(options!==undefined)requireValue(object(options)&&Object.keys(options).every(k=>k==='timeoutMs')&&
    Number.isSafeInteger(options.timeoutMs)&&options.timeoutMs>0,'invalid_config');};
  const act=(id,recipe,verb,value,options)=>{
    optionsCheck(options);
    return pinned(id,async()=>{
      const p=await evalRead('('+nodePath.toString()+')('+nodes(recipe)+')');
      requireValue(p?.count===1&&p.connected===true&&typeof p.selector==='string'&&typeof p.url==='string','ambiguous_tab');
      let result;
      if(verb==='press'){
        requireValue(p.focused===true,'target_tab_changed');
        requireValue(['Meta+A','Control+A','Backspace','Tab','Enter'].includes(value),'invalid_config');
        result=await call(['press',value.replace(/\+A$/,'+a')]);
      }else{
        requireValue(['click','fill','type'].includes(verb),'unsupported_sdk');
        if(verb!=='click')requireValue(typeof value==='string'&&!value.includes('\0'),'invalid_config');
        const before=verb==='click'?await list():null;
        const createClick=before&&target&&/(?:新建|创建|新增).*草稿|\b(?:new|create)\b.*\bdraft\b/i.test(p.text)&&
          !/发布|送审|审核|提交|删除|publish|submit|delete/i.test(p.text);
        if(createClick)popupIntent={before:new Set(before.map(t=>t.id)),expires:Date.now()+15000};
        result=await call([verb,p.selector,...(verb==='click'?[]:[value])]);
        if(before){
          const after=await list();
          const popup=after.filter(t=>createClick&&!before.some(b=>b.id===t.id)&&target&&
            new RegExp('^https://fanqienovel\\.com/main/writer/'+target.remote_book_id+'/publish/[0-9]{1,200}/?\\?enter_from=newdraft$').test(t.url));
          requireValue(popup.length<=1,'ambiguous_tab');
          for(const t of popup)owned.add(t.id);
          if(after.find(t=>t.active)?.id!==id){requireValue(popup.some(t=>t.active),'target_tab_changed');await select(id);}
        }
      }
      return result;
    });
  };
  const locator=(id,recipe)=>Object.freeze({
    locator:s=>locator(id,[...recipe,{kind:'css',value:selector(s)}]),
    nth:n=>{requireValue(Number.isSafeInteger(n)&&n>=0,'invalid_config');return locator(id,[...recipe,{kind:'nth',index:n}]);},
    count:()=>property(id,recipe,'count'),isVisible:()=>property(id,recipe,'visible'),isEnabled:()=>property(id,recipe,'enabled'),
    innerText:()=>property(id,recipe,'text'),getAttribute:name=>property(id,recipe,'attr:'+selector(name)),
    evaluate:(fn,arg)=>query(id,recipe,fn,arg),evaluateAll:(fn,arg)=>query(id,recipe,fn,arg,true),
    click:o=>act(id,recipe,'click',null,o),fill:(text,o)=>act(id,recipe,'fill',text,o),
    press:(key,o)=>act(id,recipe,'press',key,o),pressSequentially:(text,o)=>act(id,recipe,'type',text,o)
  });
  const getTab=id=>{
    requireValue(targetID(id),'invalid_config');if(tabs.has(id))return tabs.get(id);
    const tab={id,
      url:()=>pinned(id,async()=>{const p=await call(['get','url']);requireValue(typeof p.url==='string','invalid_packet');return p.url;}),
      goto:url=>{
        let destination;try{destination=new URL(url);}catch{fail('invalid_config');}
        requireValue(typeof url==='string'&&!destination.username&&!destination.password&&
          (url==='about:blank'||destination.protocol==='https:'||destination.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(destination.hostname)),'invalid_config');
        return pinned(id,()=>call(['open',url]));
      },
      reload:()=>pinned(id,()=>call(['reload'])),
      close:()=>guarded(async()=>{
        requireValue(!uncertainMutation,'unavailable');
        requireValue(owned.has(id),'unsafe_user_tab');if(closed.has(id))return;
        requireValue(!closing.has(id),'target_tab_changed');closing.add(id);
        const before=await list();if(!before.some(t=>t.id===id)){closed.add(id);return;}
        const result=await call(['tab','close',id]);requireValue(result.targetId===id&&result.closed===true,'target_tab_changed');
        requireValue(!(await list()).some(t=>t.id===id),'target_tab_changed');closed.add(id);
      },{cleanup:true}),
      markDeliverable:async()=>{requireValue(owned.has(id),'unsafe_user_tab');retained.add(id);},
      // Screenshot/clipboard exports are deliberately unsupported in this
      // first CLI adapter. No file path returned by the browser is trusted.
      screenshot:async()=>fail('unsupported_sdk'),
      playwright:{
        domSnapshot:()=>pinned(id,async()=>{const p=await call(['snapshot']);requireValue(typeof p.snapshot==='string','invalid_packet');return p.snapshot;}),
        evaluate:(fn,arg)=>{requireValue(typeof fn==='function'&&READ_FUNCTIONS.has(compact(fn)),'unsupported_sdk');return pinned(id,()=>evalRead('('+fn.toString()+')('+json(arg)+')'));},
        locator:s=>locator(id,[{kind:'css',value:selector(s)}]),
        getByRole:(role,o)=>{requireValue(role==='tab'&&object(o)&&Object.keys(o).length===2&&o.exact===true&&typeof o.name==='string','unsupported_sdk');return locator(id,[{kind:'role',name:o.name}]);}
      },
      ax:{paste:async function(target,text,o){
        requireValue(arguments.length===3&&target===null&&typeof text==='string'&&object(o)&&Object.keys(o).length===1&&o.format==='text','invalid_config');
        // Public publishing uses plain_paragraphs_v1, which has no terminal
        // line break. CLI fill can add an extra terminal LF in contenteditable;
        // refuse that unsupported input before any CLI call, never trim it.
        requireValue(!/[\r\n]$/.test(text),'unsupported_sdk');
        // The driver proved an empty focused body. Resolve focus again inside
        // the one native fill. No clipboard, per-line typing or write-eval.
        return act(id,[{kind:'css',value:'textarea:focus, [contenteditable="true"]:focus'}],'fill',text);
      }},
      cua:{scroll:o=>{
        requireValue(object(o)&&Object.keys(o).every(k=>['x','y','scrollX','scrollY'].includes(k))&&
          [o.x,o.y,o.scrollX,o.scrollY].every(Number.isFinite)&&o.scrollX===0&&Number.isSafeInteger(o.scrollY)&&o.scrollY!==0,'invalid_scroll');
        return pinned(id,async()=>{await call(['mouse','move',String(o.x),String(o.y)]);return call(['mouse','wheel',String(o.scrollY),'0']);});
      }}
    };
    tabs.set(id,Object.freeze(tab));return tab;
  };
  await guarded(async()=>list());
  const browserId=NAMESPACE+':'+sessionName;
  const descriptor=Object.freeze({id:browserId,family:'chromium',type:'cdp',name:'WorkBuddy isolated Chromium'});
  const browser={browserId,
    documentation:async()=>`agent-browser ${WORKBUDDY_CLI_VERSION}: isolated CLI session; no external user tabs, arbitrary evaluation, clipboard or screenshot export.`,
    nameSession:async name=>{requireValue(typeof name==='string','invalid_config');},
    tabs:{list:()=>guarded(list,{cleanup:true}),get:async id=>{await guarded(async()=>requireValue((await list()).some(t=>t.id===id),'target_tab_closed'),{cleanup:true});return getTab(id);},
      new:()=>guarded(async()=>{
        const before=await list(),p=await call(['tab','new','about:blank']);
        requireValue(targetID(p.targetId)&&!before.some(t=>t.id===p.targetId),'ambiguous_tab');owned.add(p.targetId);
        const after=await list();requireValue(after.filter(t=>t.id===p.targetId&&t.url==='about:blank').length===1,'ambiguous_tab');return getTab(p.targetId);
      })},
    user:{openTabs:async()=>[],claimTab:async()=>fail('unsafe_user_tab')}
  };
  return Object.freeze({browsers:{list:async()=>{await guarded(async()=>{});return [descriptor];},get:async id=>{requireValue(id===browserId,'browser_changed');return browser;}},
    // Local diagnostic facts only; never model_verified/browser health proof.
    cliSession:Object.freeze({sessionName,namespace:NAMESPACE,version:WORKBUDDY_CLI_VERSION})});
}

export async function setupWorkBuddyBrowserRuntime(options){
  const prepared=await prepareWorkBuddyBrowserCLI(options);
  const invoke=(args,input='')=>runProcess(prepared.executablePath,[...prepared.baseArgs,...args],{input,env:prepared.env,cwd:prepared.stateDirectory});
  const version=await runProcess(prepared.executablePath,['--version'],{env:prepared.env,cwd:prepared.stateDirectory});
  requireValue(version.code===0&&version.stdout.trim()==='agent-browser '+WORKBUDDY_CLI_VERSION,'native_sdk_unavailable');
  const command=async(kind,args)=>{
    const r=await invoke(kind==='session'?args:['batch','--bail','--omit-command'],kind==='batch'?json(args):'');
    requireValue(!r.overflow&&r.code===0,'cli_failed');let packet;
    // session_info serializes launchHash as u64. Preserve all digits before
    // JS parsing instead of silently rounding the runtime identity.
    try{packet=JSON.parse(kind==='session'?r.stdout.replace(/("launchHash"\s*:\s*)([0-9]+)/g,'$1"$2"'):r.stdout);}catch{fail('invalid_packet');}return packet;
  };
  return createWorkBuddyBrowserFacade({sessionName:prepared.sessionName,target:options.target??null},command);
}
