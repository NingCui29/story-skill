"""Stopping from the shelf requires current, explicitly reviewed observations."""
import unittest

import test_workbench_management_feedback as feedback


class WorkbenchPresenceShelfTests(unittest.TestCase):
    def js(self, checks):
        fixture = feedback.WorkbenchManagementFeedbackTests()
        fixture.setUp()
        fixture.js(r'''
function summary(state='unknown',fingerprint='first'){
 const pending=state==='pending',unknown=state==='unknown';
 return {running:true,presence_supported:true,presence:{instance:'service-a',book_id:'book-a',
  no_pages:false,state,fingerprint,counts:{unknown:unknown?1:0,pending_pages:pending?1:0},
  pages:[{page_id:'page-a',sequence:1,initialized:true,state,stale:unknown,
   unknown_reason:unknown?'heartbeat_lost':null,last_known_pending:unknown,
   buffers:[{id:'draft-a',title:'第一章',dirty:pending||unknown,saving:false,recovering:false,needs_recovery:false}]}]}};
}
''' + checks)

    def test_fresh_pending_cannot_be_manually_confirmed_or_stopped(self):
        self.js(r'''
response=summary('pending');await showBookService(book);fields()[0].checked=true;
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service']);
assert.ok($('manager-error').textContent.includes('不能停止'));assert.equal(manager.open,true);
''')

    def test_unknown_requires_separate_explicit_confirmation_and_latest_fingerprint(self):
        self.js(r'''
response=summary();await showBookService(book);fields()[0].checked=true;
await action('停止本书编辑服务').onclick();assert.equal(requests.length,2);
assert.ok($('manager-error').textContent.includes('明确确认'));
fields()[1].checked=true;
call=async(action,data)=>{requests.push({action,...data});
 if(action==='book-service'){const latest=summary('unknown','heartbeat-two');latest.presence.pages[0].sequence=2;return latest;}
 if(action==='confirm-book-unknown')return {unknown_confirmation:'one-use'};
 return {stop_requested:true};};
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.slice(2).map(r=>r.action),['book-service','confirm-book-unknown','stop-book']);
assert.equal(requests[3].fingerprint,'heartbeat-two');assert.equal(requests[3].confirmed,true);
assert.equal(requests[4].unknown_confirmation,'one-use');assert.equal(requests[4].saved,true);
''')

    def test_changed_visible_state_resets_both_checks_without_confirming(self):
        self.js(r'''
response=summary();await showBookService(book);fields()[0].checked=true;fields()[1].checked=true;
response=summary('unknown','new-page');response.presence.pages.push({...response.presence.pages[0],page_id:'page-b'});
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service']);
assert.equal(fields()[0].checked,false);assert.equal(fields()[1].checked,false);
assert.ok($('manager-error').textContent.includes('重新逐页核对'));
''')

    def test_confirmation_race_is_shown_and_never_automatically_retried(self):
        self.js(r'''
response=summary();await showBookService(book);fields()[0].checked=true;fields()[1].checked=true;
call=async(action,data)=>{requests.push({action,...data});if(action==='book-service')return response;
 if(action==='confirm-book-unknown')return {unknown_confirmation:'now-stale'};
 throw Object.assign(Error('页面保存状态已变化，请重新核对'),{responseReceived:true});};
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service','confirm-book-unknown','stop-book']);
assert.ok($('manager-error').textContent.includes('重新核对'));assert.equal(manager.open,true);
''')

    def test_refresh_drops_prior_human_confirmation(self):
        self.js(r'''
response=summary();await showBookService(book);fields()[0].checked=true;fields()[1].checked=true;
await action('重新读取窗口状态').onclick();assert.equal(fields()[0].checked,false);assert.equal(fields()[1].checked,false);
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service']);
''')

    def test_running_platform_job_is_visible_and_cannot_be_overridden_by_saved_checkbox(self):
        self.js(r'''
response=summary('clean');response.platform_activity={supported:true,busy:true,binding:false,jobs:[{id:'job-a',stage:'reading_back',mode:'save_draft',current_chapter:2}]};
await showBookService(book);fields()[0].checked=true;
assert.ok(managerBody.querySelectorAll('p').some(node=>node.textContent.includes('回读核对')));
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service']);
assert.ok($('manager-error').textContent.includes('平台草稿任务仍在执行'));
''')

    def test_new_platform_job_between_status_and_stop_is_shown_before_stopping(self):
        self.js(r'''
response=summary('clean');response.platform_activity={supported:true,busy:false,binding:false,jobs:[]};
await showBookService(book);fields()[0].checked=true;
response={...response,platform_activity:{supported:true,busy:true,binding:false,jobs:[{id:'job-b',stage:'waiting_login',mode:'save_draft'}]}};
await action('停止本书编辑服务').onclick();
assert.deepEqual(requests.map(r=>r.action),['book-service','book-service']);
assert.equal(fields()[0].checked,false);
assert.ok(managerBody.querySelectorAll('p').some(node=>node.textContent.includes('等待登录')));
''')


if __name__ == '__main__':
    unittest.main()
