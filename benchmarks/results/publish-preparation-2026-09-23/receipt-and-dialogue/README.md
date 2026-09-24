# 包外回执找回与正文对白分段验证

2026-09-23，Story Skill v0.5.11 开发源码。本轮为离线章节材料包增加同名包外回执、列表找回和按回执复核，并把“场景对白每轮发言独立成行、换说话人另起段”写入写作与审稿规范。正文格式仍由通读判断，不声称 `lint` 能辨认对白与一般引语。

导出成功时，ZIP 与包外回执成对保存；回执保存原始 ZIP 哈希、清单与本书身份。换会话后可用 `publish-export-list` 找回，再用 `publish-verify-export --receipt` 核对。列表中的 `invalid_total` 和 `invalid_receipts` 会报告内容损坏的回执而继续列出其他完整记录；链接和不安全路径仍会拒绝。回执保存期间 ZIP 被改写时，导出不会报告成功。回执与 ZIP 同处本地，不构成独立签名或平台上传证明。

- [全套回归](unit-tests.json)：729 项，712 通过、17 项因本机平台条件跳过，0 失败、0 错误；其中 14 项包外回执专项测试。
- `story-skill`、`story-skill-write`、`story-skill-review` 与 `story-skill-publish` 的技能结构验证均通过，结果见[技能验证](skill-validation.json)；[文档链接](links.json)和差异格式检查通过。
- [开发 ZIP](package.json)逐文件与 38 个源码载荷一致；[隔离安装](install.json)验证 8 个技能、38 个文件；[本地 npm 包](npm-package.json)按该 ZIP 生成并核验内容；[载荷哈希](source-manifest.json)记录打包期间源码未变化。

本轮在 Intel macOS、Python 3.10.21 上运行；未执行 Windows／Linux 原生验证。没有上传七猫或番茄，没有推送 GitHub、发布 Release／Packages，也没有替换日常技能安装。
