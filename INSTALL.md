# 安装或升级 Story Skill

**当前正式版：v0.6.22。** 下列附件已发布。八技能源清单为 69 个文件，目录安装的运行时需要 Python 3.10 或更新版本；桌面应用内置 Python。版本变化、固定标签安装、`version-0.6.22` 分支与完整分发记录见[发布记录](docs/releases/v0.6.22.md)；上一版 [v0.6.21](docs/releases/v0.6.21.md) 保持原样。

本版新增[结构化扫榜与抽样](skills/story-skill-research/references/rank-scan.md)及[研究交接](skills/story-skill-research/references/research-handoff.md)，从有来源的市场观察继续拆文验证和原创规划。采集与研究材料保持候选身份，不自动建立书库或写入正式正文；新软件发行不扩大平台草稿的真实后台验收范围。

[v0.6.12 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.12)保留当版49文件套件及修正历史。Claude 桌面导入包、Claude Code 与 Google Antigravity 目录安装适配从 v0.6.12 开始提供；本轮沿用这些入口与 Codex 的默认安装行为。

## Codex 本地插件

下载本版插件 [story-skill-codex-0.6.22.zip](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-codex-0.6.22.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-codex-0.6.22.zip.sha256)。归档包含本地市场目录和 72 个插件文件，共 74 个成员；不是公共插件目录上架。也可先在仓库根目录运行 `python3 -B -X utf8 scripts/package_codex.py` 构建，再按 [Codex 本地插件说明](docs/codex-plugin.md)执行隔离验证和本地市场安装。包内保留八个同级技能及完整运行时，提供 `.codex-plugin/plugin.json`、图标、许可证和市场目录。此方式不使用下文的目录安装器；已有同名技能时先核对并备份旧副本，避免重复加载。

## Claude 桌面版：导入单技能包

**桌面包使用带 `-r2` 的文件。** v0.6.22 发布文件为 `story-skill-claude-desktop-0.6.22-r2.zip`，整个 ZIP 只保留一个 `story-skill/SKILL.md`；内嵌流程使用普通 `GUIDE.md`，并转换相关引用。v0.6.12 原桌面包在子目录中还含8个 `SKILL.md`，曾被导入器以“Currently there are 9”拒绝，已由该版 r2 替代；原附件与更正记录保留。标准八技能 ZIP 用于目录安装，不用于桌面单技能上传。

下载本版 [Claude 桌面专用包](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-claude-desktop-0.6.22-r2.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-claude-desktop-0.6.22-r2.zip.sha256)。也可在 v0.6.22 源码根目录构建：

```bash
python3 -B -X utf8 scripts/package_claude.py
```

v0.6.22 源码默认输出 `dist/story-skill-claude-desktop-0.6.22-r2.zip`；本地开发源码按自身版本命名，可用 `--output "<输出ZIP路径>"` 指定位置。自行构建时，以实际源码快照为准；v0.6.22 包体、摘要及导入验证状态见[发布记录](docs/releases/v0.6.22.md)。

1. 在 Claude 的 **Settings > Capabilities** 开启 **Code execution and file creation**；组织账号需管理员开放相应能力。
2. 打开 **Customize > Skills**，点击 **+ → Create skill → Upload a skill**，上传桌面专用 ZIP。
3. 启用 Story Skill，用自然语言提出任务，例如：“使用 Story Skill 为我规划一本中文短篇，本轮只做策划，不写正文。”

