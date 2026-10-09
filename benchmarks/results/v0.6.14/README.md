# v0.6.14 发布核验

v0.6.14 已于2026-10-09发布，固定标签指向 `2d093cded35317cf8c491ffe1d8e040a9a943685`。本地检查、三组跨平台 CI、六附件公开回下载及 GitHub Packages 独立复核均已完成。许可证文件按现有内容保留：技能分发载荷为 MIT，根目录及独立 Mac 客户端携带自定义非商业／商业授权文本，授权方与联系字段仍是占位符；不宣称各分发项条款已经统一。

- `unit-tests.json` / `unit-tests.log`：macOS、Python 3.12.14、Node 24.19.0；1433项，1416通过、17按条件跳过，零失败与错误。监测的运行时代码及包装代码在测试前后未变。桌面构建器的版本元数据改动单独记录。
- `local-checks.json`：隔离CLI、中文合成事务回放、长篇功能回放、八技能格式与51文件托管安装。前后技能输入哈希一致；用户真实书库和安装目录未参与验证。
- `package-candidate.json`：首次候选检查确认标准ZIP的51载荷、Claude候选ZIP的53文件与唯一入口、npm候选载荷匹配；其中许可待确认状态按当时记录保留。
- `scaling.json`：400/4000章、2000/20000状态卡，分别运行strict/local，共4组；验证容量和运行时一致性。
- `migration.json`：合成long/short/analysis三类旧schema夹具的迁移及回滚兼容性；不含真实书稿。
- `tokens.json` / `tokens.md` / `tokens-verification.json`：固定上游提交的7场景计数及当前源码指纹复核。长篇单章冷加载指令增长39.07%，多线增长47.67%；不宣称整体节省，也不等同实际账单。
- `desktop-candidate.json`：首次Intel Mac客户端0.1.2/build 3候选的21项内容、签名和隔离运行检查通过；当时尚未确定许可分发方式、未生成最终附件。后续最终附件结果另见 `desktop-verification.json`。

上述 `*-candidate.json` 保留首次候选检查时的状态，不代表后续分发状态。最终技能包核验见 `package-build.json`、`claude-desktop-verification.json` 和 `npm-build.json`；Claude包已核对53文件、唯一入口、233处本地引用、全部映射字节及隔离CLI。各检查的原始许可证文件均保留，差异在发布说明中披露。

分发证据位于 `release/`：

- [首次 CI](release/ci-initial.json)及[失败栈](release/ci-initial-failures.txt)保留 Windows 测试夹具换行引起的5个摘要校验错误；[修正回执](release/book-kind-windows-fixture-fix.json)记录一行夹具修复和9项本地复测。
- [最终 CI](release/ci-final.json)：Ubuntu/Python3.10、Windows/Python3.10和3.12的全部步骤成功，各运行1428项；跳过事件分别32、50、50，含不计入 testsRun 的类级跳过。
- [公开附件](release/release.json)：三个 ZIP 及三个校验文件共六附件，无认证回下载后与本地字节、GitHub摘要全部一致。
- [远程固定提交安装](release/remote-commit-install.json)、[修正提交复核](release/remote-commit-recheck.json)和[远端标签](release/remote-tag.json)：8组隔离安装、32次CLI；新提交和标签按同字节载荷复用这些执行结果。
- [Packages工作流回执](release/packages-receipt.json)及[独立核验](release/packages-independent.json)：公开0.6.14包的注册表下载物经工作流artifact取得，51载荷和2包装文件、SHA/SRI与公开ZIP及本地构建一致，4项隔离CLI通过；本机未直接认证下载注册表包。

公开报告中的本机路径经过通用化；未改动状态、文件哈希或测试计数。原始输出保存在本地忽略目录 `dist/release-v0.6.14/`。完整范围见[发布记录](../../../docs/releases/v0.6.14.md)。

这些检查不证明宿主实际技能加载、模型执行质量、小说文学效果或账户成本下降。
