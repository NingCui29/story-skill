# 安装或升级 Story Skill

**本版：v0.6.11。** 八技能、49 个载荷文件，运行时需要 Python 3.10 或更新版本。固定标签、跨平台 CI、Release 附件和 Packages 的实际核验状态见[本版记录](docs/releases/v0.6.11.md)；发布完成前继续使用已核验的 [v0.6.10](docs/releases/v0.6.10.md)，不把本地构建当作已公开附件。

可把这一行发送给支持技能的应用：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

这是一条自然语言安装请求。本页不是技能目录，不能把本页地址直接当作安装脚本的 `--url`。安装前核对固定标签、ZIP 校验文件及[发布记录](docs/github-release.md)；信息缺失或不一致时保留现有安装。

## 完整套件

v0.6.11 完整套件一次安装以下八个同级目录，共49个载荷文件：

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

套件 ZIP 名为 `story-skill-0.6.11.zip`，包名为 `@ningcui29/story-skill`。npm 包本身不是可发现的技能安装。历史标签、附件与旧安装说明仍保留，不通过重命名推导旧版资源地址。

远程安装时，先用官方安装脚本从 `v0.6.11` 固定标签将八个 `--path` 安装到隔离临时目录；另从同一版本 Release 下载 [ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.11/story-skill-0.6.11.zip) 和 [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.11/story-skill-0.6.11.zip.sha256)，核对摘要，并逐文件比较临时目录与 ZIP。

本版套件 ZIP 为358,111字节，SHA-256 为 `0e0091844449e20576893b2cc2a8a1a9f73755c43c2071b839637f8a8e7eb9ad`。ZIP 成员只能是上述八个目录内的普通文件，拒绝绝对路径、越界路径和链接。固定标签、临时安装或附件任一环节不一致，停止升级。

## 从本仓库源码安装

安装发布版时使用本版已核验固定标签。明确安装本地开发源码时，先保存实际提交、工作区差异和49个载荷文件哈希，在隔离项目中核对；不能仅凭版本号判定其与发布附件相同。两种来源都可在对应仓库根目录运行项目内托管安装：

```bash
python3 -B -X utf8 scripts/install.py --project "<项目根目录>"
```

对已采用新名称、由本仓库安装器管理且清单一致、没有外部修改的项目安装，可在验证新版后使用 `--update`。用户级安装应先核对内容，完整备份并移出原有八个目录，再放入新版八个目录；项目内安装器不直接替换用户级安装。不要让两套入口在技能扫描目录中并存。备份放在扫描目录之外，保留原件及额外文件。无法确认安装来源、目录归属、链接或并发变化时，停止覆盖并说明具体路径。

本地工作台需要安装副本包含 `story_workbench.py`；源码检出或发布成功不等于本机安装已更新。

## 安装核验

比较目标的八个目录与已验证的新套件，核对文件集合和字节；安装器回执、`__pycache__` 与 `.pyc` 可按各自规则排除。用新版核心 `scripts/story.py` 在隔离书目录执行 `--version`、`--help`、`init`、`status`。只有版本为 `0.6.11`、八目录及全部49个载荷文件完整、相对引用有效且四个命令通过，才报告安装成功。若安装中断或目标被其他进程修改，保留备份与现场，不把部分结果报告为完成。

明确安装更新的本地源码时，仍保存实际快照并逐文件核对；本地验证不代表远端标签、附件或包已同步。

## macOS 客户端

[本版 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.11) 另提供 `story-workbench-0.1.0-macos-x86_64.zip` 和校验文件。它是独立的 Intel Mac 实验客户端，内含 Python 3.12 与 Story Skill 0.6.11，不自动安装八技能。采用本地临时签名，未经 Apple 公证；实测范围、使用方式及原生交互限制见[客户端说明](desktop/macos/README.md)。

升级客户端前保存所有页面需要保留的文字，再退出并替换应用包。已经运行的工作台服务可能继续使用旧代码，须在保留编辑后按服务管理流程更新，不自动结束编辑会话。

安装或升级只更换技能或客户端程序。已有小说正文和 `.story/` 书籍状态不会自动迁移；书库仍使用 schema 2，本地发布记录另用 schema 1。新版规则见[上手说明](docs/中文小说上手.md)及[目录规范](docs/目录结构.md)。
