# 2026-09-22 全量检查

当前未发布源码的本地功能检查通过，本轮发现并修复 2 处活动文档的版本说明。没有发现新的确定运行时缺陷；这不是新版本的发布验收，也不表示文学质量已经提高。

环境：Intel Mac，macOS 15.8，Python 3.10.21。基于提交 `40a5f3515c0f3762c7af20c26d0dbaf83e15816a` 加当前未提交改动，运行时版本标识仍为 `0.5.9`。本轮没有发布、改写既有版本证据或更新真实技能安装。

## 本轮修复

1. [超长篇实操](../../../docs/超长篇实操.md) 原称整页适用 v0.5.4，却使用 v0.5.5 才新增的 `history-saved`。现在区分历史基础流程与新增能力，并统一从 INSTALL 核对当前安装来源。
2. [评估指南](../../../docs/evaluation.md) 原称 v0.5.5 为当前版本。现在将其限定为历史验证归属，当前安装仍从 INSTALL 核对。

两处文档采用字节替换，保留原有换行。之前已完成的分卷路径、短篇合并、补录、平台分类、中文写作及完本审查修改全部纳入本轮检查；本轮没有再次修改这些实现。

## 验证结果

| 范围 | 实际结果 | 证据 |
|---|---|---|
| 全套单元回归 | 595 项：588 通过、7 跳过、0 失败、0 错误；71.887 秒 | [回执](unit_tests-check.json) · [7 项 Windows 专用条件](platform-skips.json) |
| CLI 与中文稿件 | 合成创作/拆文通路、固定中文长短篇重放、11 段双线历史修订演练通过 | [CLI](smoke.json) · [中文稿件](chinese.json) · [双线与历史](long-form.json) |
| 七技能 | 7 份入口通过官方基础格式校验；7 份 agents 元数据及 8 份共享引用完成规则核对 | [格式校验](skill-validation.json) · [规则复核](workflow-docs-review.json) |
| 活动文档与引用 | 修后 27 份 Markdown 的 489 处本地链接、其中 55 处锚点均有效；另检查全仓本地文件引用 | [活动文档](workflow-docs-review.json) · [最终文件引用](final-link-check.json) |
| 隔离安装 | 当前源码的 7 技能、34 文件安装及命令检查通过 | [安装](isolated_install-check.json) |
| 容量 | 400 章/2,000 卡、4,000 章/20,000 卡各测 strict/local；4 组、每组 7 项操作及硬约束超载保护通过 | [容量](probes/scaling.json) |
| 旧版迁移 | 固定 v0.2.0 的长篇、短篇、拆文 3 份夹具通过；原文及旧版回滚副本保留 | [迁移](probes/migration.json) |
| 源码升级 | v0.4.0 至当前七技能源码的 16 项检查通过，包括完整备份、幂等重试及旁侧文件保护 | [源码升级](probes/upgrade-canonical-source.json) |
| ZIP / npm | 当前 34 文件 ZIP 与源码逐字一致；实际离线 npm 打包、独立验证及解包运行通过；5 种 ZIP 成员类型保护与 npm 规则一致 | [内容链](packaging/summary.json) · [成员类型](packaging/entry-type-consistency.json) |
| 静态指令计数 | 固定上游提交、tiktoken 0.14.0，7 个既定场景重新生成 | [token 报告](tokens/README.md) |
| 输入稳定性 | skills、scripts、tests 共 100 个文件在回归前后未变；token 复核另绑定 224 个当前源文件和 605 个上游文件 | [回归快照](source-snapshot.json) · [稳定性](input-stability.json) · [token 运行回执](tokens/run-receipt.json) |

中文规则走查覆盖长短篇完本、开放结尾、首次及修订后的短篇全文/封面交付、整书纯审、普通单章、局部校对、不可靠叙述、信息反转与行动逆转。走查没有发现仍把局部修订扩成全书重写、把合并成功等同于故事完结、或把所有转折强制写成秘密揭晓的规则。

## 尚不能据此宣称通过的范围

- **正式发布仍需独立验收。** 现有 `dist` 与 v0.5.9 回执绑定历史发布源码，本次源码已经变化。历史 token/容量/迁移回执与当前源码的绑定检查因此失败；原地升级探针也拒绝把旧 ZIP 当作当前源码。保留[绑定检查原始结果](historical-evidence-binding.json)和[旧 ZIP 拒绝回执](probes/upgrade-with-existing-release.json)，新结果仅放在本目录。没有将默认发布核验流程标记为全部通过。
- **Windows 未在本机验证。** 本轮 7 项 Windows 专用测试跳过；仓库既有 WinError 32 限制没有在本轮修复或重新验证，安装范围仍以 [INSTALL](../../../INSTALL.md) 为准。也没有重新执行远端 Linux/Windows CI、Release 回下载或 GitHub Packages 注册表验证。
- **功能重放不是文学实测。** 没有重新生成完整小说、封面或进行新一轮盲评；固定中文稿件重放与规则走查不能证明作品更吸引人。
- **容量与 token 有统计边界。** 容量采用一次暖态合成探针：4,000 章 strict 的 context 约 4.21 秒、commit 约 13.25 秒，不是实际小说性能保证。短篇必读指令为 10,234 tokens，相对上游仅入口下限减少 1.8%；固定指令体积不等于账单或整轮用量，仍有精简空间。

npm 首次因环境没有可用命令而失败，之后使用本机已有 Node/npm、隔离配置和离线模式完成；[初次环境失败](packaging/initial-environment-failure.json)仍单独保留。临时 ZIP/npm 和书库路径仅用于记录执行过程，不能当作已发布附件或真实书库。

[结构化汇总](summary.json) · [差异检查](diff-check.json) · [本轮证据文件清单](evidence-manifest.json)
