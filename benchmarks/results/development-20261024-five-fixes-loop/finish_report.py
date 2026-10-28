"""Verify final evidence is bound to the tested source, then write the audit report."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent

def read(path):
    return json.loads((OUT / path).read_text())

def verify_hashes(values):
    for name, digest in values.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name

full = read('full-suite.json')
assert full['successful'] and not full['failures'] and not full['errors']
assert full['tests_run'] == full['tests_discovered'] and not full['source_changed_during_run']
final_hashes = read('validated-source-hashes.json')
verify_hashes(final_hashes)
native = read('native/validation-summary.json')
chrome = read('chrome/validation.json')
features = read('features/validation-summary.json')
independent = read('features/independent-review.json')
verify_hashes(native['files_sha256'])
verify_hashes(chrome['files'])
verify_hashes(features['changed_files_sha256'])
verify_hashes(independent['source_sha256'])
for path, key in [('independent/real-dom-result.json', 'source_hashes'),
                  ('independent/native-journal.json', 'source_sha256'),
                  ('independent/rank-visibility-dom.json', 'source_sha256'),
                  ('state-integration-result.json', 'source_sha256')]:
    value = read(path)
    assert value['ok'], path
    verify_hashes(value[key])
assert not independent['unresolved_confirmed_findings']
distribution = read('distribution/result.json')
assert distribution['manifest_sets_equal'] and distribution['no_installation']
assert all(item['latest_six_runtime_modules_exact'] for item in distribution['artifacts'])
subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
baseline = read('before-source-hashes.json')
changed = sorted(name for name, digest in final_hashes.items()
                 if not name.startswith('benchmarks/results/') and baseline.get(name) != digest)
deleted = sorted(set(baseline) - set(final_hashes))
assert not deleted, deleted

# Session-lifecycle evidence is completed by its owner and independent reviewer
# before this finalizer runs; its specific validation is also in the full suite.
lifecycle = read('native/session-recovery-summary.json')
session_cross = read('chrome/independent-python-session/summary.json')
assert lifecycle['ok'] and session_cross['ok']
verify_hashes(lifecycle['source_sha256'])
verify_hashes(session_cross['source_sha256'])

report = {
    'status': 'passed', 'date': '2026-10-24',
    'scope': 'All-feature review with targeted repairs, independent counterexample replay, local DOM checks and one final stable-source repository suite.',
    'full_suite': full, 'changed_this_round': changed,
    'validated_source_file_count': len(final_hashes),
    'native': native, 'chrome': chrome, 'features': features,
    'session_recovery': lifecycle, 'independent_session_review': session_cross,
    'independent_browser_review': independent,
    'local_dom_checks': 79, 'rank_rendered_static_boundary_cases': 16,
    'native_adapter_journal_cases': 10, 'journal_boundary_cases': 18,
    'distribution': distribution, 'unresolved_confirmed_findings': [],
    'limits': [
        'The Node driver/worker suites are two Python wrapper tests; nested cases are not added to Python totals.',
        'Local synthetic browser DOM and fake public transports do not prove live platform or native Playwright/CUA integration.',
        'Native Windows tests skipped on macOS remain unverified here.',
        'Static rank extraction conservatively ignores hidden nodes without CSS/JavaScript rendering equivalence.',
        'No real manuscripts, platform accounts, installed plugin caches, releases or commits were changed.'
    ]}
(OUT / 'validation-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
text = f'''# 全功能循环复核与修复 · 2026-10-24

本轮从前次五类修复的最终源码继续，保留仓库已有改动，完成代码审查、失败复现、直接修复、正常路径对照、独立复核及冻结源码后的完整回归。当前未解决的已确认问题为 0；这不表示穷尽所有问题。

## 修复与再次复核

1. **未分配 ID 的新建页换稿后仍写入**：Native 只允许首次取得有效草稿身份的合法迁移，空或缺失 ID 时换页立即停止。
2. **草稿状态改变后仍写入或保存**：两执行器在后续写入前核对当前 DOM 状态；发布、消失或重复状态均阻止后续动作。
3. **按钮标签未变但链接目的地改变**：两执行器把最初观察的 href 与最终按钮快照比较，变化后不点击；普通按钮、空 href 和稳定链接保留兼容。
4. **同页新建误用管理页缓存**：Chrome 将已转为编辑器的标签移出管理页缓存，后续目录核对使用独立页面，连续创建与查找通过。
5. **榜单混入模板和隐藏证据**：七猫解析在节点遍历与取文时统一排除惰性及显式 hidden 子树；保留 aria-hidden 可见文字、原始 HTML 与静态解析限制。
6. **执行会话初始化中断无法重新连接及并发连接竞争**：原始书根中断恢复和互斥边界已补齐，残留证据保留，活动或未知队列不作为空会话忽略，详见会话报告。

循环复核还确证并修复本轮 Chrome 初版产生的两处回归：合法同 ID 地址切换被误拒，以及新增状态查询后换稿造成的写入窗口。独立重放确认旧稿不变、合法切换可用。

## 最终验证

- **完整仓库回归：{full['tests_run']} 项执行，{full['passed']} 项通过，{len(full['skipped'])} 项跳过，0 失败、0 错误。** 耗时 {full['elapsed_seconds']} 秒，发现数与执行数相同，{len(final_hashes)} 个记录文件在运行中无漂移。
- Native 136 项专项；Chrome driver 160 + worker 94 = 254 项组件测试；榜单/研究/规则 163 项专项。各自与全量存在重叠，不能相加。
- 会话恢复邻域 82 项执行（81 通过、1 个原生 Windows 项跳过），独立 14 个会话/并发/未知请求场景通过。本轮新增 18 个 Python 测试方法与 10 个 Node 顶层测试。
- 独立浏览器复核：Chrome 16 个身份/切换场景、Native 28 个场景、同页连续创建/查找 2 个完整流程。
- 实际本地 Chrome 执行生产 DOM 函数 79 项；榜单浏览器/静态边界 16 项；真实 Native 适配器+持久日志+假 DOM 联测 10 场景；持久日志边界 18 场景。
- 榜单另外 240 个行内/惰性不变性场景及 12 个纯惰性证据拒绝场景通过。
- 标准、Claude、Codex 三种包分别核对全部 69 个源码载荷；8 技能清单一致，修复模块字节精确匹配。这里只构建验包，未安装。
- `git diff --check` 通过。完整套件中的两个 Node 包装测试已实际运行；参数场景与包装器不重复计入全量总数。

## 功能覆盖

八技能入口、读者视角/爽点/追更、规划与改编分镜、正文与短篇合稿、审稿与历史、世界状态、存储检索、拆文与恢复、榜单研究交接、封面规则、离线发布与平台草稿、工作台与备份恢复、桌面及分发均纳入审查或对应完整自动化测试。详细方法和语义验收边界见 [功能覆盖矩阵](features/README.md)。没有用哈希或自动化测试代替真实文学读感、商业效果或平台验收。

跳过项为当前 macOS 环境不能执行的原生 Windows 验证。真实网站、账号、原生 CUA/Playwright 端到端及任意时刻跨页面原子性没有在本轮验收。静态 HTML 对 hidden 的保守排除可能与 CSS 覆盖、行内 hidden=until-found 的实际呈现不同，真实 DOM 报告已记录该边界。

初始化异常能进入清理分支时，只退役本次尚未交付句柄的租约；若进程硬杀在租约发布后、句柄发布前，仍保留有效租约直到正常过期，不保证该情形立即重连。

## 证据入口

- [完整回归清单](full-suite.json) / [原始日志](full-suite.log) / [源码哈希](validated-source-hashes.json)
- [Native 红绿验证](native/README.md) / [Chrome 红绿验证](chrome/README.md)
- [独立浏览器复核](features/independent-review.md) / [会话恢复摘要](native/session-recovery-summary.json) / [会话独立复核](chrome/independent-python-session/summary.json)
- [生产 DOM 本地实测](independent/real-dom-result.json) / [榜单真实 DOM 边界](independent/rank-visibility-dom.json)
- [适配器与日志联测](independent/native-journal.json) / [分发验包](distribution/result.json)
- [本轮完整机器可读汇总](validation-summary.json)

本轮相对开始基线变更的源码/测试：

''' + '\n'.join('- `' + name + '`' for name in changed) + '\n'
(OUT / 'README.md').write_text(text, encoding='utf-8')
print(json.dumps({'ok': True, 'tests': full['tests_run'], 'passed': full['passed'],
                  'skipped': len(full['skipped']), 'changed_this_round': changed}, ensure_ascii=False))
