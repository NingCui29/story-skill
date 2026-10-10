# Skill 实际执行验收

这套开发验收检查代理实际做了什么：是否遵守本次范围、保护原稿、生成约定产物，并留下可复核的过程。它不取代程序回归、正文审查或读者评阅，也不要求每次小说任务运行。修改技能后选择相关案例；发布前再按本轮风险扩大覆盖。

## 固定任务与隔离运行

[prepare.py](prepare.py) 创建全新的运行目录，复制本轮技能，生成小型合成夹具，并通过 [execution_eval.py](../../scripts/execution_eval.py) 保存执行前快照。默认输出到已忽略的 `.local-writing-samples/skill-execution/`；不使用真实小说，不覆盖既有运行。夹具的历史章节由现有工具建立，准备回执与本次代理执行分开保存。

| 案例 | 本次任务 | 主要复核点 |
|---|---|---|
| [plan-only](cases/plan-only.json) | 显式调用规划，只做局部方案 | 不写正文、不初始化书库；方案回应给定处境 |
| [new-book-plan](cases/new-book-plan.json) | 正式开书，只完成策划与近期规划 | 作品简介、人物设定、总纲与卷纲、逐章细纲及采用绑定齐全；不写正文 |
| [upstream-plan-change](cases/upstream-plan-change.json) | 改已采用策划中的情节规则 | 保留正式前文和旧版，维护受影响的总纲、卷纲、未写章细纲及工具计划，重新核对绑定 |
| [review-only](cases/review-only.json) | 只审托管章候选 | 读到真实正式基线，原稿、规划和状态不变 |
| [candidate-only](cases/candidate-only.json) | 只写下一章候选 | 候选单独保存，不提交、不推进正式进度 |
| [candidate-length-override](cases/candidate-length-override.json) | 写与正式计划篇幅不同的候选 | 按本次真实例外实测，不为候选改正式计划 |
| [resume-formal](cases/resume-formal.json) | 接真实断点正式续写 | 不被较晚章号的候选误导；只提交目标章并完成导出 |
| [history-candidate](cases/history-candidate.json) | 修改早章候选 | 核对后文影响，保留正式历史，不擅自采用 |
| [non-story](cases/non-story.json) | 只审软件片段 | 不调用小说流程、不运行代码、不修改原文件 |
| [native-plan](cases/native-plan.json) | 自然语言局部规划 | 未给技能名或路径时，真实宿主能否选择并读取规划入口 |
| [native-review](cases/native-review.json) | 自然语言只审候选 | 未给技能名或路径时，能否读取审稿入口、据原文判断并保护原稿 |

执行者收到 `task.txt` 中的任务、工作目录与技能路径，以及工作区必要材料；允许的写入范围须在任务原文中说清，`allowed_changes` 不自动加入生成提示。`review_required` 是验收者的问题，不作为生成提示。`candidate-only`、`resume-formal`、`history-candidate` 三个案例同时核对随章附言和配图，候选配套与正式配套分别保存；`candidate-length-override` 按任务明确免除配套，仅核对候选篇幅与正式状态边界。图片工具不可用或生成失败时，允许先交正文和附言，但内容复核须保留缺图及配套未完成结论，不能仅据机械检查称完整交付。正例试跑提供冻结的技能路径，因此只能证明“提供技能后的执行”；即使任务未点名专用入口，也不据此宣称桌面应用自动发现已通过。验证自动发现需另在真实宿主的新会话运行，不注入技能路径，并保存该宿主的原始记录。

以下示例使用本机已登录的 Codex CLI，运行会消耗实际模型用量；脚本本身不会启动模型。先查看本机 `codex exec --help`，按可用选项运行。默认沿用模型设置，记录能核实的版本和设置；不能核实的字段写 `null`。宿主可能继续加载用户约定、记忆或其他技能，应记录实际读取范围，不把隔离文件夹称为纯净上下文盲测。不要向案例添加预期答案，也不要为通过验收放宽案例范围。

