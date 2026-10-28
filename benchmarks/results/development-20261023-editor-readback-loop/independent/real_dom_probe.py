"""Execute production DOM readers against a local HTML document in an isolated Chrome profile."""
from pathlib import Path
import ast, hashlib, html, json, re, subprocess, tempfile
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
NATIVE=ROOT/'skills/story-skill/scripts/story_platform_browser.py'
CHROME=ROOT/'skills/story-skill/scripts/story_chrome_bridge.mjs'
NODE='/Users/cuining/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
tree=ast.parse(NATIVE.read_text())
paragraphs=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_EDITOR_PARAGRAPHS_JS' for t in n.targets))
method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_current_editor_snapshot')
arg=next(n.args[0] for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='evaluate')
snapshot=eval(compile(ast.Expression(arg),'snapshot-source','eval'),{'_EDITOR_PARAGRAPHS_JS':paragraphs})
identity_arg=next(n.args[0] for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='evaluate_all')
identity_js=ast.literal_eval(identity_arg)
paragraph_method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_paragraph_fill')
focus_js=ast.literal_eval(next(n.args[0] for n in ast.walk(paragraph_method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='evaluate_all'))
script=f'import {{readParagraphs}} from {json.dumps(CHROME.as_uri())}; process.stdout.write(JSON.stringify(readParagraphs.toString()));'
chrome_reader=json.loads(subprocess.run([NODE,'--input-type=module','-e',script],check=True,capture_output=True,text=True).stdout)
js=r'''
const native = NATIVE;
const bridge = CHROME;
const identityCheck = IDENTITY;
const focusedCheck = FOCUS;
const checks=[];
function check(name,actual,expected){const ok=JSON.stringify(actual)===JSON.stringify(expected);checks.push({name,ok,actual,expected});}
function elem(tag,txt){const e=document.createElement(tag);if(txt!=null)e.textContent=txt;document.body.append(e);return e;}
function packet(el,spec={source:'dom'}){return native([['field',spec,Array.isArray(el)?el:[el]]]).fields.field;}
function bridgeValue(el,spec={source:'dom'}){return bridge(Array.isArray(el)?el:[el],spec);}
const input=elem('input');input.setAttribute('value','old attribute');input.value=' current value ';
const textarea=elem('textarea');textarea.value='首行\n\n尾行\n';
const select=elem('select');select.innerHTML='<option value="raw-value">Display text</option>';
const plain=elem('div',' ordinary text ');plain.dataset.identity='account-1';
const editable=elem('div');editable.contentEditable='true';editable.innerHTML='<div>甲</div><div>乙</div>';
for(const [name,el,value] of [['input',input,' current value '],['textarea',textarea,'首行\n\n尾行\n'],['select',select,'raw-value'],['plain',plain,' ordinary text '],['editable',editable,'甲\n乙']]){
 check('native-'+name,packet(el).value,value);check('bridge-'+name,bridgeValue(el).value,value);
}
check('native-attribute',packet(plain,{attribute:'data-identity'}).value,'account-1');
check('bridge-attribute',bridgeValue(plain,{attribute:'data-identity'}).value,'account-1');
check('native-empty-match',packet([]).count,0);check('bridge-empty-match',bridgeValue([]).count,0);
check('native-duplicate-match',packet([plain,input]).count,2);check('bridge-duplicate-match',bridgeValue([plain,input]).count,2);
const detached=elem('div','detached');detached.remove();check('native-detached',packet(detached).connected,false);
const pm=elem('div');pm.contentEditable='true';pm.innerHTML='<p> 首段 </p><p><br class="ProseMirror-trailingBreak"></p><p>末段</p><p><br class="ProseMirror-trailingBreak"></p>';
check('native-prosemirror',packet(pm,{source:'prosemirror_paragraphs'}).value,' 首段 \n\n末段');
check('bridge-prosemirror',bridgeValue(pm,{source:'prosemirror_paragraphs'}).value,' 首段 \n\n末段');
pm.querySelector('p').innerHTML='<strong>未支持的格式</strong>';
check('native-prosemirror-reject-richtext',packet(pm,{source:'prosemirror_paragraphs'}).ok,false);
check('bridge-prosemirror-reject-richtext',bridgeValue(pm,{source:'prosemirror_paragraphs'}).ok,false);
const held=elem('div','held');held.className='matched-current';
check('native-identity-unchanged',identityCheck(Array.from(document.querySelectorAll('.matched-current')),held),{count:1,same:true});
const duplicate=elem('div','duplicate');duplicate.className='matched-current';
check('native-identity-duplicate',identityCheck(Array.from(document.querySelectorAll('.matched-current')),held),{count:2,same:false});
held.className='still-attached-elsewhere';
check('native-identity-retarget-connected',held.isConnected,true);
check('native-identity-retarget-rejected',identityCheck(Array.from(document.querySelectorAll('.matched-current')),held),{count:1,same:false});
editable.innerHTML='<span tabindex="0">焦点子节点</span>';editable.focus();
check('native-focus-body',focusedCheck([editable]),true);
editable.firstChild.focus();check('native-focus-descendant',focusedCheck([editable]),true);
input.focus();check('native-focus-outside',focusedCheck([editable]),false);
check('native-focus-no-match',focusedCheck([]),false);
check('native-focus-duplicate-match',focusedCheck([editable,input]),false);
const result={ok:checks.every(c=>c.ok),checks,location:location.protocol,domEngine:navigator.userAgent};
document.getElementById('result').textContent=JSON.stringify(result);
'''.replace('NATIVE',snapshot).replace('CHROME',chrome_reader).replace('IDENTITY',identity_js).replace('FOCUS',focus_js)
with tempfile.TemporaryDirectory(prefix='story-loop-real-dom-') as tmp:
    folder=Path(tmp).resolve();document=folder/'probe.html'
    document.write_text('<!doctype html><meta charset="utf-8"><pre id="result"></pre><script>'+js+'</script>',encoding='utf-8')
    command=[BROWSER,'--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check','--disable-extensions','--metrics-recording-only','--host-resolver-rules=MAP * 0.0.0.0','--user-data-dir='+str(folder/'profile'),'--dump-dom','--virtual-time-budget=1000',document.as_uri()]
    completed=subprocess.run(command,capture_output=True,text=True,timeout=30)
    match=re.search(r'<pre id="result">(.*?)</pre>',completed.stdout,re.S)
    if completed.returncode or not match:raise RuntimeError('Chrome local probe did not produce result: '+completed.stderr[-1000:])
    result=json.loads(html.unescape(match.group(1)))
result.update(source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (NATIVE,CHROME)},scope='Production DOM functions on local HTML only. Isolated ephemeral profile; no website, account, CUA locator transport or native Playwright integration exercised.')
(OUT/'real-dom-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'ok':result['ok'],'checks':len(result['checks']),'failed':[c for c in result['checks'] if not c['ok']]},ensure_ascii=False))
raise SystemExit(0 if result['ok'] else 1)
