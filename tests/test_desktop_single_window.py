"""Execute the native page-cache policy without windows, runners or services."""
import unittest

import test_desktop_native as native


PAGE_FIXTURE = r'''
final class FixturePage {
    var text = "合成原始文字"
    var value = "合成原始文字"
    var saving = false
    var scroll = 0
    var selection = 0
}
let editor = URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
'''


class DesktopSingleWindowTests(unittest.TestCase):
    foundation = native.DesktopNativeTests.foundation

    def test_repeated_address_and_chapter_hash_reuse_original_page_without_create(self):
        self.foundation(PAGE_FIXTURE + r'''
let cache = WorkbenchPageCache<FixturePage>()
var creates = 0
let first = cache.resolve(editor) { creates += 1; return FixturePage() }!
first.value = "第一页未保存的合成编辑"
first.scroll = 432
first.selection = 17
for address in [editor, URL(string:editor.absoluteString + "#formal%3A1")!,
                URL(string:editor.absoluteString + "#formal%3A2")!, editor] {
    let repeated = cache.resolve(address) { creates += 1; return FixturePage() }!
    precondition(repeated === first)
    precondition(repeated.value == "第一页未保存的合成编辑")
    precondition(repeated.scroll == 432 && repeated.selection == 17)
}
precondition(creates == 1 && cache.count == 1 && cache.pages.count == 1)
print("repeated page cache passed")
''')

    def test_distinct_tokens_and_ports_retain_independent_page_objects(self):
        self.foundation(PAGE_FIXTURE + r'''
let cache = WorkbenchPageCache<FixturePage>()
let secondURL = URL(string:"http://127.0.0.1:12345/ZYXWVUTSRQPONMLKJIHGFEDCBA543210/")!
let otherPort = URL(string:"http://127.0.0.1:12346/abcdefghijklmnopqrstuvwxyz012345/")!
let first = cache.resolve(editor) { FixturePage() }!
let second = cache.resolve(secondURL) { FixturePage() }!
let third = cache.resolve(otherPort) { FixturePage() }!
precondition(first !== second && first !== third && second !== third)
first.value = "第一作品未保存文字"
second.value = "第二作品未保存文字"
third.value = "另一服务的未保存文字"
precondition(cache.resolve(editor) { FixturePage() } === first)
precondition(cache.resolve(secondURL) { FixturePage() } === second)
precondition(cache.resolve(otherPort) { FixturePage() } === third)
precondition(cache.count == 3 && cache.pages.count == 3)
precondition(cache.pages.contains { $0 === first })
precondition(cache.pages.contains { $0 === second })
precondition(cache.pages.contains { $0 === third })
print("independent page cache passed")
''')

    def test_unknown_blank_external_and_invalid_urls_never_create_a_page(self):
        self.foundation(PAGE_FIXTURE + r'''
let cache = WorkbenchPageCache<FixturePage>()
var creates = 0
for address in ["about:blank", "file:///tmp/editor.html", "https://example.com/editor/",
                "http://127.0.0.1:8765/", "http://localhost:12345/abcdefghijklmnopqrstuvwxyz012345/",
                "http://127.0.0.1/abcdefghijklmnopqrstuvwxyz012345/",
                "http://127.0.0.1:12345/short/",
                "http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/extra",
                "http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/?url=other",
                "http://user:secret@127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/",
                "http://127.0.0.1:12345/%61bcdefghijklmnopqrstuvwxyz012345/"] {
    let result = cache.resolve(URL(string:address)!) { creates += 1; return FixturePage() }
    precondition(result == nil)
}
precondition(creates == 0 && cache.count == 0 && cache.pages.isEmpty)
print("invalid page cache passed")
''')

    def test_cached_pages_remain_alive_and_enumerable_with_dirty_and_saving_state(self):
        self.foundation(PAGE_FIXTURE + r'''
let cache = WorkbenchPageCache<FixturePage>()
weak var hidden: FixturePage?
do {
    let first = cache.resolve(editor) { FixturePage() }!
    first.value = "非当前作品未保存的合成文字"
    hidden = first
}
let secondURL = URL(string:"http://127.0.0.1:12345/ZYXWVUTSRQPONMLKJIHGFEDCBA543210/")!
let second = cache.resolve(secondURL) { FixturePage() }!
second.saving = true
precondition(hidden != nil)
let pending = cache.pages.filter { $0.saving || $0.value != $0.text }
precondition(pending.count == 2)
precondition(pending.contains { $0 === hidden })
precondition(pending.contains { $0 === second })
precondition(cache.resolve(editor) { FixturePage() } === hidden)
precondition(hidden?.value == "非当前作品未保存的合成文字")
print("retained pending page cache passed")
''')


if __name__ == '__main__':
    unittest.main()
