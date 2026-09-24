# oh-story-claudecode 分析与 Story Skill 设计

分析日期：2026-09-08。对象：[zenstory-ai/oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode)，版本文件为 0.7.9，固定提交 `4daac79077928d0d5ba0eda93e46ce68dfcd40ae`。本次浅克隆只读检查，未运行上游部署器，未修改全局已安装技能或任何小说项目。

## 判断

这个仓库有扎实的网文方法和很多真实使用后的修正，值得保留的是“意图约束—局部上下文—写作—审查—增量追踪”的闭环。对于只用 助手 的作者，主要优化机会是缩小每次必须进入模型的指令面，而不是删掉字数要求、减少正文、漏拆章节或靠更便宜的模型掩盖流程浪费。

本次实现得到更短的指令和可复现的状态工具。尚无相同任务下的真实模型用量对照，也没有小说盲评，因此不能宣称所有维度全面优于上游。

## 上游已有的优点

| 机制 | 源码证据 | 意义 |
|---|---|---|
| 按阶段、题材和消费者选择参考 | [长篇入口](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-write/SKILL.md) | 不是把全部资料一次加载的朴素方案 |
| 增量状态与角色快照 | [state-tracking.md](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-write/references/state-tracking.md) | 关注“缺少就会写错”的事实与因果 |
| 事务化追踪和机器字数口径 | [tracking_commit.py](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-write/scripts/tracking_commit.py) | 追踪不只依赖一段聊天摘要 |
| 短篇允许主会话分批写作 | [短篇写作 L177-L185](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-short-write/SKILL.md#L177-L185) | 不能笼统指责全部写作强制多代理 |
| 大书分层聚合、可恢复章节边界 | [长篇拆文](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-analyze/SKILL.md) | 避免全量原文与摘要堆在主线程 |
| 完整拆解请求能跳过预览询问 | [L139-L149](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-analyze/SKILL.md#L139-L149) | 上游已处理“持续完成”的明确授权，不能把重复询问说成无例外规则 |

## 具体的开销来源

**1. 入口长，触发后即整份进入上下文。** 上游有 13 个 SKILL.md；本次读取的长篇写作入口约 342 行，短篇写作约 388 行，审稿约 493 行，部署约 547 行。入口混合任务路由、产物约束、平台适配、agent 回退和大量参考索引。按需发现只会推迟成本，并不会让触发后的长入口自动免费。[助手 官方技能加载规则](https://learn.chatgpt.com/docs/build-skills)

**2. 单章有明确的全量必读集合。** 长篇入口 L13-L22 指定完整读取 workflow-chapter、long-format、writing-craft、long-chapter-quality、long-chapter-hooks；悬疑、反转还会加文件。按这六份文件统计得到 30,979 tokens，尚未加入正文、细纲、对标、工具回执和子代理。[原始规则](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-write/SKILL.md#L11-L22)

**3. 语义方法和完成证明同时偏重。** workflow-chapter 既处理场景写法，又编排对标召回、风格基、多个检查和追踪收口。有些规则值得保留，但部分可由程序做一次确定性计算，没必要把长流程重新解释给模型。[单章流程](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-long-write/references/workflow-chapter.md)

**4. 默认审查存在上下文复制成本。** story-review 默认 full，优先四名 reviewer；lean 两名，solo 可用。独立视角可能增加质量，但不该把其成本视为零，也不能把同会话自审伪称同等的独立审查。[审稿模式 L23-L28](https://github.com/zenstory-ai/oh-story-claudecode/blob/4daac79077928d0d5ba0eda93e46ce68dfcd40ae/skills/story-review/SKILL.md#L23-L28)

**5. 跨平台兼容需要较多分支。** 原仓库为多个 CLI、agent 注册格式、hooks 和部署路径服务。这是合理的产品范围，但用户已明确只需要 助手，可以直接收窄安装与运行契约。[仓库说明](https://github.com/zenstory-ai/oh-story-claudecode/tree/4daac79077928d0d5ba0eda93e46ce68dfcd40ae)

## 实现选择

| 问题 | 本版选择 | 代价/边界 |
|---|---|---|
| 13 个入口与相互路由 | 一个 story-skill，四份任务参考 | 题材教程没有完整搬运，依赖模型能力与本书事实 |
| 重复读通用技法 | 当前任务只加载一份短流程 | 需要时仍补读证据，不能以预算阻止必要阅读 |
| 追踪文件全量搬运 | 可检索卡片；计划显式 requires；关键卡和到期承诺强制进入包 | 自动相关性只覆盖候选资料，requires 填漏仍需语义审查发现 |
| 混书/旧版本提交 | book_id + revision + draft_sha256 | 不保证人写的语义结论一定正确 |
| 草稿和状态各自完成 | SQLite 同一事务保存正文、增量和回执 | Markdown 导出与数据库之间是可恢复边界，不是假称跨介质原子 |
| 反复拆已经完成的部分 | 原文哈希、字符区间、逐块幂等提交 | 切块目录不是作品正式章目录；complete 标签仍须由来源证据支撑 |
| 最新章回炉遗留旧事实 | 先恢复该章变更的前值，再应用新增量，保留历史 | 更早章不自动级联重建，以独立修订基线处理 |
| 默认多代理开销 | 默认当前会话，明确请求才独立委派 | 放弃默认多视角审查，需实测质量是否受影响 |
| Windows symlink/全局配置 | 真实目录、项目内安装、哈希校验更新 | 不提供多平台 hooks / Dashboard |

```mermaid
flowchart LR
    A[用户要求] --> B[一个技能入口]
    B --> C[本次任务参考]
    C --> D[本章计划与必要状态]
    D --> E[助手 写作与语义审查]
    E --> F[字数 哈希 引文 版本校验]
    F --> G[SQLite 正文与状态事务]
    G --> H[可恢复 Markdown 导出]
```

## 验证结论与不足

已用固定提交和具名分词器生成 [逐文件 token 证据](../benchmarks/results/tokens.json)。写作、拆解、审稿各路径的指令降幅见 [可读报告](../benchmarks/results/tokens.md)。源文件全部列出，不把磁盘体积当 token，不把合成召回实验标成上游实测。

本地回归覆盖事务、隔离、并发、恢复、证据约束、原文保留和安装契约。另用真实 CLI 子进程执行了初始化、计划、上下文、lint、幂等提交、带番外的导入分析、最终报告导出。[冒烟证据](../benchmarks/results/smoke.json)

没有运行两套技能在同一小说上的端到端生成，也没有验证 技能界面 自动路由、真实多模型行为、数百章后的一致性或图片生成。因此“文学质量更好”“总账单节省 93.73%”都不是本次可以下的结论。后续验收方法见 [evaluation.md](evaluation.md)。

本版是可使用的 助手 写作核心，并非上游全部生态能力的等价重制。所有运行时代码与短流程独立编写，未使用原仓库小说 demo 作为创作材料。
