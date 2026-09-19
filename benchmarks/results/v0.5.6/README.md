# v0.5.6 验证与发布记录

本目录只登记 v0.5.6 实际完成的检查。目标平台为 macOS／Linux；Windows 既有 `WinError 32` 导出问题未在本版修复，安装仍固定 v0.4.0。[版本说明](../../../docs/releases/v0.5.6.md) · [安装指引](../../../INSTALL.md)

| 环节 | 当前证据 |
|---|---|
| 本地单元测试 | Python 3.12：559 项中 552 通过、7 按条件跳过；完整工程回执见 [verification.json](verification.json) |
| 指令计数 | [本版重测](tokens.md)：固定上游提交与本版技能文件逐一计数；只计指令输入，不推断实际账户消耗或文学质量 |
| 合成容量与迁移 | [四组容量](scaling.json)覆盖 400／4,000 章、2,000／20,000 卡与 strict／local；[三类旧库迁移](migration.json)使用生成的 schema 1 夹具，不代表真实用户书库 |
| ZIP | [package.json](package.json)：33 个技能载荷，SHA-256 `d82f7cb7f195260f26b65ea155f515b579ff672cd9da03fbd6467775fffcaba2`；附件同名 `.zip.sha256` |
| npm 包 | [npm-package.json](npm-package.json)构建 `@ningcui29/story-codex@0.5.6`；[npm-verify.json](npm-verify.json)逐文件核对 ZIP 与本地 tarball |
| 固定标签与 Release | 发布后核对标签提交、附件回下载及 SHA-256，不以本地构建代替远端验收 |
| 平台 CI | 发布提交后读取 Linux／Windows 的实际运行结果；Windows 已知问题单独列明 |
| GitHub Packages | Release 发布后由工作流同步，再核对注册表回下载的 33 个载荷文件及包装文件 |

开发阶段的分析修正、人物选择、试写和审稿分流原始材料仍在各自的 2026-09-19 目录，不追认为本版独立文学评阅。单元测试与文件哈希只能证明对应工程检查，不证明小说质量；远端状态以实际回执更新。
