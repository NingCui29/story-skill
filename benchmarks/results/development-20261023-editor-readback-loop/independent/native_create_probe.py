"""Independent native creation identity probes, including operations after locator reads."""
from pathlib import Path
from unittest.mock import patch
import copy,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[4];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import test_platform_browser_editor_readback as f
rows=[]
for platform in ('qimao','qidian'):
 for listing in (False,True):
  for autosave in (False,True):
   for stage in ('control','after_chapter','after_title','after_body','inside_title_kind','inside_body_kind','save_label'):
    case=f.EditorReadbackTests();case.setUp();a=case.opened(platform,listing=listing,sync_drafts=listing,autosave=autosave)
    before=copy.deepcopy(a._context.editors['900']);fired=[]
    old_fill=f.fixture.Locator.fill;old_eval=f.fixture.Locator.evaluate;old_attr=f.fixture.Locator.get_attribute
    def switch(locator):
     fired.append(len(a._context.events));locator.page.goto(a.urls['edit'].replace('{remote_id}','900'))
    def fill(locator,value):
     old_fill(locator,value)
     if stage=='after_'+locator.selector.removeprefix('#') and not fired:switch(locator)
    def evaluate(locator,expr,*args):
     result=old_eval(locator,expr,*args)
     prior='#chapter' if stage=='inside_title_kind' else '#title'
     selector='#title' if stage=='inside_title_kind' else '#body'
     if stage in ('inside_title_kind','inside_body_kind') and locator.selector==selector and 'editable: el.isContentEditable' in expr and ('fill',prior) in a._context.events and not fired:switch(locator)
     return result
    def attr(locator,key):
     result=old_attr(locator,key)
     if stage=='save_label' and locator.selector=='#save' and key=='title' and ('fill','#body') in a._context.events and not fired:switch(locator)
     return result
    accepted=False;code=None
    try:
     with patch.object(f.fixture.Locator,'fill',fill),patch.object(f.fixture.Locator,'evaluate',evaluate),patch.object(f.fixture.Locator,'get_attribute',attr):
      a.create_draft(2,'待创建标题','待创建正文');accepted=True
    except f.browser.BrowserAdapterError as error:code=error.code
    finally:
     after=copy.deepcopy(a._context.editors['900']);following=[] if not fired else [e for e in a._context.events[fired[0]:] if e[0]=='fill' or e==('click','#save')]
     a.close();case.tearDown()
    ok=accepted and before==after if stage=='control' else bool(fired) and not accepted and before==after and not following
    rows.append({'platform':platform,'listing':listing,'autosave':autosave,'stage':stage,'ok':ok,'accepted':accepted,'code':code,'fired':bool(fired),'existing_unchanged':before==after,'writes_after_switch':following})
# A direct autosaving route may allocate its first ID and update its own URL
# without replacing the just-created document or fields.
for platform in ('qimao','qidian'):
 case=f.EditorReadbackTests();case.setUp();a=case.opened(platform,autosave=True);old_fill=f.fixture.Locator.fill;fired=[]
 def fill(locator,value):
  old_fill(locator,value)
  if locator.selector=='#chapter' and not fired:
   remote=locator.page.nodes['#remote-id'][0].attrs['data-id'];locator.page.url=a.urls['edit'].replace('{remote_id}',remote);fired.append(remote)
 accepted=False;code=None
 try:
  with patch.object(f.fixture.Locator,'fill',fill):a.create_draft(2,'待创建标题','待创建正文');accepted=True
 except f.browser.BrowserAdapterError as error:code=error.code
 finally:a.close();case.tearDown()
 rows.append({'platform':platform,'stage':'first_allocation_same_document_url_transition','ok':accepted and bool(fired),'accepted':accepted,'code':code,'allocated':fired})
result={'ok':all(r['ok'] for r in rows),'cases':rows,'source_sha256':hashlib.sha256((ROOT/'skills/story-skill/scripts/story_platform_browser.py').read_bytes()).hexdigest(),'scope':'Offline adapter fixture. Direct/popup editors, button + fixture autosave, per-field and internal locator-read races; no real account or platform.'}
name='native-create-result.json' if result['ok'] else 'native-create-red.json'
(Path(__file__).parent/name).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'ok':result['ok'],'cases':len(rows),'failed':[r for r in rows if not r['ok']]},ensure_ascii=False))
raise SystemExit(0 if result['ok'] else 1)
