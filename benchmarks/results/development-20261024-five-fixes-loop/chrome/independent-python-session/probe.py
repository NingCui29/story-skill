import sys,os,time,json,threading,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
root=Path(__file__).resolve().parents[5];sys.path.insert(0,str(root))
from tests.test_platform_chrome_bridge import ChromeBridgeTests,bridge
case=ChromeBridgeTests();case.setUp()
try:
    binding=case.binding();config=case.config;book_id=case.book.meta('id')
    book=SimpleNamespace(root=case.root,meta=lambda key:book_id)
    old=bridge.worker_status;status_name='_worker_status' if hasattr(bridge,'_worker_status') else 'worker_status';checked=getattr(bridge,status_name);first_seen=threading.Event();release_first=threading.Event();results=[];calls=[];counter_lock=threading.Lock()
    def checked_status(*args,**kwargs):
        result=checked(*args,**kwargs)
        with counter_lock:
            calls.append(result.copy());number=len(calls)
        if number==1 and not result['attached']:
            first_seen.set()
            if not release_first.wait(5):raise RuntimeError('probe parent did not release first attach')
        return result
    def attach(index):
        try:
            response=bridge.attach(book,case.plan['id'],case.path.name,case.root/('handle'+str(index)+'.json'))
            results.append({'index':index,'ok':True,'response':response})
        except Exception as error:results.append({'index':index,'ok':False,'error':getattr(error,'code',type(error).__name__)})
    with patch.object(bridge,'_binding',return_value=(binding,config)),patch.object(bridge,status_name,side_effect=checked_status):
        first=threading.Thread(target=attach,args=(1,));first.start();assert first_seen.wait(5)
        second=threading.Thread(target=attach,args=(2,));second.start();time.sleep(.2);release_first.set()
        first.join(5);second.join(5);assert not first.is_alive() and not second.is_alive()
    try:status=old(case.root,binding)
    except Exception as error:status={'error':getattr(error,'code',type(error).__name__)}
    output={'source_sha256':hashlib.sha256((root/'skills/story-skill/scripts/story_platform_chrome_bridge.py').read_bytes()).hexdigest(),'results':sorted(results,key=lambda r:r['index']),'status':status,'live_sessions':sum(r['ok'] for r in results),'passed':sum(r['ok'] for r in results)==1 and status.get('attached') is True}
    print(json.dumps(output,ensure_ascii=False,indent=2))
    for i in (1,2):
        handle=case.root/('handle'+str(i)+'.json')
        if handle.exists():bridge.heartbeat(book,handle,detach=True)
finally:case.doCleanups()
