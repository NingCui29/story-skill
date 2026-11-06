/* Public agent-browser 0.41.0 response contracts; synthetic DOM/CLI only.
 * No process, network, browser, personal profile or author files are used. */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readDOMValue,readDocumentScrollPoint,readDOMPath,readRowFields} from '../skills/story-skill/scripts/story_chrome_bridge.mjs';
import {chromeDriverAPI} from '../skills/story-skill/scripts/story_chrome_worker.mjs';
import {createWorkBuddyBrowserFacade,WORKBUDDY_CLI_VERSION} from '../skills/story-skill/scripts/story_workbuddy_browser.mjs';

const SESSION='story-wb-'+'a'.repeat(24),TARGET={platform:'fanqie',account_id:'7',remote_book_id:'99'};
const URL='https://fanqienovel.com/main/writer/chapter-manage/99&book?type=2';
function fixture(){
  const calls=[],state={pid:123,hash:'18446744073709551614',active:'TARGET1',next:2,concurrent:0,maxConcurrent:0,
    mutate:null,deny:null,hook:null,revived:false,dialog:false,delayedPopup:false};
  const pages=new Map([['TARGET1',{url:URL,title:'book',type:'page',fields:new Map()}]]);
  const document={activeElement:null,documentElement:{clientHeight:800,clientWidth:1200},scrollingElement:{clientHeight:800,scrollHeight:1000,scrollTop:0}};
  const registry=new Map(),paths=new Map();
  const root={nodeType:1,tagName:'HTML',parentElement:null,children:[]};
  const body={nodeType:1,tagName:'BODY',parentElement:root,children:[]};root.children=[body];
  document.documentElement=root;root.clientHeight=800;root.clientWidth=1200;
  const add=(key,{tag='DIV',text='',value='',attrs={},children=[]}={})=>{
    const el={nodeType:1,tagName:tag,textContent:text,innerText:text,value,children,childNodes:children,
      parentElement:body,parentNode:body,isConnected:true,ownerDocument:document,disabled:false,readOnly:false,
      getRootNode:()=>document,getAttribute:name=>Object.hasOwn(attrs,name)?attrs[name]:null,
      hasAttribute:name=>Object.hasOwn(attrs,name),getClientRects:()=>[{}],contains:n=>n===el||children.includes(n),
      querySelectorAll:selector=>registry.get(key+' '+selector)||[],getBoundingClientRect:()=>({left:0,top:0,width:200,height:30}),
      clientHeight:30,scrollHeight:30,clientWidth:200,scrollWidth:200,scrollTop:0};
    body.children.push(el);body.childNodes=body.children;
    const css='html:nth-child(1) > body:nth-child(1) > '+tag.toLowerCase()+':nth-child('+body.children.length+')';
    paths.set(css,el);registry.set(key,[el]);return el;
  };
  const chapter=add('#chapter',{tag:'INPUT',value:'',attrs:{type:'number'}});
  const title=add('#title',{tag:'INPUT',value:'',attrs:{type:'text'}});
  const editor=add('.ProseMirror',{attrs:{contenteditable:'true'}});
  const create=add('#create',{tag:'BUTTON',text:'新建草稿'});
  const save=add('#save',{tag:'BUTTON',text:'存草稿'});
  document.activeElement=editor;
  document.querySelectorAll=selector=>{
    if(selector==='textarea:focus, [contenteditable="true"]:focus')return document.activeElement===editor?[editor]:[];
    if(paths.has(selector))return [paths.get(selector)];
    return registry.get(selector)||[];
  };
  const context={document,location:{get href(){return pages.get(state.active)?.url;}},getComputedStyle:()=>({visibility:'visible',display:'block'}),URL};
  const command=async(kind,args)=>{
    state.concurrent++;state.maxConcurrent=Math.max(state.maxConcurrent,state.concurrent);
    const call={kind,args:structuredClone(args)};calls.push(call);
    try{
      await state.hook?.(call);
      if(state.deny?.(call))throw Object.assign(new Error('unknown transport'),{code:'platform_chrome_worker_cli_failed'});
      if(kind==='session')return {success:true,data:{session:SESSION,namespace:'story-workbuddy',active:true,pid:state.pid,version:WORKBUDDY_CLI_VERSION,
        runtime:{backgroundPid:state.pid,session:SESSION,namespace:'story-workbuddy',browserLaunched:true,engine:'chrome',launchHash:state.hash,restoreKey:null,restoreLoadedPath:null}}};
      assert.equal(kind,'batch');assert.equal(args.length,1);
      const a=args[0];let result;
      if(a[0]==='tab'){
        if(a[1]==='list')result={tabs:[...pages].map(([targetId,p],index)=>({...p,targetId,tabId:'t'+(index+1),active:targetId===state.active}))};
        else if(a[1]==='new'){const id='TARGET'+state.next++;pages.set(id,{url:a[2],title:'',type:'page'});state.active=id;result={targetId:id,url:a[2]};}
        else if(a[1]==='close'){pages.delete(a[2]);if(state.active===a[2])state.active=pages.keys().next().value;result={targetId:a[2],closed:true};}
        else{assert.ok(pages.has(a[1]));state.active=a[1];result={targetId:a[1],revived:state.revived,dialogBlocked:state.dialog};}
      }else if(a[0]==='eval'){result={result:vm.runInNewContext(a[1],context),origin:pages.get(state.active).url};}
      else if(a[0]==='get'&&a[1]==='url')result={url:pages.get(state.active).url};
      else if(a[0]==='snapshot')result={snapshot:'- document: complete'};
      else if(a[0]==='open'){pages.get(state.active).url=a[1];result={url:a[1]};}
      else if(a[0]==='reload')result={url:pages.get(state.active).url};
      else if(['click','fill','type'].includes(a[0])){
        const el=paths.get(a[1]);assert.ok(el,'native actions use a resolved CSS path');document.activeElement=el;
        if(a[0]==='fill'){el.value=a[2];el.textContent=a[2];el.innerText=a[2];}
        if(a[0]==='type'){el.value+=a[2];}
        result={[{click:'clicked',fill:'filled',type:'typed'}[a[0]]]:a[0]==='type'?a[2]:a[1]};
        if(el===create&&a[0]==='click'&&!state.delayedPopup){pages.set('POPUP',{url:'https://fanqienovel.com/main/writer/99/publish/123/?enter_from=newdraft',title:'',type:'page'});state.active='POPUP';}
        await state.mutate?.(call);
      }else if(a[0]==='press'){if(a[1]==='Backspace'){document.activeElement.value='';document.activeElement.textContent='';}result={pressed:a[1]};}
      else if(a[0]==='mouse')result={moved:true};
      else throw Error('unimplemented '+a[0]);
      return [{index:0,success:true,result,error:null}];
    }finally{state.concurrent--;}
  };
  const setup=()=>createWorkBuddyBrowserFacade({sessionName:SESSION,target:TARGET},command);
  return {state,calls,pages,document,registry,paths,add,chapter,title,editor,create,save,setup,command};
}
const open=async f=>{const agent=await f.setup(),[entry]=await agent.browsers.list(),browser=await agent.browsers.get(entry.id);return {agent,entry,browser,tab:await browser.tabs.get('TARGET1')};};
const writes=f=>f.calls.filter(c=>c.kind==='batch'&&['click','fill','type','press'].includes(c.args[0][0]));

