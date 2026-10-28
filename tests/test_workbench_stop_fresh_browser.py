"""Optional Chrome acceptance flow for fresh, multi-page normal stop.

Every book, service port and browser profile is private to this test.  The
synthetic author input never visits an external site or starts an assistant.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest

import test_workbench as base


class StopFreshBrowserTests(unittest.TestCase):
    def test_two_pages_preserve_input_until_fresh_normal_stop(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        probe = subprocess.run([node, '-e', "require('playwright')"], capture_output=True)
        if probe.returncode:
            self.skipTest('Optional Playwright runtime unavailable')
        fixture = base.WorkbenchTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        before = fixture.authoritative_state()
        chapter_heading = (root / fixture.book.chapter_path(2)).read_text(encoding='utf-8').split('\n', 1)[0]
        original_candidate = root / '.story/drafts/第2章 原有停机候选.md'
        candidate_bytes = '第2章 原有停机候选\n这份既有候选不因停机或保存其他工作稿而改变。\n'.encode()
        original_candidate.write_bytes(candidate_bytes)
        server = base.story.workbench.editor_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(3), server.server_close()))
        data = {
            'url': server.editor_url,
            'python': sys.executable,
            'tool': str(base.TOOL),
            'book': str(root),
        }
        program = r'''
const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs');
const {execFile}=require('child_process');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 let browser;
 try{browser=await chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});}
 catch(error){console.log('BROWSER_UNAVAILABLE: '+error.message.split('\n')[0]);process.exitCode=77;return;}
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const one=await context.newPage(),two=await context.newPage(),errors=[],reports=[],networkOrigins=new Set();
  for(const [page,label]of [[one,'manuscript'],[two,'task']]){
   page.on('pageerror',error=>errors.push(label+': '+error.message));
   page.on('request',request=>{
    networkOrigins.add(new URL(request.url()).origin);
    if(request.url().endsWith('/api/presence'))reports.push({page:label,payload:request.postDataJSON()});
   });
  }
  const token=new URL(data.url).pathname.split('/')[1];
  async function api(action,body={}){
   const response=await context.request.post(data.url+'api/'+action,{headers:{Origin:new URL(data.url).origin,'X-Story-Token':token},data:body});
   return {status:response.status(),packet:await response.json()};
  }
  let identity;
  async function session(){const result=await api('session',{instance:identity});assert.equal(result.status,200);return result.packet;}
  async function eventually(check,message){
   const until=Date.now()+12000;let value;
   do{value=await check();if(value)return value;await wait(75);}while(Date.now()<until);
   assert.fail(message);
  }
  // The public session identity comes from the service record supplied to the
  // CLI too; browser presence credentials remain the page's private bootstrap.
  identity=JSON.parse(fs.readFileSync(data.book+'/.story/workbench-service.json','utf8')).instance;
  await Promise.all([one.goto(data.url+'#formal%3A2'),two.goto(data.url+'#formal%3A2')]);
  await Promise.all([one.waitForFunction(()=>!document.getElementById('edit').disabled),two.waitForFunction(()=>!document.getElementById('review-task').disabled)]);
  const initial=await eventually(async()=>{const packet=await session();return packet.presence.counts.registered===2&&packet.presence.state==='clean'&&packet;},'Both real pages must register clean before editing');
  assert.equal(initial.presence.coverage,'server_issued_pages_this_instance');
  assert.equal(initial.presence.legacy_services_covered,false);
  const dirtyText='第2章 停机核对\n未保存正文哨兵 BODY-PRIVATE-STOP-271，尚未保存时必须留在页面。\n';
  await one.locator('#edit').click();await one.locator('#text').fill(dirtyText);
  const dirty=await eventually(async()=>{const packet=await session();return packet.presence.counts.dirty>0&&packet;},'Unsaved manuscript must be visible to normal stop');
  let denied=await api('stop',{instance:identity,saved:true});
  assert.equal(denied.status,409);assert.equal(denied.packet.error,'workbench_presence_pending');
  assert.equal(await one.locator('#text').inputValue(),dirtyText);
  const firstSaved=one.waitForResponse(response=>response.url().endsWith('/api/save'));
  await one.locator('#save').click();const firstReceipt=await(await firstSaved).json();
  assert.equal(firstReceipt.ok,true);assert.equal(firstReceipt.formal_changed,false);
  await eventually(async()=>{const packet=await session();return packet.presence.state==='clean';},'Saving the manuscript must finish its readback and recovery bookkeeping');
  await two.locator('#more-actions > summary').click();
  const generated=two.waitForResponse(response=>response.url().endsWith('/api/review-task'));
  await two.locator('#review-task').click();const task=(await(await generated).json()).task;
  await two.locator('#assistant-task-current').click();
  const dialog=two.getByRole('dialog').filter({has:two.getByRole('heading',{name:'助手任务与结果'})});
  await dialog.getByRole('heading',{name:/审稿任务/}).waitFor();
  await dialog.getByText('导入助手回执',{exact:true}).click();
  const jsonInput=dialog.getByRole('textbox',{name:'助手结果 JSON',exact:true});
  const privateResult=JSON.stringify({...task.result_template,summary:'尚未导入的私密任务输入 TASK-PRIVATE-STOP-271'});
  await jsonInput.fill(privateResult);
  const pendingTask=await eventually(async()=>{
   const packet=await session();return packet.presence.pages.some(page=>page.buffers.some(buffer=>buffer.id.startsWith('task-form:')&&buffer.dirty))&&packet;
  },'An unimported task result must participate in stop protection');
  denied=await api('stop-prepare',{instance:identity,saved:true});
  assert.equal(denied.status,409);assert.equal(denied.packet.error,'workbench_presence_pending');
  assert.equal(await jsonInput.inputValue(),privateResult);
  assert.equal((await session()).running,true);
  // Observe the actual HTTP reports. Neither a manuscript nor a result JSON is
  // allowed in this metadata channel, including fields nested in each buffer.
  for(const {payload}of reports){
   assert.deepEqual(Object.keys(payload).sort(),['buffers','closing','initialized','page_id','page_token','sequence'].sort());
   for(const buffer of payload.buffers)assert.deepEqual(Object.keys(buffer).sort(),['id','title','dirty','saving','recovering','needs_recovery'].sort());
  }
  assert.ok(!JSON.stringify(reports).includes('BODY-PRIVATE-STOP-271'));
  assert.ok(!JSON.stringify(reports).includes('TASK-PRIVATE-STOP-271'));
  assert.ok(!JSON.stringify(pendingTask.presence).includes(privateResult));
  await jsonInput.fill('');
  await eventually(async()=>{const packet=await session();return packet.presence.state==='clean';},'Clearing the unimported form must remove its pending status');
  const prepared=await api('stop-prepare',{instance:identity,saved:true});
  assert.equal(prepared.status,200);assert.equal(prepared.packet.ok,true);
  const firstChallenge=prepared.packet.fresh_stop.challenge_id,challengeToken=prepared.packet.stop_challenge;
  assert.equal(typeof challengeToken,'string');
  const ready=await eventually(async()=>{
   const result=await api('stop-status',{instance:identity,stop_challenge:challengeToken});
   assert.equal(result.status,200);return result.packet.fresh_stop.ready&&result.packet;
  },'Both browser pages must acknowledge this exact stop challenge');
  assert.equal(ready.fresh_stop.acknowledged_pages.length,2);
  assert.deepEqual(ready.fresh_stop.waiting_pages,[]);
  assert.deepEqual(ready.fresh_stop.manually_confirmed_pages,[]);
  await one.waitForFunction(()=>document.getElementById('text').readOnly);
  await jsonInput.evaluate(element=>{if(!element.readOnly)throw Error('Task JSON is not frozen');});
  assert.equal(await dialog.getByRole('button',{name:'核对并导入',exact:true}).isDisabled(),true);
  const frozenText=await one.locator('#text').inputValue();
  await one.locator('#text').focus();await one.keyboard.type('冻结后不得新增');
  await jsonInput.focus();await two.keyboard.type('冻结后不得导入');
  assert.equal(await one.locator('#text').inputValue(),frozenText);assert.equal(await jsonInput.inputValue(),'');
  const mutation=await api('save',{});assert.equal(mutation.status,409);assert.equal(mutation.packet.error,'workbench_presence_preparing');
  const firstAcks=reports.filter(row=>row.payload.stop_ack?.challenge_id===firstChallenge);
  assert.deepEqual([...new Set(firstAcks.map(row=>row.page))].sort(),['manuscript','task']);
  const artifactDir=process.env.STORY_STOP_BROWSER_REPORT_DIR;
  if(artifactDir){fs.mkdirSync(artifactDir,{recursive:true});await one.screenshot({path:artifactDir+'/manuscript-frozen.png',fullPage:true});await two.screenshot({path:artifactDir+'/task-form-frozen.png',fullPage:true});}
  const cancelled=await api('stop-cancel',{instance:identity,stop_challenge:challengeToken});
  assert.equal(cancelled.status,200);assert.equal(cancelled.packet.cancelled,true);
  await one.waitForFunction(()=>!document.getElementById('text').readOnly);
  await eventually(async()=>!(await jsonInput.evaluate(element=>element.readOnly)),'Cancel must restore task form editing');
  await jsonInput.fill('取消后可继续输入 TASK-RESUMED-271');
  await eventually(async()=>{const packet=await session();return packet.presence.counts.dirty>0;},'Cancel must restore normal task input reporting');
  await jsonInput.fill('');await dialog.getByRole('button',{name:'返回稿件',exact:true}).click();
  const finalText=dirtyText+'取消停机后，作者又保存了一行候选文字。\n';
  await one.locator('#text').fill(finalText);
  const finalSaved=one.waitForResponse(response=>response.url().endsWith('/api/save'));
  await one.locator('#save').click();const finalReceipt=await(await finalSaved).json();
  assert.equal(finalReceipt.ok,true);assert.equal(finalReceipt.formal_changed,false);
  await eventually(async()=>{const packet=await session();return packet.presence.state==='clean';},'The final stop begins only after both pages finish saving or clearing input');
  // Exercise the ordinary CLI, whose prepare/poll/stop handshake must obtain
  // new browser ACKs rather than reuse the cancelled challenge.
  const stopped=await new Promise((resolve,reject)=>execFile(data.python,['-B',data.tool,'workbench-stop','--book',data.book,'--saved'],{timeout:20000,encoding:'utf8'},(error,stdout,stderr)=>error?reject(Error(stdout+stderr+error.message)):resolve(JSON.parse(stdout))));
  assert.equal(stopped.ok,true);assert.equal(stopped.stop_requested,true);
  assert.equal(stopped.fresh_verified,true);assert.equal(stopped.automatic_verified,true);
  assert.equal(stopped.manual_unknown_confirmation,false);
  assert.notEqual(stopped.fresh_stop.challenge_id,firstChallenge);
  assert.equal(stopped.fresh_stop.acknowledged_pages.length,2);
  const finalAcks=reports.filter(row=>row.payload.stop_ack?.challenge_id===stopped.fresh_stop.challenge_id);
  assert.deepEqual([...new Set(finalAcks.map(row=>row.page))].sort(),['manuscript','task']);
  for(const page of ['manuscript','task'])assert.ok(finalAcks.find(row=>row.page===page).payload.sequence>firstAcks.find(row=>row.page===page).payload.sequence);
  assert.ok(!JSON.stringify(reports).includes('BODY-PRIVATE-STOP-271'));
  assert.ok(!JSON.stringify(reports).includes('TASK-PRIVATE-STOP-271'));
  assert.deepEqual(errors,[]);assert.deepEqual([...networkOrigins],[new URL(data.url).origin]);
  console.log(JSON.stringify({ok:true,flow:'two-page-dirty-task-metadata-freeze-ack-cancel-cli-stop',pages:2,dirty_stop_denied:true,unimported_json_stop_denied:true,metadata_contains_body:false,frozen_ack_pages:2,cancel_restores_editing:true,final_stop_fresh_verified:true,final_stop_automatic_verified:true,cancelled_challenge_reused:false,presence_coverage:initial.presence.coverage,legacy_services_covered:false,candidate_path:finalReceipt.path,final_text:finalText}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(
            [node, '-'], input='const data=' + json.dumps(data, ensure_ascii=False) + ';\n' + program,
            capture_output=True, text=True, timeout=100,
        )
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(receipt['ok'])
        thread.join(3)
        self.assertFalse(thread.is_alive(), 'Successful normal stop must stop the isolated server loop')
        self.assertEqual(before, fixture.authoritative_state())
        self.assertEqual(candidate_bytes, original_candidate.read_bytes())
        saved_path = root / receipt.pop('candidate_path')
        saved_bytes = saved_path.read_bytes()
        self.assertEqual(saved_bytes.decode(), chapter_heading + '\n' + receipt.pop('final_text'))
        self.assertNotEqual(saved_path, original_candidate)
        receipt.update({
            'authoritative_database_and_exports_unchanged': True,
            'authoritative_database_sha256': hashlib.sha256(before['core_bytes']).hexdigest(),
            'publishing_database_sha256': hashlib.sha256(before['publishing_bytes']).hexdigest(),
            'formal_export_sha256': [hashlib.sha256(value).hexdigest() for value in before['chapters'].values()],
            'existing_candidate_unchanged': True,
            'saved_working_candidate_preserved': True,
            'saved_candidate_sha256': hashlib.sha256(saved_bytes).hexdigest(),
            'synthetic_input_only': True,
            'external_platform_access': False,
            'assistant_model_execution': False,
        })
        report_dir = os.environ.get('STORY_STOP_BROWSER_REPORT_DIR')
        if report_dir:
            target = Path(report_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / 'chrome-stop-fresh.json').write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
            )


if __name__ == '__main__':
    unittest.main()
