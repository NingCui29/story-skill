"""Independent mutation timing through public native create_draft fixture."""
from pathlib import Path
import hashlib,json,sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import test_platform_browser_editor_readback as tests
rows=[]
for platform in ('qimao','qidian'):
 for operation in ('create','save'):
  for initial in (None,'','/draft/action'):
   for changed_href in (False,True):
    h=tests.EditorReadbackTests();h.setUp()
    try:
     adapter=h.opened(platform,listing=operation=='create',sync_drafts=operation=='create')
     selector='#'+operation;armed=[];changed=[]
     original_one=adapter._one;original_attr=tests.fixture.Locator.get_attribute
     original_guard=adapter._current_editor_guard;original_identity=adapter._require_identity
     def one(page,spec):
      loc=original_one(page,spec)
      if loc.selector==selector and not armed:
       if initial is None:loc.node().attrs.pop('href',None)
       else:loc.node().attrs['href']=initial
      return loc
     def get_attribute(loc,name):
      value=original_attr(loc,name)
      if loc.selector==selector and name=='href' and not armed:armed.append(loc)
      return value
     def mutate():
      if armed and changed_href and not changed:
       armed[0].node().attrs['href']='/publish';changed.append(True)
     def guard(*args,**kwargs):
      result=original_guard(*args,**kwargs);mutate();return result
     def identity(*args,**kwargs):
      result=original_identity(*args,**kwargs);mutate();return result
     adapter._one=one;adapter._current_editor_guard=guard;adapter._require_identity=identity
     with patch.object(tests.fixture.Locator,'get_attribute',get_attribute):
      try:value=adapter.create_draft(2,'新标题','新正文');error=None
      except tests.browser.BrowserAdapterError as e:value=None;error=e.code
     clicks=[e for e in adapter._context.events if e[0]=='click']
     if changed_href:
      assert changed and ('click',selector) not in clicks,(platform,operation,initial,error,clicks)
      assert error.endswith('unsafe_'+operation),(platform,operation,error)
     else:assert value in ('new-1','empty-1') and error is None,(platform,operation,initial,error)
     rows.append({'platform':platform,'operation':operation,'initial_href':initial,'changed':changed_href,'returned':value,'error':error,'clicks':clicks})
    finally:h.tearDown()
for platform in ('qimao','qidian'):
 for allocation in (False,True):
  h=tests.EditorReadbackTests();h.setUp()
  try:
   adapter=h.opened(platform);original=adapter._preview_current_value;mutated=[]
   def state_read(page,spec):
    value=original(page,spec)
    if spec is adapter.profile['editor']['state'] and not mutated:
     page.url=adapter.urls['edit'].replace('{remote_id}','new-1' if allocation else '901')
     if allocation:page.nodes['#remote-id'][0].attrs['data-id']='new-1'
     mutated.append(True)
    return value
   adapter._preview_current_value=state_read
   try:value=adapter.create_draft(2,'新标题','新正文');error=None
   except tests.browser.BrowserAdapterError as e:value=None;error=e.code
   writes=[e for e in adapter._context.events if e[0] in ('click','fill')]
   assert mutated
   if allocation:assert value=='new-1' and error is None,(platform,error,writes)
   else:assert error.endswith('identity_mismatch') and not writes,(platform,error,writes)
   rows.append({'platform':platform,'during':'state_read','allocation':allocation,'returned':value,'error':error,'writes':writes})
  finally:h.tearDown()
summary={'ok':True,'scenarios':len(rows),'source_sha256':hashlib.sha256((ROOT/'skills/story-skill/scripts/story_platform_browser.py').read_bytes()).hexdigest(),'results':rows}
(OUT/'native-cross-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='results'}))
