# Codex 本地插件

**v0.6.23 插件正在准备，尚未完成本轮分发验收。** 预期仍为八技能、69源载荷文件，实际包体与隔离安装结果见[本轮记录](releases/v0.6.23.md)。下列下载与已通过结果属于正式 v0.6.22；开发源码按自身版本生成包名，不用同一版本号替代逐文件核对。

v0.6.22 已发布 Codex 本地插件，内含八个同级技能、共享 Python 运行时和许可证。插件使用 `.codex-plugin/plugin.json` 格式，不需要 MCP 服务或额外 API 密钥；通过本地市场安装，不表示在公共插件目录上架。

下载已发布的 [story-skill-codex-0.6.22.zip](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-codex-0.6.22.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.22/story-skill-codex-0.6.22.zip.sha256)。源清单为 69 个技能文件，加元数据、图标和根许可证为 72 个插件文件；市场说明与登记文件另占 2 个成员，共 74 个。本轮包体、隔离安装／启用、八技能发现、CLI、HTTP、三组完整源码 CI及插件 ZIP 匿名完整下载均已验收；完整分发记录见 [v0.6.22 发布记录](releases/v0.6.22.md)；这些工程检查不调用模型、不修改日常安装。

本版新增结构化扫榜、代表作抽样与研究交接，见[扫榜流程](../skills/story-skill-research/references/rank-scan.md)和[研究交接](../skills/story-skill-research/references/research-handoff.md)。完整研究命令和参考规则随八技能一起提供，研究材料保存到作者指定目录；规划候选和正式正文分别管理。既有番茄平台草稿自动连接仍需要外部 Codex CLI、官方 Chrome 插件（含 SDK）、已连接的 Chrome 与账户额度，安装本插件不会增加这些组件或图像工具。平台实页范围沿用[平台草稿说明](../skills/story-skill-publish/references/platform-drafts.md)。

## 构建

在仓库根目录使用 Python 3.10+；若 `python3 --version` 显示较旧版本，将下列命令中的 `python3` 换成已安装的 `python3.12` 等合适解释器：

```bash
python3 -B -X utf8 scripts/package_codex.py
```

v0.6.22 固定源码输出：

```text
dist/
├── story-skill-codex-0.6.22.zip
├── story-skill-codex-0.6.22.zip.sha256
└── story-skill-codex-0.6.22/
    ├── README.md
    ├── .agents/plugins/marketplace.json
    └── plugins/story-skill/
        ├── .codex-plugin/plugin.json
        ├── assets/story-skill.svg
        ├── LICENSE
        └── skills/                 # 八个同级技能，69 个源载荷文件
```

构建器按标准套件的已审查文件清单取材，技能规则、参考资料和 Python 运行时逐字节保留；仅在八份 `agents/openai.yaml` 中将界面提示词的技能引用映射到插件命名空间，例如 `$story-skill-plan` 变为 `$story-skill:story-skill-plan`。源文件保持原样，插件包另外加入元数据、图标和根许可证。市场目录与 ZIP 使用相同内容；仓库中的小说样本、书库、Git 历史、测试结果和桌面客户端均不打入包。不要把整个源码仓库作为本地插件载荷安装。

已有同名产物时构建器拒绝覆盖。重建时选择一个新的输出父目录，例如 `--output-dir dist/codex-rebuild-20261016`，保留旧产物便于比对。构建 JSON 回执提供实际版本、输出路径与 ZIP 的 SHA-256。

## 隔离验证

本机需有支持 `codex plugin` 的 Codex CLI：

```bash
python3 -B -X utf8 scripts/verify_codex_plugin.py \
  --marketplace-root dist/story-skill-codex-0.6.22 \
  --report dist/codex-plugin-verification.json
```

验证器使用临时 `CODEX_HOME` 和独立测试目录，实际执行市场注册、插件发现与安装，再从安装副本核对文件及运行 Python 命令。验证期间不调用模型，不需要登录，不改用户原有的 Codex 配置或技能安装。具体验收项、结果和客户端版本以 JSON 报告为准；本轮实际回执集中在[发布记录](releases/v0.6.22.md)；隔离验证没有更新作者日常使用的插件。此验证不替代桌面界面验收、模型触发或文学效果评价。

## 安装到日常使用的 Codex

先完成上述验证。在仓库根目录运行：

```bash
codex plugin marketplace add "$PWD/dist/story-skill-codex-0.6.22"
codex plugin add story-skill@story-skill-local
codex plugin list --marketplace story-skill-local --json
```

使用他人提供的 ZIP 时，先比对 `.sha256`，再解压到独立目录。将 `marketplace add` 的参数换成解压后包含 `.agents/plugins/marketplace.json` 的目录。市场名为 `story-skill-local`，插件名为 `story-skill`。保留市场源目录，供后续刷新和重新安装使用。

安装后重新打开 Codex，开始一个新会话，通过插件目录确认 Story Skill 已启用。先用自然语言提出实际任务，例如：“使用 Story Skill，读取我指定的书目录，只核对当前断点。”本机 Codex 的实际加载名带插件命名空间，如 `story-skill:story-skill`、`story-skill:story-skill-plan`；手动选择入口时以当前客户端补全列表为准。

如果已有 `.agents/skills/`、`~/.agents/skills/` 或旧 `~/.codex/skills/` 中的 Story Skill，切换前先核对来源和本地修改，将完整旧套件备份到技能扫描目录之外，再移出原八个目录，避免会话同时加载两份。验证器只测试临时安装，不执行这项迁移。

## 运行与更新

Python 运行时需要当前会话能访问目标书目录。作品状态继续保存在书根的 `.story/`；书架默认位于 `~/.codex/story-workbench/`，可用原有 `--state-dir` 参数指定其他位置。插件缓存用于安装文件，书稿及状态应保存在缓存之外。

联网调研、浏览器和图像生成使用当前会话可用的工具。插件安装不会增加这些工具。本机工作台需要在能运行 Python 且能访问书目录的机器上启动。

更新时先在新输出目录构建、验证新包，保留原市场目录。使用 `codex plugin remove story-skill@story-skill-local` 移除旧插件，再用 `codex plugin marketplace remove story-skill-local` 移除旧市场登记，随后添加新市场路径并安装插件，重开会话核对。移除插件不迁移小说、书架或自定义技能修改。插件保留当前源码的完整[许可证](../LICENSE)。

依据：[官方插件打包与本地市场说明](https://developers.openai.com/plugins/build/plugins)、[官方技能与支持资源说明](https://developers.openai.com/plugins/build/skills)。
