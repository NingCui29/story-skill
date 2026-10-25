"""Only explicit successful removal discards that book's unsaved status mark."""
import unittest

import test_workbench as base
import test_workbench_shelf_feedback as feedback


class WorkbenchManagementStatusCleanupTests(unittest.TestCase):
    def js(self, checks):
        fixture = feedback.WorkbenchShelfFeedbackTests('runTest')
        fixture.setUp()
        management = base.story.workbench._library_management_script()
        receipt = management[management.index('function syncUndoRemoval('):
                             management.index('function rememberShelfBook(')]
        remove = management[management.index('function showRemoveBook('):
                            management.index('function decorateBookManagement(')]
        fixture.source += r'''
let managerGeneration=0,undoRemoval=null,undoRemovalBusy=false;const manager={open:false,close(){this.open=false;}},actions=new Map(),fields=[];
function openManager(){manager.open=true;fields.length=0;actions.clear();return ++managerGeneration;}
function managementText(){}function managementField(){const field={value:'',checked:false};fields.push(field);return field;}
function managementButton(label,action){actions.set(label,action);}
async function managementRequest(action,data){return await requestShelfCatalog(action,data);}
''' + receipt + remove
        fixture.js(checks)

    def test_confirmed_remove_discards_only_target_and_does_not_recapture_its_old_node(self):
        self.js(r'''
render({books});chooseStatus('连载稿','completed');const removedDraft=shelfStatusDrafts.get('连载稿');
statusSelect('完本稿').value='serializing'; // Capture even a select change whose event has not fired yet.
showRemoveBook(books[0]);response={books:books.slice(1),removed:true,undo_id:'synthetic-undo'};await actions.get('移出书架')();
assert.equal(shelfStatusDrafts.has('连载稿'),false);assert.equal(statusSelect('连载稿'),undefined);assert.equal(shelfStatusDrafts.get('完本稿').value,'serializing');assert.equal(undoRemoval.id,'synthetic-undo');assert.equal(undoRemoval.title,'连载稿');assert.equal($('undo-remove').hidden,false);
render({books});assert.equal(statusSelect('连载稿').value,'serializing','re-adding the book must not restore its discarded draft');assert.equal(statusSelect('完本稿').value,'serializing');assert.equal(shelfStatusDrafts.has('连载稿'),false);assert.notStrictEqual(shelfStatusDrafts.get('连载稿'),removedDraft);
''')

    def test_normal_catalog_omission_preserves_hidden_draft_and_return_restores_it(self):
        self.js(r'''
render({books});chooseStatus('连载稿','completed');const original=shelfStatusDrafts.get('连载稿');render({books:books.slice(1)});
assert.strictEqual(shelfStatusDrafts.get('连载稿'),original);assert.equal(shelfRefreshBlocked(),true);
render({books});assert.equal(statusSelect('连载稿').value,'completed');assert.strictEqual(shelfStatusDrafts.get('连载稿'),original);
''')

    def test_failed_or_unconfirmed_remove_keeps_drafts(self):
        self.js(r'''
render({books});chooseStatus('连载稿','completed');chooseStatus('完本稿','serializing');const target=shelfStatusDrafts.get('连载稿'),other=shelfStatusDrafts.get('完本稿');showRemoveBook(books[0]);failure='书架写入失败';
await assert.rejects(actions.get('移出书架')(),/书架写入失败/);assert.strictEqual(shelfStatusDrafts.get('连载稿'),target);assert.strictEqual(shelfStatusDrafts.get('完本稿'),other);
failure=null;response={books:books.slice(1),removed:false};await actions.get('移出书架')();assert.strictEqual(shelfStatusDrafts.get('连载稿'),target);assert.strictEqual(shelfStatusDrafts.get('完本稿'),other);
''')

    def test_confirmed_trash_clears_target_even_if_shelf_registration_removal_failed(self):
        self.js(r'''
render({books});chooseStatus('连载稿','completed');chooseStatus('完本稿','serializing');const other=shelfStatusDrafts.get('完本稿');response={title:'连载稿',root:books[0].root,confirmation:{synthetic:true}};await showTrashBook(books[0]);fields[0].value='连载稿';fields[1].checked=true;
response={books:[{...books[0],available:false},...books.slice(1)],trashed:true,shelf_removed:false,warning:'已回收，登记尚未移除'};await actions.get('移入系统回收站')();
assert.equal(shelfStatusDrafts.has('连载稿'),false);assert.strictEqual(shelfStatusDrafts.get('完本稿'),other);assert.ok($('message').textContent.includes('登记尚未移除'));assert.equal(statusSelect('连载稿'),undefined);
''')

    def test_unconfirmed_trash_and_missing_user_confirmation_do_not_discard_any_draft(self):
        self.js(r'''
render({books});chooseStatus('连载稿','completed');const draft=shelfStatusDrafts.get('连载稿');response={title:'连载稿',root:books[0].root,confirmation:{synthetic:true}};await showTrashBook(books[0]);
await assert.rejects(actions.get('移入系统回收站')(),/完整书名/);assert.strictEqual(shelfStatusDrafts.get('连载稿'),draft);assert.equal(calls,1);
fields[0].value='连载稿';fields[1].checked=true;response={books,trashed:false};await actions.get('移入系统回收站')();assert.strictEqual(shelfStatusDrafts.get('连载稿'),draft);
''')


if __name__ == '__main__':
    unittest.main()
