import sys,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/Users/cuining/Documents/GitHub/story-skill');sys.path.insert(0,str(ROOT))
from tests import test_platform_browser as fixture
from tests import test_platform_drafts as ledger
out=[]
for platform in ['qimao','qidian']:
 for switch in [False,True]:
  book_fixture=ledger.fixtures.PublishTests();book_fixture.setUp()
  try:
   book_fixture.commit()
   plan=book_fixture.prepare(platform=platform)
   frozen=plan['manifest']['chapters'][0]
   config=fixture.profile(platform)
   wrong='远端实际错误正文'
   target={'remote_id':'900','chapter':1,'title':frozen['title'],'body':wrong,'state':'草稿'}
   other={'remote_id':'901','chapter':99,'title':'另一草稿','body':frozen['body'],'state':'草稿'}
   adapter=fixture.BrowserTests().opened(config,lists={config['urls']['drafts']:[[('900',1,'草稿'),('901',99,'草稿')]]},editors={'900':target,'901':other},account='author-one',book='book-one')
   ctx=adapter._context
   lock_root=book_fixture.root.parent/'global-draft-state';lock_root.mkdir(mode=0o700)
   raw=fixture.Locator.input_value
   switched=[]
   def read(locator):
    if switch and locator.selector=='#body' and locator.page.url.endswith('/900'):
     switched.append(True)
     locator.page.goto(config['urls']['edit'].replace('{remote_id}','901'))
    return raw(locator)
   with patch.object(fixture.Locator,'input_value',read),patch.object(ledger.drafts,'_lock_directory',return_value=lock_root):
    result=ledger.drafts.run_plan(book_fixture.book,plan['id'],adapter,execute=True)
   out.append({'platform':platform,'cross_draft_navigation':switch,'ok':result['ok'],
    'all_chapters_verified_this_run':result['all_chapters_verified_this_run'],
    'state':result['results'][0]['state'],'remote_id':result['results'][0]['remote_id'],
    'observed_hash_matches_target':result['results'][0]['title_body_verified'],
    'actual_target_body_matches':ctx.editors['900']['body']==frozen['body'],
    'switched':len(switched),'saves':sum(e[:2]==('click','#save') for e in ctx.events),
    'fills':sum(e[0]=='fill' for e in ctx.events)})
  finally:
   book_fixture.tearDown()
print(json.dumps(out,ensure_ascii=False,indent=2))
Path(__file__).with_name('generic-native-journal-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
