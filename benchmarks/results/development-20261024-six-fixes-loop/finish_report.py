"""Bind current audit evidence and package contents to the final full-suite snapshot."""
from pathlib import Path
import hashlib,json,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'scripts')]
import package

def read(name):return json.loads((OUT/name).read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def verify(values):
    for name,digest in values.items():
        path=ROOT/name
        if not path.exists() and '/' not in name:
            path=ROOT/'skills/story-skill/scripts'/name
        assert sha(path)==digest,(name,'evidence does not match current source')
full=read('full-suite.json')
assert full['successful'] and full['tests_run']==full['tests_discovered']
assert not full['failures'] and not full['errors'] and not full['source_changed_during_run']
final=read('validated-source-hashes.json');verify(final)
native=read('native/validation-summary.json');verify(native['files_sha256']);assert native['ok']
chrome=read('chrome/validation.json');verify(chrome['source_sha256']);assert not chrome['unresolved_confirmed_findings']
sessions=read('sessions/validation-summary.json');verify(sessions['source_sha256']);assert sessions['ok']
features=read('features/validation-summary.json');verify(features['changed_files_sha256']);assert not features['unresolved_confirmed_findings']
verify(features['targeted_validation']['source_sha256']);verify(features['probe_validation']['source_sha256'])
assert features['targeted_validation']['successful'] and features['dom_validation']['ok'] and features['probe_validation']['ok']
independent={}
for name,key in [('independent/real-dom-result.json','source_hashes'),('independent/native-link-dom-result.json','source_sha256'),('independent/native-journal.json','source_sha256'),('independent/rank-visibility-dom.json','source_sha256'),('state-integration-result.json','source_sha256')]:
    value=read(name);assert value['ok'],name;verify(value[key]);independent[name]=value
cross_sessions=read('chrome/independent-session-result.json');verify(cross_sessions['source_sha256']);assert cross_sessions['checks']==cross_sessions['passed']==15
cross_rank=read('sessions/independent-rank-result.json');verify({'story_rank_sources.py':cross_rank['source_sha256']});assert cross_rank['ok'] and not cross_rank['accepted_hidden_cases']
cross_native=read('features/native-link-result.json');verify({'story_platform_browser.py':cross_native['source_sha256']});assert cross_native['ok']
verify({'story_chrome_bridge.mjs':chrome['local_chrome_dom']['source_sha256']});assert chrome['local_chrome_dom']['passed']==chrome['local_chrome_dom']['checks']
verify({'story_rank_sources.py':features['dom_validation']['source_sha256']})
distribution=read('distribution/result.json');assert distribution['no_installation'] and distribution['manifest_sets_equal']
entries=dict(package.source_entries())
for artifact in distribution['artifacts']:
    typ=artifact['kind'];archive=Path(artifact['archive'])
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for key,raw in entries.items():
            expected=raw.replace(b'SKILL.md',b'GUIDE.md') if typ=='claude' and key.endswith('.md') else raw
            if typ=='codex' and key.endswith('/agents/openai.yaml'):
                skill=key.split('/')[0];expected=raw.replace(('$'+skill+' ').encode(),('$story-skill:'+skill+' ').encode())
            target=key if typ=='standard' else ('story-skill/suite/'+(key.removesuffix('SKILL.md')+'GUIDE.md' if key.endswith('/SKILL.md') else key) if typ=='claude' else 'plugins/story-skill/skills/'+key)
            assert z.read(target)==expected,(typ,key)
    artifact['archive_sha256']=sha(archive)
subprocess.run(['git','diff','--check'],cwd=ROOT,check=True)
baseline=read('before-source-hashes.json')
changed=sorted(name for name,digest in final.items() if not name.startswith('benchmarks/results/') and baseline.get(name)!=digest)
assert not set(baseline)-set(final)
report={'status':'passed','date':'2026-10-24','scope':'All-feature follow-up on six previous repair categories and two Chrome regressions, with direct repair, red/green reproduction, independent review, local browser DOM and final stable-source whole repository suite.','full_suite':full,'validated_source_file_count':len(final),'changed_this_round':changed,'native':native,'chrome':chrome,'sessions':sessions,'features':features,'independent_sessions':cross_sessions,'independent_rank':cross_rank,'independent_native':cross_native,'independent_integration':independent,'distribution':distribution,'new_python_test_methods':18,'new_node_top_level_tests':6,'unresolved_confirmed_findings':[],'limits':['Node driver and worker are two Python wrapper tests; nested scenarios and overlapping targeted tests are not added to full-suite totals.','Native Windows-only tests skipped on macOS are not verified here.','Isolated local Chromium and fixture transports do not establish live website, native Playwright/CUA or arbitrary post-RPC atomicity.','Static HTML parsing is conservative and is not a complete HTML5/CSS/JavaScript rendering engine.','No installed skill caches, real book content, account, release, commit or platform operation was changed.']}
(OUT/'validation-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
text=f'''# 全功能循环检查与修复 · 2026-10-24

从上轮六类问题及两处 Chrome 回归的最终源码继续，保留已有改动。已完成失败复现、直接修复、正常路径对照、独立复核和冻结源码后的全量验证。当前检查范围内未解决的已确认问题为 0，不代表穷尽所有输入。

## 本轮修复

1. **草稿按钮实际目的地变化未拦截**：原始 href 不变时，页面 base 地址改变仍可将新建/保存点击导向其他位置。Native 与 Chrome 现在比较最终绑定按钮的有效地址，并让 Chrome 弹窗来源校验使用同一有效地址。
2. **静态榜单惰性内容漏出**：template 内结束标签可越过模板边界；HTML 非 void 惰性容器的自闭合写法又会与真实浏览器产生差异。现在保持模板作用域，对有歧义的惰性自闭合 HTML 明确拒绝采用，并区分正常 SVG/MathML 命名空间。
3. **可捕获初始化中断遗留有效租约**：覆盖元数据锁退出及句柄发布前后的 KeyboardInterrupt/SystemExit；仅退役精确匹配的本次初始化，保留句柄和请求证据，继续抛出中断。硬杀发生在租约发布后仍须等正常 TTL 过期。
4. **旧草稿记录退役误拒绝历史会话**：授权退役流程现在复用连接互斥和严格初始化残留分类，历史目录数不再被当作活动会话数；活动执行端以及无法确认已停止的创建仍阻止退役。

循环复核还修正了候选补丁中的回归：同 ID 编辑地址别名被误拒、Unicode 空白路径被误认页内链接，以及 SVG/MathML 的开始/结束标签退出上下文遗漏和合法外来元素误拒。红灯证据保留，最终正反路径均重新验证。

## 最终验证

- **完整仓库：{full['tests_run']} 项执行，{full['passed']} 项通过，{len(full['skipped'])} 项跳过，0 失败、0 错误。** 用时 {full['elapsed_seconds']} 秒；发现数与执行数一致，{len(final)} 个记录文件在执行期间无漂移。
- 本轮新增 **18 个 Python 方法、6 个 Node 顶层测试**。Native 专项 147 项；Chrome driver 166 + worker 94 = 260 项；会话/退役邻域 283 项执行（281 通过、2 个 Windows 跳过）；非执行功能专项 294 项通过。这些与完整套件重叠，不累加。
- 独立复核：Native 48 个完整创建/保存流程、会话与退役 15 个场景、榜单实际 Chromium 27 个对照场景全部通过。
- 本地生产 DOM 函数 79 项、Native 链接回调 31 项、Chrome 链接回调 18 项；榜单来源 181 个 DOM 场景及 16 个静态/渲染边界场景通过。
- Native 适配器与持久日志联测 10 场景、三平台日志边界 18 场景通过；另有 1120 次检索 oracle 对照。
- 标准、Claude、Codex 三种包均逐项核对 69 个源码载荷，8 技能入口一致；最终报告再次解包确认内容与当前源码一致。未安装或发布。
- `git diff --check` 通过。Node 包装测试已实际运行；浏览器参数场景不重复计入 Python 全量总数。

## 功能覆盖与边界

八技能入口、读者视角/爽点/追更、规划与改编分镜、正文与短篇合稿、审稿与历史、世界与存储检索、拆文恢复、榜单研究交接、封面规则、离线发布与平台草稿、工作台、备份恢复、桌面及多宿主分发均纳入对应审查或自动化测试。详见 [覆盖矩阵](features/README.md)。规则与结构测试不代替真人阅读体验、文学质量或商业效果判断。

{len(full['skipped'])} 项跳过均为本机 macOS 无法执行的原生 Windows 验证。本轮没有验收真实平台账号、原生 Playwright/CUA 端到端或任意时刻跨页面原子性；静态 HTML 仍不是完整 HTML5 建树与 CSS/JS 渲染器，明确 hidden 保守排除的已知边界保留。所有运行使用本地临时夹具，真实书稿及本机安装未改。

## 证据

- [全量结果](full-suite.json) / [完整日志](full-suite.log) / [最终源码哈希](validated-source-hashes.json)
- [Native 修复与红绿证据](native/README.md) / [Chrome 修复与红绿证据](chrome/README.md)
- [会话与退役](sessions/README.md) / [榜单及其他功能](features/README.md)
- [独立榜单审查](sessions/independent-rank-review.md) / [Native 独立完整流程](features/native-link-result.json) / [会话独立复核](chrome/independent-session-result.json)
- [最终分发包核对](distribution/result.json) / [机器可读完整汇总](validation-summary.json)

## 本轮源码与测试变更

'''+'\n'.join('- `'+name+'`' for name in changed)+'\n'
(OUT/'README.md').write_text(text)
print(json.dumps({'ok':True,'tests':full['tests_run'],'passed':full['passed'],'skipped':len(full['skipped']),'hashed_files':len(final),'changed_files':changed},ensure_ascii=False))
