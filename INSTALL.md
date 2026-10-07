# 安装或升级 Story Skill

**本版：v0.6.12。** 八技能、49 个载荷文件，运行时需要 Python 3.10 或更新版本。固定标签、跨平台 CI、Release 附件和 Packages 的实际核验状态见[本版记录](docs/releases/v0.6.12.md)。[v0.6.12 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.12)已提供技能套件、Claude 桌面导入包、Intel Mac 客户端与各自校验文件。

v0.6.12 新增 Claude 桌面导入包、Claude Code 与 Google Antigravity 目录安装适配，保留 Codex 的默认安装行为。先按实际应用选择以下方式。

## Claude 桌面版：导入单技能包

从[本版 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.12)取得桌面专用包及校验文件；也可在本版源码根目录构建：

```bash
python3 -B -X utf8 scripts/package_claude.py
```

默认输出 `dist/story-skill-claude-desktop-0.6.12.zip`；可用 `--output "<输出ZIP路径>"` 指定位置。自行构建时，以实际源码快照为准；发布包的摘要与核验记录见[本版记录](docs/releases/v0.6.12.md)。

1. 在 Claude 的 **Settings > Capabilities** 开启 **Code execution and file creation**；组织账号需管理员开放相应能力。
2. 打开 **Customize > Skills**，点击 **+ → Create skill → Upload a skill**，上传桌面专用 ZIP。
3. 启用 Story Skill，用自然语言提出任务，例如：“使用 Story Skill 为我规划一本中文短篇，本轮只做策划，不写正文。”

