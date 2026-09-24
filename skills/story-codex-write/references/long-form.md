# 超长连载：按需维护世界状态

仅在多卷、多视角或长距伏笔需要结构化世界资料时读取，不改变正文授权范围。作者用自然语言表达意图，由 Codex 维护 JSON、证据和版本；所有书籍命令显式附加 `--book "<书目录绝对路径>"`。

## 先取本章需要的资料

继续先读创作约定和 `status`。章计划可增加 `volume`、`arc`、`line`、`entities`、`time`：前三项是稳定 ID；另外以 `volume_dir` 保存完整的 `第X卷 卷名` 目录名（如 `第一卷 雨夜`），不与 `volume` 的内部 ID 混用，同卷各章保持一致；同一个 `volume` ID 已绑定的目录不能被后续单章计划静默覆盖。省略时只能沿用该卷已保存的带卷名目录，或已符合此格式的世界卷标题；缺少卷名须先明确，不能只用卷号或 ID。`title` 保存章节名称，正文导出路径取工具回执。entities 是实体 ID 数组，time 为 `{"clock":"main","start":20,"end":25}`。同一个 clock 的整数刻度须在书内约定含义；未知时间填 null，不把不同 clock 的数字直接比较。

局部硬限制要按 scope 自动进入上下文时，章计划必须关联相应的 volume、arc、line 或 entities 稳定 ID；`volume_dir` 不提供这种关联。仅用具名卷目录或尚无相关世界记录时，按 [项目状态](../../story-codex/references/project-state.md#状态卡与工具章计划) 把硬限制卡显式加入 requires，并核对 context 的 required_cards。额外世界字段可省略，但已有全局规则仍由 context／prepare 召回与检查；按具体实体或故事线召回的资料仍须正确填写关联 ID。

本卷保存目标、进入/结束条件和代价，情节段隶属卷；细纲范围按本次授权和写作需要确定，未指定时默认细化近期3—5章。未来方案用 `author_plan`，正文已发生必须用 chapter 证据。切回故事线读取它自己的最后场景、地点和未完动作，而非只接全书上一章。`context` 自动加入关联世界资料；硬事实和已选资料超预算会报错，不能删除必要状态来凑预算。

## 按领域取小模板

运行 `python "<tool>" template world` 查看实体/别名/事实起点；只取本次需要的 `world-volumes`、`world-arcs`、`world-lines`、`world-knowledge`、`world-hooks`、`world-rules`、`world-uses`、`world-transfers`、`world-arc_steps`。模板中的人名/事件只是字段例子，必须结合本书重建。只取单类模板时先确认它引用的实体、事实、卷段和规则已存在。

独立保存作者设计或已有正文基线：`world-save --book "<书目录>" --input "<world.json>" --expect R`。输入是具名数组对象，例如 `{"knowledge":[...]}`；一次可合并多个类别。`world-check --book "<书目录>" --chapter N` 检查该章计划，不能代替读稿。回执绑定 book_id、chapter 和 revision；状态变化后须重新检查，ok 不代表没有待核警告。

| 类别 | 必须表达的含义 |
|---|---|
| entities / aliases | 实体用稳定 id、name、kind、description。别名用 alias、entity、scope；scope 是故事线 ID 或 `*`。同名或同范围别名匹配多人时必须改用 ID，不能猜人。 |
| facts | id、subject、predicate、value；同一 clock 内的 subject＋predicate 是单值状态槽，按故事时间取有效状态。start/end 是生效区间，end 不包含在内；hard 为布尔值。每次变化另设 ID，不能覆盖旧正文事实。并存关系分别建槽：例如“钥匙／持有人／甲”和“药箱／持有人／甲”，不要把甲的两件物品都记成“甲／持有物”；用 entities 关联本章要召回的持有人。召回后会核对同一状态槽的后续记录，物件换主后不要求新记录重复标注旧主人；只选旧主人也不能把已交出的物件写成仍在手中。 |
| lines | id 是本次断点事件 ID，line 是贯穿多章的线 ID；保存 at、place、summary、unfinished、entities。 |
| knowledge | id、actor、fact、state、at、channel。state 为 knows/believes/suspects/unknown；unknown 是有依据的明确不知，缺记录不等于不知。人物相信的命题可以是 author_plan 中尚未证实的说法，不得因此升级为世界真相。同一 actor＋fact 的认知变化按故事时间衔接；通过关联物件或地点召回时，也核对后续变化，不因新记录未重复这些关联就沿用旧认知。 |
| hooks | 每次变化有 id，同一伏笔共用 hook。state 为 seeded/reinforced/dormant/fulfilled/breached/abandoned/reopened；at 是发生时间，hard_deadline 是故事内期限，window_start/window_end 是作者预计章数。entities/trigger_line 用于长距唤回。履约、违约须正文证据；违约、放弃、重开须 reason。同章同 hook 的多次变化须能区分真实先后；同一已知刻度会报 world_time_ambiguous，不能靠 ID 排序或编造时间通关。先核对正文并统一更细的时钟含义；旧记录若仍无法排序，保留歧义并审查，不宣称最终状态已确定。 |
| rules / uses | rules 保存 rule、version、生效区间、description、hard、cooldown、适用 entities/line、requires 事实 ID。entities 非空时限制适用者，line 非空时还须匹配故事线；都未限制时为全局规则。先按行动者和故事线取适用版本，某个人物的新版本不会解除其他人的旧限制。uses 保存 actor、rule、at；新规则版本另设记录 ID。没有明确时间或前置事实只提示待核。exception 需说明已授权的具体例外，仍须语义审查。 |
| transfers | resource、sender、receiver、amount、quantity_text、at。amount 是精确十进制字符串；“约半数”填 null 并保留原话，不强行换算。opening=true 是有证据的起始余额，可以为 "0"；普通转移必须大于零，普通收入不能替代未知基线。参与计算的流水存在日期不明时，余额保持未知，不能因读取到 opening 就断定该流水发生在开账前。 |
| arc_steps | arc、actor、pattern、desire、strategy、choice、cost、relationship、result、irreversible。相似模式只提示比较选择与后果，不判定文学优劣。 |

同一刻度的收支按账户和资源合并检查，不按 ID 或发表章序假定收入先到。即使全部收入先到仍不足，按 resource_overdraft 阻断；仅额外支出金额未知且已知支出仍必然超额时，保留阻断并以 required_is_minimum 标明最低支出，不据此推算该批收支后的准确余额。只有收入先到才够用时，以 resource_order_ambiguous 提示核对先后，资金循环也不能仅凭净额相抵当作可行。不同刻度保持时间先后，后到收入不能补足先前支出。

所有带证据的记录使用以下两种之一，不能混用：

```json
{"kind":"author_plan","note":"接下来打算写的情节；尚未发生"}
```

```json
{"kind":"chapter","chapter":1,"sha256":"对应正文的完整SHA256","quote":"该版本中确实存在的原句"}
```

chapter 证据必须匹配数据库已提交正文的哈希及原句。作者计划不会增加实际余额、角色已知或上次用术时间。事实与相信分开；倒叙按故事时间筛选，不能把未来认知带回过去。

未知时间不能当作最早发生，也不能用发表顺序代替故事时间。同一刻度的事实、人物认知和故事线断点若无法分出先后，context 保留并列候选；以 time_uncertain／order_uncertain 及 warnings 提示待核，故事线断点放在 line_candidates。先回读证据澄清，仍未知则保持不确定，不能按 ID 或发表章序选出确定当前状态；施术冷却不明须保留待核提示。

需要修补旧记录时先 `world-read --kind facts --id ID`（可换其他记录类别）读取完整字段与证据有效性；输出的payload可作为修订起点，不能只凭ID猜旧状态。

## 与正文一起提交变化

写前 `context`，写后 `lint` / `prepare`。在真实审查后的 delta 中填写 `world_changes`，内容与 world-save 的具名数组相同，记录本章实际发生的知识、转移、断点和伏笔变化。证据写本章章号、prepare 给出的稿件哈希及真实原句。`commit` 把正文和这些变化放在同一事务，不能先宣布完成、再依赖另一次调用补状态。实际动作按章前余额和各自故事时间检查，旧拟案不替代正文。无变化时省略字段。

delta 可一并填写 `dependencies:[{"kind":"world.facts","ref":"事实ID","sha":"实际依赖摘要哈希"}]` 和 `dependency_review:{"complete":false,"note":"本章已核对的依赖与仍未确认的范围"}`。complete 只有充分复核后才能为 true，且必须包含 plan.requires 卡；省略时保守视为未完整核对，不自动宣称依赖覆盖完整。

人物可以主动违约；把期限风险交给语义审查，实际违约记 breached 及后果，不能假写 fulfilled。已明确的数字矛盾要修正事件、补真实依据或说明授权例外；不靠删字段或伪造起始余额通关。script 的 ok 只说明所记录范围未发现确定性阻断。

写本章时可用 `dependencies --chapter N` 取得候选证据哈希；返回含已选卷段、故事线候选及 world_warnings，候选哈希不代表选择了某个状态或完成歧义复核。选出实际使用项、补足遗漏，将 `dependencies` 与 `dependency_review: {"complete":true,"note":"具体核对说明"}` 一起放入delta。完整声明必须涵盖plan.requires；完整与否由实际审查决定，不能自动把候选当完整。未声明的章按依赖不完整处理，历史修订可能需要扩大复核范围。`chapter-read --chapter N --sha256 SHA --start A --end B` 可定点读当前或已归档版本，不读整个数据库。
