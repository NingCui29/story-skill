"""Reproduce recovery-center pagination gaps using production recovery saving.

Leaves only a synthetic /tmp book for independent inspection; no repo writes.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

ROOT = Path('/Users/cuining/Documents/GitHub/story-skill')
sys.path.insert(0, str(ROOT / 'tests'))
import test_workbench as fixture

story, w = fixture.story, fixture.story.workbench
scratch = Path(tempfile.mkdtemp(prefix='story-recovery-coverage-')).resolve()
root = scratch / 'book'
story.Book.create(root, '恢复分页探针', 'long')
(root / '素材.md').write_text('独立探针的原始材料。', encoding='utf-8')
history = root / '.story/drafts/000-历史归档'
history.mkdir(parents=True)
for index in range(5001):
    (history / f'{index:05}.md').write_text('历史候选材料。', encoding='utf-8')
source = w._editor_document(root, 'file:素材.md')
saved = w._editor_save(root, {**source, 'text': '由生产恢复入口保存的实际恢复正文。',
                             'recovery_key': 'independent-key-20261023'}, recovery=True)
restored = w._editor_document(root, saved['id'])
assert restored['text'] == '由生产恢复入口保存的实际恢复正文。'
assert restored['source'] == 'file:素材.md'
first = w._editor_catalog(root)
more = w._editor_catalog(root, material_cursor=first['materials']['cursor'], material_more=True)
first_recoveries = [row for row in first['files'] if row['category'] == '自动恢复']
more_recoveries = [row for row in more['files'] if row['category'] == '自动恢复']
assert not first['materials']['complete'] and first['materials']['has_more']
assert not first_recoveries and any(row['id'] == saved['id'] for row in more_recoveries)
try:
    shelf = story.workbench_archives.recovery_catalog(root)
    shelf_result = {'ok': True, 'count': shelf['count']}
except story.StoryError as error:
    shelf_result = {'error': error.code, 'message': str(error), 'details': error.details}
assert shelf_result['error'] == 'workbench_scan_limit'
tools_script = w._editor_tools_script()
handler = tools_script[tools_script.index("$('recoveries-open')"):tools_script.index("$('writing-task')")]
program = '''
import assert from 'node:assert/strict';
const elements={},$=id=>elements[id]||(elements[id]={});
const content={children:[],append(node){this.children.push(node);}};
const editorDialog=()=>({dialog:{close(){}},content});
const document={createElement:()=>({textContent:'',append(){}})};
const requests=[];
const response=__RESPONSE__;
const call=async(action,payload)=>{requests.push({action,payload});return response;};
const displayTitle=row=>row.title;
const openDoc=()=>{throw Error('No recovery can be selected from an empty result');};
__HANDLER__
await $('recoveries-open').onclick();
assert.equal(requests.length,1);
assert.ok(content.children[0].textContent.startsWith('没有可列出的磁盘恢复记录'));
console.log(JSON.stringify({requests,displayed:content.children.map(row=>row.textContent),
  ignored_incomplete_materials:response.materials,ignored_warnings:response.warnings}));
'''.replace('__RESPONSE__', json.dumps({'files': first['files'], 'materials': first['materials'],
                                      'warnings': first['warnings']}, ensure_ascii=False)).replace('__HANDLER__', handler)
node = '/Users/cuining/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
process = subprocess.run([node, '--input-type=module', '-'], input=program,
                         capture_output=True, text=True, timeout=30)
assert process.returncode == 0, process.stderr
ui = json.loads(process.stdout)
recovery_path = root / saved['path']
metadata = json.loads(Path(str(recovery_path) + '.meta.json').read_text(encoding='utf-8'))
assert metadata['recovery'] is True and metadata['content_sha256'] == hashlib.sha256(recovery_path.read_bytes()).hexdigest()
report = {'root': str(root), 'created_with': 'story.workbench._editor_save(..., recovery=True)',
          'source_id': source['id'], 'recovery_id': saved['id'], 'recovery_path': str(recovery_path),
          'recovery_metadata': metadata, 'material_files': 5001,
          'actual_author_limit': w.AUTHOR_LIMIT,
          'draft_directory_order': [path.name for path in (root / '.story/drafts').iterdir()],
          'first_scan': first['materials'], 'first_recoveries': first_recoveries,
          'next_scan': more['materials'], 'next_recoveries': more_recoveries,
          'editor_ui': ui, 'shelf_recovery_center': shelf_result,
          'direct_open_text': restored['text'], 'files_lost': False}
w._material_discard(first['materials']['cursor'])
print(json.dumps(report, ensure_ascii=False, indent=2))
