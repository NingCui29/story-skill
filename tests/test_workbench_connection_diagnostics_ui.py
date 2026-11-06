"""Production draft diagnostics with a synthetic DOM; no model or platform calls."""
import unittest

from tests import test_workbench_formal_refresh_ui as formal_ui


class ConnectionDiagnosticsUITests(unittest.TestCase):
    js = formal_ui.FormalRefreshUITests.js
    fixture = r'''
const connection=(reason,extra={})=>({stage:'connecting_chrome',ready:false,reason,
 requested_model:'gpt-6.1-terra',resolved_model:null,model_verified:false,...extra});
const failedInspection=(code='platform_chrome_worker_tool_not_started',extra={})=>job({
 mode:'inspect_drafts',state:'failed',stage:'finished',done:true,checked_chapters:0,total_chapters:30,
 error:code,connection_diagnostics:connection(code),result:{results:[{chapter:1,state:'pending'}]},...extra});
const starts=()=>requests.filter(row=>row.action.endsWith('/start'));
'''

    def test_host_denial_has_exact_stage_and_does_not_prescribe_changing_permissions(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,failedInspection('platform_chrome_worker_host_access_denied',{
 connection_diagnostics:connection('platform_chrome_worker_host_access_denied',{
  failure_phase:'browser_inventory',error_name:'Error',error_marker:'local_browser_access_denied',
  resolved_model:'gpt-6.1-terra',model_verified:true})}));
assert.ok(p.status.textContent.includes('宿主未授权'));assert.ok(p.jobNote.textContent.includes('获取浏览器列表'));
assert.ok(p.nextHint.textContent.includes('重新连接'));assert.ok(!p.nextHint.textContent.includes('请检查现有 Chrome 中的账号'));
assert.equal(p.permissionHelp.open,false);assert.equal(starts().length,0);
for(const value of ['PRIVATE','constructor','__proto__',{},['browser_inventory']]){
 const note=platformDraftConnectionNote({connection_diagnostics:connection(null,{failure_phase:value})});
 assert.ok(!note.includes('停止阶段：'));assert.ok(!note.includes('PRIVATE'));assert.ok(!note.includes('function'));
}
''')

    def test_missing_executor_stops_at_connection_without_claiming_page_failure(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]),calls=requests.length;
platformDraftDrawJob(p,failedInspection());
assert.ok(p.status.textContent.includes('连接阶段未完成'));assert.ok(p.status.textContent.includes('尚未开始章节检查'));
assert.ok(p.status.textContent.includes('未启动受认可的浏览器执行器'));
assert.equal(p.progressLabel.textContent,'本次差异检查 0 / 30 章 · 未写入平台');
assert.ok(p.nextHint.textContent.includes('浏览器执行工具'));assert.ok(!p.nextHint.textContent.includes('请检查现有 Chrome'));
assert.ok(!p.status.textContent.includes('未能读取 Chrome 页面'));assert.equal(p.permissionHelp.open,false);
assert.equal(p.run.disabled,true);assert.equal(starts().length,0);assert.equal(requests.length,calls);
assert.equal(JSON.stringify([...docs]),before);
''')

    def test_failure_displays_bounded_code_and_incident_reference(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,failedInspection('platform_chrome_worker_tool_failed',{
 id:'c'.repeat(32),audit_saved:true,
 connection_diagnostics:connection('platform_chrome_worker_tool_failed',{
  tool_error_fingerprint:'0123456789abcdef',diagnostic_id:'0123456789abcdef0123456789abcdef'})}));
assert.ok(p.status.textContent.includes('故障码：platform_chrome_worker_tool_failed'));
assert.ok(p.status.textContent.includes('错误结构：0123456789abcdef'));
assert.ok(p.status.textContent.includes('记录编号：0123456789abcdef0123456789abcdef'));
assert.ok(p.status.textContent.includes('任务记录：'+'c'.repeat(32)));
platformDraftDrawJob(p,failedInspection('platform_chrome_worker_tool_failed',{
 audit_saved:false,audit_started_saved:false,
 connection_diagnostics:connection('platform_chrome_worker_tool_failed',{
  tool_error_fingerprint:'PRIVATE_BODY',diagnostic_id:'PRIVATE_TOKEN'})}));
