# Story Codex v0.5.9：协作交接与正文分段

- 独立代理只在用户或适用规则明确要求、且宿主允许时参与边界清楚的任务；交接限于必要材料，当前会话核对结果后再采用，并如实区分自审、独立复核与盲评。
- 正文写作补充自然分段判断：说话人更换时另起段，行动焦点或人物判断转移时检查意义转折；交付前检查密集长段，保留确有作用的长段，不设固定字数门槛。

套件仍为 7 个技能、34 个载荷文件，运行时 schema 2 不变。Windows 既有 `WinError 32` 导出问题未修复，Windows 安装目标仍为 v0.4.0。

本地 Python 3.10 工程验证运行 560 项测试，553 项通过、7 项按条件跳过；ZIP、npm 包、四项运行命令、合成容量与迁移、v0.5.8→v0.5.9 隔离升级均已核对。ZIP SHA-256：`8710ed9b01a6e0d70d547eacf8be52ef30d3b22c116c420506d61b4e3276105b`。工程检查不证明小说阅读体验改善。

[安装指引](https://github.com/NingCui29/story-skill/blob/main/INSTALL.md) · [版本说明](https://github.com/NingCui29/story-skill/blob/main/docs/releases/v0.5.9.md) · [验证记录](https://github.com/NingCui29/story-skill/blob/main/benchmarks/results/v0.5.9/README.md)
