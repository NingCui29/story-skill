# v0.5.2 发布验证

本目录保存 v0.5.2 的独立发布验证，不复用旧版数字冒充新测量。本版于 2026-09-11 16:06:25（北京时间）发布；功能与兼容范围见 [版本说明](../../../docs/releases/v0.5.2.md)。

- [ZIP 构建](package.json)：七个技能、33 个载荷文件。
- [npm 构建](npm-package.json)：载荷与 ZIP 一致；保留旧版包装内容，v0.5.2 明确 macOS/Linux 范围与 Windows 已知限制。
- [v0.5.1→v0.5.2 升级](upgrade.json)：隔离项目的整套更新、旧版保留与重复升级检查。
- [旧库迁移](migration.json)：固定旧工具生成的三类 schema 1 合成夹具，不代表历史实书重验。
- [静态指令成本](tokens.md) 与 [文件哈希](tokens.json)：重新计数，不是实际账户用量或文学质量证据。
- [百万／千万字合成容量](scaling.json)：400／4,000 章、2,000／20,000 张卡，strict／local 四组合通过；不是持续创作或文学质量验证。
- [历史结果基线](prior-results.json)：发布开始前保存的旧回执哈希，收尾核对原字节不变。

[完整工程检查](verification.json) 已通过：本机 Intel macOS／Python 3.12 运行 443 项测试，436 项通过、7 项按条件跳过、零失败，12 项整包检查通过。Windows 的已知正文和报告导出问题没有纳入本次修复，不声明全平台通过。

## 远端发布与独立核验

- [固定标签](release/tag.json)：`v0.5.2` 指向 `c50bd28b14b128e287dc18f7cf12a692c4da82be`，不随后续发布文档更新移动。
- [最终 CI 34577137667](https://github.com/NingCui29/story-skill/actions/runs/34577137667) 与 [分项回执](release/ci.json)：Linux 443 项中 431 项通过、12 项按平台跳过，全部步骤成功；Windows 执行 1 项、1 项失败，确认报告导出 WinError 32，整体 CI 为失败。
- [Release 回下载](release/release.json)：ZIP、校验文件和 33 个技能文件与固定提交、源码及本地包一致；ZIP SHA-256 为 `eef07ccff5de52c73c86c1f60682535b6f49203331a3e4c04a37187884188c8a`。
- [官方固定标签隔离安装](release/remote-install.json)：7 个技能、33 个文件逐字节匹配，版本、帮助、初始化、状态四项 CLI 通过；临时数据已清理。
- [GitHub Packages 工作流](release/packages-workflow.json) 成功，[原始回执](release/packages.json) 与 [本机独立复核](release/package-check.json) 确认公开包 0.5.2、注册表 SHA-512、33 个技能文件与 2 个包装文件，以及四项 CLI 均通过。回下载 tarball SHA-256 为 `f58e3186b4d153295d4de2b0ef8e18dd04fb6f79dbd46444e62ebe2cd9891d66`，与本地构建相同。
- [本机技能更新](release/local-install.json)：保留旧版后同步 0.5.2，33 个文件及四项运行检查通过，与隔离安装分别记录。

[逐项状态](release/state.json) 与 [最终核对](release/final-check.json) 汇总分发范围和历史文件保护。本页的安装验证不等于 助手 UI 自动发现或文学质量验证。GitHub npm 下载仍需认证，npm 不会自动注册 助手 技能。
