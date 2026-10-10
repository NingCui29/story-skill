# Story Skill

让中文小说从一个想法，走到能持续修改、续写和交付的书稿。

Story Skill 是供支持 Agent Skills 的写作助手使用的中文小说技能，涵盖拆书、开书、书名与简介优化、大纲、正文、审稿、调研、封面和投稿材料准备。支持 Codex、Claude Code、Antigravity、Qoder（含 CN）、ZCode、WorkBuddy AI，以及下文列出的其他宿主。源码按八个技能分工；Claude 桌面导入包通过一个总入口按需读取这些流程。每本书独立保存，三栏本机工作台让你直接阅读、比较版本、修改并保存候选稿。

**本版：v0.6.16 · Python 3.10+ · 支持长篇与短篇**

v0.6.16 已发布，包含八个技能、51 个载荷文件，更新规划候选渲染、执行审查与用量评估、宿主安装适配、写作连续性规则及随包许可证。本版验证与分发状态见[发布记录](docs/releases/v0.6.16.md)。安装或升级按 [INSTALL.md](INSTALL.md) 核对；仓库现为私有，Release 下载需要相应访问权限。

各宿主复用同源技能规则和共享运行时；Claude 桌面包将内嵌流程映射为参考文件。本轮新增只读规划候选渲染，书库沿用 schema 2，不把规划或机械检查通过当作文学效果已实现。已发布历史标签与附件保持原有内容。

macOS 客户端 `0.1.4`（build 5，内含 Story Skill `0.6.16`）已作为独立 Intel 实验附件分发，采用本地临时签名，未经 Apple 公证。验证与分发状态见[客户端说明](desktop/macos/README.md)，GitHub 发布不会自动更新本机安装。

