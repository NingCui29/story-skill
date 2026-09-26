# 恢复流程（schema 2）

当前发布版 **v0.6.1** 包含 8 个技能、39 个载荷文件，新增本地三栏只读工作台。[发布记录](releases/v0.6.1.md)列出实际验证范围；本机安装不会自动更新。升级前备份书库及本地技能修改，保持八目录版本一致。

以下 CLI 示例均需 `python "<核心技能目录>/scripts/story.py"` 前缀；书籍命令另附 `--book "<书目录绝对路径>"`，`template` 与帮助命令不附 `--book`。本页基础恢复流程沿用 [v0.5.5](releases/v0.5.5.md) 起的行为，历史验证见 [当时的发布记录](github-release.md#v055-发布验证记录)；当前安装版本、固定来源和平台范围统一按 [INSTALL.md](../INSTALL.md) 核对。平台验证范围见上方；共享核心为 `story-skill`；数据库继续使用 schema 2，v0.3.0、v0.4.0、v0.5.0、v0.5.1、v0.5.2、v0.5.3 书库无需重新导入。分卷正文命名只应用到新保存的目标，已有旧章路径继续识别。

旧 schema 1 小说书库先停止写入，保留原书并复制到独立目录，再 `migrate --book "<副本>"`。迁移工具会先用 SQLite backup 生成一致备份并检查完整性，再在同一事务中执行所有结构变更；失败时回滚。迁移备份位于 `.story/migration-backups/`。回退时停止相关进程，在另一个独立书目录恢复备份并使用 v0.2.0；备份之后的新修改不会自动出现在旧版中。无须重新初始化或修改书籍身份。

v0.5.2 增加历史审查级别兼容、卡片与后补世界证据的修订保护、历史候选与依赖读取，以及分析定位、版本校验和导出恢复。v0.5.4 沿用这些能力，当时的发布状态与验证见 [v0.5.4 记录](../benchmarks/results/v0.5.4/README.md)，不将历史通过结果当成本版已通过。新书默认书名目录由技能选择书根后建立，既有书不会因此自动搬动；需要整理旧书时先核对现有目录和状态。[书目录规则](目录结构.md#小说一书一目录状态与可见稿件分开)

## 离线发布材料恢复

发布准备能力始于 v0.5.11；当前源码的 `story-skill-publish` 将本地准备记录保存在 `.story/publishing.sqlite3`，其 schema 1 与本页前述需迁移的旧小说书库 schema 1 是不同文件。发布准备账本不存平台上传、审核或上线状态；它的清单、ZIP 与包外回执均不能证明远端已收稿。[完整边界](platform-publishing.md)

换会话后先用 `publish-list --book "<书目录>"` 找清单，再用 `publish-export-list --book "<书目录>" --id "<清单ID>" --offset 0 --limit 10` 找回包外回执。列表只说明保存记录与 ZIP 是否存在，不核对当前正文，也不自动挑选最新包。取得原始回执后用 `publish-verify-export --book "<书目录>" --receipt "<回执绝对路径>"` 核对所属书、原清单、ZIP 原始摘要和当前正式稿；旧包不会随改稿自动更新。缺失或损坏的回执不要用待查 ZIP 现算的哈希冒充原值，应从仍有效的清单重新导出；若正式章或审查依据已变化，先用 `publish-check` 查看差异，再重新准备。

出现 `publishing_recovery_required` 时，先保留整个书根及发布账本，再运行 `publish-recover --book "<书目录>"`，随后重新查询。`publish-backup` 可为独立账本建立备份，但当前没有自动从备份覆盖恢复的命令；遇 `publishing_schema_mismatch` 或不安全路径，应保留现场核对，不删库重建。恢复和复核只涉及本地材料，仍须由作者在平台后台逐章人工填写并确认实际结果。

## 检索和历史复核不能确认完整

召回结果出现 `complete: false` 或 `truncated_reasons` 包含 `budget_limit`，表示候选或输出未完整返回。缩小查询范围或按需增加该次预算，再核对原文；空的 `matches` 不能单独证明事实不存在，需同时检查 `no_match_confirmed`。预算单位是 UTF-8 字节，不是 tokens。

历史依赖补录、候选更新或发布出现 `missing_required_dependencies` 时，先定位对应计划的 `requires`，实际读取缺失卡片及其来源，再保存真实依赖哈希和复核说明。分支中用 `history-dependencies --branch B --chapter N` 读取候选证据，不对早期章运行普通写作 `context`。当前读取的卡片是现态证据，不能冒充旧章起草时的快照。只把 `complete` 改成 true、删掉 `requires` 或替换哈希不能代替复核；处理路径见 [历史修订实操](超长篇实操.md)。

世界记录提示正文证据失效时，回到对应章节与历史修订分支处理；新写一条伏笔兑现记录不能修复旧铺垫的失效证据。导入文本因 NUL 被拒绝时，在独立文本副本中定位控制字符并核对清理结果后再导入；已有数据库不会因此自动清洗或重建。

## 历史候选已保存，但原输入文件丢失

v0.5.5 新增的 `history-saved --branch B` 只读返回分支已保存的卡片决定、世界修补与整体审查；不需要原 input 文件或 --expect。正文和逐章审查用 `history-inspect --branch B --chapter N` 取回。检查分支 revision 一致，保留原引文、说明、hash 和删除用的 null；空白模板不是已保存决定。过期分支仍可读，但续改前仍须核对变化并按回执刷新或重建。完整返回超预算时增大 --budget-bytes，不手工截断。

继续编辑时，state_changes 数组、world_changes 对象各自整段替换；保留同段中其他有效决定，未传的段不动。整体审查为 null 时不要直接重交，修改后按新回执完成审查。详见 [历史分支流程](../skills/story-skill-review/references/history.md)。该读取入口已随 v0.5.5 发布，固定发布包的能力以对应版本为准；[v0.5.5 核验状态](../benchmarks/results/v0.5.5/README.md)不会将尚未安装的能力算作本机已具备。

## 已提交，但尚未导出

回执的 `committed: true` 表示正文、审查和卡片已入库；`exports_complete: false` 表示文件交付尚未完成。查看 `export_error` 和 `export_details`，文件定位采用回执中的实际路径。从 v0.5.0 起，新章按 `chapters/第X卷 卷名/第N章 章节名称.md` 导出；已有旧路径继续识别，不需要为恢复批量重命名。磁盘、权限或 SQLite 锁问题解除后运行 `export`，也可重试同一份草稿与 delta。不要仅为重试修改 revision 或重新生成另一份提交。

正文的 `lint`、`prepare` 和提交共用目标路径预检：新目标的内容占用、目录占位、符号链接或目录联接、大小写等路径别名，以及新目标的硬链接会在写入前被拒绝。已有托管正文被外部修改时，`lint` 仍可检查草稿，并返回只读的 `external_edit.path/sha256` 观察；这不代表外改已获准提交。普通 `prepare` 仍会被未解决的外改阻断，需要对账时使用 `prepare --reconcile`。按错误回执核对实际路径并保留已有稿件；后续提交和对账仍独立核验外部版本。

回执版本在事务内捕获，提交后即使另一连接立刻取得独占锁，也会保留已提交状态。导入、计划、卡片和分析记录同样使用本次事务的回执数据。

如果一章缺失、另一章有外部改稿，普通 export 会整体拒绝。使用 `export --safe-only` 先恢复缺失文件或仍匹配已知旧版本的待导出文件，跳过外改、不安全路径及失败项。查看全局 `pending_exports`、`changed_exports` 和 `export_errors`；仍有剩余时 `exports_complete: false`、退出码 2，已恢复文件保留。随后对最新外改章执行 reconcile，解决原先互相阻断的恢复情况。

导出替换会把旧文件保留在 `.story/export-backups/<唯一目录>/`；回执中有具体备份路径。写入失败时尝试恢复原路径，已出现新文件则保留新文件与备份。备份目录不会自动清理；遇进程中断也可从该目录找回被换下的文件。正文与状态的历史版本还保留在 SQLite events 中。导出途中检测到卷目录被替换时，会保留已保存版本并报告待恢复；根据回执核对原目录、备份及当前路径后再重试。

已登记正文若有多个硬链接、且内容仍匹配已知版本，`export` 会保留原文件备份并重新写出独立文件，核验后才报告该项恢复完成；其他硬链接保留原内容，备份路径见回执。已有外部改稿仍先对账，不靠重新导出覆盖。

## 最新章在编辑器中已被修改

1. `reconcile --chapter N`：返回修改前的卡片基线、计划、book_id、revision，以及 `external_edit.path/sha256`。仅支持最新且由本工具原生提交的章节。
2. 读取该路径，把拟采用版本另存 `.story/drafts/`；根据实际改动复核连续性、因果、约束和文风。`lint --chapter N --draft "<草稿>"` 后，用 `template delta` 填写审查、摘要和状态变更；额外加入 `external_sha256`，取第 1 步的 sha256。
3. `reconcile --chapter N --draft "<草稿>" --input "<delta.json>"`：验证书 ID、状态版本、外部稿版本与审查哈希，再撤回上一版卡片增量并应用修订。草稿可以是审查后改善的版本，无须与捕获的外部稿完全相同。

`stale_external` 表示捕获后又有外部保存；重新读取、合并与复核。`stale_revision` 表示状态已变化；重新获取上下文并审查。不要只替换哈希或 revision 来跳过这些步骤。其他章节的缺失或外部修改仍会阻断新修订。

若重命名期间新旧两个路径都被外部保存，先把所有外部版本分别另存 `.story/drafts/`，核对文件内容与哈希后再处理。合并修改时从这些保留副本起草，不用一个版本覆盖另一个。若当前托管路径也有外改阻断，用 `chapter-read --chapter N` 分段取出数据库已提交正文，后续各段固定使用首段返回的 `source_sha256`，直到 `next_start` 为 null；完整拼接并核对哈希后恢复该托管路径。外部副本继续保留，旧路径的外改仍待对账。再按 `export --safe-only` 的回执恢复缺失文件，重新读取 `reconcile` 或历史分支检查得到的路径与哈希，以合并后的草稿完成审查。

更早章节、含结构化世界变化或已合并历史分支的章节，以及该章产生的状态卡在提交后又有更新的情况，使用 [历史分支](../skills/story-skill-review/references/history.md)。通过 `world-save` 后补的正文证据也受保护，不能因原提交回执没有世界增量就普通替换。遇到 `revised_state_conflict` 时保留当前卡片和正文，按回执的 `chapter` 运行 `history-start --chapter N --expect R`（R 取最新 status），在分支中复核后续状态；反复 `reconcile` 不能解决这类冲突，不回写旧卡绕过保护。导入基线若缺少章计划，先依据采用的细纲为受影响章保存计划，再创建分支。候选、旧版、证据及审核状态分别保存；复核没有完成不能发布。导入基线和已完成分析报告仍不允许无条件覆盖。

v0.5.2 的历史审查接受 `advice`；旧 `minor` 同样表示可选建议，`major` 和 `blocker` 仍阻止发布。普通审稿继续使用 `blocker/advice`，不靠改级别跳过未解决的问题。

## 拆文停在空白块

直接 `next --source ID`。工具只把纯空白范围登记为 `kind: non_content`，保留原编号、范围、哈希和事件证据，`findings` 为空；正常正文仍需带精确引文的人工或模型分析。已有分析不覆盖，整份全空白来源仍拒绝导入。无需删除来源或重跑已完成块。

## 分析证据块号

`findings` 始终返回数据库中的真实块号，包括旧记录里带有错误 `chunk` 字段的情况。v0.5.2 还返回真实 `start/end`，可直接用于 `source-read --source ID --start A --end B`，范围按 Unicode 字符计，起点包含、终点不包含；分页沿用 `next_offset`。查询范围是只读元数据，复制回 `record` 时忽略它们，不改变分析内容或同内容重交的幂等性。从查询结果复制 JSON 进行修订可以保留正确的 `chunk`；如果与 `record --chunk N` 不一致，会报 `chunk_mismatch` 且不保存。块号仍须与 `chunk_sha256`、精确引文共同核对，不要只改数字来迁就错误引用。此前已生成的报告不会自动重写，存在可疑引用时需另行复核。

## 分析稿修订与续跑

先区分已有产物。通过 `status.recent_sources` 查看 source、next_chunk 和 report_path，更多来源使用 `sources` 分页；同时检查 `pending_exports`、`changed_exports` 和导出错误。`report_path` 表示已登记报告，不能证明文件已经交付。已定稿但尚未导出时，排除路径或磁盘问题后 `export`；与外部改稿并存时用 `export --safe-only` 恢复可安全导出的项目，核对报告存在且哈希与登记内容一致，保留冲突文件。

已有来源直接 `next --source ID`，全部块完成但还没有报告时继续聚合，不重新导入。需要确认人物变化、伏笔或主题时，从 `findings` 取得相关块的 `start/end`，用 `source-read` 回读原文并按需扩展邻文、核对反例；摘要不能代替证据。已完成块仍可定位回读，不需重新导入。

| 当前状态 | 恢复或修订方式 |
|---|---|
| 逐块记录未定稿 | 先读取 `findings`，保留该条的 `analysis_sha256` 后修改内容，用 `record --source ID --chunk N --input "<修订记录.json>" --replace` 保存；旧版本事件保留，再继续未完成块与聚合 |
| 已有工具定稿 | 保留原记录和报告，在分析目录内、`.story/` 外另存修订稿，回读原文并联动修改受影响的判断和建议 |
| 只有外部分析文档 | 直接修订文档，沿用实际页码、段落或短引文定位，不必初始化书库或重新导入 |

聚合报告时保留 `findings` 顶层的 `analysis_sha256`，各页须来自同一指纹；首次定稿用 `report --source ID --file "<报告.md>" --expect-analysis SHA`。该指纹覆盖来源和全部块的分析状态，与逐条记录的同名指纹用途不同。基线不匹配时重新读取变化内容并复核报告，不只替换 SHA 强行提交。

定稿后的修改会被 `report_already_final` 或 `report_exists` 拒绝；不能删除报告、改数据库或重建书库绕过保护。若只是重试相同报告的保存，`report --source ID --file "<原提交报告.md>"` 使用原来的输入文件；同内容重试可恢复导出，无需刷新旧基线。工具导出稿带来源头，不能再次当作原输入提交。

独立修订需要跨轮处理时，在新稿旁记录原文、旧稿和当前稿的路径及哈希、已处理与待核对的问题、下一步。恢复时先读进度与当前稿，核对文件变化，再接未完成问题。`status` 里的 report_path 仍指向旧定稿，不能说明独立新稿已完成。缺少原文时注明哪些判断无法核验；新稿继承原分析范围，不把局部修订扩大称为全书分析。

交付给出新稿实际路径、修改说明和核验范围。已保存字段、引文和哈希通过，只能说明程序检查通过，文学判断仍需原文复核。[技能入口](../skills/story-skill-analyze/SKILL.md) · [修订恢复试用](../benchmarks/results/analysis-followup/README.md) · [完整九章试用](../benchmarks/results/analysis-crosschapter/README.md)

## 本地工作台没有生成或没有打开

v0.6.1才包含工作台。先用实际安装副本的 `story.py --version` 和文件清单核对是否为 0.6.1 且含 `story_workbench.py`；只更新仓库源码不会更新安装副本。稳定版 v0.6.0 没有这些命令，不要把“命令不存在”误判为书库损坏。

`workbench-snapshot` 是只读查询。出现 `stale_snapshot` 时，说明沿章节游标翻页期间核心书库、发布记录或受管文件已变化，丢弃旧页并从第一页重取。出现 `workbench_changed` 时，先等待正在进行的提交、导出或外部保存结束，再重试；不要为取得页面而修改数据库或删除正文。

`workbench-export` 只允许写 `.story/workbench/*.html`。书在 Git 仓库内时，必须忽略整个 `.story/workbench/` 和 `.backups/`，并确认没有已被跟踪的工作台文件。`workbench_not_ignored` 表示规则不完整；`workbench_tracked_output` 表示已有文件在索引中。先人工核对 Git 状态并处理，工具不会自动改索引。路径越界、链接父目录或重解析点风险应保留错误现场，不通过换一个不受保护的脚本绕过。

使用 `--open` 时，HTML 生成与浏览器打开分别报告。`ok: true`、`path` 和摘要表示页面已生成；`opened: false` 与 `open_error` 表示系统没有成功打开浏览器，可按 `path` 手动打开。不要因为浏览器未启动而重复初始化书库，也不要把存在 HTML 说成浏览器已打开。

工作台报告正式文件外改或待同步时，只把它当作恢复入口。回到本页相应的 `reconcile`、`export --safe-only` 或历史分支流程处理，再重新生成工作台。草稿、候选稿、可读大纲、封面显示“未登记”是当前能力边界，不是文件丢失；工作台不会按名称或时间猜测当前版本。[完整说明](本地工作台分析.md)

## 安装更新失败

源文件在复制期间变化时，安装器停止发布；已安装版本不受影响。等源文件保存完成后重新运行 `scripts/install.py --project "<项目>" --update`。用户在目标技能目录中的修改会触发拒绝；先复核这些改动，不要直接删除安装清单绕过检查。

同一项目的安装操作使用操作系统锁。看到 `Another installation may be running` 时，等待该安装进程退出后重试；`.agents/skills/.story-skill-install.lock` 文件可以留存，无需手动删除，进程强退也会释放锁。

若发布失败后目标目录又被外部创建，恢复不会覆盖它。错误中的 `the moved skill is preserved at <backup>` 指向被搬走的技能；核对当前目标与该备份后再决定使用哪个版本。Windows 下的发布和恢复均拒绝替换已存在的目标目录；POSIX 的目录预检不能提供同等的原子保护。

## 已有验证范围与平台限制

回归测试包含实际 SQLite 第二连接锁定、提交失败回滚、旧分析记录兼容，以及导出和安装各阶段的并发保存、目录重建、安装进程强退。Windows 在同目录写完整暂存文件并同步后，用拒覆盖重命名发布；原有不依赖硬链接的发布方式已通过历史 exFAT 实测。v0.5.0 补齐 Windows 目录句柄的遍历权限，以建立重命名保护；后续原生 Windows 验证确认该保护同时阻断普通导出重命名，出现 WinError 32，v0.5.3 仍未解决；这些是历史结果，v0.5.10 的修复与当版原生 CI 结果见[历史版本说明](releases/v0.5.10.md)。失败恢复使用另一个完整暂存副本，保留原备份，拒绝覆盖已重新出现的目标。POSIX 发布仍使用硬链接，缺少支持时保留待恢复状态。没有全局锁住外部编辑器，后续保存会成为新的外部修改，由 `status` 检出。

中文实测曾在 D 盘 exFAT 触发硬链接 WinError 1：第1章已经提交为 revision 5，正文待导出。修复后用同一书 ID 和原 delta 重试成功，并继续写作、修订到 revision 10；没有重新初始化或丢弃旧状态。见 [中文实测](中文实测.md)。

v0.5.0 首次 Windows CI 达到时限，诊断轮又确认大小写等价路径分类不符；记录分别保留为 [首轮未完成](../benchmarks/results/v0.5.0/release/ci-first-incomplete.json) 和 [诊断失败](../benchmarks/results/v0.5.0/release/ci-diagnostic-failed.json)。后续调整了等价路径比较、目录句柄权限和 ZIP 原始成员名校验；macOS／Linux 复验通过，Windows 普通导出重命名仍失败。[API 诊断](../benchmarks/results/v0.5.0/release/windows-api-probe.json) 用于定位限制，不代表已集成修复。开书的表结构与初始元数据改为一次事务提交，失败时回滚数据库改动；持久化设置不变，新增回滚及可见性回归。[初始化证据](../benchmarks/results/v0.5.0/initialization.json) 只包含本地 macOS 样本。

上述 NTFS/exFAT 说明来自已保留的历史本机实测，不代表 v0.5.0 的目录绑定实现通过 Windows 验证；该版实际 Windows 结果为已知导出失败，v0.5.3 未修复这一路径。本地 v0.4.0 另完成 311 项单测和 12 项整包检查，包含历史依赖、并发快照、安装与恢复相关路径。发布提交 `c1b3c3377573610a191e165ceb6866ae35afe5a8` 的 [远端 CI](https://github.com/NingCui29/story-skill/actions/runs/34431400166) 也已通过：Windows/Python 3.12 为 311 项通过、零跳过，Linux/Python 3.10 为 305 项通过、6 项 Windows 专用测试跳过。远端 runner 的成功不代表覆盖所有 POSIX 文件系统，也不替代本机 exFAT 实测。[发布验证与边界](github-release.md) · [v0.4.0 发布前本地审查](全仓审查与优化.md)

## 完整核验与局部运行

默认 strict 会哈希核对所有导出。`--integrity local` 只核验最近及队列中的文件，回执列出未核验历史数；`exports_complete: null` 表示局部处理已完成、全书完整性未确认，不是错误也不等同全书清洁。新会话、恢复中断、发生外部编辑后，以及阶段交付前，用 `audit --book "<书目录>"` 完整核验。不要根据mtime/大小、旧审计revision或缓存命中推断磁盘未发生变化。