test('WorkBuddy attaches only the existing named CLI session and accurately identifies Chromium CDP',async()=>{
  const f=fixture(),{agent,entry,browser}=await open(f);
  assert.deepEqual([entry.family,entry.type],['chromium','cdp']);assert.match(entry.id,/story-workbuddy:story-wb-/);
  assert.equal(Object.hasOwn(agent,'model_verified'),false);assert.deepEqual(await browser.user.openTabs(),[]);
  assert.equal(f.calls.some(c=>c.kind==='batch'&&['open','connect','close'].includes(c.args[0][0])),false);
});

test('inactive, restored, mismatched or malformed sessions fail before page commands',async()=>{
  for(const change of [s=>s.data.active=false,s=>s.data.version='0.40.0',s=>s.data.namespace='other',s=>s.data.runtime.restoreKey='personal',s=>s.data.runtime.backgroundPid=999]){
    const f=fixture();let pageCalls=0;
    const command=async(k,a)=>{const r=await f.command(k,a);if(k==='session')change(r);else pageCalls++;return r;};
    await assert.rejects(()=>createWorkBuddyBrowserFacade({sessionName:SESSION},command));assert.equal(pageCalls,0);
  }
});

test('target IDs remain stable across list reordering and an unknown ID never becomes an index',async()=>{
  const f=fixture(),{browser,tab}=await open(f);const other=await browser.tabs.new();
  f.pages.set('UNOWNED',{url:URL,title:'user',type:'page'});
  assert.equal(await tab.url(),URL);await other.goto('https://fanqienovel.com/read');
  assert.equal(await other.url(),'https://fanqienovel.com/read');
  await assert.rejects(()=>browser.tabs.get('0'));assert.equal(f.calls.some(c=>c.kind==='batch'&&c.args[0][0]==='tab'&&c.args[0][1]==='0'),false);
});

