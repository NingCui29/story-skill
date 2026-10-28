/* Direct production method probe, isolated model DOM and public locator shape. */
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const sourceURL=new URL('../../../../skills/story-skill/scripts/story_chrome_bridge.mjs',import.meta.url);
const raw=readFileSync(sourceURL,'utf8');
const {Driver,readDOMPath}=await import('data:text/javascript;base64,'+Buffer.from(raw.replace('class Driver {','export class Driver {')).toString('base64'));
const results=[];
for(const mode of ['unchanged','duplicate','retarget']){
  const root={tagName:'HTML',childNodes:[],isConnected:true};
  const doc={childNodes:[root],documentElement:root,location:{href:'https://write.qq.com/book/10/edit/11'}};
  root.ownerDocument=doc;root.parentNode=doc;
  const make=(name,value)=>{const node={tagName:'INPUT',childNodes:[],value,isConnected:true,ownerDocument:doc,parentNode:root,getAttribute:()=>null};root.childNodes.push(node);return node;};
  const expected={chapter:'11',title:'原稿',body:'原始正文',state:'草稿'},specs={},matches={};
  for(const [name,value] of Object.entries(expected)){specs[name]={selector:'#'+name};matches['#'+name]=[make(name,value)];}
  let changed=false;
  const tab={playwright:{locator(selector){return {async evaluateAll(fn,arg){
    const current=selector===':root'?[root]:matches[selector]||[];
    const result=fn(current,arg);
    if(fn===readDOMPath&&selector==='#title'&&!changed&&mode!=='unchanged'){
      changed=true;const replacement=make('chapter','22');
      matches['#chapter']=mode==='duplicate'?[...matches['#chapter'],replacement]:[replacement];
    }
    return result;
  }};}}};
  const driver=Object.create(Driver.prototype);driver.profile={editor:specs};
  driver.check=()=>{};driver.readQuery=async(_tab,operation)=>operation();
  let accepted=false,error=null;
  try{await driver.editorSnapshot(tab,doc.location.href,expected);accepted=true;}catch(exc){error=exc.code||String(exc);}
  results.push({mode,changed,accepted,error,expected_acceptance:mode==='unchanged',
    current_chapter_values:matches['#chapter'].map(node=>node.value),
    old_chapter_still_connected:root.childNodes[0].isConnected});
}
const result={ok:results.every(row=>row.accepted===row.expected_acceptance),results,
  source_sha256:createHash('sha256').update(raw).digest('hex'),
  scope:'Exact production editorSnapshot method; isolated synthetic DOM and public locator contract; no real browser/network/write action.'};
writeFileSync(new URL('chrome-snapshot-selector-result.json',import.meta.url),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
process.exitCode=result.ok?0:1;