assert.ok(!textTree(p.content).includes('PRIVATE_'));
assert.ok(p.status.textContent.includes('本地任务记录保存失败'));assert.ok(p.status.textContent.includes('任务开始记录未能保存'));
''')

    def test_audit_lookup_shows_interrupted_without_claiming_no_write(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,id='a'.repeat(32);
const findButton=node=>node.textContent==='查询记录'?node:node.children.map(findButton).find(Boolean);
const button=findButton(p.content);assert.ok(button);controls['platform-draft-audit-id'].value=id;
handler=async(action,payload)=>{assert.ok(action.endsWith('/audit-lookup'));assert.equal(payload.record_id,id);return {ok:true,id,records:[{kind:'started',state:'started'}]};};
await button.onclick();assert.ok(controls['platform-draft-audit-result'].textContent.includes('没有终态记录'));
assert.ok(!controls['platform-draft-audit-result'].textContent.includes('未写入'));assert.equal(starts().length,0);
''')

    def test_model_failures_have_specific_messages_and_model_help(self):
        self.js(self.fixture + r'''
for(const [suffix,expected]of [['model_unverified','无法核验'],['model_mismatch','不匹配'],['model_rerouted','模型切换'],['model_unavailable','当前不可用']]){
 await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection('platform_chrome_worker_'+suffix));
 assert.ok(p.status.textContent.includes(expected),suffix);assert.ok(p.nextHint.textContent.includes('GPT-6.1 Terra'));
 assert.ok(!p.nextHint.textContent.includes('请检查现有 Chrome'));assert.ok(p.jobNote.textContent.includes('模型身份尚未核验'));
 assert.ok(p.jobNote.textContent.includes('会话回执 未确认'));assert.equal(p.run.disabled,true);assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_discovery_and_old_not_ready_are_executor_issues(self):
        self.js(self.fixture + r'''
 for(const code of ['tool_discovery_failed','tool_approval_required','not_ready','unexpected_tool','runtime_unavailable','turn_metadata_missing']){
 await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection('platform_chrome_worker_'+code));
 assert.ok(p.nextHint.textContent.includes('浏览器执行工具'));assert.ok(!p.nextHint.textContent.includes('请检查现有 Chrome'));
 assert.ok(!p.status.textContent.includes('未能读取 Chrome 页面'));assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_real_permission_failure_opens_permission_help(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection('platform_chrome_worker_origin_permission_required'));
assert.ok(p.status.textContent.includes('页面访问授权'));assert.equal(p.permissionHelp.open,true);
assert.ok(p.nextHint.textContent.includes('网站访问授权帮助'));assert.ok(!p.nextHint.textContent.includes('模型配置'));
assert.equal(starts().length,0);
''')

    def test_real_page_failure_after_connection_keeps_readonly_recovery(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,failedInspection('platform_browser_read_timeout',{checked_chapters:2,current_chapter:3,
 connection_diagnostics:connection(null,{ready:true,resolved_model:'gpt-6.1-terra',model_verified:true})}));
assert.ok(p.status.textContent.includes('页面读取超时'));assert.ok(!p.status.textContent.includes('尚未开始章节检查'));
assert.ok(p.progressLabel.textContent.includes('2 / 30'));assert.equal(p.recoveryAction.textContent,'先只读核对结果');
assert.ok(p.nextHint.textContent.includes('先核对'));assert.equal(starts().length,0);
''')

    def test_unknown_sync_result_never_becomes_no_write_or_direct_retry(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({state:'unknown',stage:'finished',done:true,error:'platform_chrome_worker_model_rerouted',
 connection_diagnostics:connection('platform_chrome_worker_model_rerouted'),result:{results:[{
 chapter:1,state:'unknown',recorded_this_run:true,remote_id:'existing-draft',reason:'interrupted_sending'}]}}));
assert.ok(p.status.textContent.includes('远端结果未知'));assert.ok(!p.status.textContent.includes('未写入平台'));
assert.equal(p.recoveryAction.textContent,'先只读核对结果');assert.ok(p.nextHint.textContent.includes('远端可能已发生保存'));
assert.ok(!p.nextHint.textContent.includes('模型配置'));assert.equal(starts().length,0);
''')

    def test_historical_connection_diagnostics_remain_historical(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection(),true);
assert.ok(p.status.textContent.includes('本次尚未开始'));assert.ok(!p.status.textContent.includes('连接阶段未完成'));
assert.ok(p.historyJob.textContent.includes('连接阶段未完成'));assert.ok(p.jobNote.textContent.includes('当时的执行模型'));
assert.ok(!p.nextHint.textContent.includes('浏览器执行工具是否可用'));assert.equal(starts().length,0);
''')

    def test_untrusted_diagnostic_fields_are_not_rendered(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection(undefined,{
 connection_diagnostics:connection('platform_chrome_worker_tool_not_started',{
 requested_model:'PRIVATE_PATH',resolved_model:'<script>PRIVATE_TOKEN</script>',model_verified:true,
 error_kind:'PRIVATE_ERROR',message:'PRIVATE_BODY',rpc_error_code:'PRIVATE_RPC',http_status:'PRIVATE_STATUS'})}));
