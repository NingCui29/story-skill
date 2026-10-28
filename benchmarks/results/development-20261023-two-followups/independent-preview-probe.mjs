/* Independent await-boundary injections using only the offline fixture helpers. */
import {readFileSync} from 'node:fs';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const runtime=root+'skills/story-skill/scripts/story_chrome_bridge.mjs';
let fixture=readFileSync(root+'tests/test_chrome_bridge_driver.mjs','utf8');
fixture=fixture.slice(0,fixture.indexOf('\ntest('));
fixture=fixture.replace("'../skills/story-skill/scripts/story_chrome_bridge.mjs'",JSON.stringify(pathToFileURL(runtime).href));
const probes=String.raw`
const reports=[];
{
  const env=environment({previewHeaderPending:3,previewBodyPending:3}),base=await verified(env);
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(response.ok,true,JSON.stringify(response));
  assert.equal(response.result.body,'原始正文');
  reports.push({case:'initial header and body pending',ok:true});
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
}
{
  const env=environment(),base=await verified(env);let reads=0,changed=false;
  env.onPreviewBody=()=>{reads++;};
  env.afterLocatorEvaluate=async(tab,selector)=>{
    if(selector==='#account'&&reads===2&&!changed){
      const preview=[...env.tabs.values()].find(t=>t.preview);
      preview.current=ORIGIN+'/preview/10&22';changed=true;
    }
  };
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(changed,true);assert.equal(response.error,'platform_browser_identity_mismatch');
  assert.ok(!Object.hasOwn(response,'result'));
  reports.push({case:'remote switch during final account guard',reads,error:response.error});
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
}
for(const restoreAt of ['guard','first_ax','inventory']){
  const env=environment(),base=await verified(env);let redirected=false,returned=false,reads=0,previewId;
  env.editors.set('22',{scope:'drafts',chapter:22,title:'另一稿',body:'另一稿正文',saved:true});
  const restore=()=>{
    if(!redirected||returned)return;
    env.tabs.get(previewId).load(ORIGIN+'/preview/10&11');returned=true;
  };
  const newTab=env.api.newTab;
  env.api.newTab=async url=>{
    const tab=await newTab(url);
    if(url===ORIGIN+'/preview/10&11'){
      tab.load(ORIGIN+'/preview/10&22');redirected=true;previewId=tab.id;
    }
    return tab;
  };
  if(restoreAt==='guard')env.afterLocatorEvaluate=async(tab,selector)=>{if(selector==='#account')restore();};
  if(restoreAt==='first_ax')env.beforeAX=async tab=>{if(tab.id===previewId)restore();};
  if(restoreAt==='inventory'){
    const list=env.api.listTabs;env.api.listTabs=async()=>{restore();return list();};
  }
  env.onPreviewBody=()=>{reads++;};
  const response=await executeChromeBridgeRequest(env.api,next(base,'read_draft',{remote_id:'11'}));
  assert.equal(redirected,true);assert.equal(response.error,'platform_browser_identity_mismatch',JSON.stringify(response));
  assert.ok(!Object.hasOwn(response,'result'));assert.equal(reads,0);
  await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
  assert.ok(!env.tabs.has(previewId),'Rejected new tab remains owned for cleanup');
  assert.ok(env.tabs.has('1'),'Existing user tab remains untouched');
  reports.push({case:'newTab wrong remote then restore at '+restoreAt,redirected,returned,reads,
                error:response.error,owned_tab_cleaned:true,user_tab_retained:true});
}
globalThis.independentPreviewResults=reports;
`;
await import('data:text/javascript;base64,'+Buffer.from(fixture+probes).toString('base64'));
console.log(JSON.stringify({passed:true,cases:globalThis.independentPreviewResults,
  source_sha256:createHash('sha256').update(readFileSync(runtime)).digest('hex')},null,2));
