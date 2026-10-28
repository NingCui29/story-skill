# 开发期阶段活动映射

`scripts/execution_activity.py` 只读分析已有原生日志，不调用模型，也不给普通创作增加逐步打卡。用于定位规则是否被提前、重复读取，并为同条件比较保留证据；最终取舍仍须结合产物、失败与阅读效果。

## 输入与执行

```bash
python3 -B scripts/execution_activity.py \
  --trace /绝对路径/main-trace.jsonl \
  --child-trace /绝对路径/child-trace.jsonl \
  --map /绝对路径/activity-map.json \
  --output /绝对路径/新活动报告.json
```

`--trace` 与 `--child-trace` 可重复。没有子日志或活动映射时省略相应参数；未给映射时不生成读取完成声明。`--output` 只创建新文件，省略则输出 JSON。输入须使用实际非链接路径，且分析时停止写入；每个日志最多128 MiB、映射4 MiB、被引源文件32 MiB。不能靠改写日志或源文件以通过检查。

`activity-map.json` 示例：

```json
{
  "schema": 1,
  "annotations": [
    {
      "trace": {"path": "/绝对路径/main-trace.jsonl", "sha256": "实际日志SHA256"},
      "line": 12,
      "phase": "preparation",
      "source": {"path": "/绝对路径/冻结技能/references/chapter.md", "sha256": "实际源文件SHA256"},
      "quote": "成功工具返回和被引源文件中均存在的原始摘录"
    }
  ]
}
```

示例哈希与行号须替换为实际值。行号是 JSONL 物理行号，指向原生 `item.completed`，不指向模型的完成宣称。阶段可用 `preparation`、`drafting`、`review`、`commit`、`delivery`、`other`；这是开发者依据实际任务添加的标注，不是宿主自动阶段，也不从模型叙述猜测。

## 读取证据范围

每个 `excerpt_observed` 同时要求：日志哈希对应、事件成功返回、原始输出确有摘录、来源文件哈希未变且含同一摘录、调用确实是对该路径的受支持读取。识别范围复用现有审查器的字面 `cat/head/sed`、`read_file/read_text_file`。复合 shell 命令、动态路径、其他工具、命令只是提到文件，以及模型自己声称读过，都不据此认证。

绝对读路径可直接核对；相对路径只有原生事件携带绝对 `cwd` 才解析，开发者补写工作目录不补成宿主证据。来源应保留在运行时的冻结位置。文件后来改变会使该引用待核；不要把另一版本相同的一句话当作原版本哈希。

报告只统计被标注的摘录及摘录字符数，不计算全部文件读取字节、实际 token 或理解程度。相同原生事件的重复标注、可识别的重放事件只计一次；不同成功调用出现完全相同的引用片段时标出 `repeated_excerpt_of`。部分重叠没有自动扩成整段重复，重复也不直接等于无效工作。同一事件被标成不同阶段时保留冲突，不能双计。

## 子任务与用量

`native_task_links` 仅在原生 `spawn_agent/send_input` 完成事件的 sender 与当前日志会话一致，并且 receiver 对应所提供子日志的原生会话 ID 时，给出 `native_session_link`。没有匹配子日志则为 `child_trace_missing`。把文件放在 `--child-trace` 后面本身不能证明父子关系；`send_input` 也不证明该代理由本任务创建。

这种关联不认证实际任务内容、代理独立性、读取完整性或意见正确性。主任务 usage 是否包含子任务、是否缺失别的调用仍未知，因此 `full_task_total` 始终为 `null`；底层用量沿用 `execution_metrics.py`，不把累计快照、重叠字段或恢复日志相加造出节省比例。

退出码0表示所选证据可核对，仍不是全任务成功；1表示有待核活动或用量记录不完整，须看每项原因；2表示输入或文件错误。程序测试的合成事件、真实日志前向检查、同条件成本结论分别报告，不能相互替代。
