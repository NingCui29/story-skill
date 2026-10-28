# 2026-10-20 至 2026-10-28 开发验证记录归档

2026-10-28，将此前仅保存在本地的 21 组开发记录、共 1,202 个文件纳入源码仓库。记录保留各次验证发生时的版本、环境、失败、修复和验收范围；其中“未提交”“未发布”等描述是当时的状态，不代表本次归档重新执行了验证或发布产品。

本次仅提交记录和恢复引用链接，没有重新打包、创建 Release、标签或版本分支。随记录归档的 18 个 ZIP 是当时已存在的隔离测试产物，字节与原始记录一致，不是新增正式分发附件。15 张截图是已有测试界面的记录，保持原图。

## 隐私处理与证据边界

9 份 JSON 回执中的 27 处本地临时服务访问令牌替换为 `REDACTED-LOCAL-TOKEN`；不改变状态码、检查结果、版本、载荷摘要或其他测试数据。公开副本的字节摘要因此变化，不能用原文件摘要验证脱敏后的整份回执。原始文件已完整保存在维护者本地的 Git 忽略目录，以下列出对应摘要以明确区别。

| 回执 | 原始 SHA-256 | 公开副本 SHA-256 |
| --- | --- | --- |
| [development-20261020-classification-status/local-plugin-isolated-verification.json](development-20261020-classification-status/local-plugin-isolated-verification.json) | `ef6c7132f4431e8db8786d50cc1634ba9c43a129003afdb03c3b9683998792a3` | `1b0d96b0eae60ab53c6d90341ec064a349193c4370fced6bb5df2db149bb1deb` |
| [development-20261027-budget-links-install/codex-isolated-verification.json](development-20261027-budget-links-install/codex-isolated-verification.json) | `46f531a0d9802b894feca0a2fe9916d3852a4f9c1f19fa2f8e5f9270be33e4a3` | `cabe3b76192f6d6009817583b578d30f25c47026b4ef60b9a953d65ae919f212` |
| [development-20261027-budget-links-install/installation-after.json](development-20261027-budget-links-install/installation-after.json) | `de8e2e6cd7c7c6255f0e1e5142ef285be5d4d557b8a39bdd12fd6f1e0bd55055` | `12f621c54303c02d6284190f814c0bb7c47af1e68e42456b1742b39c57922cee` |
| [development-20261027-host-stop-platform/codex-temporary-host-before-compatibility-fix.json](development-20261027-host-stop-platform/codex-temporary-host-before-compatibility-fix.json) | `24c5cc63d7c4846b234f8c239a7635ffa74453a6160478e4a0e957935c518764` | `b5645d49691fbc4740a726d893b64f70b67bf05b61b3488f65640894d39040d4` |
| [development-20261027-host-stop-platform/codex-temporary-host-before-directory-race-fix.json](development-20261027-host-stop-platform/codex-temporary-host-before-directory-race-fix.json) | `ec72469265ec4b261783a8bf4fbbc311606dcfefd4534dd2ceaabc1d0b56e435` | `7ab63c689baaac44cacbba8e8fb944bc9cd707868c9e6f585c5683eeb49b0fb1` |
| [development-20261027-host-stop-platform/codex-temporary-host-before-final-fixes.json](development-20261027-host-stop-platform/codex-temporary-host-before-final-fixes.json) | `a8f360b7b37d9c0a9f3c0e5123624fe35d5319c5c0384d1951f66e3461216aea` | `869c2bd33a2d7cf7b9609a51ae05d86370ec6c742a88805a1b69410b981e8e65` |
| [development-20261027-host-stop-platform/codex-temporary-host-verification.json](development-20261027-host-stop-platform/codex-temporary-host-verification.json) | `858bea25a05fe55260f7e3f75633ef2fb1f5d3961235d88b54a26205f5a942b4` | `2323d68f794904094d65b164c1c0077523c3f92a730682a9406c9e9d70de8a43` |
| [development-20261027-six-priorities/distribution-verification-before-catalog-fix.json](development-20261027-six-priorities/distribution-verification-before-catalog-fix.json) | `ba44289df4a5760f0944ba51362a5467f16a7c294dac47db75216359126f657c` | `d77f0181128cdc3e817fc2a18c9cf3579d914b44b2cf55f83404d8c65c2f331e` |
| [development-20261027-six-priorities/distribution-verification.json](development-20261027-six-priorities/distribution-verification.json) | `a32d5a39bdf7415b491776ff3ad4d5282ba1b3327144b7111fa58d47d1f9870f` | `8d25c6b7bd33faf96df9e80d5724ce29e984f05d2e82ccff7db7b458ac40d02e` |

## 归档目录

- [development-20261020-classification-status](development-20261020-classification-status/)：8 个文件。
- [development-20261020-material-scan](development-20261020-material-scan/)：6 个文件。
- [development-20261021-workbuddy-qoder-cn](development-20261021-workbuddy-qoder-cn/)：62 个文件。
- [development-20261022-rank-scan](development-20261022-rank-scan/)：16 个文件。
- [development-20261022-research-handoff](development-20261022-research-handoff/)：32 个文件。
- [development-20261023-editor-readback-loop](development-20261023-editor-readback-loop/)：219 个文件。
- [development-20261023-eight-fixes](development-20261023-eight-fixes/)：19 个文件。
- [development-20261023-followup-loop](development-20261023-followup-loop/)：145 个文件。
- [development-20261023-four-followups](development-20261023-four-followups/)：16 个文件。
- [development-20261023-three-followups](development-20261023-three-followups/)：40 个文件。
- [development-20261023-two-followups](development-20261023-two-followups/)：27 个文件。
- [development-20261024-five-fixes-loop](development-20261024-five-fixes-loop/)：163 个文件。
- [development-20261024-six-fixes-loop](development-20261024-six-fixes-loop/)：245 个文件。
- [development-20261027-budget-links-install](development-20261027-budget-links-install/)：33 个文件。
- [development-20261027-host-stop-platform](development-20261027-host-stop-platform/)：87 个文件。
- [development-20261027-novel-screenplay](development-20261027-novel-screenplay/)：12 个文件。
- [development-20261027-six-priorities](development-20261027-six-priorities/)：18 个文件。
- [development-20261028-chapter-minimum](development-20261028-chapter-minimum/)：6 个文件。
- [development-20261028-chapter-minimum-handoff-review](development-20261028-chapter-minimum-handoff-review/)：16 个文件。
- [development-20261028-chapter-minimum-loop](development-20261028-chapter-minimum-loop/)：13 个文件。
- [development-20261028-chapter-minimum-scope-review](development-20261028-chapter-minimum-scope-review/)：19 个文件。