操作依据见 [Claude 官方技能使用说明](https://support.claude.com/en/articles/12512180-use-skills-in-claude)。导入后仍需在实际会话中确认技能已加载、所需文件与代码执行工具可用；本地打包检查不等于桌面导入或模型调用验证。

桌面包采用官方要求的单技能根目录格式，并把八个流程完整保留为内部资源：

```text
story-skill-claude-desktop-0.6.12.zip
└── story-skill/
    ├── SKILL.md
    ├── LICENSE
    └── suite/
        ├── story-skill/
        ├── story-skill-plan/
        └── …其余六个同级流程目录
```

顶层入口按需读取 `suite/story-skill/SKILL.md`；`suite/` 中保留原八个目录及49个源载荷文件，各相对引用仍以所在文件为基准。加上顶层入口和许可证，桌面包共51个文件。桌面只启用总入口，不把内嵌流程当成八个独立安装项。[官方打包格式](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)

同版 Release 的 `story-skill-0.6.12.zip` 用于八目录分发，桌面单技能上传应选择专用的 `story-skill-claude-desktop-0.6.12.zip`。桌面升级通过下载或构建并导入新版包处理；下文的 `--update` 是目录安装器参数，不执行桌面上传。

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

v0.6.12 完整套件一次安装以下八个同级目录，共49个载荷文件：

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

套件 ZIP 名为 `story-skill-0.6.12.zip`，包名为 `@ningcui29/story-skill`。npm 包本身不是可发现的技能安装。历史标签、附件与旧安装说明仍保留，不通过重命名推导旧版资源地址。

八个目录中的 `SKILL.md` 为标准技能入口，专用技能通过相对路径引用 `story-skill` 的共享 Python 运行时及其他技能的参考文件，必须保持同级完整安装。`agents/openai.yaml` 是 Codex 界面配置，随套件保留；其他宿主无需使用它。Claude Code、Antigravity 2.0／CLI 可通过 `/技能名` 调用，也可像 Antigravity IDE 一样用匹配描述的自然语言触发，各目录按下文的宿主选择安装。

远程安装时，先用官方安装脚本从 `v0.6.12` 固定标签将八个 `--path` 安装到隔离临时目录；另从同一版本 Release 下载 [ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.12/story-skill-0.6.12.zip) 和 [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.12/story-skill-0.6.12.zip.sha256)，核对摘要，并逐文件比较临时目录与 ZIP。

本版套件 ZIP 的大小和 SHA-256 以[发布记录](docs/releases/v0.6.12.md)和附件校验文件为准。ZIP 成员只能是上述八个目录内的普通文件，拒绝绝对路径、越界路径和链接。固定标签、临时安装或附件任一环节不一致，停止升级。

## 从本仓库源码安装

安装发布版时使用本版已核验固定标签。明确安装本地开发源码时，先保存实际提交、工作区差异和49个载荷文件哈希，在隔离项目中核对；不能仅凭版本号判定其与发布附件相同。在对应仓库根目录运行默认 Codex 项目内托管安装，目标为 `<项目根目录>/.agents/skills/`：

```bash
python3 -B -X utf8 scripts/install.py --project "<项目根目录>"
```

当前仓库源码的安装器增加 Claude Code 宿主选择。项目安装与个人安装二选一，保持八个技能同级；两个命令不需要同时运行：

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

对已采用新名称、由本仓库安装器管理且清单一致、没有外部修改的安装，可在验证新版后追加 `--update`，例如：

```bash
python3 -B -X utf8 scripts/install.py --host claude-code --project "<写作项目根目录>" --update
python3 -B -X utf8 scripts/install.py --host claude-code --user --update
```

两个升级示例仍按原安装范围任选一个。安装器保留被替换目录的备份；存在本地修改、额外文件、未托管旧目录、链接或并发变化时拒绝覆盖。Codex 用户级安装仍按原方式先核对内容，完整备份并移出原有八个目录，再放入新版八个目录；默认项目安装器不直接替换它。不要让新旧套件在同一技能扫描目录中并存，也应核对个人与项目目录是否存在同名套件。手工备份放在扫描目录之外，保留原件及额外文件。无法确认安装来源或目录归属时，停止覆盖并说明具体路径。

本地工作台需要安装副本包含 `story_workbench.py`；源码检出或发布成功不等于本机安装已更新。

## 安装核验

比较目标的八个目录与已验证的新套件，核对文件集合和字节；安装器回执、`__pycache__` 与 `.pyc` 可按各自规则排除。用新版核心 `scripts/story.py` 在隔离书目录执行 `--version`、`--help`、`init`、`status`。只有版本为 `0.6.12`、八目录及全部49个载荷文件完整、相对引用有效且四个命令通过，才报告安装成功。若安装中断或目标被其他进程修改，保留备份与现场，不把部分结果报告为完成。

明确安装更新的本地源码时，仍保存实际快照并逐文件核对；本地验证不代表远端标签、附件或包已同步。

在 Claude Code 或 Antigravity 中打开对应项目，用普通自然语言提出实际需求，确认能加载对应技能；Claude Code、Antigravity 2.0／CLI 也可输入 `/story-skill` 或专用入口（如 `/story-skill-plan`）加上需求。技能文件完整和命令核验通过，不代表联网、浏览器或图像工具已经配置。封面与配图需当前会话可用的图像工具或 MCP，联网调研按当前会话工具能力执行。[Claude Code 官方技能说明](https://code.claude.com/docs/en/skills)

本机工作台由安装副本中的 Python 运行时启动，只监听运行机器的回环地址。本机 Claude Code 可访问本机书目录与工作台；默认书架登记继续保存在 `~/.codex/story-workbench/`，不因宿主切换迁移。指定其他位置时，书架启动、状态和停止命令需使用同一个 `--state-dir`。云端会话不能把自身的 `127.0.0.1` 当作作者电脑的工作台。`~/.claude/skills/` 也不会自动在 Cowork 或云端会话加载，相关范围按 [Claude Code 官方说明](https://code.claude.com/docs/en/skills#use-skills-in-cowork-and-cloud-sessions) 核对。

## macOS 客户端

[本版 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.12) 另提供 `story-workbench-0.1.1-macos-x86_64.zip` 和校验文件。它是独立的 Intel Mac 实验客户端，内含 Python 3.12 与 Story Skill 0.6.12，不自动安装八技能。采用本地临时签名，未经 Apple 公证；实测范围、使用方式及原生交互限制见[客户端说明](desktop/macos/README.md)。

升级客户端前保存所有页面需要保留的文字，再退出并替换应用包。已经运行的工作台服务可能继续使用旧代码，须在保留编辑后按服务管理流程更新，不自动结束编辑会话。

安装或升级只更换技能或客户端程序。已有小说正文和 `.story/` 书籍状态不会自动迁移；书库仍使用 schema 2，本地发布记录另用 schema 1。新版规则见[上手说明](docs/中文小说上手.md)及[目录规范](docs/目录结构.md)。
