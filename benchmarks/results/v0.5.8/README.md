# v0.5.8 验证与发布记录

本版为番茄短篇增加独立的五栏作品分类；长篇标签流程保留。[版本说明](../../../docs/releases/v0.5.8.md) · [安装指引](../../../INSTALL.md)

| 环节 | 当前证据 |
|---|---|
| 本地工程验证 | [完整回执](verification.json)：Python 3.10 的 560 项测试中 553 项通过、7 项按条件跳过；中文稿件与长篇回放、技能格式、项目安装和本地链接通过 |
| 合成容量与迁移 | [四组容量](scaling.json)与[三类合成旧库迁移](migration.json)通过；仅为工程证据，不代表文学质量 |
| v0.5.7→v0.5.8 | [隔离升级检查](upgrade.json)通过；旧版套件、备份与新版安装载荷均核对 |
| 本地 ZIP | [构建回执](package.json)：34 个技能文件，SHA-256 `253c072cc1d28703e36209a6374e6e008fa3abaf0c939d0429e042444ebd5cbc` |
| 固定标签与 Release | `v0.5.8` 指向 `09d2522a1486cde65a1db5aaec2a96974de77962`；[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.8) 的 ZIP/checksum 已回下载，与固定源码和本地包一致。[发布核验](release/release.json) |
| 官方固定标签安装 | [隔离安装](release/remote-install.json)使用官方安装器 Git 方式；7 技能、34 文件与 Release 一致，版本／帮助／初始化／状态四项命令通过；本机现有安装未改 |
| 远端 CI | [两次推送运行](release/ci.json)的 Linux 成功；Windows 在报告导出时复现 `WinError 32`，整体 CI 失败 |
| GitHub Packages | [工作流 35449089424](https://github.com/NingCui29/story-skill/actions/runs/35449089424) 成功公开发布 `@ningcui29/story-codex@0.5.8` 并从注册表回下载；[工作流回执](release/receipt.json)及[独立核验](release/packages-independent.json)确认 34 个技能文件、2 个包装文件、SHA-512 和四项命令 |

固定标签保留发布时快照；main 的后续文档更新不移动标签或替换附件。Windows 仍有已知 `WinError 32` 导出问题，Windows 安装目标保留 v0.4.0。工程检查不证明标签适合某本书，投稿前仍须按实际页面核对。