操作依据见 [Claude 官方技能使用说明](https://support.claude.com/en/articles/12512180-use-skills-in-claude)。导入后仍需在实际会话中确认技能已加载、所需文件与代码执行工具可用；本地打包检查不等于桌面导入或模型调用验证。

桌面包采用官方要求的单技能根目录格式，并把八个流程完整保留为内部资源：

```text
story-skill-claude-desktop-0.6.22-r2.zip
└── story-skill/
    ├── SKILL.md
    ├── LICENSE
    └── suite/
        ├── story-skill/
        │   ├── GUIDE.md
        │   └── scripts/
        ├── story-skill-plan/
        │   └── GUIDE.md
        └── …其余六个同级流程目录（入口均为 GUIDE.md）
```

顶层入口按需读取 `suite/story-skill/GUIDE.md`；`suite/` 中保留八个同级目录及69个源载荷文件，8个入口改名并转换内嵌 Markdown 的相关引用，非 Markdown 资源与源文件逐字节一致。各相对引用仍以所在文件为基准。加上顶层入口和许可证，v0.6.22 桌面包为71个文件、递归仅1个 `SKILL.md`；本版引用、隔离CLI及回下载结果见发布记录。桌面只启用总入口，不把内嵌流程当成八个独立安装项。[官方打包格式](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)

本版 `story-skill-0.6.22.zip` 用于八目录分发，桌面单技能上传应选择专用的 `story-skill-claude-desktop-0.6.22-r2.zip`。桌面升级通过下载或构建并导入已核验的新版包处理；下文的 `--update` 是目录安装器参数，不执行桌面上传。

### 书稿保存与桌面能力

普通聊天使用的文件与代码环境不能当作作者电脑。继续已有作品时，提供包含 `.story/` 的完整书目录包，让助手先核对当前会话实际可读路径；完成后下载完整书目录备份，保留正文、材料与状态。不能把会话内保存回执当作文件已写入电脑。

Cowork 或统一任务界面只操作会话获准访问的目录。任务可能在云端运行；本地文件通过打开的桌面应用访问已连接文件夹，实际位置与权限以当前会话为准，不因“桌面版”就认定任务在本机运行。[官方桌面与云端说明](https://support.claude.com/en/articles/15520349-use-claude-cowork-on-web-desktop-and-mobile)

写作状态工具需要会话可执行 Python 3.10+ 并能读写书目录；技能导入不自动提供电脑上的 Python 服务。电脑上的工作台需另在本机启动，聊天或云端任务的 `127.0.0.1` 不等于本机工作台地址。联网、浏览器与图像工具按实际会话能力核对，封面与配图仍需已有图像工具或 MCP。

## Codex：发布版安装请求

在 Codex 中，可把这一行发送给支持技能安装的助手：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

这是一条自然语言安装请求。本页不是技能目录，不能把本页地址直接当作安装脚本的 `--url`。安装前核对固定标签、ZIP 校验文件及[发布记录](docs/github-release.md)；信息缺失或不一致时保留现有安装。

Claude Code 与 Antigravity 的目录安装方式见[下文](#从本仓库源码安装)。新增宿主及个人安装参数从 v0.6.12 开始提供；旧版安装器不支持这些参数。

## 目录安装的完整套件

v0.6.22 完整套件一次安装以下八个同级目录，共69个载荷文件：

```text
skills/story-skill
skills/story-skill-plan
skills/story-skill-write
skills/story-skill-analyze
skills/story-skill-review
skills/story-skill-research
skills/story-skill-cover
skills/story-skill-publish
```

本版套件 ZIP 为 [story-skill-0.6.22.zip](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-0.6.22.zip)，另附[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-0.6.22.zip.sha256)；npm 包版本为 `@ningcui29/story-skill@0.6.22`。npm 包本身不是可发现的技能安装。历史标签、附件与旧安装说明仍保留，不通过重命名推导旧版资源地址。

八个目录中的 `SKILL.md` 为标准技能入口，专用技能通过相对路径引用 `story-skill` 的共享 Python 运行时及其他技能的参考文件，必须保持同级完整安装。`agents/openai.yaml` 是 Codex 界面配置，随套件保留；其他宿主无需使用它。Claude Code、Antigravity 2.0／CLI 可通过 `/技能名` 调用，也可像 Antigravity IDE 一样用匹配描述的自然语言触发，各目录按下文的宿主选择安装。

远程安装时，核对 `v0.6.22` 固定标签与[发布记录](docs/releases/v0.6.22.md)所列提交，先将八个 `--path` 安装到隔离临时目录；另从本版 Release 下载上述 ZIP 和校验文件，核对摘要，并逐文件比较临时目录与 ZIP。仓库为私有时，安装与下载使用已有权限，不把未认证访问失败当作附件缺失。

各版套件 ZIP 的大小和 SHA-256 以对应[发布记录](docs/releases/v0.6.22.md)和附件校验文件为准。ZIP 成员只能是上述八个目录内的普通文件，拒绝绝对路径、越界路径和链接。固定标签、临时安装或附件任一环节不一致，停止升级。

## 从本仓库源码安装

安装发布版时使用对应固定标签。v0.6.22 包含八技能、69 个源载荷文件，本轮工作台、平台草稿及各分发形式的范围见发布记录；历史 v0.6.16 为51文件，v0.6.13 开发清单为50文件，v0.6.12 套件为49文件。明确安装后续本地开发源码时，先保存实际提交、工作区差异和全部载荷文件哈希，在隔离项目中核对；不能仅凭版本号判定其与发布附件相同。在对应仓库根目录运行默认 Codex 项目内托管安装，目标为 `<项目根目录>/.agents/skills/`：

```bash
python3 -B -X utf8 scripts/install.py --project "<项目根目录>"
```

安装器自 v0.6.12 支持 Claude Code 宿主选择。项目安装与个人安装二选一，保持八个技能同级；两个命令不需要同时运行：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --project "<写作项目根目录>"
```

该命令安装到 `<写作项目根目录>/.claude/skills/`，在该项目的 Claude Code 会话中使用。要在本机各项目中使用，选择个人安装：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --user
```

个人安装目标为 `~/.claude/skills/`。`--project` 与 `--user` 互斥；省略 `--host` 仍沿用原 Codex 项目安装行为。新增参数需使用 v0.6.12 或更新且已核验的安装器。

Antigravity 按实际使用的应用选择宿主，两种应用的项目目录相同，个人目录不同：

| 应用 | `--host` | `--project` 的安装位置 | `--user` 的安装位置 |
|---|---|---|---|
| Antigravity 2.0／IDE | `antigravity` | `<项目根目录>/.agents/skills/` | `~/.gemini/config/skills/` |
| Antigravity CLI | `antigravity-cli` | `<项目根目录>/.agents/skills/` | `~/.gemini/antigravity-cli/skills/` |

例如为 Antigravity 2.0／IDE 安装到本机个人目录：

```bash
python3 -B -X utf8 scripts/install.py --host antigravity --user
```

只在一个项目使用时，把 `--user` 换成 `--project "<写作项目根目录>"`；CLI 把宿主值换成 `antigravity-cli`。同一项目的 Codex 与 Antigravity 共享已核验的 `.agents/skills/` 套件，不需要再次安装同份文件；`--update` 会共同影响这份副本。Antigravity 仍兼容项目旧 `.agent/skills/`，IDE 还兼容旧 `~/.gemini/antigravity/skills/`；本安装器只写表中当前目录，不自动迁移或覆盖旧位置。先核对旧副本与本地修改，避免会话加载到非预期版本。[Antigravity 官方技能位置与调用说明](https://antigravity.google/docs/skills)

### Qoder、Qoder CN、ZCode 与 WorkBuddy AI

<a id="workbuddy-ai-与-qoder-cn-快速安装"></a>

**WorkBuddy AI 与 Qoder CN 快速安装：** 在本仓库根目录执行以下命令，分别安装完整八技能到当前用户的默认目录：

```bash
python3 -B -X utf8 scripts/install.py --host workbuddy --user
python3 -B -X utf8 scripts/install.py --host qoder-cn --user
```

已有托管安装升级时加 `--update`；只核对本机文件是否与当前源码一致时加 `--check`。核对不会安装或刷新应用。项目安装将 `--user` 换成 `--project "<写作项目根目录>"`。Windows 可将 `python3` 换成 `python`。

- **WorkBuddy AI：** 打开“专家·技能·连接器 → 技能 → 已安装”，确认八个 `story-skill*` 技能并启用，再开新任务，通过编辑器“更多操作 → 技能”选择总入口或具体流程，再描述需求；也可说“使用 Story Skill，帮我规划一本中文小说，本轮只做规划”。自行上传技能需走“添加技能 → 上传技能”，对话安装仅适用于市场技能；本库完整八技能 ZIP 尚未作为单技能上传包验证。关闭技能后，当前会话已加载的内容仍可使用，另开会话后才完全生效。见[官方技能说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。
- **Qoder CN 桌面版：** 打开“扩展 → 技能 → 已安装”，新任务通过“添加上下文 → 技能”选择 `story-skill` 或具体流程，再补充任务；也可输入 `/` 从建议中选择。也可直接用自然语言请求。见[桌面版官方技能说明](https://docs.qoder.cn/qoder/skills)。CLI 使用 `/skills reload`、`/skills` 核对，再用 `/story-skill` 加需求，见[官方加载排查](https://docs.qoder.cn/cli/troubleshoot-loading)。

v0.6.22 源码安装器支持以下目标；标准技能 ZIP 只携带八技能，不含安装器。以下为默认配置位置：

| 应用 | `--host` | `--project` 的安装位置 | `--user` 的安装位置 |
|---|---|---|---|
| Qoder 桌面版／CLI | `qoder` | `<项目根目录>/.qoder/skills/` | `~/.qoder/skills/` |
| Qoder CN 桌面版／CLI | `qoder-cn` | `<项目根目录>/.qoder/skills/` | `~/.qoder-cn/skills/` |
| ZCode | `zcode` | `<项目根目录>/.zcode/skills/` | `~/.zcode/skills/` |
| WorkBuddy AI 桌面版 | `workbuddy` | `<项目根目录>/.workbuddy-ai/skills/` | `~/.workbuddy-ai/skills/` |

Qoder 的目录、标准 `SKILL.md`、脚本与参考资源支持见 [Qoder CLI Skills](https://docs.qoder.com/cli/Skills)及[国际版 IDE Skills](https://docs.qoder.com/extensions/skills)。CN 的个人配置根目录为 `.qoder-cn`，项目仍使用 `.qoder/skills/`，见 [CN 官方配置范围](https://docs.qoder.cn/cli/config-scope)。本机原有核对覆盖 Qoder／Qoder CN 桌面 0.4.3，本轮先复核 Qoder CN 0.4.4 的解析函数，后续桌面调用使用 0.4.8，记录见本节末；CN IDE 的旧[技能说明](https://docs.qoder.cn/user-guide/skills)使用 `.lingma/skills/`，新版[配置迁移说明](https://docs.qoder.cn/qoder/data-import)采用公共配置目录。CN IDE 应按实际版本核对，不能直接套用本表的桌面目标。

ZCode 的目录与一层技能扫描规则见 [官方技能说明](https://zcode.z.ai/cn/docs/skill)、[目录源码](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/adapters/src/skills/roots.ts)及[扫描源码](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/adapters/src/skills/scan.ts)，已与本机 3.14.1 的公开安装代码交叉核对。WorkBuddy 的导入与启用入口见 [官方技能说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。WorkBuddy AI 初始安装与发现时为 5.7.6，桌面调用期间检测到客户端已更新到 5.8.4。两版产品的 `dataFolderName` 均为 `.workbuddy-ai`，桌面启动将其用于配置目录与项目扫描环境，所以个人默认位置是 `~/.workbuddy-ai/skills/`，项目默认位置是 `<项目根目录>/.workbuddy-ai/skills/`；目录核对与历史桌面调用范围见[公开摘要](benchmarks/results/v0.6.21/host-compatibility-summary.json)。此前仅凭底层 `.workbuddy` 备用值推定的目录结论已更正，也不能用 CodeBuddy 的 `.codebuddy/skills/` 代替。这里仅核对公开安装资源，未读取账户配置。

在仓库根目录，按应用和范围任选一个命令：

```bash
python3 -B -X utf8 scripts/install.py --host qoder --user
python3 -B -X utf8 scripts/install.py --host qoder-cn --user
python3 -B -X utf8 scripts/install.py --host zcode --user
python3 -B -X utf8 scripts/install.py --host workbuddy --user
```

项目安装把 `--user` 换成 `--project "<写作项目根目录>"`；已核验托管副本升级再加 `--update`。Qoder 与 CN 在同一项目共享一份套件和安装锁，升级共同影响该副本；个人目录各自独立。每个技能直接放在 `skills/<技能名>/`，不要增加一层 `story-skill-suite/`，也不要只拷贝总入口。这里使用目录安装；八技能 ZIP 是完整目录分发包，不宣称可作为 WorkBuddy 单技能上传包。

本安装器写上述默认目录，不解析宿主的自定义配置根目录，也不自动迁移或覆盖旧个人目录 `~/.workbuddy/skills/` 或项目目录 `.workbuddy/skills/` 中的文件。若设置了 `QODER_CONFIG_DIR`、`QODERCN_CONFIG_DIR` 或 WorkBuddy 配置目录覆盖，先按宿主实际扫描目录核对，不能把默认路径回执当作自定义环境已加载。Qoder CLI 安装后可执行 `/skills reload` 再用 `/skills` 查看；Qoder 桌面版重新打开技能列表或新会话核对。ZCode 在“设置 → 技能 → 刷新”后用 `$story-skill`；WorkBuddy AI 在“已安装”中确认八个技能已启用。桌面版可直接说“使用 Story Skill”并描述任务，Qoder／Qoder CN CLI 可用 `/story-skill` 加需求。

本机 ZCode 3.14.1 所带 CLI 0.16.9 已在隔离项目中通过 `skills list --json` 发现全部八技能、无诊断错误；逐个 `skills inspect <技能名> --json` 完整读取入口正文，与源码一致且未截断。Qoder、Qoder CN 0.4.3 与 WorkBuddy AI 5.6.2 的原生解析函数也在隔离测试中接受全部八入口，名称、描述及正文一致；该函数测试不代表桌面界面或自动发现已验证。所有测试均未调用模型，账号、网络和图像工具仍按实际会话核对。

2026-10-21 的开发验收覆盖当时冻结快照的八入口解析、四组隔离安装及命令帮助。后续 WorkBuddy AI 5.8.4 与 Qoder CN 0.4.8 的原生选择、实际发送及八项规则判断各通过八项；Qoder 额外编辑测试记忆的只读偏离保持失败记录并已恢复。首次 WorkBuddy 安装使用旧目录，不能作为默认目录加载证据；随后已核对并修正为 `.workbuddy-ai`。这些是历史开发快照的检查，不等于本版全部源码已在两端完整创作、生图或平台流程中验收。公开范围与原始记录摘要见[兼容性摘要](benchmarks/results/v0.6.21/host-compatibility-summary.json)，含本机路径的原始记录保留在本地。

### 其他 Agent Skills 宿主

以下目标同样由v0.6.22源码安装器提供；标准技能ZIP不含安装器。每个目标复制相同的完整八技能，项目与个人范围任选一个。表中为宿主原生默认目录，路径依据链接的官方说明：

| 应用与官方说明 | `--host` | `--project` 的安装位置 | `--user` 的安装位置 |
|---|---|---|---|
| [TRAE 国际版 IDE](https://docs.trae.ai/ide/skills) | `trae` | `<项目根目录>/.trae/skills/` | `~/.trae/skills/` |
| [TRAE 国内版 IDE](https://docs.trae.cn/ide_skills) | `trae-cn` | `<项目根目录>/.trae/skills/` | `~/.trae-cn/skills/` |
| [TraeCode CLI](https://docs.trae.cn/cli_skills) | `trae-cli` | `<项目根目录>/.traecli/skills/` | `~/.traecli/skills/` |
| [Cursor](https://cursor.com/docs/skills) | `cursor` | `<项目根目录>/.cursor/skills/` | `~/.cursor/skills/` |
| [CodeBuddy](https://www.codebuddy.ai/docs/cli/skills) | `codebuddy` | `<项目根目录>/.codebuddy/skills/` | `~/.codebuddy/skills/` |
| [OpenCode](https://opencode.ai/docs/skills) | `opencode` | `<项目根目录>/.opencode/skills/` | `~/.config/opencode/skills/` |
| [Copilot VS Code Agent](https://code.visualstudio.com/docs/agent-customization/agent-skills)／[CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) | `copilot` | `<项目根目录>/.github/skills/` | `~/.copilot/skills/` |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | `gemini-cli` | `<项目根目录>/.gemini/skills/` | `~/.gemini/skills/` |
| [Cline](https://docs.cline.bot/customization/skills) | `cline` | `<项目根目录>/.cline/skills/` | `~/.cline/skills/` |
| [Windsurf 旧目录](https://docs.devin.ai/desktop/cascade/skills) | `windsurf` | `<项目根目录>/.windsurf/skills/` | `~/.codeium/windsurf/skills/` |
| [Devin Desktop Cascade](https://docs.devin.ai/desktop/cascade/skills) | `devin` | `<项目根目录>/.devin/skills/` | `~/.config/devin/skills/` |

例如安装 Cursor 个人副本，或只用于一个 TRAE 国内版项目：

```bash
python3 -B -X utf8 scripts/install.py --host cursor --user
python3 -B -X utf8 scripts/install.py --host trae-cn --project "<写作项目根目录>"
```

其他宿主替换 `--host` 即可；已核验的托管安装升级加 `--update`。TRAE 两个 IDE 在同一项目共用一份 `.trae/skills/` 和安装锁，个人目录独立；TraeCode CLI 使用自己的目录。TRAE 的 `.agents/skills/` 兼容导入需要在设置中开启，本安装器直接使用原生目录。Gemini CLI 的个人目录不同于 Antigravity；CodeBuddy 的目录不同于 WorkBuddy AI。Windsurf 与 Devin 的目标用于各自默认位置，不自动搬迁旧目录；迁移时先核对扫描来源与同名副本，避免同时安装多份。

安装后按实际前端核对技能可见与启用状态：

| 宿主 | 刷新与调用 |
|---|---|
| TRAE IDE／TraeCode CLI | 在 IDE 的 Skills & Commands 查看技能；CLI 安装或更新后重启，用 `/skills` 检查，再用自然语言提出任务。 |
| Cursor | 在技能设置中检查，输入 `/` 选择 `story-skill` 或专用入口；也可按描述自动匹配。 |
| CodeBuddy | CLI 用 `/skills` 核对技能及来源，IDE 在技能设置中检查；用自然语言提出任务。 |
| OpenCode | 确认当前版本的技能列表或技能工具可见八个入口；用自然语言提出任务，调用方式按该版本说明。 |
| Copilot | VS Code Agent 检查技能设置，可用 `/story-skill`；CLI 用 `/skills reload` 后查看 `/skills`。两个前端分别验证。 |
| Gemini CLI | 在技能管理中确认启用及工作区信任，按客户端提示批准技能激活，再提出写作任务。 |
| Cline | 开启 Skills，在技能列表确认入口；可用 `/story-skill`。 |
| Windsurf／Devin Desktop | 在 Cascade 技能列表检查，自动匹配或用 `@story-skill`。此处适配 Desktop Cascade，不推定其他 Devin 前端使用同一目录。 |

新增目标已覆盖安装、完整载荷核对和原有升级保护；官方目录支持不等于当前机器已安装这些客户端。表中仍只处理默认目录，不解析各宿主自定义配置位置。未实测的前端需在实际会话检查加载与调用，不能用安装回执替代。云端或远端任务按其实际技能目录和文件权限安装；本机个人目录不会自动同步到远端。

### 升级已有托管副本

对已采用新名称、由本仓库安装器管理且清单一致、没有外部修改的安装，可在验证新版后追加 `--update`，例如：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --project "<写作项目根目录>" --update
python3 -B -X utf8 scripts/install.py --host claude-code --user --update
```

两个升级示例仍按原安装范围任选一个。安装器保留被替换目录的备份；存在本地修改、额外文件、未托管旧目录、链接或并发变化时拒绝覆盖。Codex 用户级安装仍按原方式先核对内容，完整备份并移出原有八个目录，再放入新版八个目录；默认项目安装器不直接替换它。不要让新旧套件在同一技能扫描目录中并存，也应核对个人与项目目录是否存在同名套件。手工备份放在扫描目录之外，保留原件及额外文件。无法确认安装来源或目录归属时，停止覆盖并说明具体路径。

本地工作台需要安装副本包含 `story_workbench.py`；源码检出或发布成功不等于本机安装已更新。

## 安装核验

包含本次完善的源码安装器提供只读 `--check`，比较所选默认位置的八技能与当前源码。例如：

```bash
python3 -B -X utf8 scripts/install.py --host cursor --user --check
python3 -B -X utf8 scripts/install.py --host trae-cn --project "<写作项目根目录>" --check
```

核验不创建目录或安装锁、不安装或修复文件；`--check` 与 `--update` 互斥。缺技能、载荷缺失或变化、额外文件、链接及无效托管清单会返回失败。完全相同的手工副本也可核验，回执中的 `managed_skills` 表示哪些目录带有效托管清单；通过核验不把手工副本转为托管安装。`verified` 只表示本次文件与源码一致，不表示宿主已发现技能或模型已执行任务。安装器忽略自己的清单以及 `__pycache__`、`.pyc` 等运行缓存，其他额外内容按原保护规则处理。

比较目标的八个目录与已验证的新套件，核对文件集合和字节；安装器回执、`__pycache__` 与 `.pyc` 可按各自规则排除。用新版核心 `scripts/story.py` 在隔离书目录执行 `--version`、`--help`、`init`、`status`。安装 v0.6.22 本版源码时，核对版本 `0.6.22` 和全部69个载荷文件；安装 v0.6.16 发布源码时，仍核对版本 `0.6.16` 和51个文件；历史 v0.6.12 发布套件为 `0.6.12` 和49个文件。八目录、全部载荷、相对引用及四个命令均通过后，才报告安装成功。若安装中断或目标被其他进程修改，保留备份与现场，不把部分结果报告为完成。

明确安装更新的本地源码时，仍保存实际快照并逐文件核对；本地验证不代表远端标签、附件或包已同步。

在 Claude Code 或 Antigravity 中打开对应项目，用普通自然语言提出实际需求，确认能加载对应技能；Claude Code、Antigravity 2.0／CLI 也可输入 `/story-skill` 或专用入口（如 `/story-skill-plan`）加上需求。技能文件完整和命令核验通过，不代表联网、浏览器或图像工具已经配置。封面与配图需当前会话可用的图像工具或 MCP，联网调研按当前会话工具能力执行。[Claude Code 官方技能说明](https://code.claude.com/docs/en/skills)

本机工作台由安装副本中的 Python 运行时启动，只监听运行机器的回环地址。本机 Claude Code 可访问本机书目录与工作台；默认书架登记继续保存在 `~/.codex/story-workbench/`，不因宿主切换迁移。指定其他位置时，书架启动、状态和停止命令需使用同一个 `--state-dir`。云端会话不能把自身的 `127.0.0.1` 当作作者电脑的工作台。`~/.claude/skills/` 也不会自动在 Cowork 或云端会话加载，相关范围按 [Claude Code 官方说明](https://code.claude.com/docs/en/skills#use-skills-in-cowork-and-cloud-sessions) 核对。

## macOS 客户端

本版提供 Intel Mac 客户端 **0.1.25 / build 26**，内含 Python 3.12 与 Story Skill 0.6.22。下载 [DMG 安装包](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.25-macos-x86_64.dmg)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.25-macos-x86_64.dmg.sha256)，核对后挂载并将应用拖入 Applications；也可使用 [ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.25-macos-x86_64.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.25-macos-x86_64.zip.sha256)。

客户端采用本地临时签名，未经 Apple 公证；Apple Silicon 尚未验证。实际构建、20 个运行时模块核对、签名、隔离 CLI／安全探针、原生合成自检和 ZIP／DMG 分发范围见[客户端说明](desktop/macos/README.md)与[发布记录](docs/releases/v0.6.22.md)。

## Windows 客户端

本版提供 Windows x64 客户端 **0.1.12**，内含 Python 与 Story Skill 0.6.22。下载[安装程序](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.12-windows-x86_64-setup.exe)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.12-windows-x86_64-setup.exe.sha256)，核对后按向导安装；便携使用可下载[完整 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.12-windows-x86_64.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-workbench-0.1.12-windows-x86_64.zip.sha256)，解压后运行 `StoryWorkbench.exe`。

安装程序未做代码签名。本版须分别核对 Windows 原生构建、完整载荷／源码、实际安装、原生窗口与卸载，以及四份资产的大小和摘要；实际结果、公开下载范围与 Windows 10／11 用户人工覆盖见[Windows 说明](desktop/windows/README.md)与[发布记录](docs/releases/v0.6.22.md)。

升级任一客户端前，保存桌面和外部浏览器全部页面需要保留的文字，退出旧客户端后再安装。已运行的工作台服务可能继续使用旧代码，应在保留编辑后按服务管理流程更新；安装包不会自动结束编辑会话。

安装或升级只更换技能或客户端程序。已有小说正文和 `.story/` 书籍状态不会自动迁移；书库仍使用 schema 2，本地发布记录另用 schema 1。新版规则见[上手说明](docs/中文小说上手.md)及[目录规范](docs/目录结构.md)。

## 随包许可证

当前源码的八技能许可证与根 `LICENSE` 一致，采用自定义非商业使用／商业授权条款；非商业用途免费，商业用途须另行授权并按约定付费。v0.6.22 标准、Claude 桌面、Codex 插件与 npm 包携带完整条款；npm 按输入归档的实际许可生成标识。`Commercial Licensor`、`Commercial Licensing Contact` 仍为占位文字。历史已发布 v0.6.15 技能包继续携带原 MIT 许可证，独立 Mac 客户端携带根自定义条款；安装时以实际归档中的许可证为准。详见[许可证与分发](docs/licensing.md)。
