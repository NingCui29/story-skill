# v0.6.5 验证记录

本目录保存当前版本的实际验证结果。指令计数、合成容量、迁移与安装检查不代表小说文学质量或真实平台投稿验收。

- `tokens.json`、`tokens.md`：按固定上游提交重新测量的指令输入。
- `scaling.json`：400／4000 章、2000／20000 状态项，strict／local 四组合成容量。
- `migration.json`：合成旧库迁移与回滚。
- `upgrade.json`：托管安装替换、备份、幂等与回滚。
- `verification.json`：完整检查结果。
- `package.json`、`npm-package.json`：本版分发构建清单。
- `release/`：固定标签、远端检查、附件回下载和 Packages 发布回执，完成后补录。

完整检查通过：813 项中 796 通过、17 条件跳过。`verification-initial.json` 保存安装测试夹具版本未同步导致的首次失败；修正后的 `verification.json` 是重新执行整套检查的结果。`npm-package-initial-error.json` 记录本机初次缺少 npm 的错误；准备隔离工具后构建与 `npm-verify.json` 通过。
