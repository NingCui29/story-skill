import sys, json, importlib.util, subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/Users/cuining/Documents/GitHub/story-skill')
sys.path.insert(0,str(ROOT))
from tests import test_platform_browser as fixture
OUT=Path(__file__).resolve().parent
old_path=OUT/'head-story-platform-browser.py'
old_path.write_bytes(subprocess.check_output(['git','show','HEAD:skills/story-skill/scripts/story_platform_browser.py'],cwd=ROOT))
spec=importlib.util.spec_from_file_location('head_platform_browser_probe',old_path)
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
results=[]
for label,module in [('current',fixture.browser),('HEAD',old)]:
 for platform in ['qimao','qidian']:
  for change in ['same_book_other_draft','account_switch']:
   config=fixture.profile(platform)
   rows=[[('900',12,'草稿'),('901',13,'草稿')]]
   original={'remote_id':'900','chapter':12,'title':'目标原稿','body':'目标原稿正文','state':'草稿'}
   other={'remote_id':'901','chapter':13,'title':'另一草稿','body':'另一草稿正文','state':'草稿'}
   adapter=module.BrowserAdapter(config,timeout_ms=10,login_timeout_ms=100)
   adapter._context=fixture.Context(config,lists={config['urls']['drafts']:rows},editors={'900':original,'901':other})
   context=adapter._context
   adapter._list_page=context.new_page()
   adapter.verify_target(platform,'author-1','book-1')
   adapter._chapter_ids['900']=12
   raw=fixture.Locator.input_value
   switched=[]
   def read(locator):
    if locator.selector=='#body' and not switched:
     switched.append(True)
     if change=='same_book_other_draft':
      locator.page.goto(config['urls']['edit'].replace('{remote_id}','901'))
     else:
      context.options['account']='different-author'
    return raw(locator)
   with patch.object(fixture.Locator,'input_value',read):
    try:
     observation=adapter.read_draft('900'); status='accepted'
    except module.BrowserAdapterError as exc:
     observation={'error':exc.code};status='rejected'
   results.append({'version':label,'platform':platform,'change':change,'status':status,
      'observation':observation,'switched':len(switched),'fills':sum(e[0]=='fill' for e in context.events),
      'saves':sum(e[:2]==('click','#save') for e in context.events),
      'editor_closed':all(p.closed for p in context.pages if getattr(p,'kind',None)=='edit')})
print(json.dumps(results,ensure_ascii=False,indent=2))
(OUT/'generic-native-read-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
