# v0.5.9 验证记录

本版收紧独立代理交接规则，并在正文场景指导中补充分段判断。[版本说明](../../../docs/releases/v0.5.9.md) · [安装指引](../../../INSTALL.md)

| 环节 | 本地证据 |
|---|---|
| 工程验证 | [完整回执](verification.json)：Python 3.10 的 560 项测试中 553 项通过、7 项按条件跳过；中文稿件与长篇回放、七技能格式、安装和 ZIP 文件核对通过 |
| 容量与迁移 | [四组容量](scaling.json)和[三类合成旧库迁移](migration.json)通过；这是工程检查，不代表文学质量 |
| v0.5.8 → v0.5.9 | [隔离升级回放](upgrade.json)通过，旧版备份和新版载荷均已核对 |
| 指令体积 | [同范围计数](tokens.md)与[原始数据](tokens.json)绑定本版技能文件；不等于实际账户用量 |
| ZIP | [构建回执](package.json)：7 个技能、34 个载荷文件，SHA-256 `8710ed9b01a6e0d70d547eacf8be52ef30d3b22c116c420506d61b4e3276105b` |
| 本地 npm 包 | [文件与摘要核验](npm-package.json)及[四项运行命令](npm-smoke.json)通过 |
| 固定标签与 Release | `v0.5.9` 指向 `d700689a801aab27c5aac3641f3a3369a860d3b4`；[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.9) 的 ZIP/checksum 已公开回下载，与本地包逐字节一致。[发布核验](release/release.json) |
| 官方固定标签安装 | [隔离安装](release/remote-install.json)使用官方安装器 Git 方式；7 技能、34 文件与 Release 一致，版本／帮助／初始化／状态四项命令通过；本机现有安装未改 |
| 远端 CI | [两次推送运行](release/ci.json)的 Linux 成功；Windows 在回归测试中复现 `WinError 32`，整体 CI 失败 |
| GitHub Packages | [工作流 35498893936](https://github.com/NingCui29/story-skill/actions/runs/35498893936) 成功公开发布 `@ningcui29/story-skill@0.5.9` 并从注册表回下载；[工作流回执](release/receipt.json)与[独立复核](release/packages-independent.json)确认 34 个技能文件、2 个包装文件、SHA-512 与四项运行命令 |

固定标签保留发布时快照；main 的后续文档更新不移动标签或替换附件。Windows 既有 `WinError 32` 导出问题未在本版修复，Windows 安装目标仍为 v0.4.0。工程通过不证明正文分段与阅读体验已在真实作品中改善。
