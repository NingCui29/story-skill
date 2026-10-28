# 预算、链接与本机 Story Skill 插件更新

日期：2026-10-27。本轮修复预算超限与历史失效链接，复核小说改剧本流程，并在测试通过后更新日常使用的 Codex 插件。版本仍为 0.6.27，属于本地开发补丁，没有发布新版本或操作真实小说。

## 修复与流程复核

精简共同工具说明、成稿格式、对白分段和审稿入口中的重复文字，保留纯文本、单换行、无空行或手工缩进、Markdown 禁用、作品符号与特殊载体保护、对白归属和仅排版权限。短篇默认无附言、长篇配图确认及剧本格式豁免均保留，并经独立只读复核。

现有规则加载清单的301项全部通过，清单节点及预算上限未改。数值为去空白字符和 UTF-8 字节，不是 token 或实际宿主用量。

| 原超限分支 | 修复前：字符／字节 | 修复后：字符／字节 | 限额：字符／字节 |
|---|---:|---:|---:|
| short_write_unknown_entry / writer / registered_branch_union | 88,394 / 246,206 | 87,840 / 244,595 | 88,000 / 245,000 |
| content_rewrite / independent_reviewer / registered_branch_union | 44,419 / 124,749 | 44,074 / 123,752 | 45,000 / 124,000 |

详见[预算结果](budget-summary.json)及[完整预算记录](budget/instruction-loads.json)。

历史分类审计中的三条作品路径无法证明迁移位置，因此保留原路径与历史结论，改为明确标注“当前不可达，迁移位置未证实”的文字，未创建目录凑通过，也未读取或修改作品正文。全仓本地 Markdown 目标检查通过，结果见[链接检查](markdown-links.json)。

小说改剧本的成稿、仅规划、审查、修订、分镜、来源与整体终点规则经独立复核无阻断。在前轮五个合成案例基础上，本轮新增两项实际执行：

- 按已有第01集及断点完成剩余[第02集](followup-artifacts/continuation/第02集.txt)、[第03集](followup-artifacts/continuation/第03集.txt)，沿用编号，止于原作结尾；原作及已交第01集哈希不变，没有建立小说状态库。
- [剧本仅排版](followup-artifacts/format/排版稿.txt)：只删除获准的行首缩进和多余空行；逐码点核对保留错字、标点、编号、说话人、声音归属与行内 U+00A0；原稿哈希不变。

核对结果见[执行记录](followup-execution.json)。合成试跑不是真实作品、实际拍摄或平台验收。

## 测试

首次全量运行因测试进程 PATH 未包含 Node.js，出现42条运行条件失败；独立核查确认没有其他失败原因。保留[首轮结果](unittest-missing-node.json)与[首轮日志](unittest-missing-node.log)。随后仅在测试子进程中使用 Codex 已有的 Node v24.19.0，没有修改全局 PATH、安装新依赖或放宽测试。

- 受影响三模块重跑40项，全部通过：[定向结果](node-targeted.json)。
- 正确环境下全量运行3,087项：3,062通过，25跳过，零失败、零错误：[全量结果](unittest.json)、[完整日志](unittest.log)。
- 八技能校验通过：[技能校验](skill-validation.json)。
- 源码、插件包及最终安装目录的禁用技术扫描均通过；`git diff --check` 通过。
- 插件包实际隔离安装、启用、八技能发现、命名空间提示词、CLI 和工作台 HTTP 检查通过：[隔离验证](codex-isolated-verification.json)。

测试环境说明见[test-environment.json](test-environment.json)。本机结果不替代 Windows 原生 GUI 验收。

## 实际本机更新

测试全部满足后，通过 Codex CLI 切换本地市场并重装 `story-skill@story-skill-local`，沿用现有插件安装方式，没有新增第二套技能目录。旧市场目录保留；旧插件72个文件及配置已备份到 `/Users/cuining/.codex/backups/story-skill-local/20261027-122719`。

从实际安装副本核对：

- 72个插件文件与验证包逐字节一致，缺失、额外、变化均为零；其中69个为技能载荷，8份界面提示词按插件命名空间适配。
- app-server 实际发现并启用八个技能，提示词检查通过，没有启动模型会话。
- 实际安装的 version、init、status、audit 及工作台 HTTP 检查通过；运行后安装文件哈希不变。
- 其他配置语义不变，原市场目录仍在；未修改真实书稿、书库或平台。

完整更新及验收见[安装回执](installation-after.json)，备份位置见[备份记录](installation-backup.json)。桌面应用未重启，当前会话中已加载的规则不会被本次文件更新追溯替换；重新打开 Codex 后在新会话使用更新。
