# v0.5.10 验证记录

本版修复 Windows 导出，并发布短篇全文合并、旧原章补齐、完本封面交付、总纲平台分类和中文语法标点指导。[版本说明](../../../docs/releases/v0.5.10.md) · [安装指引](../../../INSTALL.md)

| 环节 | 实际结果与证据 |
|---|---|
| 本机工程验证 | Intel macOS / Python 3.10：605 项中 589 通过、16 个 Windows 专用检查跳过；12 组检查通过，含七技能格式、隔离安装、中文稿件及历史修订重放。[完整回执](verification.json) |
| 后续测试兼容修正 | 保持技能载荷不变，修正 Windows 换行、路径与文件哈希夹具；109 项针对回归通过。[回执](fixture-followup.json) |
| 原生 Windows / Linux | 固定提交 `0f2bb216f2b1422ab615d990fdc97c60d7ec6941` 的[工作流 35716909379](https://github.com/NingCui29/story-skill/actions/runs/35716909379)两端全部步骤成功，含回归、打包、CLI、中文稿件和长篇历史修订；9 项新增 Windows 目录保护测试全部通过。[CI 回执](release/ci.json) · [测试结果](release/ci-tests.json) |
| 容量与迁移 | [四组容量](scaling.json)、[三类旧书库迁移](migration.json)通过；均绑定本版运行时 |
| 旧版升级 | [v0.5.9 → v0.5.10](upgrade.json)、[v0.4.0 → v0.5.10](upgrade-from-0.4.0.json)各 18 项通过，原版完整备份及旁侧文件保护已验证 |
| 指令计数 | [本版静态测量](tokens.md) · [逐文件数据](tokens.json)，短篇固定指令为 11,033 tokens，相对上游入口下限增加 5.86%；不等于实际账户用量 |
| ZIP | 7 技能、34 文件，SHA-256 `821e896a9cb3066dd6daaf56c921217ece468629622d95c597f19f6733897797`。[构建](package.json) · [固定标签与公开回下载](release/release.json) |
| npm | [本地构建](npm-package.json)、[清单复核](npm-verify.json)、[运行检查](npm-smoke.json)通过；34 技能文件与 ZIP 一致，另有 2 个包装文件 |
| 官方固定标签安装 | [隔离安装](release/remote-install.json) 7 技能、34 文件与公开 ZIP 逐文件一致；版本、帮助、初始化、状态均通过 |
| GitHub Packages | [@ningcui29/story-codex@0.5.10](https://github.com/users/NingCui29/packages/npm/package/story-codex) 已发布；注册表回下载和摘要核对见[工作流回执](release/receipt.json)，下载产物的[独立复核](release/packages-independent.json)通过 |

首轮被测试样本的换行转换中断，第二轮完成全部测试并定位 30 处相关失败：[首轮](release/ci-first.json) · [第二轮](release/ci-second.json) · [错误日志](release/ci-second-tests.log)（去除时间前缀和行尾空白）。均保留失败记录；最终版本修正样本，未删除或跳过失败断言。历史版本的 Windows 失败记录不改写。

Windows 验证限定本版 GitHub 托管原生环境；其他文件系统、网络共享和设备未据此宣称实测。工程检查与规则审查不证明文学质量提升，未进行本轮新小说盲评或持续长篇创作实测。本次没有更新真实用户技能安装；固定标签不随 main 文档补录移动。