```bash
# 每轮选择全新的目录；已有目录会被拒绝。
python3 -B benchmarks/skill-execution/prepare.py --case plan-only \
  --output .local-writing-samples/skill-execution/plan-only-run-01

# 将此变量设为准备结果返回的绝对运行路径。
eval_run="<运行目录绝对路径>"
codex exec --cd "$eval_run/workspace" --sandbox workspace-write \
  --skip-git-repo-check --ephemeral --json \
  --output-last-message "$eval_run/final.txt" - \
  < "$eval_run/task.txt" > "$eval_run/trace.jsonl" 2> "$eval_run/stderr.log"

python3 -B scripts/execution_eval.py check \
  --baseline "$eval_run/baseline.json" --trace "$eval_run/trace.jsonl" \
  --output "$eval_run/check.json"
```

不需要模型的验收器自身回归：

```bash
python3 -B -m unittest discover -s tests -p 'test_execution_eval.py'
```

## 原生选择与实际版本

`native-plan`、`native-review` 和负例 `non-story` 的 `task.txt` 不注入技能名称或路径。使用真实宿主新会话运行这三个任务；不要再把冻结入口追加到提示词，也不要用上一轮对话的续聊替代新会话。准备脚本复制的源码树只作留存，原生宿主可能读取用户级安装树，必须另记实际安装根、八个技能载荷的 SHA-256 和前后变化。两棵树有差异时分别列出，不能将未使用的源码快照当作本轮版本。

保存宿主版本、完整启动参数、实际送入的任务及其哈希、原始 JSONL 和错误输出。规划、审稿和负例分开判断：成功命令返回的技能内容可以证明该次读取；只在答复里出现技能名不能证明读取，也不能证明理解正确。负例须回看完整轨迹和原文件，不以“没有生成小说”单独判定未进入小说流程。用户约定、记忆或其他技能的实际读取也记录下来，不称纯净上下文盲测。

Codex CLI 的原生选择结论只适用于记录中的 CLI；桌面应用、其他宿主及插件安装方式分别验收。当前无法取得该宿主原始记录时保留 `unverified`，不手写执行轨迹补证。

自备案例时，先准备隔离工作区，再用 `start --case-file ... --workspace ... --skills-root ... --output ...` 保存基线。案例的 `allowed_changes` 只接受精确相对路径或目录末尾 `/**`；`required_outputs` 指定必须存在且非空的文件。工作区、技能根、证据存储须分开。正式续写允许状态库发生变化，因此还须从实际工具读取 `status`、`audit` 和必要章节，独立核对章序、版本与导出；“数据库文件允许变化”不等于其所有变化都正确。

## 三轮连续任务

[sequence_prepare.py](sequence_prepare.py) 和 [六章任务](sequences/six-chapter-continuity.json) 保留同一书根、同一冻结技能及整体目标：第一轮规划并提交第1—2章；第二轮新会话只收到“继续”，提交第3—4章并跨卷；第三轮再以“继续”接第5—6章，回读结局并整理全文。初始夹具只有用户创作输入，没有预写的正式章。每章800—1000字是该任务明示的例外，附言、配图、封面及平台材料也由任务明确免除；这些条件不修改普通写作默认值。

```bash
python3 -B benchmarks/skill-execution/sequence_prepare.py start \
  --sequence six-chapter-continuity --output "<全新绝对运行目录>"
```

每轮使用上文 CLI 调用方式，工作区始终为 `<run>/workspace`，输入改为 `<run>/round-N/task.txt`，原始轨迹、最终答复和错误输出保存到该轮目录。每次都启动新会话，不使用 `resume`；没有共享对话历史时能否恢复范围，正是本组检查的一部分。每轮结束后：

```bash
python3 -B scripts/execution_eval.py check \
  --baseline "<run>/round-1/baseline.json" \
  --trace "<run>/round-1/trace.jsonl" --output "<run>/round-1/result.json"
python3 -B benchmarks/skill-execution/sequence_prepare.py next \
  --run "<run>" --after-round 1
```

第二轮替换轮号，再用 `--after-round 2`；第三轮核对后执行 `sequence_prepare.py finish --run "<run>"`。推进前检查真实章序、书ID、正式导出及哈希、前轮正文保护、卷目录、实际章幅和新会话记录；跳轮、重复、技能漂移、旧凭据改写或候选夹入合并全文会拒绝。失败和恢复保留，不能为放行直接改状态库或放宽任务。

`finish` 只说明这组缩小规模的机械核对完成，仍需逐章独立回读因果、人员安排、物件与知情、跨卷条件、核心关注及结局。它不证明常规长篇、正常章幅的长期质量、真实读者认可或原生技能选择；这组三轮提供冻结入口，与原生选择案例分开报告。

