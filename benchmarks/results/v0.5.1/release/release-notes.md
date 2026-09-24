# Story Skill v0.5.1

本版完善作品深读、分析示范和分析稿修订续跑，面向 macOS／Linux。七个技能入口保持不变，完整套件增加到 33 个文件。

## 本版变化

- 深度分析按需读取前后文，核对可定位原文、人物情境与竞争解释，提炼有适用条件的写法。
- 新增教学对照示范，帮助修正空泛概括、推断过强和评价尺度不明的问题。
- 明确中断分析与已定稿报告的修订流程：保留原稿，核对进度，另存修订稿并登记路径和摘要。
- 保留具名分卷与固定章节文件名：`chapters/第一卷 雨夜/第1章 雨中来客.md`。旧章按登记路径读取，不自动搬动。
- 安装、ZIP、npm 校验同步到 33 文件；发布检查默认写入实际版本目录，保留历史证据。
- 更新安装指南、中文上手、目录职责、恢复、容量验收与评估文档，收录三部真实作品及跨章、修订、恢复试用的原稿和独立模型评阅。

## 验证范围

Intel macOS／Python 3.12：397 项测试中 390 项通过、7 项按条件跳过、零失败或错误；12 项整包检查通过。百万／千万字 strict、local 四种合成容量组合，以及 v0.5.0 升级、旧库迁移与回滚检查通过。

发布提交的远端 Linux 检查通过：397 项测试中 385 项通过、12 项按平台跳过，全部工作流步骤成功。Windows 在第 8 项测试遇到已知导出失败后停止。详细结果见 [CI 34469193018](https://github.com/NingCui29/story-skill/actions/runs/34469193018)。Windows 正文和报告导出的已知 WinError 32 尚未修复，Windows 用户继续使用 v0.4.0；已用 v0.5.x 处理的书先完整备份，不盲目降级。

《阿Q正傳》完整九章试用完成 14 个块、64 条逐块精确引文、定稿前 21 次原文回读，原稿与澄清稿分别保留。作品试用是有限样本及模型评阅，不代表人类编辑认证或稳定的“大师级”质量。

## 安装

在 助手 发送：

```text
$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md 安装或升级 Story Skill
```

macOS／Linux 固定 v0.5.1；Windows 固定 v0.4.0。完整技能 ZIP 为 `story-skill-0.5.1.zip`，SHA-256：

```text
419aa4277c609c5626862cdf5a78c6d6f7d67bbef17d018f53ad64dea225900c
```

GitHub 自动生成的 Source code ZIP 是整个源码仓库。npm 包名为 `@ningcui29/story-skill@0.5.1`，npm 本身不会注册 助手 技能。

[版本说明](https://github.com/NingCui29/story-skill/blob/main/docs/releases/v0.5.1.md) · [安装指引](https://github.com/NingCui29/story-skill/blob/main/INSTALL.md) · [逐项发布与回下载记录](https://github.com/NingCui29/story-skill/blob/main/docs/github-release.md)