test('parallel reads and writes serialize tab selection action and verification as one transaction',async()=>{
  const f=fixture(),{browser,tab}=await open(f),other=await browser.tabs.new();
  f.state.hook=async()=>new Promise(r=>setTimeout(r,1));
  await Promise.all([tab.playwright.locator('#title').fill('甲'),other.playwright.locator('#title').fill('乙'),tab.url()]);
  assert.equal(f.state.maxConcurrent,1);assert.equal(writes(f).length,2);
});

test('one native body fill passes accepted text unchanged with no clipboard typing or write evaluation',async()=>{
  for(const text of ['', '甲\n乙', '  前　\n\n后  ', 'CR\r\nLF']){
    const f=fixture(),{tab}=await open(f);await tab.ax.paste(null,text,{format:'text'});
    assert.equal(f.editor.textContent,text);assert.equal(writes(f).length,1);assert.equal(writes(f)[0].args[0][0],'fill');
    assert.equal(writes(f)[0].args[0][2],text);assert.equal(f.calls.some(c=>c.kind==='batch'&&['clipboard','keyboard'].includes(c.args[0][0])),false);
  }
});

test('WorkBuddy rejects unsupported terminal line breaks before any CLI call without changing text',async()=>{
  for(const text of ['甲\n','甲\r','甲\r\n','甲\n\n','\n','\r\n']){
    const f=fixture(),{tab}=await open(f),before=f.calls.length,original=text;
    await assert.rejects(()=>tab.ax.paste(null,text,{format:'text'}),{code:'platform_chrome_worker_unsupported_sdk'});
    assert.equal(f.calls.length,before);assert.equal(writes(f).length,0);
    assert.equal(f.editor.textContent,'');assert.equal(text,original);
  }
});

test('body paste refuses input focus, ambiguous body and nonplain parameters before dispatch',async()=>{
  for(const args of [[null,'x',{format:'html'}],[1,'x',{format:'text'}],[null,'x',{format:'text'},'extra']]){
    const f=fixture(),{tab}=await open(f);await assert.rejects(()=>tab.ax.paste(...args));assert.equal(writes(f).length,0);
  }
  const f=fixture(),{tab}=await open(f);f.document.activeElement=f.title;
  await assert.rejects(()=>tab.ax.paste(null,'body',{format:'text'}));assert.equal(writes(f).length,0);
});

test('unknown native fill outcome is not replayed and later mutations remain blocked',async()=>{
  const f=fixture(),{browser,tab}=await open(f),owned=await browser.tabs.new();f.state.mutate=()=>{throw Object.assign(new Error('response lost'),{code:'platform_chrome_worker_cli_failed'});};
  await assert.rejects(()=>tab.ax.paste(null,'once',{format:'text'}));
  await assert.rejects(()=>tab.ax.paste(null,'again',{format:'text'}));
  assert.equal(writes(f).length,1);assert.equal(f.editor.textContent,'once');
  assert.ok((await browser.tabs.list()).some(t=>t.id===owned.id));await assert.rejects(()=>owned.close());assert.ok(f.pages.has(owned.id));
});

test('ordinary read failure still permits same-session owned cleanup but never more input',async()=>{
  const f=fixture(),{browser}=await open(f),owned=await browser.tabs.new();
  let failed=false;f.state.deny=c=>{if(!failed&&c.kind==='batch'&&c.args[0][0]==='eval'){failed=true;return true;}return false;};
  await assert.rejects(()=>owned.playwright.locator('#title').evaluate(readDOMValue));
  await assert.rejects(()=>owned.playwright.locator('#title').fill('must not write'));
  await (await browser.tabs.get(owned.id)).close();assert.equal(f.pages.has(owned.id),false);assert.ok(f.pages.has('TARGET1'));assert.equal(writes(f).length,0);
});

