# v0.5.3 发布验证

本目录保存 v0.5.3 的独立发布验证。本版于 2026-09-11 17:18:19（北京时间）发布，本版补齐新书书名目录规则，并重写 README、增加从拆书到原创规划的使用说明；运行时只更新版本标识。功能与兼容范围见 [版本说明](../../../docs/releases/v0.5.3.md)。

本版已完成以下检查，不复用旧版数字冒充新测量：

- [ZIP 构建](package.json)：七个技能、33 个载荷文件，118,923 字节。
- [npm 构建](npm-package.json)：33 个技能文件与 ZIP 一致，另核对 2 个包装文件。
- [新书选根试用](opening-trial.json)：独立模型的两项选根试用通过，分别新建书名子目录和沿用既有明确书根，均不自动正文。执行时运行时尚报 0.5.2，三份相关指令与本版字节一致，原回执及可读产物按原状态保留；不是固定标签新试用。
- [整套升级](upgrade.json)：v0.5.2→v0.5.3 共 18 项通过，检查旧版备份、完整更新和重复执行。
- [旧库迁移](migration.json)：固定旧工具生成的三类 schema 1 合成夹具通过，不代表历史实书重验。
- [指令成本](tokens.md) 与 [文件哈希](tokens.json)：本版普通／多线写作 6,821／9,008 tokens，深读／含示范 5,010／7,019 tokens；排除原文、报告、推理、工具输出和账户实际用量。
- [合成容量](scaling.json)：400／4,000 章、2,000／20,000 张卡片，strict／local 四种组合通过；不代表持续创作或文学质量。
- [历史结果基线](prior-results.json)：已保存旧回执哈希，收尾确认原字节不变。

[完整工程检查](verification.json) 已通过：本机 Intel macOS／Python 3.12 运行 443 项测试，436 项通过、7 项按条件跳过、零失败或错误，12 项整包检查通过。Windows 的既有正文和报告导出 WinError 32 未在本版修复，不声明全平台通过，也不开展新一轮文学评测。

## 远端发布与独立核验

- [固定标签](release/tag.json) `v0.5.3` 已推送，指向 `ccb186b94f121103b09f88cb4067f692bfc2a381`，不随后续发布文档更新移动。
- [CI 34583076198](https://github.com/NingCui29/story-skill/actions/runs/34583076198) 与 [分项回执](release/ci.json)：Linux 443 项中 431 项通过、12 项按平台跳过，全部步骤成功；Windows 执行首项时报告导出触发已知 WinError 32 后停止，整体 CI 为失败。
- [Release 回下载](release/release.json)：ZIP、校验文件与 GitHub 服务端摘要通过核对，33 个技能文件与固定提交、源码及本地包一致；ZIP SHA-256 为 `6d5acf81f9d7d21dfa0719fad2dd50bf8ae0c2bed0c21fa85485924084768044`。
- [官方固定标签隔离安装](release/remote-install.json)：7 个技能、33 个文件逐字节匹配，版本、帮助、初始化、状态四项 CLI 通过，临时数据已清理。
- [GitHub Packages 工作流](release/packages-workflow.json) 成功；[原始回执](release/packages.json) 与 [本机独立复核](release/package-check.json) 确认包版本 0.5.3、归档摘要、注册表 SHA-512、33 个技能文件、2 个包装文件与四项 CLI 通过。回下载 tarball SHA-256 为 `cd27f60476a8bb764c795192c0d9094f37840dc2371eb8913f63e491251e1d8b`，与本地构建相同。
- [本机技能更新](release/local-install.json)：保留完整旧版后更新到 0.5.3，33 个文件与四项 CLI 通过，与官方隔离安装分别记录。

[逐项状态](release/state.json) 汇总文件一致性、四项 CLI 检查、平台边界与本机安装状态。安装与运行验证不等于 助手 UI 自动发现或文学质量验证。GitHub npm 下载仍需认证，npm 不会自动注册 助手 技能。
