# v0.5.11 发布验证

本目录区分 2026-09-24 的[本机检查与汇总](local-verification.json)、固定提交的远端 CI、公开 Release 附件、官方隔离安装以及 GitHub Packages 工作流与 artifact 独立复核。v0.5.11 固定标签指向 `9cdf54e75a07e3b61a7b55aa190837608b36d487`；之后补录文档不会移动标签。

| 检查 | 实际结果 |
|---|---|
| 本机工程检查 | 754 项单元测试中 737 通过、17 按条件跳过；[冒烟](smoke.json)、[中文稿](chinese.json)与[多线写作](long.json)验收通过。这些检查不代表远端平台状态或文学质量。 |
| 套件 ZIP | `dist/story-codex-0.5.11.zip` 为 180,441 字节，含 8 个技能、38 个载荷文件；本地源码、固定提交、公开 Release 回下载逐文件一致，SHA-256 为 `60edb470c255c436aa7da74ab02ff0a35e1b5402693908f1f89fcca48b56cf48`，同名校验文件一致。[附件回执](release/release.json) |
| 发布脚本定点测试 | `test_package_npm.py` 47 项、`test_sync_packages.py` 19 项，均通过。 |
| Windows／Linux CI | [main 推送运行 35969424318](https://github.com/NingCui29/story-skill/actions/runs/35969424318) 与[固定标签运行 35970009432](https://github.com/NingCui29/story-skill/actions/runs/35970009432) 两平台所有步骤成功；[首次运行 35968080584](https://github.com/NingCui29/story-skill/actions/runs/35968080584) 中 Windows 单测失败，修复后在固定提交重跑通过。[main 回执](release/ci.json) · [标签回执](release/ci-tag.json) · [首次回执](release/ci-first.json) |
| Release 与安装 | [Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.11) 于 2026-09-24 15:33:07（北京时间）发布，ZIP 与校验文件公开回下载一致；官方安装器从固定标签隔离安装 8 个技能、38 个文件，并通过版本、帮助、初始化、状态四项命令。[发布回执](release/release.json) · [安装回执](release/remote-install.json) |
| GitHub Packages | [工作流 35970230332](https://github.com/NingCui29/story-skill/actions/runs/35970230332) 使用 Node 24，从 Release 附件构建并报告发布、注册表回下载 `@ningcui29/story-codex@0.5.11`；独立复核取回工作流 artifact，确认 38 个技能文件、2 个包装文件与公开 Release 一致，tarball SHA-256 为 `bd0c973f2d37ccc942d2c49bf7178a5e1eb6232da3310845d698e45164071cd9`，四项 CLI 通过。[工作流回执](release/packages-receipt.json) · [独立复核](release/packages-independent.json) |
| 本机 npm 构建与直连 | 本机没有 `npm` 命令，未在本机打包；本机账号直接访问 Packages API 收到 403，因此没有声称本机直接从注册表回下载。 |

软件、载荷和平台 CI 核验不证明小说文学质量，也不代替七猫或番茄的投稿、审核与发布结果。Windows CI 成功仅代表本次 GitHub 托管环境中的检查范围。
