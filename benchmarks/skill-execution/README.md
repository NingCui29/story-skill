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

自备案例时，先准备隔离工作区，再用 `start --case-file ... --workspace ... --skills-root ... --output ...` 保存基线。案例的 `allowed_changes` 只接受精确相对路径或目录末尾 `/**`；`required_outputs` 指定必须存在且非空的文件。工作区、技能根、证据存储须分开。正式续写允许状态库发生变化，因此还须从实际工具读取 `status`、`audit` 和必要章节，独立核对章序、版本与导出；“数据库文件允许变化”不等于其所有变化都正确。

## 三类结论分开记录

1. **机械检查**：执行前后文件清单及 SHA-256、未授权新增／修改／删除、必要产物、技能内容是否漂移；Python 字节码缓存不纳入比较。它只覆盖明确指定的工作区与技能树，不能证明树外从未发生写入，不能发现先改后还原的瞬时操作，也不构成权限隔离。
2. **过程复核**：查看原始宿主记录中的调用、结果、失败和重试，对照实际产物与最终答复，逐项记为 `observed`、`failed` 或 `unverified`。给出轨迹行号和文件位置；仅出现技能名、命令字符串、完成声明或自填 `passed=true` 均不足以确认相应行为。成功读取文件也不证明理解正确。
3. **内容审查**：回读实际正文或方案，核对因果、连续性、约束、人物选择和本次交付范围。引文只用于定位，不以表单齐全替代判断。独立复核、知晓反馈后的复查与盲评分别注明。

`check` 接收原生 `codex exec --json` 日志。缺轨迹或不支持的宿主格式记为未验证，不能手写一份“执行成功”的日志补证。单次命令失败保留供复核，允许后续有证据的恢复；缺少完整结束事件、运行失败或损坏日志不能报告轨迹完整。退出码 0 只表示机械检查通过且轨迹结构完整，**`execution_status` 仍是 `needs_review`**；退出码 1 表示检测失败，2 表示输入错误或缺轨迹等未验证情况。

独立复核结果另存于运行目录，至少记录案例／技能版本、`check.json` 与实际产物哈希、逐项原文／轨迹位置、判断理由和未验证范围。验收工具不会因一份自评全部写“通过”便给出 Skill 成功结论。复核后改稿须重新检查受影响项并另存记录，不能继续引用旧产物哈希。原始失败、补写、重试和修改前产物保留；复用旧快照时明确其时间和版本。

证据输出由验收者保存于工作区之外，不授予执行者修改基线或评判标准的任务权限。先停止对工作区与技能文件的写入，再执行 `start` 或 `check`；快照不提供并发文件系统锁。文件快照和日志仍依赖运行环境的可信度；需要不可绕过的强制隔离时，应由宿主限制写入入口和保存可信审计，不能只靠本技能或本验收器。

## 扩充案例

优先将实际失误变成可重现的案例，例如审查后更改稿件、导出外改冲突、只得到封面提示词或图片生成失败。先固定预期行为和独立可观察结果，再运行；缺少相应工具或原文时保留未完成范围。没有运行过的案例不列为通过。新用例参与修复后不再当独立留出；单次模型试跑不代表稳定成功率或文学质量提升。

本协议参考 [OpenAI：Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills) 的任务、执行记录与产物分别验收方法。CLI 合成事务回归继续使用现有脚本；整体评估边界见 [评估方法](../../docs/evaluation.md)。
