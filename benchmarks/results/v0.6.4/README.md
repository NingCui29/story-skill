# v0.6.4 验证记录

- `verification.json`：最终完整验证，812 项中 795 通过、17 条件跳过；所有整包检查通过。
- `verification-initial.json`：首轮未通过；版本拒绝用例仍为旧值、格式校验器缺少依赖、容量尚未结束。最终报告重新执行全部检查。
- `scaling.json`：400／4000 章、2000／20000 状态项，strict／local 四组合成容量；不代表真实长篇质量。
- `migration.json`：临时合成旧库的迁移和回滚，原树不变。
- `upgrade.json`：托管安装更新、备份、幂等与回滚。
- `package.json`、`npm-package.json`：本地 ZIP 与 npm 构建清单。
- `release/`：发布后补录固定标签、远端 CI、公开附件回下载、隔离安装与认证 Packages 回下载结果。

正文规则本轮未以真实新小说进行文学质量验收。未上传作者书稿、测试篇或旧示例小说。Windows 自动化检查不等同于 Windows 桌面浏览器人工验收。