assert.ok(!textTree(p.content).includes('PRIVATE_'));assert.ok(!p.jobNote.textContent.includes('模型身份已核验'));
assert.ok(p.jobNote.textContent.includes('模型身份尚未核验'));assert.equal(starts().length,0);
''')

    def test_model_verification_never_grants_browser_readiness_or_sync(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection(undefined,{
 connection_diagnostics:connection('platform_chrome_worker_tool_not_started',{
 resolved_model:'gpt-6.1-terra',model_verified:true})}));
assert.ok(p.jobNote.textContent.includes('模型身份已核验'));
assert.ok(p.jobNote.textContent.includes('模型身份不代表浏览器连接已就绪'));
assert.equal(p.run.disabled,true);assert.equal(platformDraftCanStart(p),false);assert.equal(starts().length,0);
''')

    def test_new_job_without_diagnostics_clears_previous_model_note(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection());assert.ok(p.jobNote.textContent.includes('GPT-6.1 Terra'));
platformDraftDrawJob(p,failedInspection('platform_browser_read_timeout',{connection_diagnostics:null,checked_chapters:1}));
assert.ok(!p.jobNote.textContent.includes('GPT-6.1 Terra'));assert.ok(!p.status.textContent.includes('连接阶段未完成'));
assert.equal(starts().length,0);
''')

    def test_browser_transport_message_does_not_assume_no_platform_activity(self):
        self.js(self.fixture + r'''
const message=platformDraftErrorCode('platform_chrome_worker_browser_transport_closed');
assert.ok(message.includes('官方浏览器通信通道'));assert.ok(message.includes('收到响应前断开'));
for(const phrase of ['未访问','未写入','未开始保存','未打开','权限拒绝','重启'])assert.ok(!message.includes(phrase),phrase);
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failedInspection('platform_chrome_worker_browser_transport_closed'));
assert.ok(p.status.textContent.includes('连接阶段未完成'));assert.ok(p.progressLabel.textContent.includes('0 / 30 章 · 未写入平台'));
assert.ok(p.nextHint.textContent.includes('Codex'));assert.ok(p.nextHint.textContent.includes('官方 Chrome 连接状态'));
assert.ok(!p.nextHint.textContent.includes('请检查现有 Chrome 中的账号'));assert.equal(p.permissionHelp.open,false);
assert.equal(starts().length,0);
''')

    def test_browser_transport_disconnect_during_sync_keeps_unknown_readback(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({state:'unknown',stage:'finished',done:true,error:'platform_chrome_worker_browser_transport_closed',
 connection_diagnostics:connection(null,{ready:true,resolved_model:'gpt-6.1-terra',model_verified:true}),result:{results:[{
 chapter:1,state:'unknown',recorded_this_run:true,remote_id:'existing-draft',reason:'interrupted_sending'}]}}));
assert.ok(p.status.textContent.includes('远端结果未知'));assert.ok(p.status.textContent.includes('收到响应前断开'));
assert.ok(!p.status.textContent.includes('未写入平台'));assert.ok(!p.status.textContent.includes('尚未开始'));
assert.equal(p.recoveryAction.textContent,'先只读核对结果');assert.ok(p.nextHint.textContent.includes('远端可能已发生保存'));
assert.equal(p.permissionHelp.open,false);assert.equal(starts().length,0);
''')

    def test_known_provider_error_categories_have_fixed_chinese_details(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
for(const [kind,expected]of [['modelNotFound','指定模型不可用或未找到'],['usageLimitExceeded','账号达到用量上限'],
 ['rateLimitExceeded','限制了请求频率'],['unauthorized','Codex 服务身份认证未通过'],
 ['httpConnectionFailed','模型服务连接失败'],['responseStreamDisconnected','模型响应通道中断']]){
 platformDraftDrawJob(p,failedInspection('platform_chrome_worker_turn_failed',{
  connection_diagnostics:connection('platform_chrome_worker_turn_failed',{error_kind:kind,message:'PRIVATE_PROVIDER_TEXT'})}));
 assert.ok(p.jobNote.textContent.includes('连接诊断：'));assert.ok(p.jobNote.textContent.includes(expected),kind);
 assert.ok(!p.jobNote.textContent.includes(kind));assert.ok(!textTree(p.content).includes('PRIVATE_PROVIDER_TEXT'));
 assert.equal(starts().length,0);
}
''')

    def test_unknown_error_categories_and_prototype_names_are_dropped(self):
        self.js(self.fixture + r'''
for(const value of ['PRIVATE_ERROR','constructor','__proto__',null,{},['modelNotFound']]){
 const note=platformDraftConnectionNote({connection_diagnostics:connection(null,{error_kind:value})});
 assert.ok(!note.includes('连接诊断：'));assert.ok(!note.includes('PRIVATE'));assert.ok(!note.includes('function'));
}
assert.equal(starts().length,0);
''')


if __name__ == '__main__':
    unittest.main()
