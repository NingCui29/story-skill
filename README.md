# Story Skill

**把想法写成故事，让故事能够接着写、反复改、完整交付。**

Story Skill 是一套中文小说创作技能，供 Codex、Claude Code、Claude 桌面版及其他支持 Agent Skills 的助手使用。它把拆书、规划、正文、审稿、调研、封面和投稿材料准备连成可接续的流程，并提供本机工作台，方便阅读书稿、编辑候选和比较版本。

**当前发布：v0.6.18 · 八个技能 · Python 3.10+ · 支持长篇与短篇**

当前开发源码为 **0.6.19 候选**，新增作品回收站删除、备份恢复、恢复中心与保存全部等工作台功能；[操作说明与需求进展](docs/工作台需求与进展.md)列明已实现和待完善范围。下方正式 Release 下载仍为 v0.6.18。

本轮本机全量回归 1902 项，1882 通过、20 条件跳过；Windows 候选安装与卸载流程通过。[本轮验收记录](benchmarks/results/workbench-management-20261019/assessment.md)

[下载安装](#安装升级) · [开始写书](#开始写书) · [使用工作台](#使用工作台) · [完整文档](docs/README.md) · [发布记录](docs/releases/v0.6.18.md)

## 它能帮你做什么

| 你的目标 | Story Skill 的工作 |
|---|---|
| 把一个想法发展成新书 | 拟写书名与简介，建立人物、核心冲突、全书总纲、卷纲和近期细纲 |
| 学习一部参考作品 | 对照原文分析人物选择、冲突、信息与情绪安排，提炼方法，再设计原创故事 |
| 持续写作与续写 | 衔接正式断点、人物状态和采用的细纲，召回本书偏好，展开本章关键场面 |
| 修改已有书稿 | 找出平淡、因果、人物反应、文风和套语问题，生成候选并复核修改效果 |
| 完成一篇短故事 | 处理阅读期待、人物选择、后果和结局，交付经审查的全文及适用封面 |
| 规划剧本或拆分镜 | 根据小说规划剧本，或从小说、剧本拆出逐镜画面与声音安排，保留来源对照 |
| 整理与准备交付 | 在工作台阅读、编辑、对照书稿，为番茄、七猫准备已审查章节的离线材料 |

题材写法、人物行动、情绪兑现和文风选择按具体作品处理。长篇保留持续推进与长距伏笔，短篇关注有限篇幅中的完整体验；安静场景、合作关系和非反转结局同样适用。

## 安装升级

先按使用环境选包。正式发布的 ZIP 均有对应的 `.sha256` 校验文件，下载后按 [安装指引](INSTALL.md)核对；Windows 工作台候选包的获取方式和验证范围见下文。

| 使用环境 | 下载 | 安装说明 |
|---|---|---|
| Codex 本地插件 | [插件 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-codex-0.6.18.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-codex-0.6.18.zip.sha256) | [本地市场与插件安装](docs/codex-plugin.md) |
| Claude 桌面版 | [单入口 ZIP（r2）](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-claude-desktop-0.6.18-r2.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-claude-desktop-0.6.18-r2.zip.sha256) | [导入并启用](INSTALL.md#claude-桌面版导入单技能包) |
| Claude Code、Antigravity 等目录安装 | [完整八技能 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-0.6.18.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-0.6.18.zip.sha256) | [完整套件与宿主目录](INSTALL.md#目录安装的完整套件) |
| Intel Mac 本机工作台 | [客户端 0.1.6 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-workbench-0.1.6-macos-x86_64.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-workbench-0.1.6-macos-x86_64.zip.sha256) | [实验客户端说明](desktop/macos/README.md) |
| Windows x64 本机工作台（0.1.3 候选版） | [安装包 ZIP](https://github.com/NingCui29/story-skill/actions/runs/39230308569/artifacts/12176299211) · [便携包 ZIP](https://github.com/NingCui29/story-skill/actions/runs/39230308569/artifacts/12177990375)（需登录 GitHub） | [双端安装步骤](#独立应用下载与安装) |

Mac 客户端内嵌运行时 0.6.18，采用本地临时签名，未经 Apple 公证；完整创作技能另行安装。[GitHub Packages](https://github.com/users/NingCui29/packages/npm/package/story-skill) 也提供 `@ningcui29/story-skill@0.6.18` 内容包，npm 安装本身不向宿主注册技能。

### Codex 本地插件

使用支持插件管理的 Codex 客户端。核对插件 ZIP 的摘要后，解压并保留市场目录，再运行：

```bash
codex plugin marketplace add "<解压后含 .agents/plugins/marketplace.json 的市场根目录>"
codex plugin add story-skill@story-skill-local
codex plugin list --marketplace story-skill-local --json
```

确认 Story Skill 的版本为 `0.6.18`，且 `installed`、`enabled` 均为 `true`。重新打开 Codex 并开始新会话，以实际补全列表和技能发现结果核对加载。插件入口带命名空间，例如 `$story-skill:story-skill-plan`；也可直接说“使用 Story Skill”并描述任务。

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

当前开发候选为 **Mac 0.1.11／Windows 0.1.5**。明暗模式入口在**书架右上方“外观”**，或**作品编辑页顶栏“外观”**；可选择浅色、深色或跟随系统，选择会记住，切换保留未保存文字。

本轮界面使用天空蓝主题（主色 `#38BDF8`）：书架集中搜索与筛选，新建为主操作；作品卡片保留标题、简要进度与打开入口，完整更新时间和低频信息收进管理菜单；重复标题及总数已移除，刷新与使用说明归入“书架工具”。编辑页减少重复说明，保存候选更加醒目；切换外观保留输入、选区及阅读位置。参见[界面预览与验收记录](benchmarks/results/development-20261019-workbench-sky-theme/assessment.md)。

Mac 应用菜单与 Windows 工具栏提供“检查更新”。发现正式新版后，可在应用内下载，查看下载百分比、已下载大小、速度、预计剩余时间和 SHA256 校验状态；支持取消及失败重试。校验通过后，Mac 打开安装包供手动安装，Windows 可启动安装向导；便携包需手动解压替换。安装交给系统后会提示保存、安装和重新打开的步骤，不将“下载完成”当成“升级完成”。缺少校验信息、网络失败或没有对应安装包时会明确提示。检查和下载只访问公开发布文件，不上传书稿，不自动结束作品服务。详见[更新流程说明](docs/工作台更新流程.md)。

本轮作品管理与恢复功能使用内嵌运行时 **0.6.19**。Mac 当前公开 Release 仍是 0.1.6；0.1.11 开发候选可按 [Mac 构建说明](desktop/macos/README.md#从源码构建)生成。若取得 `.dmg` 候选包，先核对同名 `.sha256`，双击挂载后将“写作工作台.app”拖入 Applications，再按下方步骤打开。

### 独立应用下载与安装

两端应用均内置 Python 运行时，直接安装即可打开书架，无需另装 Python。完整创作技能需在所用助手中另行安装。

**Mac（当前验证范围为 Intel Mac）：**

1. 下载[客户端 0.1.6 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-workbench-0.1.6-macos-x86_64.zip)及[SHA256 校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-workbench-0.1.6-macos-x86_64.zip.sha256)。
2. 解压 ZIP，将“写作工作台.app”拖入“应用程序”（Applications）。
3. 从“应用程序”打开工作台，在书架添加已初始化的作品目录，再打开作品阅读或编辑。

客户端内嵌 Story Skill 0.6.18，采用本地临时签名、未经 Apple 公证；Apple Silicon 尚未验证。详见 [Mac 客户端说明](desktop/macos/README.md)。

**Windows（x64 候选版）：**

1. 登录 GitHub，下载[工作台 0.1.4 安装包 ZIP](https://github.com/NingCui29/story-skill/actions/runs/39230308569/artifacts/12176299211)。
2. 解压 ZIP，运行其中的 `story-workbench-0.1.4-windows-x86_64-setup.exe`，按向导安装；默认安装到当前用户目录，可选创建桌面快捷方式。
3. 从开始菜单打开“写作工作台”。点击“复制作品目录”选择目录，再将路径粘贴到书架添加栏。需要卸载时，在 Windows 应用列表中卸载。

便携使用方式：下载[便携包 ZIP](https://github.com/NingCui29/story-skill/actions/runs/39230308569/artifacts/12177990375)，先解压外层，再解压其中的 `story-workbench-windows-x86_64.zip`，保留完整目录后运行 `StoryWorkbench.exe`。

Windows 候选已在 Windows 构建机上通过安装、内置运行时、桌面窗口启动、作品新建与备份恢复、系统回收站、浅色／深色切换、更新入口与合成进度窗口、隔离服务关闭及卸载检查；尚未完成所有交互的人工验收和 Windows 10/11 用户环境验收，安装程序未签名，未作为正式 Release 附件发布。该 Actions 下载包预计于 **2027-01-17** 到期，到期或被删除后需重新构建获取新包。可查看[本次构建与验收](https://github.com/NingCui29/story-skill/actions/runs/39230308569)及[对应源码的 Windows 说明](https://github.com/NingCui29/story-skill/blob/a96dad938243ad08db715fdbd5b6a0251d4bc31d/desktop/windows/README.md)。

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

开发候选的书架提供“新建作品”；“书架工具”中提供“从备份恢复”“恢复中心”；作品卡“管理作品”可备份、重新定位、移出或删除至系统回收站。删除需输入完整书名并先停止本书编辑服务，目录和身份异常时中止，不提供永久删除回退。编辑页“设置”可保存全部候选、找回恢复稿，“更多”可选择两稿对照或生成下一章写作任务。详见[工作台需求与进展](docs/工作台需求与进展.md)。

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
- **投稿材料：** 番茄、七猫的离线准备使用已审查正式章节，分别核对书名、平台署名、简介、封面、分类、素材权利和 AI 使用。当前由作者在平台后台完成上传、审核提交和定时发布。

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
| `story-skill-publish` | 七猫、番茄离线投稿材料准备与复核 |

手动调用时，以宿主实际补全列表为准：Codex 插件使用 `$story-skill:story-skill-plan` 等命名空间入口；Codex 目录安装使用 `$story-skill-plan` 等入口，Claude Code 使用 `/story-skill-plan`。Claude 桌面包仅启用一个总入口，内部按需读取八个流程。

## 版本与验证

v0.6.18 完善了四个环节：

1. **写前交接与偏好召回。** 衔接本章计划、采用的细纲和本书偏好，保留适用范围、例外与来源。
2. **关键情节点覆盖与新增事实分级。** 检查关键事件是否真正呈现，区分自然补充、需要依据和改变既定约束的新增事实。
3. **精简执行路径。** 按任务条件读取相应规则，保留已有语义审查与提交要求。
4. **短篇平台化设计。** 结合实际平台与入口处理阅读期待、选择、后果及结局，按需设计付费停笔位置。

题材写法卡、文风统一裁决、兑现归属、事务提交、历史依赖、世界状态与采用细纲绑定继续保留。书库沿用 schema 2。创作方法与实际试用范围见 [本轮开发验证](benchmarks/results/development-20261018-execution-handoff/assessment.md)。

本版发布验收记录：本地 1743 项回归中 1725 通过、18 跳过，零失败、零错误；Linux 与两组 Windows CI、固定标签安装、Release 八附件匿名回下载及 npm 独立核验均通过。各项结果和首次失败保存在 [v0.6.18 发布记录](docs/releases/v0.6.18.md)。固定标签为 `v0.6.18`，版本分支为 `version-0.6.18`，完整套件包含八技能、55 个源载荷文件。

文件、CLI 和事务检查分别证明其验收范围；小说的阅读效果、实际成图、宿主 GUI 和平台审核仍按对应证据判断。本地通过不保证市场表现。[评估方法](docs/evaluation.md)

## 文档与开发

[中文小说上手](docs/中文小说上手.md) · [完整文档导航](docs/README.md) · [工作台](docs/本地工作台分析.md) · [超长篇实操](docs/超长篇实操.md) · [发布维护](docs/github-release.md)

`skills/` 保存分发源码，`scripts/` 提供安装、打包与验证工具，`tests/` 为程序回归。工程验证使用临时合成文本；实际执行与内容判断另按 [执行验收案例](benchmarks/skill-execution/README.md)核对。

```bash
python3 -B -X utf8 -m unittest discover -s tests
python3 -B -X utf8 scripts/smoke.py --output dist/manual-smoke.json
python3 -B -X utf8 scripts/package.py
```

## 许可与参考

当前源码及 v0.6.18 分发采用 [自定义非商业使用／商业授权协议](LICENSE)：非商业用途免费，商业用途须另行取得授权并按约定付费。协议中的授权主体和联系字段仍为占位内容，历史版本以实际随包许可为准。[许可证与分发说明](docs/licensing.md)

本项目参考 [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) 的公开流程独立实现。[设计取舍与对照](docs/upstream-analysis.md)
