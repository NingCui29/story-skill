# WorkBuddy AI／Qoder CN 本机验收

日期：2026-10-21。两端安装、原生发现与八技能逐项调用均已完成，各八项规则判断正确，共 16 项。Qoder 的最后一项另有超出只读要求的记忆编辑，已恢复并保留失败记录。

| 应用 | 实测版本 | 本机用户技能目录 | 安装与原生发现 |
|---|---|---|---|
| WorkBuddy AI | 5.8.4（初始安装／发现为 5.7.6） | `~/.workbuddy-ai/skills/` | 八技能、64 文件；八入口均显示并启用 |
| Qoder CN | 0.4.8 | `~/.qoder-cn/skills/` | 八技能、64 文件；八入口均出现在原生选择菜单 |

WorkBuddy 在验收期间版本变为 5.8.4 并重建进程，实际逐项调用使用 5.8.4；初始的 5.7.6 静态与列表记录作为历史保留。本轮没有主动执行应用升级。

## 安装修复

WorkBuddy 的实际启动配置使用 `.workbuddy-ai`。已修复安装器的个人与项目路径，同步安装文档和 npm 包内说明，并增加旧 `.workbuddy/skills/` 保护回归。原有两个自定义技能保持完整；本轮误装到旧目录的副本已移入可恢复备份。目录依据与纠正过程见 [5.7.6 启动配置链](workbuddy-directory-chain.json)、[5.8.4 启动配置链](workbuddy-directory-chain-5.8.4.json)和[目录纠正记录](workbuddy-directory-correction.json)。

两端各 64 个文件与本轮冻结安装快照一致，八个 `SKILL.md` 核对时亦与当前源码一致。验收期间另有平台草稿代码开发，不能把冻结快照的通过表述为当前仓库全部文件已经同步。见[完整性核对](final-installation-integrity.json)。本轮目录修复尚未发布到固定标签或 Release 附件。

## 桌面调用范围

通过应用自己的技能菜单选择各技能，确认完整测试问题实际进入会话，再将真实答复与安装文件中的规则逐项比较。Qoder 使用 `Qwen3.8-Max, 中`；WorkBuddy 使用 `Deepseek-V4.1-Flash`，均保留原模型设置。题目及判分标准见[验收方案](desktop-load-cases.md)和[预期结果](expected-results.md)。

原生选择、实际发送和正确答复用于核对桌面调用。独立文件读取和完整来源路径另列记录：没有逐项展开来源时保持“未核”，不以模型自述路径代替证据。此轮没有验收完整正文创作、生图、联网调研或平台草稿流程。

## 逐项结果

下表的“正确”指原生选择、完整问题实际发送及规则判断正确，不代表完整业务流程验收。

| 技能 | WorkBuddy AI | Qoder CN |
|---|---|---|
| `story-skill` 总入口 | 正确 | 正确 |
| `story-skill-plan` 规划 | 正确 | 正确 |
| `story-skill-write` 写作 | 正确 | 正确 |
| `story-skill-analyze` 分析 | 正确 | 正确 |
| `story-skill-review` 审稿 | 正确 | 正确 |
| `story-skill-research` 调研 | 正确 | 正确 |
| `story-skill-cover` 封面 | 正确 | 正确 |
| `story-skill-publish` 发布 | 正确 | 规则正确；额外记忆编辑超出只读要求 |

逐题输入、真实答复与证据索引见 [WorkBuddy 结果](workbuddy-desktop/results.json)及 [Qoder 结果](qoder-desktop/results.json)。WorkBuddy 当前运行版本由[原生“关于”面板](workbuddy-desktop/runtime-about-version-ax.json)确认，版本为 5.8.4（73952edb）。

## 已知偏离

Qoder 的八项规则判断均正确，但第八项后额外编辑了本轮验收记忆，违反只读要求。该范围项保持失败；测试记忆已移至可恢复备份，索引只移除唯一的测试引用，其他记忆未改动。见[八项结果](qoder-desktop/results.json)、[额外工具动作](qoder-desktop/case8-scope-violation.json)和[清理记录](qoder-memory-cleanup.json)。

回答包含引文和完整路径后普遍超过要求的 120 字，原始输出与字数偏离均保留，不截改输出。WorkBuddy 首题包含输入兼容性试验留下的前缀和重复题目；实际原生选择、完整问题与正确答复分别留证，后续题目使用空白输入框。

安装目录回归共四项通过，覆盖首次安装、真实托管升级、Qoder 项目共享及 WorkBuddy 旧目录保护；见[回归记录](directory-regression.json)。两端实际安装的共享运行时均能在本机终端显示帮助，见[启动核对](local-runtime-help.json)；这不代替桌面内业务命令验收。
