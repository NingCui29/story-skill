"""Optional real Chromium flow against an isolated loopback book."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import test_workbench as base


class AssistantTaskBrowserTests(unittest.TestCase):
    def test_task_return_evidence_review_and_reload_preserve_manuscript(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        probe = subprocess.run([node, '-e', "require('playwright')"], capture_output=True)
        if probe.returncode:
            self.skipTest('Optional Playwright runtime unavailable')
        fixture = base.WorkbenchTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root, book = fixture.root, fixture.book
        before = fixture.authoritative_state()
        path = '.story/drafts/第2章 回流候选.md'
        text = '第2章 雨夜 & 账本#2%\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n'
        (root / path).write_bytes(text.encode())
        output = {'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
        sha = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=1').fetchone()[0]
        server = base.story.workbench.editor_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(3), server.server_close()))
        data = {'url': server.editor_url, 'output': output,
                'evidence': [{'chapter': 1, 'sha256': sha, 'quote': '沈禾把唯一的钥匙交给守门人。', 'line': 2},
                             {**output, 'quote': '沈禾把唯一的钥匙交给守门人。', 'line': 2}]}
        program = r'''
const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 let browser;
 try{browser=await chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});}
 catch(error){console.log('BROWSER_UNAVAILABLE: '+error.message.split('\n')[0]);process.exitCode=77;return;}
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(data.url+'#formal%3A2');await page.locator('#review-task').waitFor({state:'attached'});
  await page.waitForFunction(()=>!document.getElementById('review-task').disabled);
  await page.locator('#more-actions > summary').click();
  const returned=page.waitForResponse(response=>response.url().endsWith('/api/review-task'));
  await page.locator('#review-task').click();const packet=await (await returned).json();assert.equal(packet.ok,true);const task=packet.task;
  await page.locator('#assistant-task-current').click();
  const dialog=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'助手任务与结果'})});
  await dialog.getByRole('heading',{name:/审稿任务/}).waitFor();
  await dialog.getByRole('button',{name:'确认已交给助手',exact:true}).click();
  await dialog.getByRole('heading',{name:/已交/}).waitFor();
  await dialog.getByText('导入助手回执',{exact:true}).click();
  const result={...task.result_template,summary:'已核对钥匙交接，保留原文依据。',output:data.output,issues:[{id:'handoff',severity:'advice',issue:'核对两章的钥匙交接是否重复',evidence:data.evidence}]};
  await dialog.getByRole('textbox',{name:'助手结果 JSON',exact:true}).fill(JSON.stringify(result));
  await dialog.getByRole('button',{name:'核对并导入',exact:true}).click();
  await dialog.getByRole('heading',{name:/待核对/}).waitFor();
  await dialog.getByRole('button',{name:/查看依据 1/}).click();
  const proof=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'审稿原文依据'})});
  assert.ok((await proof.innerText()).includes('沈禾把唯一的钥匙交给守门人。'));await proof.getByRole('button',{name:'返回稿件',exact:true}).click();
  await dialog.getByRole('combobox',{name:'问题 handoff 的处理状态',exact:true}).selectOption('dismissed');
  await dialog.getByRole('textbox',{name:'问题 handoff 的核对说明',exact:true}).fill('已回读两侧原文，保留为待作者选择的建议。');
  await dialog.getByRole('button',{name:'保存逐项核对结果',exact:true}).click();await dialog.getByRole('heading',{name:/已核对/}).waitFor();
  assert.ok(!(await dialog.innerText()).includes('正式稿已匹配'));
  if(process.env.STORY_TASKS_BROWSER_SCREENSHOT)await page.screenshot({path:process.env.STORY_TASKS_BROWSER_SCREENSHOT,fullPage:true});
  await dialog.getByRole('button',{name:'打开返回的候选稿',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.locator('#edit').click();await page.locator('#text').fill('这段尚未保存，任务查看不得覆盖。');
  await page.locator('#more-actions > summary').click();await page.locator('#assistant-tasks-open').click();
  await page.getByRole('dialog').getByRole('button',{name:/审稿任务.*已核对/}).click();
  assert.equal(await page.locator('#text').inputValue(),'这段尚未保存，任务查看不得覆盖。');
  await page.getByRole('dialog').getByRole('button',{name:'打开返回的候选稿',exact:true}).click();
  await page.getByRole('dialog').getByText(/候选稿未打开：版本已变化/).waitFor();
  assert.equal(await page.locator('#text').inputValue(),'这段尚未保存，任务查看不得覆盖。');
  await page.getByRole('dialog').getByRole('button',{name:'返回稿件',exact:true}).click();
  page.on('dialog',dialog=>dialog.accept());await page.reload();
  await page.locator('#more-actions > summary').click();await page.locator('#assistant-tasks-open').click();
  await page.getByRole('dialog').getByRole('button',{name:/审稿任务.*已核对/}).waitFor();
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,flow:'generate-handoff-return-evidence-review-reload',formal_adopted:false}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-'], input='const data=' + json.dumps(data, ensure_ascii=False) + ';\n' + program,
                                capture_output=True, text=True, timeout=90)
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"ok":true', result.stdout)
        self.assertEqual(before, fixture.authoritative_state())


if __name__ == '__main__':
    unittest.main()
