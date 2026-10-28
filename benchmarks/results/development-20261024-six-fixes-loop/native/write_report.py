from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
log=(OUT/'native-final-green.log').read_text()
assert re.search(r'Ran 147 tests.*\n\nOK',log),log[-500:]
paths=['skills/story-skill/scripts/story_platform_browser.py','tests/test_platform_browser.py','tests/test_platform_browser_link_base.py']
report={'ok':True,'files_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},'new_test_methods':3,'targeted_tests_run':147,'passed':147,'skipped':0,'failures':0,'errors':0,'new_action_scenarios':62,'findings':['Unchanged relative href with a changed document base sent an actual create/save click to a different destination. Effective href is now checked in the final bound button snapshot.'],'candidate_regressions_fixed':['Configured same-draft aliases with empty or fragment save links were initially over-rejected. Both snapshot document identities and actual self-targets now prove eligibility.','Unicode whitespace classified a path as a self-link. Eligibility now uses browser-resolved document URL equality without fragment, preserving real ASCII/C0 self-links.'],'red_evidence':{'base-red.log':6,'alias-red.log':4,'unicode-red.log':6},'unresolved_confirmed_findings':[],'limits':['Full adapter tests use local fake DOM and public Playwright-shaped transport; actual DOM callbacks are verified separately in isolated local Chromium.','No real platform, account, novel content, installation or release modified.']}
# 6 changed-target, 24 stable-target, 32 alias/base/Unicode/ASCII scenarios.
(OUT/'validation-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
(OUT/'README.md').write_text('''# Native 草稿链接最终复核

原始 `href` 保持不变时，页面 `<base>` 仍可能把创建/保存链接改指其他目的地。新增首尾有效地址快照，身份核对后在已绑定按钮上再次比较；异常时不点击。

候选校验中另外发现并修正两种回归：合法同草稿地址别名切换被误拒，以及 Unicode 空白被误当作页内锚点。最终只对已绑定同一草稿、两个配置内编辑地址、两份实际目的地均仍为当前文档的保存按钮开放别名兼容。浏览器自己解析 URL，避免 Python 的空白与 URL 行为代替浏览器语义。

- 新增 3 个 Python 方法，62 个创建/保存参数场景。
- 最终专项 147 项执行，147 通过，0 跳过、0 失败、0 错误。
- 基线错误目的地点击红灯 6 项；候选合法别名误拒红灯 4 项；Unicode 路径误放行红灯 6 项，均有原始日志。
- 完整适配器测试检查实际点击事件与返回值，正向覆盖普通按钮、稳定相对/绝对链接、同 ID 别名、ASCII/C0 空白；独立本地 Chromium 验证见上级 independent/native-link-dom-result.json。

[最终专项日志](native-final-green.log) · [源码哈希和结构化汇总](validation-summary.json)

测试使用临时内存页面；真实平台及原生 Playwright 传输端到端未经本轮验证。未知结果仍由持久日志阻止盲重发。
''')
print(json.dumps(report,ensure_ascii=False))
