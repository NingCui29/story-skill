# Token 基准

上游提交：`4daac79077928d0d5ba0eda93e46ce68dfcd40ae`；计数器：tiktoken 0.14.0 / `o200k_base`。

本次开发工作树基线：`adc948da380d49ac85847b772dd56b319c5ad12f`；有未提交改动：`True`。各指令文件、配置、计数脚本及合成夹具运行代码的 SHA-256 见 JSON。

以下只统计冷加载的指令文件。计数可复现，不等同于实际账单或一轮总 token；正文、推理、工具结果和缓存计价均未纳入。

场景按显式列出的适用必读文件校验；引用只需部分段落时仍计完整文件，未自动推导 Markdown 引用闭包。上游仅入口的场景是保守下限，不能据此比较双方完整审稿能力。

| 场景 | 上游 tokens | 新版 tokens | 指令减少 |
|---|---:|---:|---:|
| 长篇单章：明确必读文件 | 30,979 | 38,336 | -23.75% |
| 多线超长篇：含完整超长篇附加流程 | 30,979 | 41,000 | -32.35% |
| 短篇：上游仅入口 vs 新版入口与流程 | 10,422 | 38,336 | -267.84% |
| 长篇拆文：上游仅入口 vs 新版入口与流程 | 8,996 | 8,296 | 7.78% |
| 深读示范：含按需分析对照样例 | 8,996 | 10,305 | -14.55% |
| 完整章内容审稿：上游仅入口 vs 新版适用参考 | 11,334 | 34,401 | -203.52% |
| 完整章冲突与看点审查：含内容审查与场景参考 | 11,334 | 34,401 | -203.52% |

原始文件清单、SHA-256 和每个场景的统计边界见 [tokens.json](tokens.json)。

发现元数据：上游 13 个入口合计 1257 tokens；新版 8 个入口 450 tokens。仅含名称/描述；旧技能仍启用时，不能把这个差额当成真实节省。

合成召回夹具（不是上游实测）：

```json
{
  "kind": "synthetic_not_upstream_runtime",
  "cards_total": 160,
  "naive_all_cards_tokens": 27038,
  "bounded_packet_tokens": 1017,
  "packet_bytes": 3532,
  "budget_bytes": 8000,
  "required_selected": 2,
  "optional_selected": 3,
  "omitted_optional": 155,
  "note": "Demonstrates this implementation's retrieval, not a measured upstream project. All candidates and source contents are synthetic."
}
```

没有证据据此声称小说质量更高。质量、实际使用量和重写次数需要相同任务、相同模型、相同输出长度的端到端对照。
