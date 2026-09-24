# Story Skill v0.5.2

本版整理大纲与细纲规范，并修复状态保护、历史修订和分析恢复中的问题。完整套件仍为7个技能、33个文件，沿用 schema 2。

- 大纲按全书、具名分卷、独立章节组织，已采用细纲与工具计划同步。
- 卡片局部更新保留硬限制与作用范围；正文仍按具名卷目录和固定章节文件名保存。
- 世界状态保留时间不确定性，正确区分人物适用的规则版本，支持有证据的零期初余额，避免按记录名称猜测伏笔先后。
- 历史修订可读取早期章依赖及候选正文；导入稿先补计划，后补的世界证据也受替换保护。
- 分析记录替换与报告定稿绑定所读分析版本；补全断点回读及待导出报告的恢复说明。

本机 Intel macOS／Python 3.12：443项测试中436通过、7项按条件跳过、零失败；12项整包检查、18项整套升级检查、三类合成旧库迁移和四组百万／千万字容量检查通过。这些工程检查不代表文学质量认证。

发布后核验：Release ZIP 与校验文件回下载通过；官方固定标签隔离安装的7个技能、33个文件与源码逐字节一致。GitHub Packages `@ningcui29/story-skill@0.5.2` 已公开发布，注册表回下载的33个技能文件及2个包装文件一致，四项运行检查通过。[Packages 工作流](https://github.com/NingCui29/story-skill/actions/runs/34577548793)

固定发布提交的 [CI](https://github.com/NingCui29/story-skill/actions/runs/34577137667)：Linux 443项中431通过、12项平台跳过，全部步骤成功；Windows 在首项报告导出测试以已有 WinError 32 停止，整体 CI 为失败，未宣称全平台通过。

**兼容说明：** 本版面向 macOS/Linux。Windows 正文和报告导出的已知 WinError 32 未修复，Windows 用户继续使用固定 v0.4.0；已有新版书库先完整备份，不盲目降级。自编分析脚本须为逐块替换提供 analysis_sha256，为首次 report 提供 --expect-analysis；同内容重试仍可恢复。

[安装或升级指引](https://github.com/NingCui29/story-skill/blob/main/INSTALL.md) · [完整版本说明](https://github.com/NingCui29/story-skill/blob/main/docs/releases/v0.5.2.md) · [逐项发布验证](https://github.com/NingCui29/story-skill/blob/main/docs/github-release.md)
