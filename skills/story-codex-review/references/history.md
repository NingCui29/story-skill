# 历史分支、迁移与恢复

仅在更早章节、导入基线、结构化世界增量或证据绑定章、已合并历史分支章节、提交后相关卡片另有更新的修订，或旧版库迁移时读取；不是普通续写的必读流程。所有书籍命令调用共享 `<tool>` 并显式附加 `--book "<书目录绝对路径>"`，R 取最新 status。

需要理解或重绑定世界记录的字段与证据时，按需读 [世界状态说明](../../story-codex-write/references/long-form.md)；先 `world-read --kind facts --id ID`（可换其他类别）取回真实旧记录，不凭 ID 猜字段。建立分支后用 `history-dependencies --branch B --chapter N` 取该章依赖候选；普通 dependencies/context 不用于历史修订。结果含当前记录哈希及本分支已保存章节候选的哈希，不等于章前事实状态；实际读取、选择并补齐遗漏，处理 unavailable 后才能声明依赖完整。`chapter-read --chapter N --sha256 SHA --start A --end B` 可定点读取当前、归档或该章已保存的分支候选正文；它不会把候选发布为正文。

先核对目标章及受影响章的当前计划；导入基线通常还没有该章计划，按实际原文与本次授权补齐必要目标、停笔点、约束和容量，不虚构此前剧情。然后 `history-start --chapter N --expect R` 创建候选；若报 plan_missing，分支未建立，按回执先补缺失计划，再取最新 R 重试。已在分支范围内的章计划若在建立后补改，须重新建分支，不能只刷新哈希。

按分支返回的影响范围、状态要求和 review_template 准备修订，用 `history-update` 保存，`history-inspect` 继续取断点。每个受影响章须刷新正文/摘要/依赖及审查，整个候选包另做状态和覆盖审查；`history-publish` 才发布。正文证据变化后的世界记录须在分支 world_changes 中重绑定，或用 retirements 明确撤销，并处理仍引用它的认知/规则；包含通过 world-save 后补的正文基线。分支未发布时原版继续有效。发布后用 `history-inspect` 分页条目的 `affected[].path` 定位当前托管正文（相对于书目录），即使重复发布时 `exported` 为空也可查到。该路径指向当前导出，基线与已保存候选版本仍按各自 SHA 定点读取。不能按章号猜文件名；旧工程仍识别原导出路径，不借历史修订批量搬动文件。