## 三类结论分开记录

1. **机械检查**：执行前后文件清单及 SHA-256、未授权新增／修改／删除、必要产物、技能内容是否漂移；Python 字节码缓存不纳入比较。它只覆盖明确指定的工作区与技能树，不能证明树外从未发生写入，不能发现先改后还原的瞬时操作，也不构成权限隔离。
2. **过程复核**：查看原始宿主记录中的调用、结果、失败和重试，对照实际产物与最终答复，逐项记为 `observed`、`failed` 或 `unverified`。给出轨迹行号和文件位置；仅出现技能名、命令字符串、完成声明或自填 `passed=true` 均不足以确认相应行为。成功读取文件也不证明理解正确。
3. **内容审查**：回读实际正文或方案，核对因果、连续性、约束、人物选择和本次交付范围。引文只用于定位，不以表单齐全替代判断。独立复核、知晓反馈后的复查与盲评分别注明。

`check` 接收原生 `codex exec --json` 日志。缺轨迹或不支持的宿主格式记为未验证，不能手写一份“执行成功”的日志补证。单次命令失败保留供复核，允许后续有证据的恢复；缺少完整结束事件、运行失败或损坏日志不能报告轨迹完整。退出码 0 只表示机械检查通过且轨迹结构完整，**`execution_status` 仍是 `needs_review`**；退出码 1 表示检测失败，2 表示输入错误或缺轨迹等未验证情况。

独立复核结果另存于运行目录，至少记录案例／技能版本、`check.json` 与实际产物哈希、逐项原文／轨迹位置、判断理由和未验证范围。验收工具不会因一份自评全部写“通过”便给出 Skill 成功结论。复核后改稿须重新检查受影响项并另存记录，不能继续引用旧产物哈希。原始失败、补写、重试和修改前产物保留；复用旧快照时明确其时间和版本。

[execution_review.py](../../scripts/execution_review.py) 可核对开发复核记录的引用是否仍有效。记录使用 `schema: 1`，绑定 `baseline_sha256`、`check_sha256`、`task: {path, sha256}`；注明 `reviewer: {kind, mode}`，其中来源为 `model`、`human` 或 `developer`，方式为 `independent`、`feedback_aware` 或 `self`。`invocation` 区分 `supplied_skill`、`native_discovery` 与 `unverified`，保存宿主及 `launch: {path, sha256}`；原生选择另保存 `host_skill_snapshot: {path, sha256}`。启动回执含实际 `host`、`argv` 与 `task_sha256`；`cwd` 记录进程启动工作目录的绝对路径，未知写 `null`。相对 `--cd` 或 `-C` 缺少可核实的启动目录时，保留路径歧义为未验证，不能仅因目录名含技能名称就判定注入。安装快照含 `root`、`before.files` 与 `after.files`。

每个 `claims` 条目记录 `id`、`kind`、`status`、`reason` 和 `evidence`。产物引用为 `{source: "artifact", path: "工作区相对路径", sha256, line, quote}`；轨迹引用为 `{source: "trace", sha256, line, quote}`，技能读取再加实际绝对 `target` 与 `target_sha256`。状态只用 `observed`、`failed`、`unverified`；类别支持技能读取、行为、产物、内容、独立复核、另一代理文本返回及读者反应。它核对原生调用的成功返回与引用文件身份；只在产物中写“独立审查已完成”不能证明分派及返回，真人反应也不能由模型文本认证。

### 审稿分派与版本关联

需要机械核对独立审稿交接时，`independent_review` 条目可选填 `review_assignment`。这是开发验收的交接协议，普通创作及当前会话自审不要求使用。旧记录格式继续可读；仅有 `wait` 返回的旧独立审稿声明保留为 `unverified`。只想记录收到另一代理文本时，可用 `other_agent_return`，其任务、稿件版本和意见正确性仍未验证。

```json
{
  "schema": 1,
  "receiver_thread_id": "reviewer-thread",
  "input": {"path": "candidate.md", "sha256": "<稿件SHA-256>"},
  "dispatch": {"source": "trace", "sha256": "<原生日志SHA-256>", "line": 4, "quote": "STORY_REVIEW_ASSIGNMENT "}
}
```

