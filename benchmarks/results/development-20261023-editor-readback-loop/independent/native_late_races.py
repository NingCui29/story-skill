"""Independent late snapshot/guard races against the production native adapter."""
from pathlib import Path
from unittest.mock import patch
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import test_platform_browser_editor_readback as f
rows=[]
for platform in ('qimao','qidian'):
 for mode in ('unchanged','duplicate_after_handle','unique_retarget_after_handle','mutate_after_last_guard'):
  test=f.EditorReadbackTests();test.setUp();adapter=test.opened(platform,guard=True)
  fired=[];original_handles=f.fixture.Locator.element_handles;original_eval=f.fixture.Page.evaluate
  original_guard=adapter._current_editor_guard;guard_calls=[]
  def handles(locator):
   result=original_handles(locator)
   if locator.selector=='#body' and mode in ('duplicate_after_handle','unique_retarget_after_handle') and not fired:
    page=locator.page;old=page.nodes['#title'][0];new=f.fixture.Node(tag='INPUT',value='different-title')
    page.nodes['#title']=[old,new] if mode=='duplicate_after_handle' else [new]
    fired.append(True)
   return result
  def evaluate(page,expression,*arguments):
   result=original_eval(page,expression,*arguments)
   if mode=='unique_retarget_after_handle' and 'editorReadbackSnapshot' in expression:
    # The old element remains attached elsewhere in real DOM; fixture normally
    # conflates selector membership with connectedness, so correct this one bit.
    result['fields']['title']['connected']=True
   return result
  def guard():
   result=original_guard();guard_calls.append(True)
   if mode=='mutate_after_last_guard' and len(guard_calls)==2 and not fired:
    page=next(p for p in adapter._context.pages if p.kind=='edit' and not p.closed)
    page.nodes['#chapter'][0].value='99';fired.append(True)
   return result
  code=None;accepted=False
  try:
   with patch.object(f.fixture.Locator,'element_handles',handles),patch.object(f.fixture.Page,'evaluate',evaluate),patch.object(adapter,'_current_editor_guard',guard):
    adapter.read_draft('900');accepted=True
  except f.browser.BrowserAdapterError as error:code=error.code
  finally:
   writes=[event for event in adapter._context.events if event[0] in ('fill','click')]
   adapter.close();test.tearDown()
  passed=accepted if mode=='unchanged' else not accepted and bool(fired)
  rows.append({'platform':platform,'case':mode,'ok':passed and not writes,'accepted':accepted,'code':code,'mutation_fired':bool(fired),'writes':len(writes)})
result={'ok':all(r['ok'] for r in rows),'cases':rows,'source_sha256':hashlib.sha256((ROOT/'skills/story-skill/scripts/story_platform_browser.py').read_bytes()).hexdigest(),'scope':'Offline public adapter fixtures; no real website or account; unique-retarget fixture explicitly preserves old node connectedness.'}
(Path(__file__).parent/'native-late-races.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
raise SystemExit(0 if result['ok'] else 1)