[安装升级](INSTALL.md) · [开始写书](#开始写书) · [使用工作台](#使用工作台) · [完整文档](docs/README.md) · [版本记录](docs/releases/v0.6.16.md) · [Releases](https://github.com/NingCui29/story-skill/releases) · [Packages](https://github.com/users/NingCui29/packages/npm/package/story-skill)

## 它能帮你做什么

| 你想做的事 | 得到什么 |
|---|---|
| 从想法开书 | 书名、简介、人物动机、核心冲突、全书总纲、分卷规划与近期细纲 |
| 拟写或优化简介 | 有本书依据、体现具体阅读理由的简介；可从策划生成，也可优化已有文案 |
| 从参考作品学习 | 有原文依据的分析、可迁移方法及独立的新书设计 |
| 连载或写完短篇 | 沿前文和计划推进的章节、明确断点及实际字数 |
| 改好已有章节 | 问题分析、修改候选、版本对照与后续依赖复核 |
| 在浏览器整理书稿 | 章节与材料导航、阅读、候选编辑、搜索与差异定位 |
| 准备投稿 | 已审查正式章节的离线材料包；书名、简介、封面、分类、素材权利与 AI 使用逐项交接核对 |

技能负责约束助手的工作方式，Python 工具负责会话可访问的文件与状态；本机工作台本身不调用模型。联网调研与浏览器操作取决于当前会话可用的工具；Claude 制作封面或附言配图需要用户已有的图像工具或 MCP，安装技能不会让模型自动获得生图能力。

## 安装升级

### Claude 桌面版

下载 [story-skill-claude-desktop-0.6.16-r2.zip](https://github.com/NingCui29/story-skill/releases/download/v0.6.16/story-skill-claude-desktop-0.6.16-r2.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.16/story-skill-claude-desktop-0.6.16-r2.zip.sha256)。包内53个文件、递归唯一 `SKILL.md`，八个内嵌流程作为 `GUIDE.md` 参考文件加载；本版引用与隔离CLI核验、实际客户端导入范围见[发布记录](docs/releases/v0.6.16.md)。

历史 v0.6.12 原桌面 ZIP 含9个 `SKILL.md`，无法通过导入器检查，已由该版 r2 替代；本版沿用修正包装。也可从 v0.6.16 固定源码构建：

```bash
python3 -B -X utf8 scripts/package_claude.py
```

输出为 `dist/story-skill-claude-desktop-0.6.16-r2.zip`；自行构建不等于发布或导入验收通过。导入已核对的包时，在 Claude 的 **Settings > Capabilities** 开启 **Code execution and file creation**，再到 **Customize > Skills**，点击 **+ → Create skill → Upload a skill**，上传并启用 Story Skill。组织账号按管理员开放的能力操作。[官方导入说明](https://support.claude.com/en/articles/12512180-use-skills-in-claude)

该包只有一个顶层 `story-skill/`，内含入口和完整八流程资源。同版 `story-skill-0.6.16.zip` 是八目录分发包，桌面导入请使用专用包。导入后可直接说：“使用 Story Skill，帮我规划一本中文长篇，本轮不写正文。”书稿需放在当前会话能读取和保存的位置；普通聊天中的文件与代码环境不等于作者电脑，完整书目录应下载备份。Cowork 或新的统一任务界面只按已开放的文件权限使用，详细边界见 [安装指引](INSTALL.md#claude-桌面版导入单技能包)。

### Codex 与 Claude Code

在 Codex 中，可向支持技能安装的助手发送：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

按照 [安装指引](INSTALL.md) 核对固定标签、下载摘要、备份与八个技能目录。安装完成后，发送 `$story-skill` 加上你的需求即可开始。

Claude Code 使用当前仓库源码时，在仓库根目录运行以下命令，将八个技能安装到写作项目的 `.claude/skills/`：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --project "<写作项目根目录>"
```

要在本机各项目中使用，可改用 `--host claude-code --user`，安装到 `~/.claude/skills/`；两种范围任选一个。已有托管安装升级时加 `--update`，安装器保留备份并拒绝覆盖外部修改。完整参数和核验步骤见 [安装指引](INSTALL.md#从本仓库源码安装)。安装后在 Claude Code 中发送 `/story-skill` 加上需求，也可直接描述写作任务。[Claude Code 官方技能说明](https://code.claude.com/docs/en/skills)

### Google Antigravity

在当前仓库根目录运行项目安装：

```bash
python3 -B -X utf8 scripts/install.py --host antigravity --project "<写作项目根目录>"
```

Antigravity 2.0／IDE 与 CLI 的项目技能都使用 `.agents/skills/`；个人目录按实际应用区分：

| 使用的应用 | `--host` | 改用 `--user` 时的个人目录 |
|---|---|---|
| Antigravity 2.0／IDE | `antigravity` | `~/.gemini/config/skills/` |
| Antigravity CLI | `antigravity-cli` | `~/.gemini/antigravity-cli/skills/` |

安装后直接描述任务；Antigravity 2.0 与 CLI 也可用 `/story-skill` 加需求。同一项目的 Codex 与 Antigravity 共享已核验的 `.agents/skills/` 套件，无需重复安装；使用 `--update` 会更新这份共同副本。旧 `.agent/skills/` 与 IDE 旧个人目录仍由宿主兼容识别，安装器不会自动迁移或覆盖它们；升级前核对是否存在旧副本。[Antigravity 官方技能说明](https://antigravity.google/docs/skills)

### Qoder、Qoder CN、ZCode 与 WorkBuddy AI

本版源码安装器新增四个目录安装目标。在仓库根目录按使用的应用任选一条，安装完整八技能到个人目录：

```bash
python3 -B -X utf8 scripts/install.py --host qoder --user
python3 -B -X utf8 scripts/install.py --host qoder-cn --user
python3 -B -X utf8 scripts/install.py --host zcode --user
python3 -B -X utf8 scripts/install.py --host workbuddy --user
```

个人目录分别为 `~/.qoder/skills/`、`~/.qoder-cn/skills/`、`~/.zcode/skills/`、`~/.workbuddy/skills/`。只用于一个写作项目时，改为 `--project "<写作项目根目录>"`；Qoder 与 CN 项目共用 `.qoder/skills/`。Qoder CN 此处指当前桌面版／CLI，CN IDE 按实际版本核对目录。上述入口由本版源码安装器提供，标准技能ZIP不含安装器；详细目录依据、刷新和核验步骤见 [安装指引](INSTALL.md#qoderqoder-cnzcode-与-workbuddy-ai)。

安装并刷新后，可直接说“使用 Story Skill，帮我规划一本中文短篇，本轮不写正文”。ZCode 也可用 `$story-skill`；Qoder／Qoder CN CLI 可用 `/story-skill`。WorkBuddy AI 在技能管理中确认八个技能已安装并启用。

### TRAE、Cursor、CodeBuddy 等宿主

本版源码安装器还补齐以下目标，范围任选 `--user` 或 `--project "<写作项目根目录>"`，复制完整八技能到宿主的默认目录：

| 应用 | `--host` |
|---|---|
| TRAE 国际版／国内版 IDE | `trae`／`trae-cn` |
| TraeCode CLI | `trae-cli` |
| Cursor | `cursor` |
| CodeBuddy | `codebuddy` |
| OpenCode | `opencode` |
| Copilot VS Code Agent／CLI | `copilot` |
| Gemini CLI | `gemini-cli` |
| Cline | `cline` |
| Windsurf 旧目录／Devin Desktop Cascade | `windsurf`／`devin` |

例如 `python3 -B -X utf8 scripts/install.py --host cursor --user`。升级未修改的托管副本加 `--update`；只读核验加 `--check`，比较安装文件与当前源码，不创建或修复安装。新增目标及核验参数由本版源码安装器提供；完整目录、官方依据和刷新调用方法见[其他宿主安装说明](INSTALL.md#其他-agent-skills-宿主)与[核验说明](INSTALL.md#安装核验)。TRAE 国内／国际版、Gemini CLI／Antigravity、CodeBuddy／WorkBuddy AI 的个人目录按各自应用区分。ZCode 已通过本机客户端解析器的八入口发现与完整读取测试；其他前端的实际加载及所有宿主的模型调用仍需分别核对。

克隆仓库、下载 ZIP 或 npm 包都不等于宿主已加载技能。GitHub 更新也不会自动升级本机安装；升级只更换技能文件，不自动改写小说。安装与发布是否完成，以对应版本的核验记录为准。

## 开始写书

把 `<…>` 替换为当前会话可访问的实际路径和要求。不确定该用哪个技能时，用总入口描述目标。下列示例采用 Codex／ZCode 的 `$story-skill…` 写法；Claude Code、Antigravity 2.0／CLI、Qoder CLI、Cursor、Copilot VS Code Agent、Cline 可用 `/story-skill…`；Windsurf／Devin Desktop 用 `@story-skill…`。其他界面可改为“使用 Story Skill”，再说明本轮是规划、写作还是审稿，其余需求照用。实际技能启用与调用方式按[宿主说明](INSTALL.md#其他-agent-skills-宿主)核对。

### 开一本新书

```text
$story-skill-plan 开一本番茄女频长篇。
写作父目录：<绝对路径>
题材与核心想法：<你的想法>
预计总篇幅：<目标或待讨论>
单章字数：2400—2800 字（正文可见字符，含标点，不含章名；有明确例外时注明）

先确定书名，明确标出主角及关键人物的姓名或称呼、角色定位、身份关系和主线作用，再确定人物动机、核心冲突、开篇看点和长期推进方向。
另建书名目录，保存创作约定、开书策划、全书总纲、分卷规划和前三章细纲。
记录本书长短篇定位，默认分别给齐番茄、七猫的分类，其他已涉及平台也列全；各按实际内容及拟选创建入口填写。本轮只做规划，不写正文。
```

写短篇时，将类型改为短篇，补充预计篇幅与结局方向。新写完整章节按 2400—2800 字规划；用户或书根 `创作约定.md` 明确要求其他篇幅或统计口径时，在章计划的 `length_exception` 中记录原话或可核对的书内引文。保存新章计划、检查及提交时均要求这项记录；书内引文还须与当前文件吻合。旧计划不会自动改写，续写前需核对并同步本次未写章节。章中续写、局部修改不按整章字数扩写，旧正式章节也不追溯修改。

### 从拆书开始

```text
$story-skill 分析 <参考作品文件绝对路径>，再规划一本原创新书。
写作父目录：<绝对路径>
新书方向：<题材、读者、篇幅>

先核对原文范围，分析人物选择、冲突推进、信息安排和情绪兑现。
分析结论要有原文依据，区分可迁移的方法、成立条件与风险。
参考分析保存到“作品分析/<参考书名>”，新书另建书名目录。
重新设计人物、关系、事件与解决方式，不做换名改写。
完成策划、总纲和前三章细纲后停下。
```

只点评开篇可以粘贴原文；完整深读和跨会话续跑请提供可读取文件。只取得部分原文，就只对该范围作结论。[拆书与深读说明](docs/作品深读与评估.md)

### 拟写或优化简介

```text
$story-skill-plan 优化 <书目录绝对路径> 的小说简介，重点吸睛。
目标读者与用途：<沿用本书约定或补充要求>
保留本书事实和语气，把最有辨识度的看点前置，让读者想点开正文，按本书要求控制剧透。
交一份可直接使用的推荐稿；需要保存时先另存候选。
本轮只优化简介。
```

也可直接粘贴现有简介，或指定本书策划生成简介；只提供简介时按该文本优化，并说明涉及的未核实承诺。只要检查时写“只审不改”，有字数、风格或剧透要求时一并注明。[简介指导](skills/story-skill-plan/references/blurb.md)

### 写下一章

```text
$story-skill-write 继续 <书目录绝对路径>。
读取当前进度、前文衔接和本章计划，按创作约定完成下一章。
人物行动优先，把尝试、阻力、回应和后果写成场景。
实际统计字数，完成审查后保存正式章节。
```

先试写时加上“只保存候选，暂不采用”。章中续写时说明“接着当前章往下写”，不重复前文或自动另起新章。

### 审稿与修改

```text
$story-skill-review 审查 <章节或候选稿绝对路径>。
重点检查开篇吸引力、人物选择、情绪变化、因果与段落节奏。
保留核心剧情，另存修改候选，暂不替换正式稿。
```

只分析时说“只审不改”；只排版时说“仅调整段落，不改字词和标点”。采用候选需明确提出，再核对正式版本、来源与后续章节影响。

## 使用工作台

本节用于作者电脑上的 Python 运行时。桌面版普通聊天的代码执行环境、Cowork 的任务环境与作者电脑需分别核对；上传技能不会自动启动电脑上的服务，任务环境中的 `127.0.0.1` 也不能直接当作作者本机工作台。

当前版本支持独立固定书架，不需要先指定一本书：

```bash
python3 -B -X utf8 "<核心技能目录>/scripts/story.py" workbench-library-serve --open
```

服务启动成功后访问 `http://127.0.0.1:8765/`，可收藏该入口。在页面添加已初始化的书目录，再选择作品打开；书架会记住已添加的作品，各书编辑页在新标签打开，原页面的编辑保留。没有书也能先打开空书架。固定地址需要服务保持运行；停止书架不会停止已打开的各书编辑服务。已发布 v0.6.10 不含这个新入口，先核对实际安装命令。

`--port` 可指定其他固定端口，端口占用时会明确报错，不自动改地址。`workbench-library-status` 检查书架服务，`workbench-library-stop --saved` 停止；它们都不需要 `--book`。

书架登记默认保存在 `~/.codex/story-workbench/`，同一台机器上的各宿主使用相同的默认运行时数据位置。需要指定其他位置时，为书架启动、状态与停止命令使用同一个 `--state-dir`；不会自动迁移已有登记。

向助手发送：

```text
$story-skill 打开 <书目录绝对路径> 的可编辑工作台。
```

### 三栏各做一件事

| 区域 | 操作 |
|---|---|
| 左栏：找章节 | 每章一个入口；切换文件视图浏览策划、大纲、细纲、草稿和图片 |
| 中栏：读与改 | 在“本章稿件与材料”中切换正式稿、候选和计划，阅读、编辑或对照差异 |
| 右栏：看上下文 | 查看字数目标、实际字数、停笔点；按需展开衔接、约束与审查记录 |

拖动两侧分隔线调整宽度，也可收起任一侧栏或恢复默认布局。窄窗口用可展开侧栏，让正文保留可读宽度。

### 修改一章的顺序

1. 在左栏选章节，在中栏确认当前打开的稿件。
2. 进入编辑，修改后点击保存，或按 `⌘S / Ctrl+S` **另存新候选**。
3. 打开对照查看增删，跳转到上一处或下一处差异。
4. 复制审稿任务交给助手。决定采用时明确提出，由审稿流程处理。

**保存候选不会覆盖正式正文，也不表示已经采用或审查通过。** 同章号关联只是导航线索，不能证明两个文件属于同一采用链。

### 阅读、查找与恢复

- 字号、行距、段距和阅读宽度可调；显示排版不改原稿。查看或编辑正文时，章名与内容区分开呈现。
- 全文搜索支持结果定位；当前文件可逐处跳转，编辑时可查找尚未保存的内容。
- 切换文件保留本页会话中的阅读位置与编辑选区；目录刷新和重新读取当前文件是不同操作。
- 自动恢复辅助找回中断编辑，仍应主动保存或下载。外部文件变化、待核对保存等情况会明确提示。
- 多书切换和书外材料需明确登记；`--material-root` 关联的外部材料只读，不自动扫描整个父目录。

侧栏宽度等设置保存在浏览器当前地址下，重启服务换端口后不保证继承。全文搜索有覆盖上限，页面会提示范围。[完整操作与限制](docs/本地工作台分析.md)

手动启动服务：

```bash
python3 -B -X utf8 "<核心技能目录>/scripts/story.py" workbench-status --book "<书目录绝对路径>"
python3 -B -X utf8 "<核心技能目录>/scripts/story.py" workbench-serve --book "<书目录绝对路径>" --open
```

已有服务且代码未过期时复用其地址。升级前先保存各页面编辑，再停止旧服务并重新启动。服务只监听本机回环地址；`workbench-export` 导出的是只读静态快照，不支持编辑，也不会自动更新。

这些命令由当前会话所在机器运行，需要 Python 和书目录访问权限。Claude Code 在本机运行时可使用本机工作台；云端会话的 `127.0.0.1` 指向云端运行环境，不能当作作者电脑上的入口。个人技能目录也不会自动同步到云端，云端技能加载范围按 [Claude Code 官方说明](https://code.claude.com/docs/en/skills#use-skills-in-cowork-and-cloud-sessions) 核对。

## 写作与文件约定

以人物欲望、选择和行动推动故事，关注情绪兑现与追读，同时保护因果和人物逻辑。不用办事流程、记录清单或作者解释代替场景。

正文按语义分段，段间空一行，不手工缩进。换人发言默认换段，同一人物紧密相关的动作和短对白可以同段；不按屏幕行数强拆。正文不混入 Markdown 标记、评分、分析或创作过程说明。长篇每个完整章节正文完成后，自动交付 [作者有话说和一张配图](skills/story-skill-write/references/drama.md#作者有话说)，长篇完整章候选和续写补完当前章同样执行，无需再次要求。短故事／短篇默认不写、不保存、不展示附言及其配图，分章、改稿和完本交付均不要求；用户明确要求时按指定范围处理，已有文件保留，完本封面仍按原流程交付。适用的附言简短回应本章，不剧透、不计入正文字数；落盘交付时另存无标题的 `.md`，引用旁边 `配图/` 子目录中的真实图片，纯对话交稿无需另建文件；正文、附言和图片一起交付，并在正文或保存回执后展示附言及配图。用户明确免除的项目按其要求处理；图片工具不可用或生成失败时说明适用范围内的缺图，不用提示词冒充成图。新写、续写、润色、排版和审校分别处理，硬性字数要求实际统计。

总纲与分类答复先统一列出主角及关键人物的角色定位，再默认分别给齐番茄、七猫，其他本次要求或书内约定涉及的平台也列全；主投选择不缩减覆盖，只有明确限定本轮单平台时才收窄。各平台按自己的入口和原生栏目填写，标签依据真实内容与已核对选项选择。作品阶段按实际后台记录；分类逐栏区分拟选、后台已确认和待核对，经确认可选而留空的栏目标“不选”。当前 [七猫作品类型与标签表](skills/story-skill-plan/references/qimao-tags.md) 已在七猫中文网新建小说、新建短故事页核对男、女频分类及四栏标签；本次实测的普通分类分支弹窗逐栏要求 1～3 个，不推定覆盖所有分支。现实题材分支另有“仅选1个”的页面提示，但未保存表单的交互与该提示并不一致，最终创建限制尚未确认，规划时标为待核对。短篇完本还需以书名命名的全文合并文件、全文审查与封面；封面生成依赖可用的图像工具。

一书一目录，小说存放在技能源码和安装目录之外。以下仅为目录示意，规划阶段不提前生成空正文：

```text
小说/
├── 作品分析/参考书名/
└── 新书书名/
    ├── 创作约定.md
    ├── 00_项目策划/开书策划.md
    ├── 01_大纲细纲/
    │   ├── 全书总纲.md
    │   └── 第一卷 卷名/
    │       ├── 本卷大纲.md
    │       └── 第1章 章节名称.md
    ├── chapters/第一卷 卷名/第1章 章节名称.md
    └── .story/drafts/
```

卷目录包含卷名，章节文件为 `第N章 章节名称.md`，章号不补零。细纲与正文分目录；已有工程沿用已记录路径，不自动批量搬迁。备份请复制整个书目录，包括 `.story/`，只备份正文不能恢复完整进度。

[目录规范](docs/目录结构.md) · [章节流程](skills/story-skill-write/references/chapter.md) · [语言与格式](skills/story-skill-write/references/drama.md) · [商业连载任务模板](docs/中文小说上手.md)

## 八个技能入口

Codex 用 `$技能名`，Claude Code、Antigravity 2.0／CLI 用 `/技能名`，Antigravity IDE 可直接描述任务。目录安装时八个目录需同级完整。Claude 桌面包只启用一个 Story Skill 总入口，下表各流程作为内嵌资源按需读取，不要求八个独立命令。`agents/openai.yaml` 是 Codex 界面配置，其他宿主无需使用。

| 技能名 | 用途 |
|---|---|
| `story-skill` | 总入口、跨流程任务与工作台 |
| `story-skill-plan` | 开书、设定、大纲细纲、接入已有作品 |
| `story-skill-write` | 正文创作、续写、连载与短篇完本 |
| `story-skill-review` | 审稿、修改、排版与历史章节修订 |
| `story-skill-analyze` | 开篇点评、完整深读与分析续跑 |
| `story-skill-research` | 榜单、题材市场与素材查证 |
| `story-skill-cover` | 封面生成与修改 |
| `story-skill-publish` | 七猫、番茄离线投稿材料准备与复核 |

投稿准备使用已审查的正式章节；目标平台作品和账号标识已知时，可导出绑定该目标的材料包，并比较作者提供的草稿副本。未在平台建书或缺作品 ID 时，先核对现有稿件与待办，不填虚构 ID，也不宣称 ZIP 已可用。作者名／笔名按本书各平台分别记录，封面及交接材料使用对应署名；未明确同名时不跨平台沿用。交接按书名、平台署名、简介、封面、分类、素材权利与 AI 使用逐项列明已核和待核；这不自动代替全文内容审稿。**当前不执行平台上传、审核提交或定时发布**，最后由作者在平台后台操作。本地检查不等于平台审核通过，也不保证作品的市场表现。[投稿流程](docs/platform-publishing.md)

## 文档与开发

[中文小说上手](docs/中文小说上手.md) · [工作台](docs/本地工作台分析.md) · [恢复指南](docs/recovery.md) · [工程全貌](docs/工程全貌.md) · [评估方法](docs/evaluation.md) · [发布维护](docs/github-release.md)

`skills/` 为分发源码，`scripts/` 为安装、打包和验证工具，`tests/` 为程序回归。当前仓库不包含作者测试篇及旧示例小说；历史提交和既有标签可能保留旧副本。事务验收使用临时合成文本，不作为文学质量证据。技能的范围遵守与实际交付另用 [执行验收案例包](benchmarks/skill-execution/README.md)，分别核对文件变化、原始执行记录和内容判断。

总纲、卷纲、章细纲的最低内容、可选可读示例及人工质量验收集中见 [大纲细纲规范](skills/story-skill-plan/references/outline.md)。规划、写作和审稿按本次范围共用；目录、采用状态与版本绑定继续按项目状态规则处理。v0.6.16 为八技能、51 个载荷文件，更新规划候选、执行审查与评估、宿主安装及许可；安装时核对固定标签，不能仅凭版本号把后续开发源码视为发布包。

本版新增 [工具章计划转可读候选](skills/story-skill/references/project-state.md#从工具章计划生成可读候选)，可检查候选的生成字段是否仍对应保存计划；生成不自动采用或绑定。开发验收另增加 [审稿分派与版本关联](benchmarks/skill-execution/README.md#审稿分派与版本关联) 和 [原始日志用量汇总](benchmarks/skill-execution/README.md#已报告用量汇总)，见 [本轮验证记录](benchmarks/results/development-2026-10-10-plan-review-metrics/assessment.md)。这些过程检查不证明文学质量提升，本版实际发布与安装状态以发布记录和安装回执为准。

创作指导还包括 [生活观察与核心追问](skills/story-skill-write/references/drama.md#生活观察与核心追问)、[作品的复杂性](skills/story-skill-write/references/drama.md#作品的复杂性)、[作品的观看方式](skills/story-skill-write/references/drama.md#作品的观看方式) 和 [在重写中发现故事](skills/story-skill-write/references/drama.md#在重写中发现故事)。这些方法按作品与本次任务取用，帮助从具体经验、人物和文本发现作出创作选择；不要求逐章填写主题或统一采用某种文学风格。

```bash
python3 -B -X utf8 -m unittest discover -s tests
python3 -B -X utf8 scripts/smoke.py --output dist/manual-smoke.json
python3 -B -X utf8 scripts/chinese_acceptance.py --output dist/manual-chinese.json
python3 -B -X utf8 scripts/package.py
```

当前源码的根许可证与八技能许可证已统一为自定义非商业使用／商业授权条款：非商业用途免费，商业用途须另行取得授权并按约定付费。`Commercial Licensor` 和 `Commercial Licensing Contact` 仍为占位文字。本版构建沿用完整条款，npm 按输入归档的实际许可证标注；已发布版本保留原随包许可与历史权利。具体见[许可证与分发](docs/licensing.md)。

本项目参考 [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) 的公开流程独立实现。[设计取舍](docs/upstream-analysis.md) · [仓库根许可证](LICENSE)
