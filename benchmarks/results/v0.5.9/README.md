# v0.5.9 验证记录

本版收紧独立代理交接规则，并在正文场景指导中补充分段判断。[版本说明](../../../docs/releases/v0.5.9.md) · [安装指引](../../../INSTALL.md)

| 环节 | 本地证据 |
|---|---|
| 工程验证 | [完整回执](verification.json)：Python 3.10 的 560 项测试中 553 项通过、7 项按条件跳过；中文稿件与长篇回放、七技能格式、安装和 ZIP 文件核对通过 |
| 容量与迁移 | [四组容量](scaling.json)和[三类合成旧库迁移](migration.json)通过；这是工程检查，不代表文学质量 |
| v0.5.8 → v0.5.9 | [隔离升级回放](upgrade.json)通过，旧版备份和新版载荷均已核对 |
| 指令体积 | [同范围计数](tokens.md)与[原始数据](tokens.json)绑定本版技能文件；不等于实际账户用量 |
| ZIP | [构建回执](package.json)：7 个技能、34 个载荷文件，SHA-256 `8710ed9b01a6e0d70d547eacf8be52ef30d3b22c116c420506d61b4e3276105b` |
| 本地 npm 包 | [文件与摘要核验](npm-package.json)及[四项运行命令](npm-smoke.json)通过；远端 GitHub Packages 另行核对 |

固定标签、Release 附件和 GitHub Packages 的结果以[发布记录](../../../docs/github-release.md)为准。Windows 既有 `WinError 32` 导出问题未在本版修复，Windows 安装目标仍为 v0.4.0。工程通过不证明正文分段与阅读体验已在真实作品中改善。
