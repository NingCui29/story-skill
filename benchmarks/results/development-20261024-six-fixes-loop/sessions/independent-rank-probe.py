"""Independent namespace-transition cases against isolated Chromium DOM."""
from pathlib import Path
import hashlib,html,json,re,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tests'))
import test_rank_sources as f
cases=[]
def add(label,prefix='',suffix='',hidden=False):
    cards=prefix+(f.card('900') if hidden else '')+suffix+f.card('100')
    cases.append({'label':label,'html':f.page(cards=cards)})
add('visible baseline')
add('ordinary hidden template','<template>','</template>',True)
add('ordinary hidden attribute','<div hidden="false">','</div>',True)
add('template outer end tags','<template></html></body></ul>','</template>',True)
add('svg selfclosed hidden node','<svg><g hidden/><circle/></svg>')
add('math selfclosed hidden node','<math><mspace hidden/><mi>x</mi></math>')
add('svg nested integration','<svg><foreignObject><svg><g hidden/></svg></foreignObject></svg>')
add('math integration html','<math><mtext><span hidden>ignore</span></mtext></math>')
for container in ('svg','math'):
    for breakout in ('p','div','font color="red"'):
        end=breakout.split()[0]
        add(container+' '+end+' breakout then template',f'<{container}><{breakout}>outside</{end}><template/>',f'</template></{container}>',True)
        add(container+' '+end+' breakout then hidden custom',f'<{container}><{breakout}>outside</{end}><g hidden/>',f'</g></{container}>',True)
for container in ('svg','math'):
    for closing in ('p','br'):
        add(container+' end '+closing+' breakout then template',f'<{container}><g></{closing}><template/>',f'</template></{container}>',True)
# Names that are integration points only in the matching namespace.
add('SVG mi is foreign','<svg><mi><g hidden/></mi></svg>')
add('SVG annotation-xml is foreign','<svg><annotation-xml encoding="text/html"><g hidden/></annotation-xml></svg>')
add('MathML foreignObject is foreign','<math><foreignObject><mspace hidden/></foreignObject></math>')
payload=json.dumps(cases,ensure_ascii=False).replace('</','<\\/')
script='''const cases=PAYLOAD; const results=cases.map(c=>{ const d=new DOMParser().parseFromString(c.html,'text/html');return {label:c.label,ids:Array.from(d.querySelectorAll('li.qm-cover-text-item')).filter(n=>!n.closest('[hidden]')).map(n=>n.querySelector('.s-tit a').getAttribute('href').match(/shuku\\/(\\d+)/)[1])}; }); const p=document.createElement('pre');p.id='result';p.textContent=JSON.stringify(results);document.body.append(p);'''.replace('PAYLOAD',payload)
with tempfile.TemporaryDirectory() as td:
    root=Path(td);path=root/'oracle.html';path.write_text('<!doctype html><meta charset=utf-8><body><script>'+script+'</script>')
    result=subprocess.run(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check','--disable-extensions','--host-resolver-rules=MAP * 0.0.0.0','--user-data-dir='+str(root/'profile'),'--dump-dom','--virtual-time-budget=1000',path.as_uri()],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr[-2000:]
    m=re.search(r'<pre id="result">(.*?)</pre>',result.stdout,re.S);assert m
    actual=json.loads(html.unescape(m[1]))
rows=[]
for case,dom in zip(cases,actual,strict=True):
    try:
        ids=[x['book_id'] for x in f.sources.parse_qimao_page(case['html'],1)]
        error=None
    except ValueError as exc:
        ids=None;error=str(exc)
    permitted_rejection='breakout' in case['label'] and error is not None and '重新导出' in error
    rows.append({'label':case['label'],'chromium_ids':dom['ids'],'parsed_ids':ids,'error':error,'same':ids==dom['ids'],'passed':ids==dom['ids'] or permitted_rejection,'unsafe_extra_ids':sorted(set(ids or [])-set(dom['ids']))})
summary={'ok':all(r['passed'] for r in rows),'passed':sum(r['passed'] for r in rows),'source_sha256':hashlib.sha256((ROOT/'skills/story-skill/scripts/story_rank_sources.py').read_bytes()).hexdigest(),'checks':len(rows),'matches':sum(r['same'] for r in rows),'rejected':sum(r['error'] is not None for r in rows),'accepted_hidden_cases':sum(bool(r['unsafe_extra_ids']) for r in rows),'rows':rows,'scope':'Local synthetic HTML, isolated Chromium DOMParser; no live platform or CSS execution.'}
(OUT/'independent-rank-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
