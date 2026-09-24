# v0.5.5 发布验证

**状态：已于 2026-09-12 19:53:27（北京时间）发布。** 目标平台为 macOS／Linux，Windows 的既有 WinError 32 未修复，安装继续固定 v0.4.0。七个技能、33 个载荷文件和 schema 2 的布局沿用；本版修改运行时与技能指导，不是只更新版本号。[版本说明](../../../docs/releases/v0.5.5.md) · [发布状态表](../../../docs/github-release.md#v055-发布验证记录)

## 本版验收与分发

本表只登记 v0.5.5 新产生的实证；尚未产生的 JSON 不以空文件代替，也不复用旧版数字。

| 环节 | 状态 |
|---|---|
| ZIP 与 npm 构建 | 已完成：[ZIP](package.json) 33 文件、126,882 字节；[npm](npm-package.json) 109,898 字节，33 个载荷文件与 ZIP 一致，另有 2 个包装文件 |
| 完整工程验收 | 已完成：[完整回执](verification.json) 543 项中 536 通过、7 按条件跳过，零失败或错误；12 项整包检查全部通过，162 份 Markdown 的 813 个本地文件链接核对通过 |
| 指令 token 计数 | 已完成：[本版计数](tokens.md)普通／短篇 7,023、多线 9,610、深读 5,068、含示范 7,077、审稿 3,063、冲突审稿 4,528；只统计固定指令 |
| 合成容量 | 已完成：[四组合](scaling.json) 400／4,000 章、2,000／20,000 张卡，strict／local 均通过；不代表真实长篇持续创作 |
| 整套升级与旧库迁移 | 已完成：[v0.5.4→v0.5.5 含 ZIP 升级](upgrade.json) 18 项通过；[三类合成 schema 1 迁移](migration.json)通过，不是用户实书迁移 |
| 固定标签提交 | 已核验：`v0.5.5` → `c53703bb57f42504022edb1c5d5011943e53cdf1`；[标签与源码](release/release.json)在下载及安装前后保持一致 |
| 发布提交 CI | [CI 34692130624](release/ci.json)：Linux 全步骤成功；Windows 首项报告导出复现既有 WinError 32，后续检查跳过，整体 CI 为失败 |
| Release 与附件回下载 | 已通过：[Release 回执](release/release.json)确认 ZIP/checksum 回下载、服务端摘要与固定提交源码的 33 文件一致 |
| 官方固定标签隔离安装 | 已通过：[安装回执](release/remote-install.json)确认 7 技能、33 文件与固定提交／Release 一致，版本／帮助／初始化／状态 4 项检查通过 |
| GitHub Packages | 已通过：[工作流](release/packages-workflow.json)公开发布并回下载，[注册表回下载回执](release/packages.json)与[归档产物独立复核](release/packages-independent.json)确认 35 个文件与本地产物一致，4 项运行检查通过；本机直连注册表 HTTP 403 |
| 本机安装 | 未更新：本次未请求安装，不作为发布验收阻塞项，不能由发布状态推定已安装 |
| 历史证据与最终核对 | [最终核对](release/final-check.json)通过：865 份历史文件与原基线一致，当前文档链接、发布回执与固定标签载荷保持一致；最终状态记录随 main 文档提交，不移动标签 |

构建摘要：ZIP SHA-256 为 `c3b621859256ef9cdd350bcfde1d09c959c7e4b45c423082b83b26133e1711db`；npm tarball SHA-256 为 `a46b6116551cac1405417b199c61ddd8d6ef00d93b23c2de176961bb7c663eb6`。Release 回下载 ZIP 与本地产物一致。npm 工作流从注册表回下载的 tarball 也与本地构建一致；独立复核读取该工作流的归档产物。本机直接访问注册表返回 HTTP 403，不据此宣称本机注册表下载权限已验证。

[新增指令独立兼容复核](instruction-review.json)已完成：近期细纲范围在共享状态和长篇说明中对齐为按授权确定、未指定时默认 3—5 章；该问题经定点复核关闭。局部点评与资料问答默认保存边界未发现需再修项；这是静态指令复核，未开展新的文学比较。

## 已有维护证据

以下是本版改动的开发验证，均保留原来的运行时版本 0.5.4、日期、输入及适用范围，不冒充固定 v0.5.5 的验收。

| 阶段 | 原始记录与范围 |
|---|---|
| 首次审查修正 | [audit-fixes](../audit-fixes-2026-09-12/validation.json)：全局规则、标题字数、未知时间余额、同刻度候选、状态快照及历史／安装恢复说明 |
| 读取与收支跟进 | [audit-followup](../audit-followup-2026-09-12/validation.json)：同刻度收支、世界检查快照、只读正式依赖 |
| 证据与最低支出 | [audit-continuity](../audit-continuity-2026-09-12/validation.json)：卷段／故事线候选、世界警告、已知最低资金不足 |
| 物件交接 | [audit-handover](../audit-handover-2026-09-12/validation.json)：关联改变后补读同槽事实，查询去重 |
| 认知与历史卡片 | [audit-state-review](../audit-state-review-2026-09-12/validation.json)：认知后续召回、额外卡片变更的修订扩围 |
| 分支恢复与世界关系 | [audit-branch-recovery](../audit-branch-recovery-2026-09-12/validation.json)：完整保存决定回读、世界变更消费者及显式关系传递；最终 543 项中 536 通过、7 跳过，20 项 CLI 冒烟通过 |

最后一轮维护记录包含 [独立 CLI 恢复比较](../audit-branch-recovery-2026-09-12/recovery-comparison.json)和[独立读取与范围审查](../audit-branch-recovery-2026-09-12/read-boundary-review.json)。作者计划事实到规则再到正式章的漏范围反例已修正并原样复验：范围扩大、旧审查失效，正式内容不变。合成夹具均不使用用户小说作探针。

本版另外调整局部点评、简单调研的默认保存行为及未指定时的细纲范围；发布验证已绑定最终技能文本。工程测试不等于文学质量评估，也不证明 助手 界面发现、路由或长期创作表现。历史版本的原始测量和 [v0.5.4 验证](../v0.5.4/README.md)不改。
