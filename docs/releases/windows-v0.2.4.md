# Windows 写作工作台 0.2.4

Windows **0.2.4** 使用 **C#／WPF／.NET 10／Microsoft WebView2**，内嵌 **Story Skill 0.6.25**。发布状态：已正式发布，4个附件齐全。

本版整合工作台保存与恢复、继续编辑、书架状态和草稿同步改进。在添加作品、新建作品表单中可打开系统目录选择器；选择后仍需确认添加或创建，同名已有目录不会被覆盖。工作台也补充了启动期间文件占用的有界只读重试，保留原有服务身份与未保存编辑保护。

草稿同步按确认范围更新已有章节的章名和正文、补齐缺失草稿，并在保存后逐章独立完整回读。新保存固定去掉正文空白行，保留非空行字符、缩进和段落换行，严格核对标题及转换后的全文；本次进度、停止原因与历史核对记录分别显示。完整变化见 [v0.6.25 发布说明](v0.6.25.md)。

## 下载与升级

[Windows 独立发行页](https://github.com/NingCui29/story-skill/releases/tag/windows-v0.2.4)提供两个 x64 文件及各自 SHA256，共 **4 个附件**。

| 分发 | 文件 | 校验 |
| --- | --- | --- |
| 安装程序 | [story-workbench-0.2.4-windows-x86_64-setup.exe](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.4/story-workbench-0.2.4-windows-x86_64-setup.exe) | [SHA256](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.4/story-workbench-0.2.4-windows-x86_64-setup.exe.sha256) |
| 便携 ZIP | [story-workbench-0.2.4-windows-x86_64.zip](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.4/story-workbench-0.2.4-windows-x86_64.zip) | [SHA256](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.4/story-workbench-0.2.4-windows-x86_64.zip.sha256) |

升级前保存所有页面编辑并退出旧客户端，安装后重新打开。书稿、书架及历史状态沿用原有位置；新安装包不会自动替换仍在运行的旧作品服务。本轮已在隔离 Windows 环境验证 **0.2.3 → 0.2.4** 升级，保留旧安装备份与回滚保护，见[跨版本升级回执](../../benchmarks/results/v0.6.25/windows-previous-upgrade.json)。

工作台及安装程序未做代码签名。随包 WebView2 运行时发行文件的 Microsoft 签名已核对，详见[分发核验](../../benchmarks/results/v0.6.25/windows-distribution-verification.json)。

## 构建与验收

实际构建源码为 [`45fe93530a3e07975ad5728a7d575ef23f8ae3d5`](https://github.com/NingCui29/story-skill/commit/45fe93530a3e07975ad5728a7d575ef23f8ae3d5)。最终标签目标为 [`b344e15dbcfb2c10f70f8d97ad706b9afdf092be`](https://github.com/NingCui29/story-skill/commit/b344e15dbcfb2c10f70f8d97ad706b9afdf092be)，标签核对状态：主标签安装前后对象和提交一致，两个稳定发行的目标提交均与本节一致。

标签目标仅包含测试期望与测试进程总时限修正，涉及 `tests/test_workbench_platform_draft_ui.py` 和 `tests/test_chrome_bridge_driver.py`。115个实际构建输入的 mode、blob 与 SHA256 等价性核验结果：全部一致；变化仅涉及两份测试，生产源码与实际构建输入未变。Windows 运行时及原生构建回执继续记录真实构建提交，见[源码等价性核验](../../benchmarks/results/v0.6.25/build-source-equivalence.json)。

[Windows 原生构建运行 40180245149](https://github.com/NingCui29/story-skill/actions/runs/40180245149)已成功，验收范围如下。

- C# 契约测试 **39／39** 通过，零失败、零跳过，见[测试回执](../../benchmarks/results/v0.6.25/windows-csharp-tests.json)。
- 便携包及已安装应用分别核验 **1125 个文件**；运行时与固定源码对应，完整分发和安装目录禁用技术检查通过，见[分发与安装载荷核验](../../benchmarks/results/v0.6.25/windows-distribution-verification.json)。
- 原生书架、浅色／深色主题、关闭保护、隐藏缓冲区、输入法保护、故障标签恢复与未知崩溃保护通过。更新进度界面采用合成数据验证，不据此声称真实更新下载链路已完成，见[原生界面回执](../../benchmarks/results/v0.6.25/windows-native-smoke.json)。
- 隔离作品创建、登记、备份、恢复身份、候选与二进制保留及 SQLite 完整性检查通过，见[作品载荷安全回执](../../benchmarks/results/v0.6.25/windows-runtime-safety.json)。
- 未保存服务阻止升级／卸载、确认已保存后的认证停止、安装回滚、旧安装保留与未知文件保留通过，见[安装生命周期回执](../../benchmarks/results/v0.6.25/windows-installation-lifecycle.json)。

最终源码 CI 三组任务全部通过；Linux / Python 3.10：3060 项，通过 3012、跳过 48；Windows / Python 3.10：3060 项，通过 2989、跳过 71；Windows / Python 3.12：3060 项，通过 2989、跳过 71。证据：[最终源码 CI 回执](../../benchmarks/results/v0.6.25/release/source-ci.json)。

公开附件回下载：两个校验文件已完整匿名回下载；安装程序和便携 ZIP 分别完成首尾各 64 KiB 匿名采样。完整二进制摘要依据 Windows 原生完整核验与 GitHub 服务端 digest，不声称完整匿名回下载 Windows 二进制。证据：[主发行匿名下载](../../benchmarks/results/v0.6.25/release/public-downloads.json)与[Windows 匿名下载](../../benchmarks/results/v0.6.25/release/windows-public-downloads.json)。公开下载的实际核验范围以该回执为准；原生构建中的完整文件核验与本机匿名下载范围分别记录。

## 实际使用边界

本轮使用隔离合成作品验证软件和安装包，没有据此确认真实作品已保存或发布。番茄、七猫、起点的真实多平台、全书、分页和多卷草稿验收不在本轮软件发布结论内。

平台动作仍限已授权的草稿保存及独立回读。结果未知先对账，不自动重发，不修改已发布章节，不提交审核或定时发布。真实使用前须核对当前登录连接、页面配置、作品身份与所选正文范围，见[平台草稿的实际范围](v0.6.25.md#平台草稿的实际范围)。

Windows 原生结果不替代 Mac 验收；Intel Mac 分发仍使用临时本地签名、未经 Apple 公证，Apple Silicon 未验证，详见 [v0.6.25 主发行记录](v0.6.25.md)。
