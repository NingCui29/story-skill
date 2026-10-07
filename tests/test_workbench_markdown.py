"""Run the actual workbench Markdown renderer in Node with a text-only DOM."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "workbench_markdown_renderer", ROOT / "skills/story-skill/scripts/story_workbench.py")
workbench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workbench)


class WorkbenchMarkdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("Node.js is required for workbench Markdown regressions")
        cls.node = node
        packet = {"book": {"title": "Markdown测试", "kind": "long", "root": "/unused"},
                  "chapters": {"limit": 10}}
        page = workbench._editor_page(packet, {}, "test").decode("utf-8")
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        cls.source = script[script.index("function inlineText"):script.index("function renderProse")]

    def render_js(self, paths, test):
        documents = {"file:作者有话说/" + path: {"image": "data:image/png;base64,AA=="}
                     for path in paths}
        mock = r"""
const assert=require('node:assert/strict');
class E{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
 append(...x){this.children.push(...x);}
 replaceChildren(...x){this.children=x;this.textContent='';}
 setAttribute(k,v){this[k]=v;}
 set innerHTML(value){throw Error('Markdown must use text nodes');}
}
const document={createElement:t=>new E(t),createTextNode:t=>({tag:'text',textContent:t})};
const requests=[],opened=[],messages=[];
const openDoc=id=>opened.push(id),note=message=>messages.push(message);
const call=async(action,payload)=>{
 assert.equal(action,'open');requests.push(payload.id);
 if(!documents[payload.id])throw Error('文件未列入本书材料目录。');
 return documents[payload.id];
};
const nodes=e=>[e,...(e.children||[]).flatMap(nodes)];
const text=e=>(e.textContent||'')+(e.children||[]).map(text).join('');
async function flush(){for(let i=0;i<8;i++)await Promise.resolve();}
function render(value,path='作者有话说/第1章 附言.md'){
 const root=new E('root');renderMarkdown(root,value,path);return root;
}
"""
        program = ("const documents=" + json.dumps(documents, ensure_ascii=False) + ";\n"
                   + mock + self.source + "\n(async()=>{\n" + test
                   + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")
        result = subprocess.run([self.node, "-"], input=program, capture_output=True,
                                text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_balanced_parentheses_are_complete_image_destinations(self):
        self.render_js(["配图(1).png", "配图(彩色(1)).png"], r"""
const root=render('![图](配图(1).png)\n![图](配图(彩色(1)).png)');await flush();
assert.deepEqual(requests,['file:作者有话说/配图(1).png','file:作者有话说/配图(彩色(1)).png']);
assert.equal(nodes(root).filter(e=>e.tag==='img').length,2);
assert.equal(text(root),'');
""")

    def test_optional_titles_are_separate_from_image_destinations(self):
        self.render_js(["配图(1).png", "第1章 中文配图.png"], r"""
const root=render('![图](配图(1).png "本章配图")\n![图](配图(1).png \'本章配图\')\n![图](配图(1).png (本章配图))\n![图](<第1章 中文配图.png> "本章配图")');await flush();
assert.deepEqual(requests,['file:作者有话说/配图(1).png','file:作者有话说/配图(1).png','file:作者有话说/配图(1).png','file:作者有话说/第1章 中文配图.png']);
assert.equal(nodes(root).filter(e=>e.tag==='img').length,4);
assert.equal(text(root),'');
""")

    def test_ordinary_links_use_the_same_complete_destination(self):
        self.render_js([], r"""
const root=render('[括号](策划(1).md "标题") [空格](<第1章 中文策划.md> \'标题\') [标题](策划.md (标题))');
for(const a of nodes(root).filter(e=>e.tag==='a'))a.onclick({preventDefault(){}});
assert.deepEqual(opened,['file:作者有话说/策划(1).md','file:作者有话说/第1章 中文策划.md','file:作者有话说/策划.md']);
assert.deepEqual(requests,[]);
assert.deepEqual(messages,[]);
""")

    def test_existing_spaces_encodings_and_inline_formats_are_preserved(self):
        self.render_js(["配图/第1章 中文配图.png", "配图/第1章 #100%.png"], r"""
const root=render('正文 ![图](配图/第1章 中文配图.png) **强调** `![示例](配图/示例.png)` [附言](第1章%20附言.md)\n![图](<配图/第1章 中文配图.png>)\n![图](配图/第1章%20%23100%25.png)\n```markdown\n![示例](配图/围栏示例.png)\n```');await flush();
assert.deepEqual(requests,['file:作者有话说/配图/第1章 中文配图.png','file:作者有话说/配图/第1章 中文配图.png','file:作者有话说/配图/第1章 #100%.png']);
assert.ok(nodes(root).some(e=>e.tag==='strong'&&e.textContent==='强调'));
assert.ok(nodes(root).some(e=>e.tag==='code'&&e.textContent==='![示例](配图/示例.png)'));
assert.ok(nodes(root).some(e=>e.tag==='pre'&&e.textContent==='![示例](配图/围栏示例.png)'));
nodes(root).find(e=>e.tag==='a').onclick({preventDefault(){}});
assert.deepEqual(opened,['file:作者有话说/第1章 附言.md']);
""")

    def test_complex_or_unclosed_references_remain_literal_without_requests(self):
        self.render_js([], r"""
for(const value of ['![图](配图(1).png','![图 [嵌套]](配图.png)','![图](配图.png (嵌套(标题)))','![图](<配图.png> "标题" 多余文字)','![图](配图.png "未闭合标题)']){
 const root=render(value);await flush();
 assert.equal(text(root),value);
 assert.equal(nodes(root).filter(e=>e.tag==='img'||e.tag==='a').length,0);
}
assert.deepEqual(requests,[]);
""")

    def test_untrusted_targets_remain_blocked_after_reference_parsing(self):
        self.render_js([], r"""
const root=render('![远端](https://example.com/image(1).png "标题")\n![网络](//example.com/image.png)\n![绝对](/private/image.png)\n![本机](file:///private/image.png)\n![数据](data:image/png;base64,aA==)\n![脚本](javascript:alert(1))\n![越界](../../image.png)\n![编码越界](..%2fimage.png) [坏链接](javascript:alert(1) "标题")');await flush();
assert.deepEqual(requests,[]);
assert.equal(nodes(root).filter(e=>e.tag==='img').length,0);
assert.ok(!nodes(root).some(e=>e.href?.startsWith('javascript:')));
const missing=render('![来源](配图.png "标题")',null);await flush();
assert.deepEqual(requests,[]);
assert.ok(text(missing).includes('无法定位本书配图来源'));
""")


if __name__ == "__main__":
    unittest.main()
