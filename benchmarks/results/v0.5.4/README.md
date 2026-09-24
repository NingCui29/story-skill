# v0.5.4 发布验证

本版已于2026年9月11日21:21:30（北京时间）发布，目标平台为 macOS／Linux；Windows 的既有 WinError 32 未修复，安装继续固定 v0.4.0。运行时只更新版本标识，schema 2 和33个技能载荷文件的布局不变。新增规划承诺核对、正文详略取舍和跨章审稿指导；[版本说明](../../../docs/releases/v0.5.4.md)与[逐项发布状态](release/state.json)分别记录功能及分发进度。

已完成：

- [ZIP 构建](package.json)：7个技能、33个文件，119657字节，SHA-256 `5f66a93a47178cf15a8f6ca4a8390885e7f1dd2dba0fe12dd4951fcd48d4a9af`。
- [npm 构建](npm-package.json)：33个技能文件与ZIP一致，另有2个包装文件；新版本包装保留Windows兼容提示。
- [重新测量指令](tokens.md)：普通／多线写作6956／9143 tokens，深读／含示范5010／7019，审稿／冲突审稿3063／4528；这是静态计数，不是账户实际用量。
- [整套升级](upgrade.json)：v0.5.3→v0.5.4，18项通过。
- [旧库迁移](migration.json)：3类合成schema 1夹具通过，不代表用户实书迁移。
- [指令与开发记录的绑定](quality-instruction-binding.json)：发布的三份创作指令与开发最终文本一致。三题匿名开发比较绑定更早的初版候选，结果为规划持平、正文略偏候选、跨章审稿略偏基线；最终只收紧一句并语义复核，没有重新盲评。[原始输入、结果与限制](../quality-focus-2026-09-11/README.md)保持原字节。

[合成容量](scaling.json)四组合通过；[完整工程检查](verification.json)通过：443项测试中436通过、7按条件跳过，12项整包检查通过。远端 Release、固定标签安装与 GitHub Packages 均已完成回下载核验。本目录将保留实际输出，不把旧版验证数字或单次模型偏好当成新版本的完整质量保证。[此前结果基线](prior-results.json)用于核对830个既有结果及发布说明文件不变。

发布后核验：

- [Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.4)：固定提交 `89945075e0917acff8f23b9fa356c3a8cbba658e`。[附件回下载](release/release.json)与本地ZIP、固定提交源码33个文件一致；下载及安装后再次核对标签。
- [官方固定标签隔离安装](release/remote-install.json)：7个技能、33个文件一致，版本、帮助、初始化与状态4项运行检查通过。
- [远端CI](release/ci.json)：Linux 443项中431通过、12跳过；Windows在首项报告导出测试出现既有WinError 32，后续步骤跳过，因此整轮CI为失败，不代表全平台通过。
- [GitHub Packages](release/packages.json)：公开发布 `@ningcui29/story-skill@0.5.4`；[独立复核](release/package-check.json)确认回下载包与本地产物一致，33个技能文件及4项运行检查通过。
- [本机升级](release/local-install.json)：原位置从0.5.3更新至0.5.4，旧版完整备份，33个文件与Release一致，4项运行检查通过；没有需要合并的本地修改。

[最终核对](release/final-check.json)记录当前文档、发布回执、固定标签和830个既有结果文件的一致性。技能可在下一条消息使用；未显示时重启助手。本版开发比较范围及限制保持不变，不据此承诺作品吸引力或读者留存提升。
