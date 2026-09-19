# v0.5.7 验证与发布记录

本目录登记 v0.5.7 实际完成的检查。目标平台为 macOS／Linux；Windows 的既有 `WinError 32` 导出问题未修复，安装目标仍为 v0.4.0。[版本说明](../../../docs/releases/v0.5.7.md) · [安装指引](../../../INSTALL.md)

| 环节 | 当前证据 |
|---|---|
| 本地工程验证 | [完整回执](verification.json)：Python 3.10 的 560 项测试中 553 通过、7 项按条件跳过，零失败；CLI、中文稿件和长篇回放、七个技能格式、项目安装及本地链接均通过 |
| 合成容量与迁移 | [四组容量](scaling.json)覆盖 400／4,000 章、2,000／20,000 卡与 strict／local；[三类旧库迁移](migration.json)使用生成的 schema 1 夹具，不代表真实用户书库 |
| 本地 ZIP | [构建回执](package.json)：34 个技能文件，SHA-256 `dc798d6880a682932f43f627bd7327e80ab8236a7c7f353024f398f5f1e6cb7d` |
| 本地 npm 包 | [构建](npm-package.json)、[逐文件核验](npm-verify.json)及[临时安装四项命令](npm-smoke.json)通过，34 个载荷文件与 ZIP 一致，另有 2 个包装文件 |

固定标签、Release 附件回下载、远端 CI、官方固定标签安装和 GitHub Packages 注册表回下载的结果将在完成后逐项补记；未完成的项目不视为通过。工程检查不证明标签适合某本书，投稿前仍须按实际页面核对。
