# 安装或升级 Story Skill

**本版目标：v0.6.0。** 新版统一使用 `story-skill` 名称。八个入口、安装器、压缩包和包名都已更换；旧版 [v0.5.11 发布页](https://github.com/NingCui29/story-skill/releases/tag/v0.5.11)仍保留原发布身份，不能从该固定标签获取新版入口。只有 [v0.6.0 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.6.0) 已公开、ZIP 与校验附件齐全并完成回下载验证时，才按本页远程升级；未满足时保留现有安装。

满足上述发布条件后，可把这一行发送给支持技能的应用：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

这是一条自然语言安装请求。本页不是技能目录，不能把本页地址直接当作安装脚本的 `--url`。安装前核对固定标签解析出的提交、ZIP 校验文件、附件回下载结果及[发布记录](docs/github-release.md)；信息缺失或不一致时保留现有安装。运行时需要 Python 3.10 或更新版本。

## 完整套件

一次安装以下八个同级目录，共 38 个载荷文件：

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

新版 ZIP 名为 `story-skill-0.6.0.zip`，包名为 `@ningcui29/story-skill`。这两个名称不表示资源已经公开；应分别核对 Release 与包的实际状态。历史文档经过名称遮盖；旧版资源的真实文件名不会因文档改写而改变。

远程安装时，先用官方安装脚本从 `v0.6.0` 固定标签将八个 `--path` 安装到隔离临时目录；另从同一版本 Release 下载 ZIP 和 `.zip.sha256`，核对摘要，并逐文件比较临时目录与 ZIP。ZIP 成员只能是上述八个目录内的普通文件，拒绝绝对路径、越界路径和链接。固定标签、临时安装或附件任一环节不一致，停止升级。

## 从本仓库源码安装

需要在发布前试用时，使用已取得并自行核验的 v0.6.0 源码。先运行仓库测试和 `python3 -B -X utf8 scripts/verify.py`，再把上述八个目录作为一个整体复制到目标的技能父目录。项目内托管安装可在仓库根目录运行：

```bash
python3 -B -X utf8 scripts/install.py --project "<项目根目录>"
```

对已采用新名称、由本仓库安装器管理且清单一致、没有外部修改的项目安装，可在验证新版后使用 `--update`。仍使用旧入口名称的项目安装和用户级安装，应先核对内容，完整备份并移出原有八个旧版目录，再放入新版八个目录；不能直接对旧入口执行新版 `--update`。不要让两套入口在技能扫描目录中并存。备份放在扫描目录之外，保留原件及额外文件。无法确认安装来源、目录归属、链接或并发变化时，停止覆盖并说明具体路径。

## 安装核验

比较目标的八个目录与已验证的新套件，核对文件集合和字节；安装器回执、`__pycache__` 与 `.pyc` 可按各自规则排除。用新版核心 `scripts/story.py` 在隔离书目录执行 `--version`、`--help`、`init`、`status`。只有版本为 `0.6.0`、八目录及全部 38 个载荷文件完整且四个命令通过，才报告安装成功。若安装中断或目标被其他进程修改，保留备份与现场，不把部分结果报告为完成。

安装或升级只更换技能文件。已有小说正文和 `.story/` 书籍状态不会自动迁移；书库仍使用 schema 2，本地发布记录另用 schema 1。新版的章节、目录、平台分类和离线发布准备规则见[上手说明](docs/中文小说上手.md)及[目录规范](docs/目录结构.md)。