test('cleanup refuses a replacement daemon even when target IDs look identical',async()=>{
  const f=fixture(),{browser}=await open(f),owned=await browser.tabs.new();f.state.pid++;
  await assert.rejects(()=>owned.close(),{code:'platform_chrome_worker_browser_changed'});assert.ok(f.pages.has(owned.id));
  assert.equal(f.calls.filter(c=>c.kind==='batch'&&c.args[0][1]==='close').length,0);
});

test('daemon or exact u64 launch identity change is terminal before a write',async()=>{
  for(const key of ['pid','hash']){
    const f=fixture(),{tab}=await open(f);f.state[key]=key==='pid'?124:'18446744073709551615';
    await assert.rejects(()=>tab.playwright.locator('#title').fill('blocked'),{code:'platform_chrome_worker_browser_changed'});
    assert.equal(writes(f).length,0);
  }
});

test('revived or dialog blocked target cannot receive an interaction',async()=>{
  for(const key of ['revived','dialog']){const f=fixture(),{tab}=await open(f);f.state[key]=true;
    await assert.rejects(()=>tab.playwright.locator('#title').fill('blocked'));assert.equal(writes(f).length,0);}
});

test('owned tabs close once and initial user login tab cannot be closed',async()=>{
  const f=fixture(),{browser,tab}=await open(f),owned=await browser.tabs.new();
  await owned.close();await owned.close();assert.equal(f.calls.filter(c=>c.kind==='batch'&&c.args[0][1]==='close').length,1);
  await assert.rejects(()=>tab.close(),{code:'platform_chrome_worker_unsafe_user_tab'});assert.ok(f.pages.has('TARGET1'));
});

test('new-draft popup ownership is bound to observed click and exact book target',async()=>{
  const f=fixture(),{browser,tab}=await open(f);await tab.playwright.locator('#create').click();
  assert.equal(f.state.active,'TARGET1');await (await browser.tabs.get('POPUP')).close();assert.equal(f.pages.has('POPUP'),false);
  const g=fixture(),opened=await open(g);g.pages.set('FOREIGN',{url:'https://fanqienovel.com/main/writer/100/publish/123/?enter_from=newdraft',title:'',type:'page'});
  await assert.rejects(()=>(async()=>{await (await opened.browser.tabs.get('FOREIGN')).close();})(),{code:'platform_chrome_worker_unsafe_user_tab'});
});

test('delayed new-draft popup is owned only after the matching create intent',async()=>{
  const f=fixture(),{browser,tab}=await open(f);f.state.delayedPopup=true;await tab.playwright.locator('#create').click();
  f.pages.set('LATE',{url:'https://fanqienovel.com/main/writer/99/publish/456/?enter_from=newdraft',title:'',type:'page'});
  await browser.tabs.list();await (await browser.tabs.get('LATE')).close();assert.equal(f.pages.has('LATE'),false);
});

test('only fixed read callbacks are accepted and complete values pass without truncation',async()=>{
  const f=fixture(),{tab}=await open(f);const full='正文'.repeat(20000);f.title.value=full;
  assert.equal(await tab.playwright.locator('#title').evaluate(readDOMValue),full);
  const count=f.calls.length;
  assert.throws(()=>tab.playwright.locator('#title').evaluate(el=>{el.value='bad';return el.value;}),{code:'platform_chrome_worker_unsupported_sdk'});
  assert.equal(f.calls.length,count);assert.equal(f.title.value,full);
});

