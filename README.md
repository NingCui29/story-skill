# Story Skill

**把想法写成故事，让故事能够接着写、反复改、完整交付。**

Story Skill 是一套中文小说创作技能，供 Codex、Claude Code、Claude 桌面版及其他支持 Agent Skills 的助手使用。它把拆书、规划、正文、审稿、调研、封面和投稿材料准备连成可接续的流程，并提供本机工作台，方便阅读书稿、编辑候选和比较版本。

**发布版本：v0.6.19 · 八个技能 · Python 3.10+ · 支持长篇与短篇**

本版整合工作台界面、作品管理、备份恢复、更新下载进度和平台草稿功能，已发布 `v0.6.19`，版本分支为 `version-0.6.19`。完整变化、验收与分发结果见 [v0.6.19 发布记录](docs/releases/v0.6.19.md)。上一正式版本见 [v0.6.18](docs/releases/v0.6.18.md)。

[下载安装](#安装升级) · [开始写书](#开始写书) · [使用工作台](#使用工作台) · [完整文档](docs/README.md) · [发布记录](docs/releases/v0.6.19.md)

## 它能帮你做什么

| 你的目标 | Story Skill 的工作 |
|---|---|
| 把一个想法发展成新书 | 拟写书名与简介，建立人物、核心冲突、全书总纲、卷纲和近期细纲 |
| 学习一部参考作品 | 对照原文分析人物选择、冲突、信息与情绪安排，提炼方法，再设计原创故事 |
| 持续写作与续写 | 衔接正式断点、人物状态和采用的细纲，召回本书偏好，展开本章关键场面 |
| 修改已有书稿 | 找出平淡、因果、人物反应、文风和套语问题，生成候选并复核修改效果 |
| 完成一篇短故事 | 处理阅读期待、人物选择、后果和结局，交付经审查的全文及适用封面 |
| 规划剧本或拆分镜 | 根据小说规划剧本，或从小说、剧本拆出逐镜画面与声音安排，保留来源对照 |
| 整理与准备交付 | 阅读、编辑、对照书稿，备份整书，为番茄、七猫、起点准备离线材料与受控平台草稿 |

[平台草稿](skills/story-skill-publish/references/platform-drafts.md)支持正式章节清单预览、整批草稿保存、进度和只读对账。番茄可在工作台填写账号 ID、章节管理网址与章节范围，准备本机配置和冻结清单；保存时需要当前助手连接既有、已登录的 Chrome 并持续执行。缺少连接或可靠回读配置时阻止保存，不会自动登录、提交审核或定时发布。

番茄已有指定测试作品的合成稿完整链路验证：新建草稿、独立预览全文回读及重复执行时避免重复新建。该证据仅覆盖单卷完整列表；七猫、起点、真实分页与多卷仍待实页验收。正文与冻结稿的段落、空白或标题不一致时保留冲突。具体样本与限制见[发布记录](docs/releases/v0.6.19.md#平台草稿的实际范围)。

题材写法、人物行动、情绪兑现和文风选择按具体作品处理。长篇保留持续推进与长距伏笔，短篇关注有限篇幅中的完整体验；安静场景、合作关系和非反转结局同样适用。

## 安装升级

先按使用环境选包，并核对同名 `.sha256` 校验文件。固定提交、附件摘要及实际下载核验范围见[发布记录](docs/releases/v0.6.19.md)。

| 使用环境 | 下载 | 安装说明 |
|---|---|---|
| Codex 本地插件 | [插件 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-codex-0.6.19.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-codex-0.6.19.zip.sha256) | [本地市场与插件安装](docs/codex-plugin.md) |
| Claude 桌面版 | [单入口 ZIP（r2）](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-claude-desktop-0.6.19-r2.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-claude-desktop-0.6.19-r2.zip.sha256) | [导入并启用](INSTALL.md#claude-桌面版导入单技能包) |
| Claude Code、Antigravity 等目录安装 | [完整八技能 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-0.6.19.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-skill-0.6.19.zip.sha256) | [完整套件与宿主目录](INSTALL.md#目录安装的完整套件) |
| Intel Mac 本机工作台 0.1.18 | [DMG 安装包](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.dmg) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.dmg.sha256) | [Mac 客户端说明](desktop/macos/README.md) |
| Windows x64 本机工作台 0.1.9 | [安装程序](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe.sha256) | [Windows 客户端说明](desktop/windows/README.md) |

两端客户端内嵌运行时 0.6.19，完整创作技能另行安装。Mac 使用本地临时签名、未经 Apple 公证；Windows 安装程序未做代码签名。另提供 Mac ZIP 和 Windows 便携 ZIP，见[独立应用安装步骤](#独立应用下载与安装)。[GitHub Packages](https://github.com/users/NingCui29/packages/npm/package/story-skill) 已同步 `@ningcui29/story-skill@0.6.19`，注册表下载物与运行时已独立核验；npm 安装本身不向宿主注册技能。

### Codex 本地插件

使用支持插件管理的 Codex 客户端。核对插件 ZIP 的摘要后，解压并保留市场目录，再运行：

```bash
codex plugin marketplace add "<解压后含 .agents/plugins/marketplace.json 的市场根目录>"
codex plugin add story-skill@story-skill-local
codex plugin list --marketplace story-skill-local --json
```

确认 Story Skill 的版本为 `0.6.19`，且 `installed`、`enabled` 均为 `true`。重新打开 Codex 并开始新会话，以实际补全列表和技能发现结果核对加载。插件入口带命名空间，例如 `$story-skill:story-skill-plan`；也可直接说“使用 Story Skill”并描述任务。

插件安装与传统技能目录安装任选一种。已有旧副本时，先备份并核对本地修改，将旧套件移出技能扫描目录，避免重复加载。完整操作见 [Codex 插件说明](docs/codex-plugin.md#安装到日常使用的-codex)。

### Claude 桌面版

使用上表中的 `-r2` 专用包，按[桌面导入步骤](INSTALL.md#claude-桌面版导入单技能包)上传并启用。它通过一个总入口按需调用完整八流程。启用后，直接说“使用 Story Skill，帮我规划一本短篇，本轮不写正文”。

书稿需放在会话实际可读写的位置。普通聊天或云端任务保存的文件，应下载完整书目录备份；会话文件环境与作者电脑、本机工作台的访问范围按 [安装指引](INSTALL.md#书稿保存与桌面能力)分别核对。

### Claude Code 与其他宿主

使用源码安装器时，在对应版本的仓库根目录运行，例如：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --project "<写作项目根目录>"
python3 -B -X utf8 scripts/install.py --host antigravity --project "<写作项目根目录>"
```

只选择适用的一条。个人安装改用 `--user`；已有托管副本升级加 `--update`，只读核对加 `--check`。安装器保留升级备份并保护本地修改，标准技能 ZIP 中不含安装器。

源码安装器还支持 Qoder、Qoder CN、ZCode、WorkBuddy AI、TRAE、Cursor、CodeBuddy、OpenCode、Copilot、Gemini CLI、Cline、Windsurf 和 Devin 等宿主。具体应用版本、目录与调用方式见 [宿主安装表](INSTALL.md#从本仓库源码安装)。目录安装需完整保留八个同级技能。

**安装后核对宿主实际发现的技能与版本。GitHub 发版不会自动更新本机副本。**

## 开始写书

把 `<…>` 替换为实际路径和要求。下列自然语言请求可按需修改；不确定该选哪个入口时，直接使用总入口。

### 开一本新书

```text
使用 Story Skill，帮我规划一本中文小说。
写作父目录：<会话可访问的绝对路径>
题材与核心想法：<你的想法>
长短篇定位与预计篇幅：<目标或待讨论>
主投平台：<已定平台或暂不确定>

先确定书名、主角与关键人物、核心冲突和持续推进方向。
另建书名目录，保存创作约定、策划、全书总纲、卷纲和近期逐章细纲。
本轮只做规划，不写正文。
```

平台未定时仍可推进独立构思。规划默认分别给出番茄、七猫、起点的分类与作品分析；明确限定单平台时按本次范围处理。本地拟选、后台已确认和待核对项分别记录。

完整新章默认按 2400—2800 个正文可见字符规划，含标点、不含章名。用户或本书创作约定明确采用其他篇幅、统计口径时按有依据的例外处理。片段、章中续写和局部修改按实际范围完成。[大纲细纲规范](skills/story-skill-plan/references/outline.md)

### 从拆书开始

```text
使用 Story Skill，分析 <参考作品文件绝对路径>，再规划一本原创新书。
写作父目录：<绝对路径>
新书方向：<题材、读者、篇幅>

先核对可读原文范围，分析人物选择、冲突推进、信息安排和情绪兑现。
结论给出原文依据，提炼可迁移的方法、成立条件与风险。
参考分析保存到“作品分析/<参考书名>”，新书另建书名目录。
重新设计人物、关系、事件与解决方式，不做换名改写。
完成策划、总纲和前三章细纲后停下。
```

只点评开篇可直接粘贴文本；完整深读或跨会话续跑应提供可读取文件。结论范围以实际取得的原文为准。[拆书与深读说明](docs/作品深读与评估.md)

### 拟写或优化简介

```text
使用 Story Skill，优化 <书目录或现有简介> 的小说简介。
面向 <目标读者与用途>，把最有辨识度的冲突和阅读期待前置。
保留本书事实与语气，按 <字数及剧透要求> 交一份推荐稿。
本轮只处理简介，需要保存时另存候选。
```

### 写下一章

```text
使用 Story Skill，继续 <书目录绝对路径>。
先核对正式断点、前文衔接、本书约定与采用的细纲，再完成下一章。
把人物的尝试、阻力、回应和后果写成场景，实际统计字数。
完成审查后保存正式章节，并核对导出结果。
```

先试写时加上“只保存候选，暂不采用”；接着当前章写时说明“继续本章，不另起新章”。已绑定下一正式章优先使用写前简报，核对本书偏好及其例外、来源，再进入写作。

### 审稿与修改

```text
使用 Story Skill，审阅 <章节或候选稿绝对路径>。
重点检查 <本次关注的问题>，给出文本依据并说明影响。
保留核心剧情，另存修改候选，暂不替换正式稿。
```

只分析时说“只审不改”；只排版时说明是否允许改字词和标点。采用候选时明确提出，再核对正式版本和后续章节影响。

### 剧本与分镜

可以说“根据 <有权改编的小说文件> 规划剧本，本轮只交改编规划”，或“把 <小说或剧本文件> 拆成分镜”。规划技能负责来源对照、改编取舍与逐镜安排，材料独立保存；分镜中的实际图像、旁白和成片需另有相应工具与任务授权。[剧本与分镜规则](skills/story-skill-plan/references/outline.md#根据小说规划剧本)

## 使用工作台

工作台提供章节与材料导航、正文阅读、候选编辑、全文查找和版本差异对照。它在本机运行，本身不调用模型。

本版客户端为 **Mac 0.1.18／Windows 0.1.9**，内嵌运行时 **0.6.19**。天空蓝界面集中搜索与筛选，作品卡保留书名、简要进度和打开入口，低频操作放在“管理作品”。书架右上方和编辑页顶栏的“外观”可切换浅色、深色或跟随系统，保留未保存输入与阅读位置。

书架可新建、移出、重新定位、备份和恢复作品；删除只使用系统废纸篓／回收站。编辑页可保存全部候选、找回恢复稿、选择两稿对照和生成下一章写作任务。备份与恢复显示执行状态及结果，具体入口见[工作台操作说明](docs/工作台需求与进展.md)。

Mac 应用菜单和 Windows 工具栏提供“检查更新”。发现正式新版后，可下载并查看百分比、大小、速度、预计剩余时间与 SHA256 校验，支持取消和失败重试。核验后打开安装包，由用户完成安装并重新打开客户端；下载完成与安装完成分别提示。更新不会自动结束作品服务。详见[更新流程](docs/工作台更新流程.md)。

### 独立应用下载与安装

两端均内置 Python，直接安装即可打开书架。安装前保存全部页面编辑，退出旧客户端。本版已完成对应平台的构建与隔离验收；Mac 原生自检在本机完成，Windows 原生自检在 CI runner 完成，具体覆盖范围见[发布记录](docs/releases/v0.6.19.md)。

**Mac（Intel）：**

1. 下载[0.1.18 DMG](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.dmg)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.dmg.sha256)，核对后挂载，将“写作工作台.app”拖入 Applications。
2. 也可下载[客户端 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.18-macos-x86_64.zip.sha256)，解压后复制应用。
3. 打开工作台并添加作品目录。macOS 请求访问作品所在文件夹时，按实际需要允许。客户端未经 Apple 公证；Apple Silicon 尚未验证。[Mac 说明](desktop/macos/README.md)

**Windows（x64）：**

1. 下载[0.1.9 安装程序](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe.sha256)，核对后运行，按向导安装到当前用户目录。
2. 从开始菜单打开“写作工作台”，选择作品目录并添加到书架；卸载可使用 Windows 应用列表。
3. 便携使用时下载[版本化 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64.zip.sha256)，完整解压后运行 `StoryWorkbench.exe`，保留同目录的运行文件。安装程序未签名；Windows 10/11 用户环境的人工验收范围见[Windows 说明](desktop/windows/README.md)。

### 通过助手或命令行打开

也可以让助手“打开 <书目录绝对路径> 的可编辑工作台”。手动启动独立书架时，将 `<核心技能目录>` 换成安装后的 `story-skill` 目录：

```bash
python3 -B -X utf8 "<核心技能目录>/scripts/story.py" workbench-library-serve --open
```

服务启动后，书架默认位于 `http://127.0.0.1:8765/`。可先打开空书架，再添加已初始化的书目录；选择作品后在新标签打开该书的编辑页。

| 区域 | 主要用途 |
|---|---|
| 左栏 | 找章节、策划、细纲、草稿和图片 |
| 中栏 | 阅读、编辑候选、并排比较版本差异 |
| 右栏 | 核对篇幅、断点、约束和审查记录 |

在工作台保存修改会另存新候选；采用为正式稿仍走审稿与修订流程。升级或重启前先保存各页面编辑，再核对并停止旧服务。书架固定入口、单书编辑服务和只读静态导出各有用途，详见 [工作台操作与限制](docs/本地工作台分析.md)。

书架提供“新建作品”；“书架工具”中提供“从备份恢复”“恢复中心”；作品卡“管理作品”可备份、重新定位、移出或删除至系统回收站。删除需输入完整书名并先停止本书编辑服务，目录和身份异常时中止，不提供永久删除回退。编辑页“设置”可保存全部候选、找回恢复稿，“更多”可选择两稿对照或生成下一章写作任务。详见[工作台需求与进展](docs/工作台需求与进展.md)。

手动运行源码需要本机安装 Python，并能访问书目录；独立应用已内置运行时。云端任务中的 `127.0.0.1` 指向其运行环境；桌面技能导入也不会自动启动作者电脑上的服务。

## 书稿与交付

**一书一目录，书稿放在技能源码和安装目录之外。** 已有工程沿用已记录的路径。以下为目录示意：

```text
新书书名/
├── 创作约定.md
├── 00_项目策划/
├── 01_大纲细纲/
├── chapters/
└── .story/
    └── drafts/
```

规划、候选、正式正文和审查状态分别保存。备份时复制整个书目录，包括 `.story/`，以保留完整进度；规划阶段按实际需要建文件。[目录规范](docs/目录结构.md) · [恢复指南](docs/recovery.md)

正文按语义分段，段间空一行，不手工缩进。换人发言默认换段；正文与分析、评分、创作过程说明分别交付。字数要求实际统计。

- **完整长篇章节：** 默认独立交付作者有话说和实际配图，完整章候选与续写补完当前章同样适用。配图采用竖版 **9:16**，突出一个具体视觉重点；附言 Markdown 只放文字，图片独立保存。用户明确免除时按其要求处理。
- **短故事／短篇：** 默认不写附言、不生成附言配图，已有文件保留。完本交付包含以书名命名的全文合并文件、全文审查与适用封面。
- **封面、联网与图像：** 使用当前会话实际可用的工具。技能安装不会自动增加这些能力；缺少能力或生成未完成时，交付中如实说明。
- **投稿材料：** 番茄、七猫、起点使用已审查正式章节准备离线材料；经核对的平台配置可用于保存草稿与回读。书名、署名、简介、封面、分类、素材权利和 AI 使用分别核对，审核提交与定时发布由作者在后台完成。

[章节与附言规则](skills/story-skill-write/references/chapter.md) · [平台投稿流程](docs/platform-publishing.md)

## 八个技能入口

| 源码技能名 | 用途 |
|---|---|
| `story-skill` | 总入口、跨流程任务与工作台 |
| `story-skill-plan` | 开书、书名简介、设定、大纲细纲、剧本规划与分镜 |
| `story-skill-write` | 正文创作、续写、连载与短篇完本 |
| `story-skill-review` | 审稿、修改、排版与历史章节修订 |
| `story-skill-analyze` | 开篇点评、完整深读与分析续跑 |
| `story-skill-research` | 榜单、题材市场与素材查证 |
| `story-skill-cover` | 封面生成与修改 |
| `story-skill-publish` | 番茄、七猫、起点离线材料、平台草稿与回读核对 |

手动调用时，以宿主实际补全列表为准：Codex 插件使用 `$story-skill:story-skill-plan` 等命名空间入口；Codex 目录安装使用 `$story-skill-plan` 等入口，Claude Code 使用 `/story-skill-plan`。Claude 桌面包仅启用一个总入口，内部按需读取八个流程。

## 版本与验证

v0.6.19 整合以下更新：

- 工作台采用紧凑的天空蓝界面，完善筛选、菜单、返回刷新和操作反馈。
- 新增作品管理、系统回收站、整书备份恢复、恢复中心与保存全部候选。
- Mac 和 Windows 提供检查更新、下载进度、取消、重试与完整性核对。
- 平台草稿支持冻结清单、预览、受控保存、全文回读、重复执行核对和番茄自助配置。

写前交接、偏好召回、关键情节点覆盖、短篇平台化设计及原有语义审查继续保留。书库沿用 schema 2。计划固定标签为 `v0.6.19`，版本分支为 `version-0.6.19`；八技能源清单为 62 文件，最终包体与文件数仍按构建回执核对。

本版完整回归、跨平台 CI、固定标签安装、Release 附件回下载和 npm 独立核验正在进行，状态与实际摘要集中记录在 [v0.6.19 发布记录](docs/releases/v0.6.19.md)。历史版本结果保存在各版记录，不作为本版通过依据。文件与程序核验的范围、宿主界面、平台实页和小说阅读效果分别报告。[评估方法](docs/evaluation.md)

## 文档与开发

[中文小说上手](docs/中文小说上手.md) · [完整文档导航](docs/README.md) · [工作台](docs/本地工作台分析.md) · [超长篇实操](docs/超长篇实操.md) · [发布维护](docs/github-release.md)

`skills/` 保存分发源码，`scripts/` 提供安装、打包与验证工具，`tests/` 为程序回归。工程验证使用临时合成文本；实际执行与内容判断另按 [执行验收案例](benchmarks/skill-execution/README.md)核对。

```bash
python3 -B -X utf8 -m unittest discover -s tests
python3 -B -X utf8 scripts/smoke.py --output dist/manual-smoke.json
python3 -B -X utf8 scripts/package.py
```

## 许可与参考

当前源码及 v0.6.19 分发采用 [自定义非商业使用／商业授权协议](LICENSE)：非商业用途免费，商业用途须另行取得授权并按约定付费。协议中的授权主体和联系字段仍为占位内容，历史版本以实际随包许可为准。[许可证与分发说明](docs/licensing.md)

本项目参考 [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) 的公开流程独立实现。[设计取舍与对照](docs/upstream-analysis.md)
