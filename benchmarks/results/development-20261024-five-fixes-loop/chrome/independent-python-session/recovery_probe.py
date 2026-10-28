import sys,json,hashlib,copy,time
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[5];sys.path.insert(0,str(root))
from tests.test_platform_chrome_bridge import ChromeBridgeTests,bridge
rows=[]
def call(fn,*args):
    try:return {'ok':True,'value':fn(*args)}
    except Exception as error:return {'ok':False,'error':getattr(error,'code',type(error).__name__)}
for write_name in ('session.json','lease.json'):
    for timing in ('before','after'):
        case=ChromeBridgeTests();case.setUp()
        try:
            original=bridge._write;fired=[False]
            def inject(directory,name,value,**kwargs):
                if name==write_name and not fired[0]:
                    fired[0]=True
                    if timing=='after':original(directory,name,value,**kwargs)
                    raise OSError('independent initial publish failure')
                return original(directory,name,value,**kwargs)
            with patch.object(bridge,'_write',side_effect=inject):
                result=call(bridge.attach,case.book,case.plan['id'],case.path.name,case.handle)
            folder=case.root/bridge.DIRECTORY
            before={str(p.relative_to(folder)):p.read_bytes().hex() for p in folder.rglob('*') if p.is_file()}
            status=call(bridge.worker_status,case.root,case.binding())
            again=call(bridge.attach,case.book,case.plan['id'],case.path.name,case.root/'new-handle.json')
            after={str(p.relative_to(folder)):p.read_bytes().hex() for p in folder.rglob('*') if p.is_file()}
            retained=all(after.get(name)==contents for name,contents in before.items())
            rows.append({'case':'initial_'+write_name+'_'+timing,'first':result,'status':status,'next_attach':again,'original_files_retained':retained,'passed':not result['ok'] and status['ok'] and not status['value']['attached'] and again['ok'] and retained})
        finally:case.doCleanups()
for name in ('outbox-'+'a'*32+'.json','claimed-'+'a'*32+'.json','response-'+'a'*32+'.json','health.json','health-challenge.json','lease.json','unknown.json','.chrome-stage-not_uuid'):
    case=ChromeBridgeTests();case.setUp()
    try:
        with bridge._directory(case.root,'b'*32,create=True) as directory:bridge._write(directory,name,{'evidence':'must remain'})
        path=case.root/bridge.DIRECTORY/('b'*32)/name;before=path.read_bytes()
        result=call(bridge.worker_status,case.root,case.binding())
        rows.append({'case':'uninitialized_with_'+name,'result':result,'original_file_retained':path.read_bytes()==before,'passed':not result['ok'] and path.read_bytes()==before})
    finally:case.doCleanups()
output={'source_sha256':hashlib.sha256((root/'skills/story-skill/scripts/story_platform_chrome_bridge.py').read_bytes()).hexdigest(),'cases':rows,'passed':all(row['passed'] for row in rows),'count':len(rows)}
print(json.dumps(output,ensure_ascii=False,indent=2))