短篇正式 `history-publish` 后，本次明确要求完整成品时，按 [短篇完本与修订交付](../../story-codex-write/references/chapter.md#短篇完本与修订交付) 核对总纲平台分类、全文和实际封面；此前没有全文或封面时也执行首次交付。普通修订已有全文时只核对同步状态、导出回执和实际文件，必要时恢复导出；封面仅在书名、署名、题材或投稿规格变化时复核或修订。未发布的历史候选不刷新正式全文或封面。

`history-inspect --branch B --chapter N` 返回所请求 N 章的 base/candidate 正文及空白 chapter_review_template；顶层 chapter 仍是分支起点，不能用它替代本次请求的章号。先读稿，再填观察和原句。一般 inspection 还给 state_review_template 的精确 before_sha/before，after 不预填：须明确填完整卡片以保持/修改，或 null 删除，并补候选章号、原句和理由。默认50张，可用 --state-offset/--state-limit 分页，单页最多200张。

中断后先用 `history-saved --branch B` 只读取回 `saved.state_changes`、`saved.world_changes` 和 `saved.semantic_review` 的完整已保存内容，包括删除用的 null、引文、理由和整体审查说明。`candidate_chapters` 列出已有候选，正文和逐章审查用 `history-inspect --chapter N` 读取；空白模板不代表原审查未保存。取回结果保留原 hash，`revision` 是分支版本，`current_revision` 是读取快照中的书库版本；过期或已发布分支也可读取，这不表示旧审查重新有效。先核对同一分支版本及当前基线再续改，不能仅换版本号。未保存或已失效的整体审查返回 null。

续改时，传入的 `state_changes` 数组或 `world_changes` 对象会整段替换原值：修改其中一项须保留该段其他仍有效的决定，未传的段才保持原样。不要把包含 `semantic_review:null` 的整个 saved 对象直接重交；状态或正文等发生变化后，按新回执重做必要审查。`history-state` 和 `world-read` 返回正式状态，不能代替这些分支决定。

`history-update --branch B --input "<候选更新.json>" --expect R` 的输入是对象，首次保存候选的结构如下；text 放完整候选正文，章号、摘要和依赖按本书实际内容填写：

```json
{"chapters":[{"chapter":1,"text":"<完整候选正文>","summary":"<本章实际变化>","dependencies":[],"complete":false}]}
```

随后读取该章 inspection，把填好的 `chapter_review_template` 放在对应 chapters 项的 `review` 中；text、summary 等未修改字段可省略。`complete` 表示依赖已实际核全，只有核对并补足依赖后才改为 true；跨章依赖指向此次分支中实际采用的正文 SHA。卡片决定放顶层 `state_changes` 数组，世界证据修复放 `world_changes` 对象，字段沿用前述模板与世界状态说明。先保存这些修改，再读取最新 `review_template`，实际复核全范围后作为顶层 `semantic_review` 保存。正文、摘要、依赖或状态变化会使旧审查失效，不能只换指纹；分支审查齐全后再 `history-publish`。

`state_changes` 若真实新增、修改或删除了额外卡片，或 `world_changes` 改变了已有实体、作者计划等世界记录，`history-update` 会把已声明引用这些记录的章节及其后续依赖自动纳入分支；entity/rule 与对应的 world.entities/world.rules 引用一致识别，已记录的事实到认知/规则的引用也会继续传递。返回 `scope_expanded:true` 和 `added_chapter_count` 时，原逐章审查及整个分支审查已清空；按完整分页范围重新读稿，补齐各章候选、依赖、状态和世界记录复核，再逐章及整体审查。规范化后仍是当前原值的卡片或世界记录不会额外拉入消费者；已经扩大过的范围不会因随后改回原值而自动缩小。发布会保留世界变更的来源关联，以便以后再次修订该章时继续复核消费者；不会自动更换声明中已经读取的依赖 SHA，变更后旧缓存可能失效。

扩围新章缺计划时，更新原子报 `plan_missing`，本次 payload 尚未保存：保留该文件，补回执列出的新章计划，取最新 R 对原分支执行 `history-refresh`，再重交原 `history-update` payload。若刷新因其他基线变化要求重建，必须在新分支重交这些卡片或世界决定，才会重新触发扩围。旧版候选发布若报 `history_scope_changed`，先向同一分支提交 `history-update` 的空对象 `{}`（或保留的完整卡片/世界决定）触发扩围，再按新范围复核；不要只取新 hash 或重新建立一个未包含这些决定的分支后直接发布。

逐章审查的问题使用 blocker/advice；历史回执也兼容旧 minor/major，advice/minor 不阻断，blocker/major 必须解决后才能保存审查。不为通过校验把阻断问题降级；整个候选包的状态与覆盖审查仍须消除未解决问题。

最新章若返回 `revised_state_conflict`，说明其产生的卡片在提交后有新值，普通替换及 `reconcile` 都不能安全回滚。保留当前卡片与各版正文，取最新 R 后 `history-start --chapter N --expect R`；按 state_review_template 明确核对并保留、修改或删除卡片，不自动恢复为章前旧状态。存在外部改稿时，候选同时绑定本次实际读取的 external_sha256。

start/update/refresh/inspect 的 affected、world、hints 三段共用 --limit，默认25、最大200；只有 inspect 用 --affected-offset、--world-offset、--hints-offset 分别续页，state 独立分页。读取 pages 中各段的 total/next_offset，next_offset 为 null 才到末页。complete 表示本次响应已含该段全部内容，非零 offset 的末页仍不完整。affected 分页时 review_template.reviewed_chapters 为 null，review_scope.chapter_ids_page 仅含当前页；收齐同一分支版本的全部范围并实际复核后，才能填全量 reviewed_chapters，不能把当前页当全书覆盖。

上述四命令、`history-deps --chapter N` 及 history-dependencies/history-saved/history-state/cache-get 默认 --budget-bytes 64000，最少256，按 UTF-8 字节计。正文对照、审查凭据或缓存超限会报 budget_exceeded，不截断；可减小页数、用 chapter-read 分段读原文，或明确增大本次预算。history-deps 返回完整声明，history-saved 返回完整已保存决定，两者超限时增大预算，不丢弃字段或依赖项。读取候选须使用 affected[].draft_sha256 或 candidate.sha，不使用绑定摘要和依赖的 candidate_sha256。缓存虽可保存至200000字节，读取仍受本次预算限制。

需要章前状态时用 `history-state --chapter N --before`，按 --offset/--limit 续取。它还原该出版 revision 的卡片状态，非故事时间状态，也不等于自动完成历史语义回放；不要从最新人物卡猜旧时状态。分支因外部进展过期可先 `history-refresh` 核对；依赖正文真正改变则重新建立分支，不只替换 revision/hash。

## 补正漏报依赖并扩大复核范围

原声明即使是 `complete:true`，也可能漏掉真实依赖。审查发现第 M 章依赖本次修订的第 N 章、却未进入分支范围时，先保留旧候选稿，用 `history-deps --chapter M` 只读取得当前正式版本的完整 `dependencies/complete/note`、`chapter_sha`、`revision` 和可编辑的 `payload`；读取不需计划、分支、`--input` 或 `--expect`，无计划的导入基线也可读。原依赖哈希不会自动刷新，未记录过 note 时返回 null，payload 中留空，须在实际复核后补写。按 `chapter_sha` 用 `chapter-read --chapter M --sha256 SHA` 读取对应正文；缺失的跨章证据同样补读当前正式正文，取其 `source_sha256`，不能用未发布的候选正文哈希修正正式章声明。

在返回的 payload 中保留其他有效依赖并合入新发现项；`history-deps --input ... --expect R` 替换整份声明，不是追加，不能与读取用的 `--chapter` 混用。每项 `sha` 绑定实际核验的当前依赖；遇旧哈希失效须回读证据，不只换指纹。只有全部核清、包含 `plan.requires` 后才填 `complete:true`；尚有遗漏或疑点则如实填 false。下面仅演示原声明为空、核对后唯一依赖为第1章的第2章：

```json
{"chapter":2,"chapter_sha":"<当前第2章正文SHA256>","dependencies":[{"kind":"chapter","ref":1,"sha":"<当前第1章正文SHA256>"}],"complete":true,"note":"第2章取出昨日留下的钥匙，依赖第1章将钥匙放入衣袋；已核对当前原文与全部依赖。"}
```

```text
python "<tool>" history-deps --book "<书目录绝对路径>" --chapter M
python "<tool>" history-deps --book "<书目录绝对路径>" --input "<完整依赖声明.json>" --expect R
python "<tool>" status --book "<书目录绝对路径>"
python "<tool>" history-refresh --book "<书目录绝对路径>" --branch "<原分支ID>" --expect R
```

写入声明的 R 沿用本次读取回执的 revision；遇 `stale_revision` 先重读并合并已发生的变更，不能只取最新版本号重交旧 payload。多章补正逐章重新读取。完成补正后，再取最新 status 的 R 执行 `history-refresh`；它只核对已记录关系，不会主动从原文找出漏项。范围或基线改变会报 `stale_branch`（其他依赖变化也可能报 `stale_dependency`）；此时核对新增受影响章的计划，取最新 R，从原目标 N 重新 `history-start`，确认完整分页范围包含 M，再重新绑定各章候选、依赖、逐章审查及整个分支的状态和覆盖审查。旧候选可作为复核材料，旧审查不能直接沿用；不得手改 `affected`、数据库或只更新指纹绕过扩围审查。

## 旧版迁移

旧 schema 不自动升级。停止写入，保留整个书目录的独立拷贝，在副本上 `migrate`；保存返回的 `.story/migration-backups/` 一致性备份路径，再做 `audit`、`status` 和本章 context。回滚时停止全部书进程，把备份恢复到另一个独立书副本并使用旧版工具；迁移后的新章不在旧备份内，不能覆盖丢弃。切勿让新旧工具同时写同一本书。

合成容量测试、跨章编号模拟和脚本校验都不证明百万/千万字作品质量。实际连载仍逐章读稿，核对人物选择、代价、伏笔、公平信息和读者期待。
