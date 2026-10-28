"""Build an independent public-driver probe from the shared offline host fixture."""
from pathlib import Path
import json,subprocess,hashlib
ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).parent
source=(ROOT/'tests/test_chrome_bridge_driver.mjs').read_text()
prefix=source.split('const MANAGEMENT=')[0]
prefix=prefix.replace("from '../skills/story-skill/scripts/story_chrome_bridge.mjs'",'from '+json.dumps((ROOT/'skills/story-skill/scripts/story_chrome_bridge.mjs').as_uri()))
start=source.index('async function ordinaryFixture(');end=source.index("for(const platform of ['qimao','qidian'])test('ordinary ",start)
script=prefix+'\n'+source[start:end]+r'''
const rows=[];
for(const platform of ['qimao','qidian'])for(const stateSource of ['dom','listing'])for(const stage of ['control','same_id_route','#chapter','#title','#body','inside_title_count','inside_body_count','save_label']){
 const {env,base}=await ordinaryFixture(platform,stateSource);const before=JSON.stringify(env.editors.get('11'));
 const rawFill=Locator.prototype.fill,rawCount=Locator.prototype.count,rawAttribute=Locator.prototype.getAttribute;let fired=false,index=-1;
 Locator.prototype.fill=async function(value){
  await rawFill.call(this,value);
  if(!fired&&this.selector===(stage==='same_id_route'?'#chapter':stage)&&this.tab.data===env.editors.get('99')){
   fired=true;index=env.events.length;this.tab.load(ORIGIN+'/book/10/edit/'+(stage==='same_id_route'?'99':'11')+'?enter_from=modifydraft');
  }
 };
 Locator.prototype.count=async function(){
  const result=await rawCount.call(this);const target=stage==='inside_title_count'?'#title':'#body',prior=stage==='inside_title_count'?'#chapter':'#title';
  if(['inside_title_count','inside_body_count'].includes(stage)&&!fired&&this.selector===target&&this.tab.data===env.editors.get('99')&&env.events.some(e=>e[0]==='fill'&&e[1]===prior)){fired=true;index=env.events.length;this.tab.load(ORIGIN+'/book/10/edit/11?enter_from=modifydraft');}
  return result;
 };
 Locator.prototype.getAttribute=async function(key){const result=await rawAttribute.call(this,key);
  if(stage==='save_label'&&!fired&&this.selector==='#save'&&key==='title'&&this.tab.data===env.editors.get('99')){fired=true;index=env.events.length;this.tab.load(ORIGIN+'/book/10/edit/11?enter_from=modifydraft');}
  return result;
 };
 let response;
 try{response=await executeChromeBridgeRequest(env.api,next(base,'create_draft',{chapter:12,title:'新标题',body:'新正文'}));}
 finally{Locator.prototype.fill=rawFill;Locator.prototype.count=rawCount;Locator.prototype.getAttribute=rawAttribute;}
 const unchanged=before===JSON.stringify(env.editors.get('11'));
 const followingWrites=index<0?[]:env.events.slice(index).filter(e=>e[0]==='fill'||e[0]==='pasteText'||(e[0]==='click'&&e[1]==='#save'));
 const ok=['control','same_id_route'].includes(stage)?response.ok&&unchanged&&(stage==='control'||fired):!response.ok&&fired&&unchanged&&followingWrites.length===0;
 rows.push({platform,stateSource,stage,ok,response_ok:response.ok,error:response.error??null,fired,old_draft_unchanged:unchanged,writes_after_switch:followingWrites,creates:env.creates,saves:env.saves});
 await closeChromeBridgeSession(env.api,base.session_id,{keepReadback:false});
}
console.log(JSON.stringify({ok:rows.every(r=>r.ok),cases:rows}));
'''
path=OUT/'chrome-create-generated.mjs';path.write_text(script)
completed=subprocess.run(['/Users/cuining/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node',str(path)],capture_output=True,text=True,timeout=50,check=True)
result=json.loads(completed.stdout)
result['source_sha256']=hashlib.sha256((ROOT/'skills/story-skill/scripts/story_chrome_bridge.mjs').read_bytes()).hexdigest()
result['scope']='Offline host fixture; actual public driver; no real browser, website or account. Both platforms x DOM/listing state x control/same-ID route migration/after chapter/title/body switch and internal locator-count/save-label reads.'
(OUT/'chrome-create-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'ok':result['ok'],'cases':len(result['cases']),'failed':[r for r in result['cases'] if not r['ok']]},ensure_ascii=False))
raise SystemExit(0 if result['ok'] else 1)
