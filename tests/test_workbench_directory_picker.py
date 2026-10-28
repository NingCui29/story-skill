"""Exercise real folder browsing and add/create forms using isolated books only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_workbench as base
import test_workbench_management as management


class WorkbenchDirectoryPickerTests(unittest.TestCase):
    setUp = management.WorkbenchManagementHTTPTests.setUp
    serve = management.WorkbenchManagementHTTPTests.serve
    post = management.WorkbenchManagementHTTPTests.post

    def test_browse_is_paginated_read_only_and_returns_directories_not_files(self):
        folder = self.root / '中文 存放位置'
        folder.mkdir()
        for number in range(105):
            (folder / f'作品 {number:03}').mkdir()
        (folder / '正文.md').write_text('不能作为文件夹选择。')
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        status, first = self.post('directories', {'path': str(folder)})
        self.assertEqual(status, 200, first)
        self.assertEqual(first['total'], 105)
        self.assertEqual(len(first['directories']), 100)
        self.assertEqual(first['parent'], str(self.root))
        status, last = self.post('directories', {'path': first['path'], 'offset': first['next_offset']})
        self.assertEqual(status, 200, last)
        self.assertEqual(len(last['directories']), 5)
        self.assertIsNone(last['next_offset'])
        self.assertEqual(len({r['path'] for r in first['directories'] + last['directories']}), 105)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertEqual(self.post('books')[1]['books'], [])

    def test_browse_auth_and_invalid_paths_fail_before_disclosing_names(self):
        for headers in ({'Origin': 'https://example.invalid'}, {'X-Story-Token': ''}, {'Host': 'example.invalid'}):
            with patch.object(self.w.os, 'scandir', side_effect=AssertionError('must not scan')):
                self.assertEqual(self.post('directories', {'path': str(self.root)}, headers)[0], 403)
        for payload in ({'path': '../relative'}, {'path': 1}, {'offset': True}, {'path': '\x00'}, {'unexpected': True}):
            self.assertEqual(self.post('directories', payload)[0], 409)
        status, result = self.post('directories', {'path': str(self.root / '不存在')})
        self.assertEqual(status, 409)
        self.assertEqual(result['error'], 'workbench_directory_unavailable')
        with patch.object(self.w.os, 'scandir', side_effect=PermissionError()):
            status, result = self.post('directories', {'path': str(self.root)})
        self.assertEqual(status, 409)
        self.assertIn('访问权限', result['message'])

    def test_browser_select_cancel_create_and_native_callbacks(self):
        node = shutil.which('node')
        chrome = next((p for p in [os.environ.get('WORKBENCH_LAYOUT_CHROME', ''),
                      shutil.which('google-chrome'), shutil.which('chromium'),
                      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if p and Path(p).is_file()), None)
        if not node or not chrome:
            self.skipTest('Installed Node and Chrome required')
        parent = self.root / '中文 存放位置'
        parent.mkdir()
        existing = parent / '已有作品'
        base.story.Book.create(existing, '已有作品', 'long')
        program = r'''
const fs=require('fs'),path=require('path'),assert=require('assert');
let playwright;try{playwright=require(require.resolve('playwright',{paths:[process.cwd(),path.resolve(path.dirname(process.execPath),'..')]}));}
catch{process.stdout.write('PLAYWRIGHT_UNAVAILABLE');process.exit(77);}
const f=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 const browser=await playwright.chromium.launch({executablePath:f.chrome,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:850}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===f.url?route.continue():route.abort());
  await page.goto(f.url+'/');await page.locator('#add-book-panel summary').click();
  await page.locator('#book-path').fill(f.parent);await page.locator('#choose-book-directory').click();
  await page.locator('#directory-items button').filter({hasText:'已有作品'}).click();
  await page.waitForFunction(value=>document.getElementById('directory-path').value===value,f.existing);
  await page.locator('#directory-select').click();assert.equal(await page.locator('#book-path').inputValue(),f.existing);
  await page.locator('#choose-book-directory').click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('#directory-browser').isVisible(),false);assert.equal(await page.locator('#book-path').inputValue(),f.existing);
  assert.equal(await page.locator('.book').count(),0,'selection must not register');
  await page.locator('#add').click();await page.waitForSelector('.book');
  await page.locator('#new-book').click();await page.getByLabel('书名',{exact:true}).fill('中文 新作品');
  await page.locator('.directory-pick').click();await page.locator('#directory-path').fill(f.parent);await page.locator('#directory-go').click();
  await page.waitForFunction(value=>document.getElementById('directory-path').value===value&&!document.getElementById('directory-select').disabled,f.parent);
  if(f.report)await page.screenshot({path:path.join(f.report,'web-directory-browser.png')});
  await page.locator('#directory-select').click();
  assert.equal(await page.getByLabel('存放位置',{exact:true}).inputValue(),f.parent);
  assert.equal(await page.getByLabel('作品文件夹名',{exact:true}).inputValue(),'中文 新作品');
  assert.ok((await page.locator('#manager-body').innerText()).includes(path.join(f.parent,'中文 新作品')));
  assert.ok(!fs.existsSync(path.join(f.parent,'中文 新作品')),'selection must not create');
  if(f.report)await page.screenshot({path:path.join(f.report,'create-directory-preview.png')});
  await page.getByRole('button',{name:'创建并加入书架',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.book').length===2);
  assert.ok(fs.existsSync(path.join(f.parent,'中文 新作品','.story','state.sqlite3')));
  await page.locator('#new-book').click();await page.getByLabel('书名',{exact:true}).fill('取消作品');
  await page.locator('.directory-pick').click();await page.locator('#directory-cancel').click();
  assert.equal(await page.getByLabel('存放位置',{exact:true}).inputValue(),'');assert.ok(!fs.existsSync(path.join(f.parent,'取消作品')));
  await page.getByLabel('存放位置',{exact:true}).fill(f.parent);await page.getByLabel('作品文件夹名',{exact:true}).fill('../越界');
  await page.getByRole('button',{name:'创建并加入书架',exact:true}).click();assert.ok((await page.locator('#manager-error').innerText()).includes('文件夹名'));
  // Native results only fill the original, unchanged form; never auto-submit.
  const native=await page.evaluate(async()=>{
   const input=document.getElementById('book-path');let purpose,resolvePick;
   globalThis.webkit={messageHandlers:{storyDirectory:{postMessage:request=>{purpose=request.purpose;return new Promise(resolve=>resolvePick=resolve);}}}};
   input.value='原值';const cancelled=fillDirectoryField(input,'existing-book',()=>true,()=>{});resolvePick({cancelled:true});await cancelled;if(input.value!=='原值')throw Error('cancel changed path');
   const pending=fillDirectoryField(input,'existing-book',()=>true,()=>{});input.value='后来输入';resolvePick({path:'/不得覆盖',cancelled:false});await pending;if(input.value!=='后来输入')throw Error('stale callback overwrote input');
   let current=true;const obsolete=fillDirectoryField(input,'create-parent',()=>current,()=>{});current=false;resolvePick({path:'/已关闭表单',cancelled:false});await obsolete;if(input.value!=='后来输入')throw Error('closed form changed');
   const selected=fillDirectoryField(input,'existing-book',()=>true,()=>{});resolvePick({path:'/中文 空格/作品',cancelled:false});await selected;if(input.value!=='/中文 空格/作品')throw Error('mac path not filled');delete globalThis.webkit;
   let listener,request;globalThis.chrome={webview:{addEventListener:(_,fn)=>listener=fn,removeEventListener:()=>listener=null,postMessage:value=>request=value}};
   const windows=chooseWorkbenchDirectory('create-parent');listener({data:{kind:'directory-result',id:request.id,cancelled:false,path:'C:\\中文 目录'}});const result=await windows;
   if(result!=='C:\\中文 目录'||listener!==null)throw Error('windows reply not matched');delete globalThis.chrome;
   return {purpose,windows:directoryChildPath(result,'新作品'),root:directoryChildPath('/','作品')};
  });
  assert.equal(native.windows,'C:\\中文 目录\\新作品');assert.equal(native.root,'/作品');
  await page.getByRole('button',{name:'返回书架',exact:true}).click();
  for(const width of [390,820,1280]){
   await page.setViewportSize({width,height:900});await page.locator('#new-book').click();
   const bounds=await page.locator('#book-manager').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1);
   await page.locator('.directory-pick').click();const picker=await page.locator('#directory-browser').boundingBox();assert.ok(picker.x>=0&&picker.x+picker.width<=width+1);
   await page.locator('#directory-cancel').click();await page.getByRole('button',{name:'返回书架',exact:true}).click();
  }
  assert.deepEqual(errors,[]);process.stdout.write(JSON.stringify({ok:true,native,books:await page.locator('.book').count()}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', program], input=json.dumps({
            'chrome': chrome, 'url': f'http://127.0.0.1:{self.shelf.server_port}',
            'parent': str(parent), 'existing': str(existing), 'report': os.environ.get('WORKBENCH_DIRECTORY_REPORT')}),
            capture_output=True, text=True, timeout=90)
        if result.returncode == 77:
            self.skipTest('Playwright package required')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_browser_pending_directory_reads_preserve_path_and_unlock_only_current_request(self):
        node = shutil.which('node')
        chrome = next((p for p in [os.environ.get('WORKBENCH_LAYOUT_CHROME', ''),
                      shutil.which('google-chrome'), shutil.which('chromium'),
                      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if p and Path(p).is_file()), None)
        if not node or not chrome:
            self.skipTest('Installed Node and Chrome required')
        parent = self.root / '中文 存放位置'
        parent.mkdir()
        program = r'''
const fs=require('fs'),path=require('path'),assert=require('assert');
let playwright;try{playwright=require(require.resolve('playwright',{paths:[process.cwd(),path.resolve(path.dirname(process.execPath),'..')]}));}
catch{process.stdout.write('PLAYWRIGHT_UNAVAILABLE');process.exit(77);}
const f=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 const browser=await playwright.chromium.launch({executablePath:f.chrome,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:850}}),errors=[],pending=[];
  page.on('pageerror',error=>errors.push(error.message));
  // Hold actual directory responses after the server has read them. Observe
  // JSON consumption in a later task, after the UI's promise continuation.
  await page.addInitScript(()=>{
   globalThis.directoryResponsesConsumed=0;const original=globalThis.fetch;
   globalThis.fetch=async(...args)=>{
    const response=await original(...args);
    if(String(args[0]).endsWith('/api/directories')){
     const json=response.json.bind(response);
     response.json=async()=>{try{return await json();}finally{setTimeout(()=>directoryResponsesConsumed++,0);}};
    }return response;
   };
  });
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==f.url)return route.abort();
   if(!url.pathname.endsWith('/api/directories'))return route.continue();
   const response=await route.fetch(),entry={payload:request.postDataJSON(),release:null};
   const gate=new Promise(resolve=>entry.release=resolve);pending.push(entry);await gate;
   await route.fulfill({response});
  });
  async function received(count){
   const deadline=Date.now()+7000;
   while(pending.length<count&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
   assert.equal(pending.length,count,'directory request must reach the held server response');
   return pending[count-1];
  }
  async function consumed(count){await page.waitForFunction(count=>directoryResponsesConsumed>=count,count,{timeout:7000});}
  const input=page.locator('#directory-path'),choose=page.locator('#directory-select');
  const readOnly=()=>input.evaluate(element=>element.readOnly);
  await page.goto(f.url+'/');await page.locator('#new-book').click();await page.getByLabel('书名',{exact:true}).fill('延迟目录合成作品');
  await page.locator('.directory-pick').click();const cancelled=await received(1);
  assert.equal(await readOnly(),true);assert.equal(await choose.isDisabled(),true);
  await page.locator('#directory-cancel').click();assert.equal(await readOnly(),false);
  assert.equal(await page.getByLabel('存放位置',{exact:true}).inputValue(),'');
  await page.locator('.directory-pick').click();const superseded=await received(2);
  assert.equal(await readOnly(),true);
  cancelled.release();await consumed(1);
  assert.equal(await page.locator('#directory-browser').isVisible(),true);
  assert.equal(await readOnly(),true,'a cancelled request cannot unlock a reopened picker');
  // Navigation remains available while the input is protected. The older
  // request's finally must not unlock the replacement request either.
  await page.locator('#directory-home').click();const current=await received(3);
  superseded.release();await consumed(2);assert.equal(await readOnly(),true);
  current.release();await consumed(3);await page.waitForFunction(()=>!document.getElementById('directory-path').readOnly);
  assert.equal(await choose.isDisabled(),false);
  await input.fill(f.missing);await page.locator('#directory-go').click();const failed=await received(4);
  assert.equal(await readOnly(),true);assert.equal(await choose.isDisabled(),true);
  failed.release();await consumed(4);await page.waitForFunction(()=>!document.getElementById('directory-path').readOnly);
  assert.equal(await choose.isDisabled(),true);assert.ok((await page.locator('#directory-note').innerText()).includes('无法读取'));
  // A complete absolute path can now replace the input without the delayed
  // initial HOME response moving the selection to the end during fill.
  await input.fill(f.parent);await page.locator('#directory-go').click();const selected=await received(5);
  assert.equal(selected.payload.path,f.parent);assert.equal(await readOnly(),true);
  selected.release();await consumed(5);await page.waitForFunction(()=>!document.getElementById('directory-path').readOnly);
  assert.equal(await input.inputValue(),f.parent);assert.equal(await choose.isDisabled(),false);
  if(f.report)await page.screenshot({path:path.join(f.report,'directory-pending-readback.png')});
  await choose.click();assert.equal(await page.getByLabel('存放位置',{exact:true}).inputValue(),f.parent);
  assert.equal(await page.locator('.book').count(),0,'read and selection cannot register or create a book');
  assert.ok(!fs.existsSync(path.join(f.parent,'延迟目录合成作品')));
  assert.deepEqual(errors,[]);process.stdout.write(JSON.stringify({ok:true,held_responses:5,cancel_unlocks:true,old_response_cannot_unlock:true,navigation_while_pending:true,success_and_error_unlock:true,absolute_path_preserved:true}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', program], input=json.dumps({
            'chrome': chrome, 'url': f'http://127.0.0.1:{self.shelf.server_port}',
            'parent': str(parent), 'missing': str(self.root / '不存在'),
            'report': os.environ.get('WORKBENCH_DIRECTORY_REPORT')}),
            capture_output=True, text=True, timeout=90)
        if result.returncode == 77:
            self.skipTest('Playwright package required')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
