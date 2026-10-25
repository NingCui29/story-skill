"""Production shelf DOM/CSS must keep the resume caption clear of progress."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_workbench as base


class WorkbenchResumeShelfLayoutTests(unittest.TestCase):
    def test_actual_grid_rows_do_not_overlap_at_desktop_tablet_or_phone_width(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required for real browser layout acceptance')
        candidates = [os.environ.get('WORKBENCH_LAYOUT_CHROME', ''),
                      shutil.which('google-chrome') or '', shutil.which('chromium') or '',
                      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        if sys.platform == 'win32':
            candidates += [str(Path(os.environ.get(name, '')) / 'Google/Chrome/Application/chrome.exe')
                           for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
        executable = next((value for value in candidates if value and Path(value).is_file()), None)
        if executable is None:
            self.skipTest('An installed Chrome executable is required; no browser is downloaded')
        html = base.story.workbench._library_page('isolated-layout-token').decode()
        book = {'key': 'synthetic-resume', 'book_id': 'synthetic-book-id', 'title': '合成继续编辑布局',
                'root': '/synthetic/never-opened', 'kind': 'long', 'kind_verified': True, 'available': True,
                'classification': {'status': 'recorded', 'tags': ['都市', '悬疑']},
                'progress': {'verified': True, 'formal_chapters': 0},
                'writing_status': {'verified': False, 'value': 'unknown'},
                'resume': {'status': 'ready', 'document': {'title': '继续定位材料与需要折行的合成文稿标题' * 3,
                            'kind': 'material', 'chapter': None}, 'updated_at': '2026-10-20T12:00:00Z',
                           'buffer_dirty': False, 'note': '只恢复已保存文稿位置。'}}
        ordinary = {**book, 'key': 'synthetic-ordinary', 'book_id': 'ordinary-book-id',
                    'title': '合成普通作品', 'resume': {'status': 'empty'}}
        program = r'''
const fs=require('fs'),path=require('path'),assert=require('assert');
let playwright;try{playwright=require(require.resolve('playwright',{paths:[process.cwd(),path.resolve(path.dirname(process.execPath),'..')]}));}
catch(error){process.stdout.write('PLAYWRIGHT_UNAVAILABLE');process.exit(77);}
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 const browser=await playwright.chromium.launch({executablePath:fixture.executable,headless:true});
 try{
  const page=await browser.newPage(),results=[];
  // Every request is fulfilled or rejected here. No real server/profile or
  // network resource is used, and the supplied book paths are never opened.
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.origin!=='http://127.0.0.1:49178')return route.abort();
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fixture.html});
   if(url.pathname==='/api/books')return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,books:fixture.books})});
   return route.fulfill({status:404,contentType:'application/json',body:'{}'});
  });
  for(const width of [1280,820,390]){
   await page.setViewportSize({width,height:900});await page.goto('http://127.0.0.1:49178/');
   await page.waitForSelector('.book-management');
   const value=await page.evaluate(()=>[...document.querySelectorAll('.book')].map(card=>{
    const item=selector=>{const node=card.querySelector(selector);if(!node)return null;const rect=node.getBoundingClientRect();return {row:getComputedStyle(node).gridRowStart,top:rect.top,bottom:rect.bottom};};
    return {resume:item('.book-resume'),progress:item('.book-progress'),status:item('.book-status'),button:item(':scope > button'),management:item('.book-management')};
   }));
   const ready=value.find(card=>card.resume),ordinary=value.find(card=>!card.resume);assert.ok(ready);assert.ok(ordinary);
   assert.equal(ready.resume.row,'3');assert.equal(ready.progress.row,'4');assert.equal(ready.status.row,'4');
   assert.equal(ready.button.row,'5');assert.equal(ready.management.row,'5');
   assert.ok(ready.resume.bottom<=ready.progress.top,JSON.stringify({width,ready}));
   assert.ok(ready.resume.bottom<=ready.status.top,JSON.stringify({width,ready}));
   assert.ok(ready.progress.bottom<=ready.button.top,JSON.stringify({width,ready}));
   assert.equal(ordinary.progress.row,'3');assert.equal(ordinary.status.row,'3');assert.equal(ordinary.button.row,'4');
   results.push({width,cards:value});
  }
  process.stdout.write(JSON.stringify(results));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', program],
                                input=json.dumps({'html': html, 'books': [book, ordinary], 'executable': executable}),
                                text=True, capture_output=True, timeout=45)
        if result.returncode == 77 and 'PLAYWRIGHT_UNAVAILABLE' in result.stdout:
            self.skipTest('Playwright package required; no dependency is installed by this test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([row['width'] for row in json.loads(result.stdout)], [1280, 820, 390])


if __name__ == '__main__':
    unittest.main()
