"""Publish the final evidence report only after a complete, stable passing run."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
read = lambda name: json.loads((OUT / name).read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()

full = read("full-suite.json")
manifest = read("validated-source-hashes.json")
baseline = read("before-source-hashes.json")
assert full["successful"] and full["tests_discovered"] == full["tests_run"]
assert not full["failures"] and not full["errors"] and not full["source_changed_during_run"]
assert all("Windows" in row["reason"] for row in full["skipped"])
assert all((ROOT / p).is_file() and sha(ROOT / p) == value for p, value in manifest.items())

native = read("native/validation-summary.json")
chrome = read("chrome/validation.json")
features = read("features/validation-summary.json")
reviewer = read("independent/reviewer-summary.json")
dom = read("independent/real-dom-result.json")
journal = read("state-integration.json")
markup = read("rank-markup-oracle.json")
for values in [native["files"], chrome["production_and_test_sha256"], features["changed_files_sha256"],
               features["probe_validation"]["source_sha256"], dom["source_hashes"],
               journal["source_sha256"], markup["source_sha256"]]:
    assert all(manifest.get(p) == value for p, value in values.items())
assert all(row["ok"] and manifest[row["source"]] == row["source_sha256"]
           for row in reviewer["checks"].values())
assert dom["ok"] and journal["ok"] and markup["ok"]
assert not features["unresolved_confirmed_findings"] and not reviewer["unresolved_confirmed_findings"]

changed = sorted(p for p, value in baseline.items() if sha(ROOT / p) != value)
expected = sorted(set(native["files"]) | set(chrome["production_and_test_sha256"]) |
                  {"skills/story-skill/scripts/story_rank_sources.py"})
assert changed == expected, (changed, expected)
packages = read("distribution/distribution-result.json")
for archive in packages["artifacts"]:
    with zipfile.ZipFile(archive["archive"]) as z:
        for name in ("story_platform_browser.py", "story_chrome_bridge.mjs", "story_rank_sources.py"):
            paths = [p for p in z.namelist() if p.endswith("/story-skill/scripts/" + name)
                     or p == "story-skill/scripts/" + name]
            assert len(paths) == 1
            assert hashlib.sha256(z.read(paths[0])).hexdigest() == manifest["skills/story-skill/scripts/" + name]
    archive["archive_sha256"] = sha(Path(archive["archive"]))
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

summary = {
    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "verified_with_documented_platform_limits",
    "fixed_problem_groups": [
        "configured same-draft initial redirects rejected by prior readback hardening",
        "ordinary editor and preview final-observation drift, including locator checks",
        "create/save button semantics changed during account checks before click",
        "paragraph input focus changed or entered independent controls and shadow inputs",
        "ranking HTML inline elements inserted unintended spaces into evidence fields"
    ],
    "new_implementation_findings_closed": ["Chrome selector path drift", "Native terminal snapshot interval",
                                             "Native shadow host focus mistaken for editor focus"],
    "changed_existing_files": changed,
    "added_test_files": ["tests/test_rank_source_markup.py"],
    "new_regression_tests": {"python_methods": native["targeted_tests"]["new_methods_this_turn"] + features["tests_added"],
                             "node_top_level_tests": chrome["new_top_level_driver_tests"]},
    "full_suite": {"discovered": full["tests_discovered"], "executed": full["tests_run"],
                   "passed": full["passed"], "skipped_native_windows": len(full["skipped"]),
                   "failures": 0, "errors": 0, "elapsed_seconds": full["elapsed_seconds"],
                   "source_drift": [], "report": "full-suite.json", "log": "full-suite.log"},
    "source_manifest": {"file": "validated-source-hashes.json", "entries": len(manifest),
                        "sha256": sha(OUT / "validated-source-hashes.json"), "final_recheck_matches": True},
    "component_validation": {"native": native["targeted_tests"], "chrome": chrome["combined_node_counts"],
                             "rank_research": features["targeted_validation"]},
    "independent_checks": {"real_local_chrome_dom": len(dom["checks"]), "journal_transitions": journal["cases"],
                           "markup_oracle": markup["cases"], "reviewer": reviewer["checks"]},
    "distribution": packages,
    "unresolved_confirmed_findings": [],
    "boundaries": ["Offline fixtures and isolated local HTML in real Chrome; no live platform/account acceptance",
                   "Native Windows-specific tests remain skipped", "No installation, release, commit or real book modifications",
                   "One-page observations and pre-action checks do not guarantee cross-process atomic UI actions",
                   "No new reader study or literary-quality measurement"],
    "counting_note": "Nested Node suites are represented by two Python wrapper tests; do not add their counts or probe assertions to the complete Python suite."
}
(OUT / "validation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

document = f'''# 2026-10-23 全功能循环复核与直接修复

本轮沿上轮最终源码继续执行“复现 → 修复 → 回归 → 独立反例 → 再修复”。检查涵盖全部功能分组；重点检查上轮浏览器变更及相邻路径。在已完成的检查范围内，没有尚未解决的已确认问题。

## 已修复的问题

1. **同一草稿的合法初始重定向被拒。** 上轮严格 URL 绑定导致 Native/Chrome 拒绝已经配置的同 ID 编辑地址跳转。现在先确认实际到达地址合法且草稿 ID 一致，再固定到达地址；后续回跳或换稿仍拒绝。Chrome 使用上轮最终分发包中的原始模块重放，保留了基线失败证据。
2. **最终回读期间正文变化漏检。** 普通编辑器与番茄预览都补齐同页完整快照。独立复核进一步发现并修正选择器重复/转移、定位核验之后正文变化等窗口，避免组合旧正文和后续身份信息而报告成功。已有严格段落序列化保持不变。
3. **新建/保存按钮在账号核对期间变成其他操作仍被点击。** 最后一次身份读取之后，重核当前按钮的文字和可访问标签；反例验证实际没有发布点击，正常新建和存草稿仍通过。
4. **段落输入写入错误目标。** 最终焦点核对之后不再插入账号或 URL 读取。拒绝独立输入框、按钮、iframe、不可编辑工具及开放 Shadow DOM 内工具输入，保留正文根和同一编辑根内正常段落的输入。
5. **七猫榜单 HTML 行内标签污染文本。** 不再在行内标签边界凭空插入空格，保留真实空白与块/换行边界，继续排除脚本和样式文字。修复书名、作者、分类变形与排序误拒绝，验证采集→按原书名抽样→研究交接。

本轮新实现也经过再次反查：Chrome 路径快照的选择器漂移、Native 快照尾部核验间隔，以及 Shadow DOM 焦点误判均留有红灯证据，并用同一反例完成修后验证。

## 最终验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 冻结源码完整回归 | 发现并执行 {full['tests_run']:,} 项；通过 {full['passed']:,}、跳过 {len(full['skipped'])}、失败 0、错误 0 | [报告](full-suite.json)、[完整日志](full-suite.log) |
| Native 相关回归 | 131/131；本轮新增 8 个 Python 方法 | [Native 记录](native/README.md) |
| Chrome driver 与 worker | 150 + 94 = 244/244；本轮新增 13 个 Node 顶层测试 | [Chrome 记录](chrome/README.md) |
| 来源、榜单与研究交接 | 105/105；本轮新增 5 个 Python 方法 | [功能覆盖矩阵](features/README.md) |
| 独立浏览器反例及回调契约 | Native 4、Chrome 选择器 3、动作契约 16 场景通过 | [独立复核](independent/reviewer-summary.md) |
| 隔离真实 Chrome DOM | {len(dom['checks'])} 项通过，含实际值读取、预览与段落格式、选择器路径、开放 Shadow DOM 与焦点归属 | [结果](independent/real-dom-result.json) |
| 草稿执行与对账 | 3 平台共 18 个失败/恢复场景；无重复创建、创作状态不变 | [结果](state-integration.json) |
| 根线程独立榜单等价对照 | 160 组随机行内标记与纯文本解析结果一致 | [结果](rank-markup-oracle.json) |
| 其他功能探针 | 检索 841、行内标记 100、抽样/研究交接 20 场景通过 | [专项汇总](features/validation-summary.json) |
| 最终分发 | 标准/Claude/Codex 三包各核对 69 个源码载荷，修改的运行时文件匹配受测源码 | [结果](distribution/distribution-result.json) |

完整回归计数包含调用 Node driver 和 worker 的两个 Python 包装测试。上述专项、Node 内部测试和探针场景不再次加进完整回归总数。{len(full['skipped'])} 项跳过全部需要原生 Windows 环境，详细原因见完整报告。

## 覆盖与源码一致性

八技能入口、读者当前位置/爽点兑现/追更规则、规划与改编分镜、正文和短篇合稿、审稿与历史依赖、世界状态与存储、拆文/检索/研究/扫榜、封面、离线材料与平台草稿、工作台恢复归档、分发安装及桌面更新均纳入本轮检查。未修改模块以静态审查和完整现有回归为主要依据；专项探针重点验证实际发现的问题及相邻路径。细分方法和未验证范围见功能覆盖矩阵。

相对本轮开始快照，修改 3 份生产运行时和 4 份既有测试文件，新增 1 份榜单解析回归测试，其他已有未提交改动保留。完整回归期间源码未变化，结束后再次核对 {len(manifest)} 个受测文件的 SHA256；组件与独立验证也绑定同一源码。三种最终归档再次核对了修改模块的字节内容，`git diff --check` 通过。

文件清单和校验数据见 [最终汇总](validation-summary.json)、[受测源码哈希](validated-source-hashes.json)。证据目录保存的中间红灯和较早绿灯仅用于追溯，不代表最终完整回归结果。

## 验证边界

- 仅完成仓库源码修复与本地验证，未同步已安装插件、发布版本、提交代码或操作真实作品/平台账号。
- 真实 Chrome 仅使用隔离临时 profile 访问本地 HTML；不等于真实番茄、七猫或起点页面的保存验收。
- 原生 Windows 的专属检查尚未在本轮执行。
- 同页快照和动作前核对不构成跨页面/服务器原子事务，也不保证 UI 动作 RPC 发起后的任意变化都能阻止。封闭 Shadow DOM 不可检查；按钮自动等待的边界见独立复核记录。
- 规则与自动化回归通过，不等于测量过真实读者爽点、追更意愿、封面点击率或文学质量。
'''
(OUT / "README.md").write_text(document, encoding="utf-8")
(OUT / "after-git-status.txt").write_text(subprocess.run(["git", "status", "--short"], cwd=ROOT,
    check=True, capture_output=True, text=True).stdout)
print(json.dumps({"report": str(OUT / "README.md"), "passed": full["passed"],
                  "skipped": len(full["skipped"]), "changed_existing_files": changed,
                  "hashes_match": True}, ensure_ascii=False))
