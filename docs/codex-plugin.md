# Codex 本地插件

已发布的v0.6.18包含一个 Codex 本地插件，内含八个同级技能、共享 Python 运行时和许可证。插件使用 `.codex-plugin/plugin.json` 格式，不需要 MCP 服务或额外 API 密钥。本版固定标签安装、Release八附件匿名回下载及Packages独立核验均通过，实际状态见 [v0.6.18 发布记录](releases/v0.6.18.md)；它是可下载的本地市场包，不表示已在公共插件目录上架。

下载 [story-skill-codex-0.6.18.zip](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-codex-0.6.18.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-skill-codex-0.6.18.zip.sha256)。归档共 60 个文件成员：58 个插件文件（55 个技能载荷及元数据、图标、根许可证）加市场说明与登记文件。仓库当前公开；正式附件的下载可用性以本版回下载回执为准。

本版插件验收覆盖市场注册、插件安装、八技能发现、运行时和工作台；实际结果集中在[发布记录](releases/v0.6.18.md)。v0.6.18发布时已核验本机安装；2026-10-19又按作者要求将本次开发功能更新到日常0.6.19插件，实际已安装且启用，65文件及八技能加载验证通过，完整备份保留，见[本机更新回执](../benchmarks/results/development-20261019-platform-drafts/local-installed-update.json)。此本机更新不是公开发版。9:16配图、单一视觉重点和纯文字附言规则保持生效；文件核验不等于实际生图效果验证。

## 构建

在仓库根目录使用 Python 3.10+；若 `python3 --version` 显示较旧版本，将下列命令中的 `python3` 换成已安装的 `python3.12` 等合适解释器：

```bash
python3 -B -X utf8 scripts/package_codex.py
```

当前版本输出：

```text
dist/
├── story-skill-codex-0.6.18.zip
├── story-skill-codex-0.6.18.zip.sha256
└── story-skill-codex-0.6.18/
    ├── README.md
    ├── .agents/plugins/marketplace.json
    └── plugins/story-skill/
        ├── .codex-plugin/plugin.json
        ├── assets/story-skill.svg
        ├── LICENSE
        └── skills/                 # 八个同级技能，55 个源载荷文件
```

构建器按标准套件的已审查文件清单取材，技能规则、参考资料和 Python 运行时逐字节保留；仅在八份 `agents/openai.yaml` 中将界面提示词的技能引用映射到插件命名空间，例如 `$story-skill-plan` 变为 `$story-skill:story-skill-plan`。源文件保持原样，插件包另外加入元数据、图标和根许可证。市场目录与 ZIP 使用相同内容；仓库中的小说样本、书库、Git 历史、测试结果和桌面客户端均不打入包。不要把整个源码仓库作为本地插件载荷安装。

已有同名产物时构建器拒绝覆盖。重建时选择一个新的输出父目录，例如 `--output-dir dist/codex-rebuild-20261016`，保留旧产物便于比对。构建 JSON 回执提供实际版本、输出路径与 ZIP 的 SHA-256。

## 隔离验证

本机需有支持 `codex plugin` 的 Codex CLI：

```bash
python3 -B -X utf8 scripts/verify_codex_plugin.py \
  --marketplace-root dist/story-skill-codex-0.6.18 \
  --report dist/codex-plugin-verification.json
```

验证器使用临时 `CODEX_HOME` 和独立测试目录，实际执行市场注册、插件发现与安装，再从安装副本核对文件及运行 Python 命令。验证期间不调用模型，不需要登录，不改用户原有的 Codex 配置或技能安装。具体验收项、结果和客户端版本以 JSON 报告为准；本版结果集中在 [Codex 插件验收](../benchmarks/results/v0.6.18/codex-plugin-verification.json)。此验证不替代桌面界面验收、模型触发或文学效果评价。

## 安装到日常使用的 Codex

先完成上述验证。在仓库根目录运行：

```bash
codex plugin marketplace add "$PWD/dist/story-skill-codex-0.6.18"
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
