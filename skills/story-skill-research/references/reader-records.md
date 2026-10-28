# 分次阅读记录工具

只在本次要求设计、保存或比较阅读观察时使用。它沿用 [分次试读与追更验证](reader-validation.md#分次试读与追更验证)，保存本地研究材料；不运行模型、不邀读、不上传、不定时通知，也不更改小说状态。普通创作不用执行。工具路径沿公共入口的 `<tool>`。

## 冻结阅读计划

准备 UTF-8 JSON `plan.json`。文件路径为绝对路径，程序冻结实际字节的 SHA-256；同一文件改稿后应保留旧稿，再新建研究。范围、开放及截止时间、提供方式和提醒计划都属于比较条件。多个轮次分别引用对应文本；后续轮次用 `follows_round_id` 指向更早轮次。不同候选、修改前后版本可放不同轮次，不伪装为同稿。

下面是格式示例，日期、路径、问题和人群须替换为实际计划。匿名 ID 只用于本次研究，别填姓名、联系方式或账号；登记计划不表示这些人已经参加。

```json
{
  "study_id": "reading-01",
  "question": "这次关系回报之后，读者具体想继续看什么？",
  "participants": [
    {"participant_id": "reader-01", "source_kind": "human", "cohort": "目标读者"},
    {"participant_id": "model-01", "source_kind": "model", "cohort": "模型评阅"}
  ],
  "rounds": [
    {
      "round_id": "round-1",
      "manuscript": "/绝对路径/第一轮正文.md",
      "reading_scope": "第一章完整正文",
      "available_at": "2026-10-01T10:00:00+08:00",
      "closes_at": "2026-10-02T10:00:00+08:00",
      "delivery": "按既定授权提供本地文件",
      "reminder_policy": "本轮不提醒",
      "follows_round_id": null
    },
    {
      "round_id": "round-2",
      "manuscript": "/绝对路径/第二轮正文.md",
      "reading_scope": "第二章完整正文",
      "available_at": "2026-10-03T10:00:00+08:00",
      "closes_at": "2026-10-04T10:00:00+08:00",
      "delivery": "与第一轮相同",
      "reminder_policy": "本轮不提醒",
      "follows_round_id": "round-1"
    }
  ]
}
```

```bash
python <tool> reader-init --plan /绝对路径/plan.json --output-dir /绝对路径/试读记录-01
```

输出新目录中的 `study.json`、待填 `observation-template.json`、`summary.json` 和 `summary.md`。目录必须尚不存在；旧稿和旧研究不覆盖。计划、匿名参与者来源类型或提供条件有实质变化时，保留旧研究，另存新计划再初始化，不修改旧 `plan_sha256`。

## 追加观察

复制观察模板到另一文件，填写实际观察；多个对象放入 `items`。模板自带 `plan_sha256`，不得从其他研究混入。主要字段如下：

| 字段 | 填写依据 |
|---|---|
| `participant_id`、`round_id` | 已登记匿名 ID 与轮次 |
| `observed_at` | 实际报告或核对时间，带时区；不是推定的开始阅读时间 |
| `opportunity` | `provided` 已提供、`not_provided` 未提供、`unknown` 不清楚；已提供还须填 `provided_at` |
| `reminder` | 实际 `none` 未提醒、`sent` 已提醒、`unknown` 不清楚，与计划分开 |
| `reading` | `not_started`、`started`、`completed`、`unknown`；不能把开始当读完 |
| `evidence_kind` | 真人用 `participant_report` 或 `local_record`；模型用 `model_output`；来源或观察不足用 `unknown` 并保留未知阅读状态 |
| `evidence_file` | 本地记录或模型原始输出的绝对文件路径，程序保存哈希；真人原话可直接保存在下一项 |
| `reader_words` | 参与者或模型原话；保留不同意见，不改写成自己希望得到的反馈 |
| `text_quote`、`text_locator` | 对应正文原句与位置；摘录须逐字存在于该轮稿件，只有定位没有摘录时不声称字面核验 |
| `revision_hypothesis` | 根据原话与正文提出的修改假设，与原话分开；可以为空 |
| `limitations` | 时间安排、重复阅读熟悉效应、反馈不完整等实际限制 |
| `supersedes` | 首次观察为 `null`；进度更新或修正时填同一对象同一轮最新 `record_id` |

`participant_report` 至少保留原话或本地记录；`local_record` 和 `model_output` 必须有本地文件。程序核对填写类型和文件，不认证内容真假、参与者身份、实际阅读或模型调用。仅计划阅读的人不能填成实际已读。

```bash
python <tool> reader-record --input /绝对路径/试读记录-01/study.json --observations /绝对路径/观察-01.json --output-dir /绝对路径/试读记录-02
```

每次另存新目录。重复导入相同观察不重复计数；变更必须追加 `supersedes`，原记录仍保留。一个对象的不同意见不通过平均或最后一条覆盖原话；汇总取截止时间前最新状态，报告同时列出全部历史。不要手改旧记录的内容或哈希。

## 核对与解释汇总

```bash
python <tool> reader-check --input /绝对路径/试读记录-02/study.json --as-of 2026-10-05T00:00:00+08:00
python <tool> reader-render --input /绝对路径/试读记录-02/study.json --output-dir /绝对路径/试读汇总-01 --as-of 2026-10-05T00:00:00+08:00
```

不填 `--as-of` 则使用当前 UTC 时间。汇总按稿件版本、轮次、人群、来源、实际提醒、是否获得机会及是否逾期提供分组，只列计数，不生成留存率、胜负、因果解释或文学评分。

| 汇总字段 | 可说明什么 |
|---|---|
| `planned_pairs`、`unobserved` | 计划中的对象与轮次组合、尚无观察；不等于实际参加或流失 |
| `no_opportunity`、`opportunity_unknown` | 未提供后续阅读机会、机会不清楚；不能据此评价正文 |
| `started`、`completed` | 最新观察报告开始或完成阅读；`completed` 是 `started` 的子集，不相加 |
| `reported_return` | 后续轮已报告开始，且存在该对象更早轮次的先前阅读观察；仅表示记录相容，不认证真实返回 |
| `prior_reading_unverified` | 有本轮阅读报告但没有可接续的前次阅读观察，不能算追更返回 |
| `window_open` | 尚未到观察截止；不算未返回 |
| `not_started_at_observation` | 获得机会后，在截止之后仍明确报告未开始；原因需另查，不自动归因于正文 |
| `reading_unknown_at_cutoff` | 截止后的状态没有证据；截止前的“还没看”不被推成最终未读 |
| `late_opportunity` | 截止后才提供机会，单独分组，不混进正常窗口判断 |

稿件或证据文件改变、丢失时，检查和报告保留原观察并显示 `source_changed` 及具体来源；旧反馈仍只属于已记录哈希，不自动套给新稿。追加该轮观察要求当前稿件仍匹配冻结版本。哈希与摘录通过只说明材料关联可核对，真人试读是否执行、阅读效果与修改取舍仍须根据实际证据判断。
