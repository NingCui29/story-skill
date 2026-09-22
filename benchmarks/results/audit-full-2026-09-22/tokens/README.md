# 当前源码静态 token 复核

本目录记录 2026-09-22 尚未发布工作区源码的静态指令计数，使用固定上游提交 `4daac79077928d0d5ba0eda93e46ce68dfcd40ae` 的干净 checkout 与 tiktoken 0.14.0 / `o200k_base`。沿用已有临时依赖，未安装新依赖；上游文件只读，不执行上游代码。

七个预设场景的结果及各自统计边界见 [tokens.md](tokens.md) 和 [tokens.json](tokens.json)。静态指令计数不等于实际账单、单轮总用量或文学质量；合成上下文夹具仅验证本地实现的召回行为，不是上游实测。

[运行回执](run-receipt.json) 记录准确命令与成功状态。[当前源码清单](source-snapshot.json) 中 224 个文件及[上游源码清单](upstream-snapshot.json) 在运行前后哈希一致；清单排除生成报告和 Python 字节码。`profiles.json` 与历史结果保持原样。