test('production account child has/text-is selector and empty-state text-is are supported exactly',async()=>{
  const f=fixture(),{tab}=await open(f);
  const account=f.add('.user-info-line',{children:[]}),label=f.add('label',{tag:'LABEL',text:'番茄小说网ID'}),number=f.add('account-span',{tag:'SPAN',text:'7'});
  account.children=[label];f.registry.set('.user-info-line :scope > div > span',[number]);
  assert.equal(await tab.playwright.locator('.user-info-line:has(> label:text-is("番茄小说网ID")) > div > span').evaluate(readDOMValue),'7');
  assert.equal(await tab.playwright.locator('.user-info-line:has(> label:text-is("另一账号")) > div > span').count(),0);
  f.add('.empty',{tag:'SPAN',text:'暂无章节内容'});
  assert.equal(await tab.playwright.locator('.empty:text-is("暂无章节内容")').count(),1);
  assert.equal(await tab.playwright.locator('.empty:text-is("暂无草稿")').count(),0);
  const container=f.add('.empty-box',{text:'暂无草稿'}),span=f.add('.empty-span',{tag:'SPAN',text:'暂无草稿'});
  f.registry.set('.arco-tabs-content-item-active *',[container,span]);f.registry.set('.empty-box *',[span]);
  assert.equal(await tab.playwright.locator('.arco-tabs-content-item-active :text-is("暂无草稿")').count(),1);
});

test('exact management role, nested locator and nth resolve by DOM without mutating it',async()=>{
  const f=fixture(),{tab}=await open(f),a=f.add('.roleA',{text:'草稿箱',attrs:{role:'tab'}}),b=f.add('.roleB',{text:'章节管理',attrs:{role:'tab'}});
  f.registry.set('[role="tab"]',[a,b]);
  assert.equal(await tab.playwright.getByRole('tab',{name:'草稿箱',exact:true}).count(),1);
  f.registry.set('.rows',[a,b]);f.registry.set('.roleB .value',[f.title]);
  assert.equal(await tab.playwright.locator('.rows').nth(1).locator('.value').count(),1);
  assert.throws(()=>tab.playwright.getByRole('button',{name:'save',exact:true}));
});

test('real input key sequence remains native and a lost focus cannot press a different field',async()=>{
  const f=fixture(),{tab}=await open(f),title=tab.playwright.locator('#title');
  await title.click();await title.press('Meta+A');await title.press('Backspace');await title.pressSequentially('正确标题');await title.press('Tab');
  assert.equal(f.title.value,'正确标题');assert.equal(writes(f).filter(c=>c.args[0][0]==='type').length,1);
  f.document.activeElement=f.editor;await assert.rejects(()=>title.press('Backspace'));assert.equal(f.title.value,'正确标题');
});

test('shared worker driver facade accepts CLI read/input contracts without changing Codex code',async()=>{
  const f=fixture(),{browser}=await open(f),api=chromeDriverAPI(browser,{target:TARGET});
  f.registry.set('html',[f.document.documentElement]);f.document.documentElement.ownerDocument=f.document;
  const tab=await api.getTab('TARGET1');assert.equal(await tab.getAXState(),'- document: complete');
  const metrics=await tab.playwright.locator('html').evaluate(el=>{const scroller=el.ownerDocument?.scrollingElement||el;return {height:scroller.clientHeight,total:scroller.scrollHeight,top:scroller.scrollTop,width:el.clientWidth};});
  assert.equal(metrics.height,800);assert.equal(metrics.total,1000);assert.equal(metrics.top,0);
  f.document.scrollingElement=f.document.documentElement;f.document.elementFromPoint=()=>f.editor;
  f.title.getBoundingClientRect=()=>({left:300,right:500,top:200,bottom:230});
  const point=await tab.playwright.locator('#title').evaluate(readDocumentScrollPoint);
  assert.equal(point.x,400);assert.equal(point.y,100);
  await tab.scroll([30,40],'down',1);
  assert.ok(f.calls.some(call=>call.kind==='batch'&&call.args[0].join(',')==='mouse,wheel,800,0'));
  await tab.pasteText('一\n二');assert.equal(f.editor.textContent,'一\n二');
  const created=await api.newTab('https://fanqienovel.com/read');await created.close();
  assert.ok(f.pages.has('TARGET1'));assert.equal(writes(f).length,1);
});

test('unsupported external claims screenshot and arbitrary scroll shapes fail closed',async()=>{
  const f=fixture(),{browser,tab}=await open(f);
  await assert.rejects(()=>browser.user.claimTab({id:'x'}));await assert.rejects(()=>tab.screenshot());
  assert.throws(()=>tab.cua.scroll({x:1,y:1,scrollX:4,scrollY:1}));
  assert.equal(writes(f).length,0);
});
