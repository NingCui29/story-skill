# Story Skill v0.5.3

本版补齐从拆书开始创作新书的使用流程，并修正开书时书名目录不明确的问题。完整套件仍为 7 个技能、33 个文件。

- README 重写为安装、开书、目录、七入口和验证说明，加入可直接复制的“拆书 → 方法提炼 → 原创开书 → 大纲细纲”请求。
- 参考分析保存在独立目录；新书重建人物、关系、事件链和结局，保留来源与可迁移方法，规划请求不自动授权正文。
- 新书只给写作父目录时，先建立以书名命名的独立目录，核对实际落点后保存创作约定、策划和大纲，并交付可打开的路径。
- 用户明确指定书根或继续旧书时沿用原位置，不重复嵌套书名目录；具名分卷、固定章名和逐章细纲继续沿用。

本机 Intel macOS／Python 3.12：443 项测试中 436 通过、7 项按条件跳过，12 项整包检查通过。v0.5.2→v0.5.3 升级 18 项、三类合成旧库迁移和四组合容量检查通过；工程验证不代表文学质量认证。

发布提交的 [Linux 检查](https://github.com/NingCui29/story-skill/actions/runs/34583076198) 通过：443 项中 431 通过、12 跳过；Windows 首项报告导出因已知 WinError 32 失败，整体 CI 未通过。Release ZIP 回下载的 33 个文件与源码一致，官方固定标签安装的 4 项命令检查通过。GitHub Packages 的 [v0.5.3 发布流程](https://github.com/NingCui29/story-skill/actions/runs/34583518477) 成功，注册表下载包逐字节一致，独立安装的 4 项命令检查通过。

运行时行为和 schema 2 保持不变，仅更新版本标识；不会自动搬动已有作品。README 中的完整示例也明确给出文件路径与任务范围。

**平台范围：macOS/Linux。** Windows 正文与报告导出的已知 WinError 32 未在本版修复，Windows 用户继续使用固定 v0.4.0；已经用新版处理的书先完整备份，不盲目降级。

[安装与升级](https://github.com/NingCui29/story-skill/blob/main/INSTALL.md) · [从拆书开始，新起一本书](https://github.com/NingCui29/story-skill/blob/main/README.md#从拆书开始新起一本书) · [本版说明](https://github.com/NingCui29/story-skill/blob/main/docs/releases/v0.5.3.md) · [逐项验证](https://github.com/NingCui29/story-skill/blob/main/docs/github-release.md#v053-发布验证记录)
