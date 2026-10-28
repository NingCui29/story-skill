import sys,json,hashlib
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[5];sys.path.insert(0,str(root))
from tests.test_platform_chrome_bridge import ChromeBridgeTests,bridge
case=ChromeBridgeTests();case.setUp()
try:
    case.attach()
    with patch.dict(bridge.OP_SECONDS,create_draft=1):
        thread,result,errors,claim,request=case.enqueue('create_draft',{'chapter':1,'title':'测试','body':'仅本地验证'})
        request_path=Path(claim['request_path']);before=request_path.read_bytes()
        during=bridge.worker_status(case.root,case.binding())
        try:bridge.attach(case.book,case.plan['id'],case.path.name,case.root/'duplicate.json');duplicate='unexpected_success'
        except Exception as error:duplicate=getattr(error,'code',type(error).__name__)
        thread.join(5);assert not thread.is_alive()
        after=bridge.worker_status(case.root,case.binding());second_claim=bridge.next_request(case.book,case.handle)
        retained=request_path.read_bytes()==before
    output={'source_sha256':hashlib.sha256((root/'skills/story-skill/scripts/story_platform_chrome_bridge.py').read_bytes()).hexdigest(),'during':during,'after_timeout':after,'duplicate_attach':duplicate,'request_errors':[getattr(e,'code',type(e).__name__) for e in errors],'claimed_evidence_retained':retained,'claim_replayed':second_claim['request_id'] is not None}
    output['passed']=(during['attached'] is True and after['attached'] is True and during['active_sessions']==1 and after['active_sessions']==1 and duplicate=='platform_chrome_worker_busy' and retained and second_claim['request_id'] is None and output['request_errors']==['platform_chrome_request_timeout'])
    print(json.dumps(output,ensure_ascii=False,indent=2))
finally:case.doCleanups()
