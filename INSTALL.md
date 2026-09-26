# 安装或升级 Story Skill

**v0.6.2 发布准备中，暂勿据此升级。** 固定标签 `v0.6.2`，发布提交将在回下载核验后补录。八技能、39 文件；Release 附件和 Packages 的核验结果见[发布记录](docs/releases/v0.6.2.md)。

可把这一行发送给支持技能的应用：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

这是一条自然语言安装请求。本页不是技能目录，不能把本页地址直接当作安装脚本的 `--url`。安装前核对固定标签提交、ZIP 校验文件及[发布记录](docs/github-release.md)；信息缺失或不一致时保留现有安装。运行时需要 Python 3.10 或更新版本。

## 完整套件

v0.6.2 完整套件 一次安装以下八个同级目录，共 39 个载荷文件：

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

新版 ZIP 名为 `story-skill-0.6.2.zip`，包名为 `@ningcui29/story-skill`。Release 附件和 GitHub Packages 的新包尚待本轮发布核验；npm 包不是直接可发现的技能安装。历史文档经过名称遮盖；旧版资源的真实文件名不会因文档改写而改变。

远程安装时，先用官方安装脚本从 `v0.6.2` 固定标签将八个 `--path` 安装到隔离临时目录；另从同一版本 Release 下载 [ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.2/story-skill-0.6.2.zip) 和 [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.2/story-skill-0.6.2.zip.sha256)，核对摘要，并逐文件比较临时目录与 ZIP。套件 ZIP 为 204,726 字节，SHA-256 为 `09135f199a66cffd83676a7f7ba29b9de95bd97aef6122eaa33e7b651222e66f`；公开回下载尚待核验。ZIP 成员只能是上述八个目录内的普通文件，拒绝绝对路径、越界路径和链接。固定标签、临时安装或附件任一环节不一致，停止升级。

## 从本仓库源码安装

使用已核验的 v0.6.2 固定标签。main 可能包含后续更改，不能仅凭版本号判定其与附件相同。项目内托管安装可在仓库根目录运行：

```bash
python3 -B -X utf8 scripts/install.py --project "<项目根目录>"
```

对已采用新名称、由本仓库安装器管理且清单一致、没有外部修改的项目安装，可在验证新版后使用 `--update`。仍使用旧入口名称的项目安装和用户级安装，应先核对内容，完整备份并移出原有八个旧版目录，再放入新版八个目录；不能直接对旧入口执行新版 `--update`。不要让两套入口在技能扫描目录中并存。备份放在扫描目录之外，保留原件及额外文件。无法确认安装来源、目录归属、链接或并发变化时，停止覆盖并说明具体路径。

本地工作台需要安装副本包含 `story_workbench.py`；源码检出或发布成功不等于本机安装已更新。

## 安装核验

比较目标的八个目录与已验证的新套件，核对文件集合和字节；安装器回执、`__pycache__` 与 `.pyc` 可按各自规则排除。用新版核心 `scripts/story.py` 在隔离书目录执行 `--version`、`--help`、`init`、`status`。只有版本为 `0.6.2`、八目录及全部 39 个载荷文件完整且四个命令通过，才报告安装成功。若安装中断或目标被其他进程修改，保留备份与现场，不把部分结果报告为完成。

如测试 main 源码，单独记录提交与文件哈希，不将它冒充固定标签安装。

安装或升级只更换技能文件。已有小说正文和 `.story/` 书籍状态不会自动迁移；书库仍使用 schema 2，本地发布记录另用 schema 1。新版的章节、目录、平台分类和离线发布准备规则见[上手说明](docs/中文小说上手.md)及[目录规范](docs/目录结构.md)。
