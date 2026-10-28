import sys
from unittest.mock import patch
sys.path.insert(0,'/Users/cuining/Documents/GitHub/story-skill')
from tests import test_platform_browser_preview as f
case=f.NativePreviewTests()
case.setUp()
try:
    for operation in ['read','find','create']:
        adapter=case.opened(sync_drafts=True)
        ctx=adapter._context
        original=f.PreviewLocator.evaluate_all
        injected=[]
        def current(locator,expression,*args):
            value=original(locator,expression,*args)
            if locator.selector=='.preview-title' and ctx.preview_reads==2:
                locator.page.url=locator.page.url.replace('/fixture/100/','/fixture/200/')
                injected.append(len(ctx.events))
            return value
        with patch.object(f.PreviewLocator,'evaluate_all',current):
            try:
                if operation=='create': adapter.create_draft(13,'新标题','新正文')
                elif operation=='find': adapter.find_drafts(12)
                else: adapter.read_draft('900')
            except f.browser.BrowserAdapterError as err:
                assert err.code=='platform_browser_identity_mismatch',(operation,err.code)
            else:raise AssertionError('final-header navigation accepted')
        assert len(injected)==1
        assert not any(e[0] in ('goto','wait') for e in ctx.events[injected[0]:])
        saves=sum(e==('click','#save') for e in ctx.events)
        assert saves==(1 if operation=='create' else 0)
        if operation=='create':
            results=adapter.find_drafts(13)
            assert len(results)==1 and results[0]['body']=='新正文'
            assert sum(e==('click','#save') for e in ctx.events)==1
        print({'operation':operation,'post_final_header_redirect':'rejected','saves':saves,'reconcile_without_rewrite':operation=='create'})
finally:
    case.doCleanups()
