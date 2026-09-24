# Token 基准

上游提交：`4daac79077928d0d5ba0eda93e46ce68dfcd40ae`；计数器：tiktoken 0.14.0 / `o200k_base`。

以下只统计冷加载的指令文件。计数可复现，不等同于实际账单或一轮总 token；正文、推理、工具结果和缓存计价均未纳入。

| 场景 | 上游 tokens | 新版 tokens | 指令减少 |
|---|---:|---:|---:|
| 长篇单章：明确必读指令下限 | 30,979 | 12,762 | 58.8% |
| 多线超长篇：含完整超长篇附加流程 | 30,979 | 15,349 | 50.45% |
| 短篇：上游仅入口 vs 新版入口与流程 | 10,422 | 12,762 | -22.45% |
| 长篇拆文：上游仅入口 vs 新版入口与流程 | 8,996 | 5,697 | 36.67% |
| 深读示范：含按需分析对照样例 | 8,996 | 7,706 | 14.34% |
| 审稿：上游仅入口 vs 新版入口与流程 | 11,334 | 4,574 | 59.64% |
| 冲突与看点审查：含中文场景参考 | 11,334 | 7,840 | 30.83% |

原始文件清单、SHA-256 和每个场景的统计边界见 [tokens.json](tokens.json)。

发现元数据：上游 13 个入口合计 1257 tokens；新版 8 个入口 378 tokens。仅含名称/描述；旧技能仍启用时，不能把这个差额当成真实节省。

合成召回夹具（不是上游实测）：

```json
{
  "kind": "synthetic_not_upstream_runtime",
  "cards_total": 160,
  "naive_all_cards_tokens": 27038,
  "bounded_packet_tokens": 1009,
  "packet_bytes": 3501,
  "budget_bytes": 8000,
  "required_selected": 2,
  "optional_selected": 3,
  "omitted_optional": 155,
  "note": "Demonstrates this implementation's retrieval, not a measured upstream project. All candidates and source contents are synthetic."
}
```

没有证据据此声称小说质量更高。质量、实际使用量和重写次数需要相同任务、相同模型、相同输出长度的端到端对照。
