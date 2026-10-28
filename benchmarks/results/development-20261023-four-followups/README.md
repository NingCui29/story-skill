# 四处后续修复与验证

本次修改基于已有未提交改动，修复上一轮复核确认的四处问题。未发布、同步安装或操作真实平台；以下平台执行测试使用离线夹具和临时作品。

## 修复范围

| 问题 | 修复后的行为 | 主要验证 |
|---|---|---|
| 旧抽样及研究记录因方法改变而无法继续使用 | 新样本显式记录 schema 2 / algorithm 2；两种已生成的 schema 1 按各自原算法严格重建，不修改历史样本或验证 ID；采用新算法另建研究版本 | `tests/test_rank_compatibility.py`：冻结旧样本、旧决定、真实 CLI check/render/verify、增量证据、篡改及未知版本拒绝；历史算法不再受展示条数常量影响 |
| 编辑器标志的非法 `source` 到创建或保存后才失败 | Python 配置入口及 JS 驱动构造期拒绝 `editor.new/ready/saved` 的非 DOM 来源；省略、null 和显式 dom 兼容 | `tests/test_platform_profile_markers.py`、`test_platform_browser.py`、`test_chrome_bridge_driver.mjs`：配置目录、预检、连接及驱动操作均提前拒绝，断言无浏览器调用、句柄或桥接队列；合法配置继续创建 |
| 崩溃残留的恢复会话缺少 `session.json`，阻止重新连接 | 读取状态及验证旧句柄时，先识别恢复证据标记；不解析或锁定证据目录，仍保留租约阶段的二次检查 | `test_workbench_archives.py`：实际子进程在首次会话文件发布前退出，然后备份、恢复、重新连接；4 种缺失/空文件状态 × 6 个旧句柄入口拒绝后文件集合和字节不变；`test_platform_chrome_bridge.py`：未标记损坏会话仍拒绝，链接标记不能掩盖错误 |
| ZIP 容器权限比私有源文件更宽 | ZIP 从创建时即使用私有文件描述符；写入、关闭、校验和发布保留该权限 | `test_workbench_archives.py`：umask 022 / 000 下创建、暂存和发布均为 0600；ZIP 可读、摘要匹配；写入/发布失败清理暂存并保护现有目标 |

恢复问题的交叉复核还发现旧句柄在被拒绝前会把空 `session.lock` 写成非空；已一并修复，新增的 24 种参数组合覆盖该相邻问题。

## 验证证据

最终完整回归共发现并执行 **2613 项**：**2592 项通过、21 项原生 Windows 用例跳过、0 失败、0 错误**。Python 3.12.14、Node 24.19.0、macOS x86_64；总耗时 647.98 秒。运行前后及交付前源码哈希一致。相较本轮修复前的 2594 项，新增 19 项 Python 测试；另外新增 2 项 Node 驱动契约测试，由完整回归中的 Node 包装测试实际执行。

- [完整回归结果](full-suite.json)、[详细日志](full-suite.log)、[验证源码哈希](validated-source-hashes.json)：由 [run_validation.py](run_validation.py) 生成；运行前后源码一致才可判定成功。最终统计见结果文件。
- [规则加载预算](instruction-budget/instruction-loads.md)：301 项通过，未提高预算上限。该检查不衡量文学质量或真实读者反应。
- 兼容性、榜单与研究定向测试分别为 8、74、21 项通过；平台 Python 定向组 227 项中 226 通过、1 项原生 Windows 跳过；Node 驱动及工作线程共 200 项通过；归档 48 项通过；桥接 72 项中 71 通过、1 项原生 Windows 跳过。各组有重叠，不将其相加作为独立测试总数。
- 冻结旧数据及来源记录见 `tests/fixtures/rank-samples/README.md`。独立复核还使用了保留的 2026-10-22 旧样本和研究包：check/render 可用，verify 追加证据并保持既有 ID，再次验证不重复追加。

## 验证脚本问题及边界

前两次运行在第 145 项首次就绪检查处失败。根因是本次新建的验证脚本未把仓库根加入 Python 导入路径，既造成测试发现错误，又使工作线程无法导入测试夹具；不是生产控制器的就绪逻辑回归。已修复导入路径，增加发现阶段错误立即失败及发现/执行数量一致检查，未放宽测试超时。

原失败结果保留于 [初次结果](full-suite-initial.json)、[第二次结果](full-suite-second-failed.json)，以及对应日志和源码哈希；诊断见 [说明](initial-run-note.json) 和 [独立复现](runner-diagnosis.md)。这些失败运行不计为通过。

本机验证为 macOS；原生 Windows 用例的跳过不代表 Windows 已验证。ZIP 的 0600 断言使用 POSIX 权限语义。离线平台测试和本地备份恢复不证明真实平台写入、远端草稿保存或发布成功。
