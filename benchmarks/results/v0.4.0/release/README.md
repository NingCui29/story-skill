# v0.4.0 正式发布回执

发布日期：2026-09-10。固定标签 `v0.4.0` 指向 `c1b3c3377573610a191e165ceb6866ae35afe5a8`；此目录随发布后的文档提交归档，不移动已发布标签。

| 文件 | 记录内容 |
|---|---|
| [release.json](release.json) | 正式 Release、附件大小与 GitHub 返回的 SHA-256 |
| [ci.json](ci.json) | 发布提交的 Linux/Python 3.10、Windows/Python 3.12 全步骤通过 |
| [ci-first-failed.json](ci-first-failed.json) | 首轮 Windows TEMP 短路径测试夹具失败与诊断；保留原失败 |
| [unit-shortpath.json](unit-shortpath.json) | 修复后在本机真实 8.3 短路径下的 311 项测试，零失败、零跳过 |
| [remote-install.json](remote-install.json) | 官方安装器公共固定标签下载、7 个技能/31 个文件逐字节比对、4 个运行命令 |
| [packages.json](packages.json) | GitHub Actions 原始同步回执：发布、注册表回下载、完整性与运行验证 |
| [packages-workflow.json](packages-workflow.json) | 同步任务、步骤、Actions 附件摘要与下载摘要核对 |
| [package-check.json](package-check.json) | 再次下载 Actions 产物后比对本地包；未登录 Packages 页面版本核对 |
| [repository.json](repository.json) | 仓库地址、公开状态与更新后的七入口简介 |
| [independent-review.json](independent-review.json) | 独立复核的 15 项通过：公共 Release、标签、附件、安装文件、CI 与旧版保留 |
| [manifest.json](manifest.json) | 本目录回执与说明文件的 SHA-256 清单，不包含清单自身 |

Windows CI 的 311 项测试全部通过；Linux CI 为 305 项通过，6 项 Windows 专用检查按条件跳过。两端的打包、CLI 冒烟、中文稿件重放、多线历史修订演练均通过。此前本地干净检出的 12 项整包验证仍见上级 [verification.json](../verification.json)，与远端 CI 分开记录。

Release ZIP 的 SHA-256：`087ad76fe32714ea776853cab579c3b6087ed092ef0857c55a76ae20aff7d54b`。

GitHub npm 包为公开的 `@ningcui29/story-skill@0.4.0`，回下载的 TGZ 与本地构建逐字节一致，SHA-256：`0cc37dcf98580b1ab7379ec7c54f23167b972e9e5d404679502fd975d982fa71`。公开包页面不等于匿名 npm 下载；npm 注册表仍要求认证，内容包本身不自动注册 助手 技能。

安装验证只使用隔离临时目录，没有更新用户技能或真实书库。CLI 与文件一致性验证不替代 助手 UI 自动发现测试，也不证明长程小说质量或实际账户 token 降幅。

[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.4.0) · [发布提交 CI](https://github.com/NingCui29/story-skill/actions/runs/34431400166) · [Packages 同步](https://github.com/NingCui29/story-skill/actions/runs/34431905475) · [Packages](https://github.com/NingCui29/story-skill/pkgs/npm/story-skill) · [安装与升级](../../../../docs/github-release.md)
