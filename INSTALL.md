# 一行安装或升级 Story Codex

**平台选择：macOS、Linux、Windows 统一安装 v0.5.10。安装前须核对固定标签、Release 附件与下列摘要。** Windows 导出修复已在 GitHub 托管的原生 Windows CI 环境中通过本版验收，不代表所有磁盘、文件系统或共享目录均已验证。[验证范围与限制](docs/releases/v0.5.10.md#验证与限制)

把这一行发给 Codex，首次安装和后续升级都使用它：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Codex
```

Codex 读取本页，定位安装位置，下载并核对完整套件：没有安装就新装，已有旧版就保留备份后升级，文件完全一致就报告已是当前版本。已有本地修改会保存在备份中并列出差异，不会被当成已合并到新版。需要指定位置时，在同一行末尾补充“安装到某项目”或“安装到某个 skills 父目录”。

这是发给 Codex 的自然语言请求。本页是执行指引，不是技能目录，不能把本页地址直接传给官方脚本的 `--url`。官方 `skill-installer` 遇到已有目录仍会拒绝覆盖；下面的检查、备份和升级由 Codex 编排完成。

## 固定的发布来源

macOS、Linux、Windows 均按下表安装或升级到 v0.5.10。先核验固定标签解析出的提交和实际下载附件，下载或摘要不符时保留原安装。升级前保留技能备份和书籍状态，不覆盖旧版留下的待恢复文件。一次安装过程中固定使用同一标签及其解析出的提交；已安装版本高于目标版本时，停止自动降级并说明情况。

| 参数 | 值 |
|---|---|
| 仓库 | `NingCui29/story-skill` |
| 三平台目标标签 | `v0.5.10` |
| 固定提交 | `0f2bb216f2b1422ab615d990fdc97c60d7ec6941`；标签不随后续文档更新移动 |
| 发布与验证状态 | [Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.10)、附件回下载和官方安装器 Git 方式的隔离安装已核验；[本版验证目录](benchmarks/results/v0.5.10/README.md)记录 GitHub Packages 与平台 CI 的实际结果 |
| Python | 3.10 或更高 |
| 目标载荷 | 7 个同级技能目录，共 34 个文件；固定提交源码、Release ZIP 与官方固定标签隔离安装已逐文件核对 |
| Release ZIP | [story-codex-0.5.10.zip](https://github.com/NingCui29/story-skill/releases/download/v0.5.10/story-codex-0.5.10.zip) · [校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.5.10/story-codex-0.5.10.zip.sha256) |
| ZIP SHA-256 | `821e896a9cb3066dd6daaf56c921217ece468629622d95c597f19f6733897797`；本地构建回执见 [package.json](benchmarks/results/v0.5.10/package.json)，远端按本表重新核对 |

完整路径清单由 Codex 使用，用户不用逐个填写：

```text
skills/story-codex
skills/story-codex-plan
skills/story-codex-write
skills/story-codex-analyze
skills/story-codex-review
skills/story-codex-research
skills/story-codex-cover
```

## 执行顺序

1. **确定唯一目标。** 尊重用户指定的项目或 skills 父目录。升级已有安装时使用原位置；可检查当前项目及本机官方安装器默认的用户级 skills 目录，无需扫描所有磁盘。没有旧安装也没有指定位置时，采用该安装器的默认用户目录。若发现多份安装且无法确定用户所指，再询问具体位置。记录解析后的绝对路径，只处理上面七个名称，保留其他技能。

2. **先准备完整新版。** 找到并读取本机 `skill-installer`，使用它的官方脚本，先安装到新建的隔离临时目录。展开全部七个 `--path`，三平台均固定 `--ref v0.5.10`；`--dest` 是临时 skills 父目录。下载 v0.5.10 的 Release ZIP 及其同名 `.zip.sha256` 校验文件，核对上表的 SHA-256，再逐文件比较临时安装与 ZIP。ZIP 中的成员必须是这七个目录内的普通文件，检查原始成员名，拒绝路径规范化前就不安全的名字、绝对路径、路径越界及链接。核对七份 `SKILL.md`、共享运行时及全部 34 个载荷文件后再操作目标。下载或验证失败时，保留原安装。

3. **判断是否需要更新。** 比较七个目标目录与验证后的新版，包括文件集合及字节。比对时可忽略本项目安装器的 `.story-codex-install.json`、Python 的 `__pycache__` 和 `.pyc`；其他额外文件属于本地内容。七个目录都存在且载荷完全一致时，报告当前版本已安装并结束，不重复覆盖或制造备份。部分安装、旧版或本地修改则进入下一步。版本读取使用文本解析，先不要执行尚未核验的旧脚本。

4. **保留旧版，再完成整套安装。** 按下表选择一种流程。实际变更前记录旧目录的完整文件清单与哈希，再核对一次以发现准备期间的改动。备份放在所有技能扫描目录之外的唯一新目录，保留本地额外文件和修改，不在 `skills/` 内改名为 `*-old`。目录移动前核对来源与目标的绝对路径及边界；遇到链接、占用、路径归属不明或并发改动时保留现场并说明，不能使用强制删除绕过。

   | 安装状态 | 执行方式 |
   |---|---|
   | 全新安装 | 将已验证的七个临时技能目录复制到目标父目录，逐目录采用“目标已存在则拒绝”的操作；随后校验整套。也可用官方脚本以相同标签、相同七个路径再次安装到目标并重新核验。 |
   | 本仓库安装器管理、清单一致且没有本地修改的项目安装 | 取得同一固定标签的源码，运行 `python -B -X utf8 scripts/install.py --project "原项目根目录" --update`。保留其回执与 `.agents/.story-codex-backups/` 中的旧版。不要把 skills 父目录传给 `--project`。 |
   | 官方安装器、手动复制、部分安装或含本地修改的安装 | 将目标中已有的上述七个技能目录完整移到扫描目录之外的备份位置，校验备份与刚才记录一致，再用已验证的临时套件完成安装。保留本地修改与额外文件的原件并列出差异，明确其尚未应用到新版；不要生成虚假的托管清单或自动合并指令。 |

5. **处理失败与并发保存。** 记录本次移动和新建的每个目录。移动旧版中途失败时，优先将已移动目录恢复到仍空缺的原位置。新版安装或核验失败时，只撤回内容仍等于本次输出的新目录，并保留到临时恢复位置，然后恢复备份；原位置已出现新文件或本次输出已被别人修改时，保留两份并报告具体路径，不覆盖或删除外部修改。不得把部分成功报告成整套成功。文档编排依赖执行者逐步核验，不承诺为官方脚本增加事务或并发锁。

6. **核验并报告。** 再次比对目标的七个技能及全部 34 个载荷文件与已验证 ZIP。使用新核心的 `scripts/story.py` 在隔离临时书目录执行 `--version`、`--help`、`init` 和 `status`；只将版本为 `0.5.10`、全部文件和四个命令都通过的结果报告为成功。汇报安装位置、版本、新装／升级／无需更新、备份位置及本地修改差异。告诉用户技能可在下一条消息使用；未显示时重启 Codex。

以上操作只更新技能文件。书库继续使用 schema 2，已有 0.3.0／0.4.0 及后续 schema 2 书库无需重新导入；小说正文、书籍状态和创作约定保留在原位置，旧导出不会自动批量移动。v0.5.10 加入短篇全文合并、旧短篇原章补齐、合法全文文件名、完本封面与总纲分类交付，并完善长短篇完本审查和中文校对指导；同时修复 Windows 导出问题，验证范围见本版说明。新增正文继续按 `chapters/第一卷 雨夜/第1章 雨中来客.md` 保存；普通书名的短篇全文为书根下的 `<书名>.txt`，特殊或过长书名使用回执给出的实际路径。平台分类仍须依据作品和目标平台核对，已有平台确认值不静默改写。[本版变化与兼容说明](docs/releases/v0.5.10.md)

## 官方脚本参数示例

下列参数用于三平台的第 2 步隔离下载，固定使用同一标签和七个路径。Codex 应替换本机安装器位置与新建的临时目录，并选用本机可用的 Python 3.10 或更新版本：

```bash
python3 "<skill-installer目录>/scripts/install-skill-from-github.py" --repo NingCui29/story-skill --ref v0.5.10 --path skills/story-codex skills/story-codex-plan skills/story-codex-write skills/story-codex-analyze skills/story-codex-review skills/story-codex-research skills/story-codex-cover --dest "<隔离临时目录>/skills"
```

[安装、升级与发布证据](docs/github-release.md) · [技能目录与职责](docs/目录结构.md) · [中文小说上手](docs/中文小说上手.md)
