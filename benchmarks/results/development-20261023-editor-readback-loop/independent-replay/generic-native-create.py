import sys,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/Users/cuining/Documents/GitHub/story-skill');sys.path.insert(0,str(ROOT))
from tests import test_platform_browser as fixture
out=[]
for platform in ['qimao','qidian']:
 config=fixture.profile(platform)
 other={'remote_id':'901','chapter':99,'title':'另一草稿','body':'预期正文','state':'草稿'}
 adapter=fixture.BrowserTests().opened(config,editors={'901':other})
 ctx=adapter._context
 adapter.verify_target(platform,'author-1','book-1')
 saved=ctx.persist
 def persist(page):
  saved(page)
  ctx.editors['new-1']['body']='远端实际错误正文'
 ctx.persist=persist
 raw=fixture.Locator.input_value
 switched=[]
 def read(locator):
  if locator.selector=='#body' and locator.page.kind=='edit' and not switched:
   switched.append(True)
   locator.page.goto(config['urls']['edit'].replace('{remote_id}','901'))
  return raw(locator)
 with patch.object(fixture.Locator,'input_value',read):
  try:
   result=adapter.create_draft(12,'目标标题','预期正文');status='accepted'
  except fixture.browser.BrowserAdapterError as exc:
   result=exc.code;status='rejected'
 out.append({'platform':platform,'status':status,'result':result,'remote_body':ctx.editors.get('new-1',{}).get('body'),'expected_body':'预期正文','switched':len(switched),'saves':sum(e[:2]==('click','#save') for e in ctx.events)})
print(json.dumps(out,ensure_ascii=False,indent=2))
Path(__file__).with_name('generic-native-create-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
