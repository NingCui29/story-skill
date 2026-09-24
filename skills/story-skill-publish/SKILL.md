---
name: story-skill-publish
description: 为七猫或番茄准备已审查正式章节的离线材料，导出并复核 ZIP，可比较作者提供的草稿副本并留存本地核对摘要。当前不执行平台上传、提交或定时发布。
---

# Story Skill · 发布准备

使用前读取同版 [story-skill](../story-skill/SKILL.md) 的共同约束与目录边界；已在当前上下文则不重复读。共享工具为同级 `story-skill/scripts/story.py`，记其绝对路径为 `<tool>`，所有书籍命令显式传书根 `--book`。

本功能把当前已审查的正式章节固定为本地清单，不登录平台。准备清单、平台接收草稿、提交审核和读者可见是不同结果；本版只完成离线准备及作者提供副本的核对。用户请求直接发布时说明当前缺少平台适配，继续其已授权且有资料可用的本地准备，不声称已经发布。2026-09-23 核对的[番茄小说网用户协议](https://fanqienovel.com/protocal/agreement)第 4.1.1 条和[七猫作家助手用户服务协议](https://zhushou.qimao.com/writer-rules/68464fdfe4a81e7ec312f874/)第五节 2.1(7)(9) 均限制未经授权的第三方工具接入；未经适用平台许可，不通过浏览器自动操作或非公开接口代用户上传、回读。

## 准备章节材料

从用户要求和书内信息核对目标书、平台、章节范围及平台账号标识、作品 ID。`fanqie` 表示番茄，`qimao` 表示七猫；账号和作品 ID 是用户提供的目标信息，程序不核验其远端真实性，不凭书名猜 ID。缺少这些信息时先指出具体缺项；不索取密码、验证码或 Cookie。

1. 用 `status --book "<书根>"` 取得当前 revision 和导出状态，再运行 `template publish` 获得输入结构。由 助手 填写机器字段，`chapters` 使用连续且升序的数字章号，`mode` 仅支持 `draft`。
2. 使用 `publish-prepare --book "<书根>" --input "<输入JSON路径>" --expect R --summary` 保存清单并取得简洁回执；完整稿件仍保存在账本中，不加 `--summary` 时回执包含完整清单。输入文件保存在本书 `.story/` 内；普通写作、提交和导出不隐式执行此命令。相同正式章节和目标复用仍有效的清单；无关笔记更新不会生成重复清单，回执中的准备版本仍保留原值。
3. 用 `publish-inspect --book "<书根>" --id "<清单ID>" --summary --offset 0 --limit 20` 分页核对章序、章名和本地字数；按 `chapter_page.next_offset` 继续。需要核对正文时使用 `--chapter N` 回读该章完整冻结内容，不把目录摘要或单章当成全部正文已回读。展示清单 ID、目标平台和作品 ID。平台字数可能不同，不以当前文件替换冻结内容。
4. 需要交付文件或手工填写平台时，运行 `publish-export --book "<绝对书根>" --id "<清单ID>"`。工具在导出前重新核对正式稿，仅 prepared 且本次匹配的清单生成 ZIP 和同名包外回执；stale、cancelled 或存在正文外改时不生成。stale 不因后来匹配而复活，解决问题后显式重新准备。核对 `export_created`，报告清单 ID、真实 ZIP 路径、SHA-256 和 `export.receipt_path`；每次导出保留新的 ZIP 与包外回执，无需用户手写 JSON。
5. 交付或再次使用 ZIP 前，运行 `publish-verify-export --book "<绝对书根>" --receipt "<包外回执绝对路径>"`；也可继续使用 `--file "<ZIP绝对路径>" --id "<预期清单ID>" --sha256 "<原始SHA-256>"`，其中 ID 和 SHA-256 必须来自原始导出命令回执或新保存的包外回执。工具比对原始哈希、预期清单、包内容和所属书，同时只读核对当前正式稿。分别检查 `artifact_verified` 与 `usable_now`；只有包通过核验且清单仍为 prepared、本次来源匹配，才可考虑人工填写，在平台页面另行确认账号、作品、章节位置和实际可用的输入栏位。不能把包内导出时的 prepared 当成清单现存状态。

工具会检查正式版本、对应审查收据、登记路径和导出冲突。原始导入稿的 `imported_unverified` 不是已审查；有效的历史审查可使导入章具备准备资格。遇到阻断时按现有 [审稿修订](../story-skill-review/SKILL.md) 流程在用户授权范围内处理，不伪造通过的审查收据。短篇可以准备指定逐章材料，但这不证明已完本，也不是短故事整篇投稿。

ZIP 保存在本书 `.story/publishing-exports/material-<UUID>.zip`，其旁边另存同 UUID 的 `material-<UUID>.receipt.json` 包外回执，记录本书 ID、清单 ID、同名 ZIP 名称、原始 SHA-256、字节数和导出时间。每次生成新的一对文件，不覆盖旧包。ZIP 内有 `使用说明.txt`、`目录.txt`、`manifest.json`、`receipt.json`，各章保存在 `章节/第N章/` 下的 `标题.txt`、`正文.txt`、`作者的话.txt`；三个字段按冻结值原样写为 UTF-8。`作者的话.txt` 只是本地保留的候选字段，番茄和七猫后台是否有对应栏、名称及位置均须在实际页面核对，不能假定两平台的输入栏位相同。导出结果只证明导出当时版本匹配；之后改稿须重新核对或准备，旧 ZIP 不会自动更新或撤销。

已取消、过期或来源变化的包可核验并保留作历史存档，不再用于手工填报。`publish-verify-export` 返回包核验、清单现存状态与本次正式稿匹配结果，不修改账本的 `status`、`checked_at` 或创作 revision。需要持久记录过期状态时另用 `publish-check`；仅核验存档时不自动重新准备或修改清单状态。包外回执缺失或损坏时，从仍有效的清单重新导出；不从待查文件临时计算哈希冒充原值，也不用包内 `receipt.json` 替代包外回执。回执和 ZIP 同在本地，可减少拿错包、发现意外损坏；两者若被人为同时改写，不能据此主张独立身份认证。包外回执不是签名或平台回执。包损坏、所属书不符或原始清单无法核实时保留文件并处理具体问题，不依据文件名或列表记录推断可用。

## 续接、核对与备份

- 新会话先用 `publish-list --book "<书根>" --offset 0 --limit 10` 找清单，再用 `publish-export-list --book "<书根>" --id "<清单ID>" --offset 0 --limit 10` 找本书该清单的各次包外回执和对应 ZIP 是否存在，按分页字段继续；不要根据目录文件名或时间猜测最近任务。损坏回执见 `invalid_total` 与 `invalid_receipts`，不要把它当作可选材料包。导出列表只用于选择，不核对当前稿，不自动选最新包；选定后须运行 `publish-verify-export --receipt`。
- `publish-inspect --book "<书根>" --id "<清单ID>"` 回读完整固定清单；`--summary` 看章目页，`--chapter N` 看一章，两者不并用。预算不足时提高该次 `--budget-bytes` 或选择较小的章目页，不截断正文。`content_scope` 标明本次返回范围，所有视图保留完整清单指纹。
- `publish-check --book "<书根>" --id "<清单ID>"` 核对清单是否仍对应当前正式稿，按 `chapter_checks` 查看各章差异，`checked_revision` 是本次核对的创作版本。head、审查证明、路径等变化或导出不干净时处理返回问题；旧清单不自动换成新内容。过期清单再次检查会刷新差异，但即使后来又匹配，也保持 stale，需显式重新准备；已取消清单不重新核对或启用。
- 列表、回读、取消和恢复中的清单状态是保存记录，不表示本次已核对正式稿。`source_check_performed=false`、`source_matches_current=null` 表示当前匹配情况未知；实际准备、检查、导出前核对或材料包复核才报告本次结果。材料包复核会只读检查当前正式稿，但不会更新账本状态或检查时间。导出已取消清单会跳过核对，不生成 ZIP。
- `content_verification=unread` 专指远端上传正文尚未核验；本地 inspect 回读和 check 版本核对不会改变它，也不记录平台已读确认。`ok=false` 的 stale／cancelled 回读仍可包含完整旧清单，不应因此重新初始化书库。
- 用户取消本地清单时运行 `publish-cancel --book "<书根>" --id "<清单ID>"`。本地取消不代表取消任何平台草稿、排期或已发布章节。
- `publish-backup --book "<书根>"` 创建同时核对数据库结构和各清单内容指纹的独立账本备份，报告回执中的真实路径。日常创作 revision 不因准备、核对、ZIP 导出、材料包复核和备份而增加；材料包复核也不修改发布账本。当前没有从备份自动覆盖现有账本的命令；恢复书根或移到另一台电脑不产生重发授权。
- 查询若返回 `publishing_recovery_required`，运行 `publish-recover --book "<书根>"` 恢复未完成的本地事务并检查清单，再重新查询；无需旧清单 ID。此操作不会更新清单状态、确认稿件仍有效或上传到平台；之后仍用 `publish-check` 核对正式稿。文件关联不明、内容损坏或路径检查失败时保留原文件，不删除日志或重建账本来绕过错误。
- 若返回 `publishing_connection_unverifiable`，先关闭同进程中其他直接访问发布账本的 SQLite 连接，再从独立工具进程重试；宿主无法提供文件句柄核对时保留账本并报告限制，不跳过核对。
- 若旧开发账本返回 `publishing_schema_mismatch`，保留原库和备份并报告布局不兼容；不改版本号或删掉旧清单来强行继续。

## 人工填写后的副本核对

作者在官方后台自行操作后，若提供其回读的标题、正文及可取得的作者的话，可用 `publish-compare --book "<书根>" --id "<清单ID>" --chapter N --input "<JSON路径>"` 对照冻结章节。JSON 的 `title`、`body` 必填，`author_note` 可选；缺少的字段列为未核对，不当作一致。比较仅统一 CRLF／CR 与 LF，其他汉字、标点、空格和段落差异均保留。结果报告各字段是否匹配、`fields_checked`、`unchecked_fields` 与 `copy_matches_frozen`；`usable_now` 仅表示这份作者提供的副本和当前正式稿是否适合继续人工核对，不表示平台已保存。副本来源恒记为 `user_supplied_copy`，`platform_verified=false`、`ready_to_upload=false`、`remote_state=unknown`；不要将其写作平台回执或独立远端回读。输入副本可能含未公开正文，应放在本书 `.story/` 的本地私有路径，不提交公开仓库。实际后台缺少作者的话栏位时，报告该字段未核对，不编造一个已填写的栏位。

需要留存本次本地核对时，改用显式的 `publish-compare-record --book "<书根>" --id "<清单ID>" --chapter N --input "<JSON路径>"`。返回 `record_saved`、`record_id` 和 `record_sha256`；即使字段不一致或清单已经过期，也只记录当时结果，不写“平台已保存”。此命令在发布账本中新增摘要和字段哈希，不保存作者副本文字；相同输入再次执行会新增历史。只想临时核对时仍用只读 `publish-compare`。

跨会话先用 `publish-compare-history --book "<书根>" --id "<清单ID>" --offset 0 --limit 10` 分页找记录，再用 `publish-compare-inspect --book "<书根>" --record-id "<记录ID>"` 查看。历史结果里的 `usable_at_record` 只表示保存当时，本次查询不核对当前正式稿；继续人工填报前另运行 `publish-check`，必要时用当前作者副本重新比较。`publish-backup` 与 `publish-recover` 会校验并计数本地比较记录，旧 schema 1 账本无需迁移。记录及其自带摘要不是数字签名或平台回执；数据库管理员仍可修改账本。

交付明确写“本地材料已准备，尚未上传”，报告清单状态及未解决项。`platform_verified=false`、`ready_to_upload=false` 表示目标与上传能力尚未核验，不能为了满足“自动发布”而改为 true。用户已在平台手动操作也不能据此伪造本版工具不支持的远端回执。