分派时先确认工作区相对稿件路径和实际文件 SHA-256，在原生 `spawn_agent` 或 `send_input` 的 `prompt` 中附一行交接信息；返回意见时由同一接收代理附一行对应回执。任务 ID 在该轨迹内保持唯一。

```text
STORY_REVIEW_ASSIGNMENT {"schema":1,"assignment_id":"review-1","task":"review","input":{"path":"candidate.md","sha256":"<稿件SHA-256>"}}
STORY_REVIEW_RETURN {"schema":1,"assignment_id":"review-1","input_sha256":"<稿件SHA-256>"}
```

`evidence` 引用实际 `wait` 完成事件中的意见文字，不能只引用交接回执。检查会核对真实分派、接收代理、先后顺序、任务 ID、返回意见及当前稿件指纹；中途重新分派、错稿或旧稿不作为关联成功。原生日志未提供 `prompt` 时，不从自填记录重构分派内容。当前正例来自合成原生事件结构测试；真实旧记录没有因此补成已验证。关联成功只支持过程与引用可追溯，不能证明代理实际读稿、判断正确或文学质量。

原生字段适配依据 [Codex CLI 事件定义](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)。其他宿主的记录须另加有实际证据的适配，不手写日志补证。

```bash
python3 -B scripts/execution_review.py check \
  --baseline "<run>/baseline.json" --check "<run>/check.json" \
  --record "<run>/review-record.json" --output "<run>/review-check.json" \
  --source-skills-root skills
python3 -B -m unittest discover -s tests -p 'test_execution*.py'
```

该工具只验证可追溯性，退出码0仍不表示文学判断正确或技能理解通过。失效哈希、错误引文等证据失败返回1，依据不足返回2。当时安装树的前后漂移、结束后当前安装树的更新、当前源码与冻结版的差异分别保存；后来的合法升级不改写历史试验的版本归属。

证据输出由验收者保存于工作区之外，不授予执行者修改基线或评判标准的任务权限。先停止对工作区与技能文件的写入，再执行 `start` 或 `check`；快照不提供并发文件系统锁。文件快照和日志仍依赖运行环境的可信度；需要不可绕过的强制隔离时，应由宿主限制写入入口和保存可信审计，不能只靠本技能或本验收器。

## 已报告用量汇总

[execution_metrics.py](../../scripts/execution_metrics.py) 仅读取已保存日志，不启动模型。`--trace` 可重复给入主任务文件，`--child-trace` 可重复给入调用者标注的子任务文件；输出目录须已存在，`--output` 拒绝覆盖既有报告，省略则输出到标准输出。

```bash
python3 -B scripts/execution_metrics.py \
  --trace "<run>/trace.jsonl" --child-trace "<run>/child-trace.jsonl" \
  --output "<run>/usage.json"
```

没有子任务日志时省略 `--child-trace`，其覆盖范围仍是未知。Codex CLI 的逐轮 `usage` 与 session JSONL 的累计 `token_count` 分别报告；累计快照只保留最新值，不相加，也不充当本次任务增量。逐轮字段分别给出已观察小计与完整小计，缺字段为 `null`；输入与缓存、输出与推理可能重叠，不把它们相加造出总量。工具去除可识别的重复事件和相同文件；缺稳定事件 ID 时，拼接重放与合法恢复可能无法区分，须回看部分小计。同一会话的不同文件可能重叠时不汇总。主任务与子任务不合为全任务总量，其包含关系未经认证。

报告列出原生工具事件、明确失败及未完成记录；重试数保留 `null`，不从错误或重复命令推断。它不判断读取全文、文学质量、计费或节省比例，也不认证日志来源。退出码0表示解析完成，仍须查看 `status`、字段覆盖与 `issues`；格式损坏或不支持返回1，文件或调用错误返回2。完整任务用量及成本比较仍需相同条件和覆盖完整的供应商记录。

## 扩充案例

优先将实际失误变成可重现的案例，例如审查后更改稿件、导出外改冲突、只得到封面提示词或图片生成失败。先固定预期行为和独立可观察结果，再运行；缺少相应工具或原文时保留未完成范围。没有运行过的案例不列为通过。新用例参与修复后不再当独立留出；单次模型试跑不代表稳定成功率或文学质量提升。

本协议参考 [OpenAI：Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills) 的任务、执行记录与产物分别验收方法。CLI 合成事务回归继续使用现有脚本；整体评估边界见 [评估方法](../../docs/evaluation.md)。
