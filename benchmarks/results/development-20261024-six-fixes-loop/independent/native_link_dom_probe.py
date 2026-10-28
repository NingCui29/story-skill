"""Run actual production Native button callbacks in isolated local Chromium."""
from pathlib import Path
import ast, hashlib, html, json, re, subprocess, tempfile
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
SOURCE=ROOT/'skills/story-skill/scripts/story_platform_browser.py'
BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
tree=ast.parse(SOURCE.read_text())
def callback(name):
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
    return ast.literal_eval(next(n.args[0] for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='evaluate'))
js='const initial='+callback('_draft_button_link')+';\nconst final='+callback('_confirm_draft_button')+r''';
const checks=[];
function check(name,actual,expected){checks.push({name,actual,expected,ok:JSON.stringify(actual)===JSON.stringify(expected)});}
const a=document.createElement('a');a.textContent='保存草稿';document.body.append(a);
const base=document.createElement('base');document.head.append(base);
for(const [label,href,self] of [['empty','',true],['fragment','#save',true],['ASCII','\t#save\r\n',true],['ASCII-empty',' \t',true],['NBSP','\u00a0#save',false],['ideographic','\u3000#save',false],['NBSP-path','\u00a0',false],['relative','next',false]]){
 base.removeAttribute('href');a.setAttribute('href',href);
 const before=initial(a),after=final(a);
 check(label+'-initial-document-target',before.documentHref,new URL(href,document.URL).href);
 check(label+'-final-equals-initial',[after.href,after.resolvedHref,after.documentHref,after.url],[before.href,before.resolvedHref,before.documentHref,before.url]);
 check(label+'-actual-self',after.documentHref.split('#')[0]===after.url.split('#')[0],self);
}
a.href='next';base.href='https://example.test/draft/';
const before=initial(a);base.href='https://example.test/publish/';const after=final(a);
check('unchanged-raw',[before.href,after.href],['next','next']);
check('effective-destination-mutation',[before.resolvedHref,after.resolvedHref],['https://example.test/draft/next','https://example.test/publish/next']);
check('base-not-document',after.resolvedHref===after.documentHref,false);
const clicks=[];a.addEventListener('click',e=>{e.preventDefault();clicks.push(a.href);});
if(before.href===after.href&&before.resolvedHref===after.resolvedHref)a.click();
check('changed-link-has-no-click',clicks,[]);
for(const href of [null,'https://example.test/draft/fixed','/draft/fixed']){
 if(href===null)a.removeAttribute('href');else a.setAttribute('href',href);
 base.href='https://example.test/draft/';const first=initial(a);base.href='https://example.test/publish/';const second=final(a);
 check('stable-link-'+String(href),first.resolvedHref,second.resolvedHref);
}
document.getElementById('result').textContent=JSON.stringify({ok:checks.every(c=>c.ok),checks,engine:navigator.userAgent});
'''
with tempfile.TemporaryDirectory(prefix='story-native-link-dom-') as tmp:
    folder=Path(tmp); page=folder/'probe.html'
    page.write_text('<!doctype html><meta charset="utf-8"><pre id="result"></pre><script>'+js+'</script>')
    run=subprocess.run([BROWSER,'--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check','--disable-extensions','--metrics-recording-only','--host-resolver-rules=MAP * 0.0.0.0','--user-data-dir='+str(folder/'profile'),'--dump-dom','--virtual-time-budget=1000',page.as_uri()],capture_output=True,text=True,timeout=40)
    match=re.search(r'<pre id="result">(.*?)</pre>',run.stdout,re.S)
    if run.returncode or not match: raise RuntimeError(run.stderr[-1000:])
    result=json.loads(html.unescape(match.group(1)))
result.update(source_sha256={str(SOURCE.relative_to(ROOT)):hashlib.sha256(SOURCE.read_bytes()).hexdigest()},scope='Production JavaScript callbacks, isolated local Chromium; no website/account, no Native Playwright transport. Combined with full adapter fixture tests, not live end-to-end proof.')
(OUT/'native-link-dom-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'ok':result['ok'],'checks':len(result['checks']),'failed':[c for c in result['checks'] if not c['ok']]},ensure_ascii=False))
raise SystemExit(0 if result['ok'] else 1)
