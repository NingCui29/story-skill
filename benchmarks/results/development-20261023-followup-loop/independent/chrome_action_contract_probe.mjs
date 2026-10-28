/* Pure DOM callback and last-action contract checks, no UI or platform writes. */
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const sourceURL=new URL('../../../../skills/story-skill/scripts/story_chrome_bridge.mjs',import.meta.url);
const raw=readFileSync(sourceURL,'utf8');
const module=await import('data:text/javascript;base64,'+Buffer.from(raw.replace('class Driver {','export class Driver {')).toString('base64'));
const read=Function('return ('+module.readActionState.toString()+')')();
const url='https://write.qq.com/book/10/edit/11';
const checks=[];
function node(tag,doc,parent=null,attrs={}){
  const result={tagName:tag,ownerDocument:doc,parentNode:parent,childNodes:[],innerText:'存草稿',
    getAttribute:key=>attrs[key]??null,contains(other){for(let n=other;n;n=n.parentNode)if(n===this)return true;return false;}};
  parent?.childNodes.push(result);return result;
}
for(const mode of ['root','span','input','textarea','select','button','iframe','false_boundary','nested_true_in_false','outside']){
  const doc={location:{href:url}},root=node('DIV',doc,null,{contenteditable:'true'});
  let focused=root,expected=['root','span'].includes(mode);
  if(mode==='span')focused=node('SPAN',doc,root);
  else if(['input','textarea','select','button','iframe'].includes(mode))focused=node(mode.toUpperCase(),doc,root);
  else if(mode==='false_boundary'||mode==='nested_true_in_false'){
    const boundary=node('SPAN',doc,root,{contenteditable:'false'});
    focused=node('SPAN',doc,boundary,mode==='nested_true_in_false'?{contenteditable:'true'}:{});
  }else if(mode==='outside')focused=node('INPUT',doc);
  doc.activeElement=focused;
  const state=read([root]);checks.push({kind:'serialized_focus',mode,actual:state.editableFocus,expected,ok:state.editableFocus===expected});
}
for(const mode of ['safe','text','aria','title','disabled','href']){
  const doc={location:{href:url}},attrs={href:'/new?source=verified'};
  if(mode==='aria')attrs['aria-label']='提交审核';
  if(mode==='title')attrs.title='发布';
  if(mode==='disabled')attrs['aria-disabled']='true';
  if(mode==='href')attrs.href='/other?source=changed';
  const button=node('A',doc,null,attrs);button.innerText=mode==='text'?'发布章节':'新建草稿';
  const events=[],locator={evaluateAll:async fn=>{events.push('final-state');return fn([button]);},click:async()=>events.push('click')};
  const driver=Object.create(module.Driver.prototype);driver.check=()=>{};driver.timeoutOptions=()=>({timeoutMs:50});
  let error=null;try{await driver.clickDraftButton(locator,{expected_text:'新建草稿'},url,'create','/new?source=verified');}catch(exc){error=exc.code;}
  const clicked=events.includes('click');checks.push({kind:'last_button_check',mode,clicked,error,events,ok:clicked===(mode==='safe')});
}
const result={ok:checks.every(row=>row.ok),checks,source_sha256:createHash('sha256').update(raw).digest('hex'),
  scope:'Exact serialized readActionState callback and production clickDraftButton; local model DOM and recorded click method only.'};
writeFileSync(new URL('chrome-action-contract-result.json',import.meta.url),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({ok:result.ok,checks:checks.length,failed:checks.filter(row=>!row.ok)}));
process.exitCode=result.ok?0:1;
