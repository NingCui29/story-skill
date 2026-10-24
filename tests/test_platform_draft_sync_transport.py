import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_platform_chrome_bridge as fixtures

bridge,drafts,story=fixtures.bridge,fixtures.drafts,fixtures.story

class SyncTransportTests(unittest.TestCase):
    setUp=fixtures.ChromeBridgeTests.setUp
    cleanup_jobs=fixtures.ChromeBridgeTests.cleanup_jobs
    attach=fixtures.ChromeBridgeTests.attach
    health=fixtures.ChromeBridgeTests.health
    binding=fixtures.ChromeBridgeTests.binding
    write_response=fixtures.ChromeBridgeTests.write_response
    assert_code=fixtures.ChromeBridgeTests.assert_code

    def test_default_adapter_refuses_update_and_authenticated_update_carries_before_image_once(self):
        self.attach()
        with bridge.HostAdapter(self.root,self.binding(),self.config) as adapter:
            self.assert_code('platform_draft_update_unsupported',adapter.update_draft,'900',1,'新标题','新正文',expected_title='旧标题',expected_body='旧正文')
        result,errors=[],[]
        with bridge.HostAdapter(self.root,self.binding(),self.config,update_existing=True) as adapter:
            def sender():
                try: result.append(adapter.update_draft('900',1,'新标题','新正文',expected_title='旧标题',expected_body='旧正文'))
                except BaseException as error: errors.append(error)
            thread=threading.Thread(target=sender);self.request_threads.append(thread);thread.start()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                claim=bridge.next_request(self.book,self.handle)
                if claim['request_id']: break
                time.sleep(.01)
            self.assertTrue(claim['request_id'])
            request=json.loads(Path(claim['request_path']).read_text())
            self.assertEqual(request['operation'],'update_draft')
            self.assertEqual(request['params'],{'remote_id':'900','chapter':1,'title':'新标题','body':'新正文','expected_title':'旧标题','expected_body':'旧正文'})
            self.assertIsNone(bridge.next_request(self.book,self.handle)['request_id'])
            self.write_response(request,result='900');bridge.respond(self.book,self.handle,request['id'],self.response)
            thread.join(5)
        self.assertEqual(errors,[]);self.assertEqual(result,['900'])

    def test_save_or_readonly_preview_token_cannot_authorize_update(self):
        self.attach()
        for readonly in [False,True]:
            preview=drafts.workbench_preview(self.root,self.plan['id'],self.path.name,expected_book_id=self.book.meta('id'),reconcile_only=readonly)
            self.assertTrue(preview['can_start'])
            self.assert_code('platform_draft_preview_required',drafts.workbench_start,self.root,self.plan['id'],self.path.name,
                expected_book_id=self.book.meta('id'),expected_profile_sha256=preview['profile_sha256'],
                preview_token=preview['preview_token'],execute=True,update_existing=True)
        preview=drafts.workbench_preview(self.root,self.plan['id'],self.path.name,expected_book_id=self.book.meta('id'),update_existing=True)
        self.assertEqual(preview['mode'],'sync_existing');self.assertTrue(preview['can_start'])
        with patch.object(drafts.threading,'Thread') as thread:
            result=drafts.workbench_start(self.root,self.plan['id'],self.path.name,expected_book_id=self.book.meta('id'),
                expected_profile_sha256=preview['profile_sha256'],preview_token=preview['preview_token'],execute=True,update_existing=True)
            self.assertEqual(result['mode'],'sync_existing');thread.return_value.start.assert_called_once()
            # No worker runs in this dispatch contract test.
            drafts._finish_job(result['id'],error='platform_draft_worker_unavailable');self.jobs.append(result['id'])

    def test_update_receipt_must_preserve_remote_identity_and_metadata_evidence(self):
        self.assert_code('platform_browser_identity_mismatch',bridge._validate_result,'update_draft','901',{}, {'remote_id':'900'})
        observed={'remote_id':'900','title':'标题','body':'原文','state':'draft','formatting_clean':False}
        self.assertEqual(bridge._validate_result('read_draft',observed,{}, {'remote_id':'900'})['formatting_clean'],False)
        self.assert_code('platform_draft_invalid_observation',drafts._observation,{**observed,'formatting_clean':'true'})
