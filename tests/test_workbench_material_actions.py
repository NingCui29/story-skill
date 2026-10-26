"""Post-creation actions preserve identity and remain explicit."""
import unittest
from tests import test_workbench_management_open_results as ui


class MaterialActionsTests(unittest.TestCase):
    js = ui.WorkbenchManagementOpenResultsTests.js

    def test_success_offers_open_without_opening_automatically(self):
        self.js(r'''
const book={key:'a',book_id:'identity-a',title:'新作品'};shelfBooks=[book];offerOpenBook(book);
const button=$('message').children[0];assert.equal(button.textContent,'打开作品');assert.equal(requests.length,0);
reply=async()=>({url:'http://127.0.0.1/isolated-new-book/'});
await button.onclick();assert.equal(requests.length,1);assert.equal(requests[0].expected_book_id,'identity-a');
assert.deepEqual(openedURLs,['http://127.0.0.1/isolated-new-book/']);assert.equal(button.disabled,false);
''')

    def test_changed_identity_blocks_old_receipt_open_without_submitting(self):
        self.js(r'''
const book={key:'a',book_id:'identity-a',title:'新作品'};shelfBooks=[book];offerOpenBook(book);
const button=$('message').children[0];button.after=node=>$('message').append(node);
shelfBooks=[{...book,book_id:'replaced-identity'}];await button.onclick();
assert.equal(requests.length,0);assert.equal(openedURLs.length,0);assert.ok($('message').textContent.includes('作品登记已变化'));
''')


if __name__=='__main__':unittest.main()
