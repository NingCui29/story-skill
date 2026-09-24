# 安装、升级与 GitHub 发布

> **历史记录提示：** 下列 v0.5.11 及更早发布记录经过名称遮盖。固定标签、附件和包身份并未更名；旧版路径不能由新技能名推导。历史资料中的来源路径与哈希可能无法用遮盖后的文件复验，旧资源请从各版本 Release 页面核对。v0.6.0 是否可远程安装，应按该版 Release 附件和回下载验证判断。

**v0.5.11 历史发布记录。** 本版加入离线章节发布准备，套件包含 8 个技能、38 个载荷文件，创作库仍用 schema 2。固定标签、远端 CI、Release 附件、隔离安装及 Packages 工作流已分别核验；包的独立复核证据见下表。[v0.5.11 版本说明](releases/v0.5.11.md) · [安装指引](../INSTALL.md)。

## v0.5.11 发布验证记录

v0.5.11 于 2026-09-24 15:33:07（北京时间）发布，固定标签对应 `9cdf54e75a07e3b61a7b55aa190837608b36d487`。ZIP 包含 8 个同级技能、38 个载荷文件，180,441 字节，SHA-256 为 `60edb470c255c436aa7da74ab02ff0a35e1b5402693908f1f89fcca48b56cf48`。本版各项结果分别见下表和[验证目录](../benchmarks/results/v0.5.11/README.md)；历史 v0.5.10 数值结果保留，名称已遮盖。

| 环节 | 当前状态 |
|---|---|
| 固定标签提交 | `v0.5.11` → `9cdf54e75a07e3b61a7b55aa190837608b36d487`；38 个技能文件与 ZIP 一致。[发布回执](../benchmarks/results/v0.5.11/release/release.json) |
| 本机工程检查 | 754 项单元测试中 737 通过、17 按条件跳过；冒烟、中文稿与多线写作验收通过。[本地记录](../benchmarks/results/v0.5.11/README.md) |
| 远端 Windows／Linux CI | [main 推送运行 35969424318](https://github.com/NingCui29/story-skill/actions/runs/35969424318) 与[固定标签运行 35970009432](https://github.com/NingCui29/story-skill/actions/runs/35970009432) 两平台全部步骤成功；[首次运行 35968080584](https://github.com/NingCui29/story-skill/actions/runs/35968080584) 的 Windows 失败已修复，失败记录仍保留。[main CI 回执](../benchmarks/results/v0.5.11/release/ci.json) · [标签 CI 回执](../benchmarks/results/v0.5.11/release/ci-tag.json) |
| GitHub Release 与附件 | [v0.5.11 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.11) 已公开；ZIP 和 `.zip.sha256` 回下载，与固定源码和本地构建一致。[附件回执](../benchmarks/results/v0.5.11/release/release.json) |
| 官方固定标签隔离安装 | 官方安装器从固定标签安装 8 个技能、38 个文件，逐文件等于 Release ZIP；版本、帮助、初始化、状态四项通过。[安装回执](../benchmarks/results/v0.5.11/release/remote-install.json) |
| GitHub Packages | [工作流 35970230332](https://github.com/NingCui29/story-skill/actions/runs/35970230332) 报告已发布并从注册表回下载 旧名称 npm 包（v0.5.11）；独立复核从工作流 artifact 下载包，确认 38 个技能文件、2 个包装文件与公开 Release 一致，SHA-256 为 `bd0c973f2d37ccc942d2c49bf7178a5e1eb6232da3310845d698e45164071cd9`，四项 CLI 通过。本机账号直接访问 Packages API 返回 403，未称本机直连注册表成功。[工作流回执](../benchmarks/results/v0.5.11/release/packages-receipt.json) · [独立复核](../benchmarks/results/v0.5.11/release/packages-independent.json) |

发布准备只生成离线材料和本地核对记录，不登录七猫或番茄，不上传或提交章节，也不把作者提供的后台副本当作平台已验证。功能和原有书库兼容边界见[本版说明](releases/v0.5.11.md)。

## v0.5.10 发布验证记录

固定标签 `0f2bb216f2b1422ab615d990fdc97c60d7ec6941`、[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.10) 的 ZIP/checksum 公开回下载、官方安装器固定标签隔离安装和 GitHub Packages 注册表回下载均已核验。[本版验证目录](../benchmarks/results/v0.5.10/README.md)保留经名称遮盖的回执；历史数值和失败结论未重算。

| 环节 | 实际结果与证据 |
|---|---|
| 本机工程验证 | 605 项：589 通过、16 个 Windows 专用检查跳过；七技能格式、安装、CLI、中文稿件、历史修订、容量和迁移通过。[回执](../benchmarks/results/v0.5.10/verification.json) |
| Windows / Linux | [固定提交工作流 35716909379](https://github.com/NingCui29/story-skill/actions/runs/35716909379)两端全部步骤成功；9 项新增 Windows 原生保护测试通过。[分项记录](../benchmarks/results/v0.5.10/release/ci-tests.json) |
| 固定标签与 ZIP | `v0.5.10` → `0f2bb216f2b1422ab615d990fdc97c60d7ec6941`；34 文件 ZIP SHA-256 `821e896a9cb3066dd6daaf56c921217ece468629622d95c597f19f6733897797`。[回下载核验](../benchmarks/results/v0.5.10/release/release.json) |
| 官方隔离安装 | 7 技能、34 文件与公开 ZIP 一致，版本、帮助、初始化、状态四项通过；本机现有安装未改。[回执](../benchmarks/results/v0.5.10/release/remote-install.json) |
| GitHub Packages | v0.5.10 的旧名称 npm 包已发布（[该版 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.10)）并回下载；[工作流回执](../benchmarks/results/v0.5.10/release/receipt.json)和[独立核验](../benchmarks/results/v0.5.10/release/packages-independent.json)确认 34 个技能文件、2 个包装文件、摘要与运行命令 |

Windows 验证限定本次原生 CI 环境，不将本机 Mac 检查当成 Windows 实测；文学规则没有本轮新小说质量验证。标签保留发布提交，main 后续补录分发记录不移动标签或替换附件。

## v0.5.9 发布验证记录

v0.5.9 已于 **2026-09-20 16:11:44（北京时间）** 发布。固定标签、Release 附件公开回下载、官方安装器 Git 方式隔离安装及 GitHub Packages 注册表回下载均已核验；Linux CI 成功，Windows 复现既有 WinError 32，整体 CI 失败。[本版验证目录](../benchmarks/results/v0.5.9/README.md)保存本地与远端回执。下方历史数值保留，名称已遮盖。

| 环节 | 实际状态与证据 |
|---|---|
| 本地工程验证 | Python 3.10：560 项中 553 通过、7 项按条件跳过；CLI、中文稿件和长篇回放、七技能格式、安装与本地链接通过。[完整回执](../benchmarks/results/v0.5.9/verification.json) |
| ZIP 与固定标签 | `v0.5.9` → `d700689a801aab27c5aac3641f3a3369a860d3b4`；34 文件 ZIP SHA-256 `8710ed9b01a6e0d70d547eacf8be52ef30d3b22c116c420506d61b4e3276105b`，与固定源码逐文件一致。[构建](../benchmarks/results/v0.5.9/package.json) · [发布核验](../benchmarks/results/v0.5.9/release/release.json) |
| Release 与附件 | [v0.5.9 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.9) 已公开；ZIP 和 checksum 从公开地址回下载后与本地构建逐字节一致。[回执](../benchmarks/results/v0.5.9/release/release.json) |
| 官方固定标签安装 | 官方安装器从 `v0.5.9` 以 Git 方式隔离安装 7 技能、34 文件，与 Release 一致；版本、帮助、初始化和状态检查通过。本机现有安装未改。[回执](../benchmarks/results/v0.5.9/release/remote-install.json) |
| 发布提交的 Linux／Windows CI | 两次推送运行的 Linux 检查成功；Windows 在回归测试报告导出时复现 `WinError 32`，整体 CI 失败。[分项回执](../benchmarks/results/v0.5.9/release/ci.json) |
| GitHub Packages | [工作流 35498893936](https://github.com/NingCui29/story-skill/actions/runs/35498893936) 成功公开发布 旧名称 npm 包（v0.5.9） 并从注册表回下载；[工作流回执](../benchmarks/results/v0.5.9/release/receipt.json)与[独立复核](../benchmarks/results/v0.5.9/release/packages-independent.json)确认 34 个技能文件、2 个包装文件、SHA-512 与四项运行命令 |

## v0.5.8 发布验证记录

v0.5.8 已于 **2026-09-19 22:33:11（北京时间）** 发布。固定标签、Release 附件回下载、官方安装器 Git 方式隔离安装及 GitHub Packages 注册表回下载均已核验；Linux CI 成功，Windows 复现既有 WinError 32，整体 CI 失败。[本版验证目录](../benchmarks/results/v0.5.8/README.md)保存本地与远端回执。下方历史数值保留，名称已遮盖。

| 环节 | 实际状态与证据 |
|---|---|
| 本地工程验证 | Python 3.10：560 项中 553 通过、7 跳过；CLI、中文稿件和长篇回放、技能格式、安装及本地链接通过。[完整回执](../benchmarks/results/v0.5.8/verification.json) |
| ZIP 与固定标签 | `v0.5.8` → `09d2522a1486cde65a1db5aaec2a96974de77962`；34 文件 ZIP SHA-256 `253c072cc1d28703e36209a6374e6e008fa3abaf0c939d0429e042444ebd5cbc`，与固定源码逐文件一致。[构建](../benchmarks/results/v0.5.8/package.json) · [发布核验](../benchmarks/results/v0.5.8/release/release.json) |
| Release 与附件 | [v0.5.8 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.8) 已公开；ZIP 和 checksum 回下载与本地构建逐字节一致。[回执](../benchmarks/results/v0.5.8/release/release.json) |
| 官方固定标签安装 | 官方安装器从 `v0.5.8` 以 Git 方式隔离安装 7 技能、34 文件，与 Release 一致；版本、帮助、初始化和状态检查通过。本机现有安装未改。[回执](../benchmarks/results/v0.5.8/release/remote-install.json) |
| 发布提交的 Linux／Windows CI | 两次推送运行的 Linux 检查成功；Windows 在报告导出时复现 `WinError 32`，整体 CI 失败。[分项回执](../benchmarks/results/v0.5.8/release/ci.json) |
| GitHub Packages | [工作流 35449089424](https://github.com/NingCui29/story-skill/actions/runs/35449089424) 成功公开发布 旧名称 npm 包（v0.5.8） 并从注册表回下载；[工作流回执](../benchmarks/results/v0.5.8/release/receipt.json)与[独立复核](../benchmarks/results/v0.5.8/release/packages-independent.json)确认 34 个技能文件、2 个包装文件、SHA-512 和四项运行命令 |

## v0.5.7 发布验证记录

v0.5.7 已于 **2026-09-19 20:09:23（北京时间）** 发布。固定标签、Release 附件回下载、官方安装器 Git 方式隔离安装及 GitHub Packages 注册表回下载均已核验；Linux 全步骤成功，Windows 复现既有 WinError 32，整体 CI 失败。[本版验证目录](../benchmarks/results/v0.5.7/README.md)保存本地与远端回执。下方 v0.5.6 及更早记录保持历史原值。

| 环节 | 实际状态与证据 |
|---|---|
| 本地工程验证 | Python 3.10：560 项中 553 通过、7 跳过；CLI、中文稿件和长篇回放、技能格式、安装及本地链接通过。[完整回执](../benchmarks/results/v0.5.7/verification.json) |
| ZIP 与本地 npm 包 | 34 文件 ZIP SHA-256 `dc798d6880a682932f43f627bd7327e80ab8236a7c7f353024f398f5f1e6cb7d`；本地 npm 包及四项命令检查通过。[ZIP](../benchmarks/results/v0.5.7/package.json) · [npm](../benchmarks/results/v0.5.7/npm-package.json) |
| 固定标签与 Release | `v0.5.7` → `b8814b49a236e97213b38b46f72dd037d1c83580`；[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.7) 的 ZIP/checksum 已回下载，服务端摘要、34 个技能文件与固定源码一致。[发布核验](../benchmarks/results/v0.5.7/release/release.json) |
| 官方固定标签安装 | [隔离安装](../benchmarks/results/v0.5.7/release/remote-install.json)采用官方安装器 Git 方式，7 技能、34 文件与 Release 一致；版本、帮助、初始化和状态检查通过。本机直接下载方式超时，现有安装未改 |
| 发布提交的 Linux／Windows CI | [两次运行](../benchmarks/results/v0.5.7/release/ci.json)：Linux 全步骤成功；Windows 在报告导出时复现 `WinError 32`，整体 CI 失败 |
| GitHub Packages | [工作流 35442058102](https://github.com/NingCui29/story-skill/actions/runs/35442058102) 成功公开发布 旧名称 npm 包（v0.5.7） 并从注册表回下载；[工作流回执](../benchmarks/results/v0.5.7/release/receipt.json)与[独立复核](../benchmarks/results/v0.5.7/release/packages-independent.json)确认 34 个载荷文件、2 个包装文件、SHA-512 与本地构建一致 |

## v0.5.6 发布验证记录

v0.5.6 已于 **2026-09-19 17:35:13（北京时间）** 发布。固定标签、Release 附件回下载、官方隔离安装及 GitHub Packages 注册表回下载均已核验；Linux 全步骤成功，Windows 复现既有 WinError 32，整体 CI 失败。[本版验证目录](../benchmarks/results/v0.5.6/README.md)保存本地与远端回执。

| 环节 | 当前结果 |
|---|---|
| 本地单元测试 | Python 3.12：559 项中 552 通过、7 按条件跳过；零失败或错误 |
| 指令计数与合成检查 | [本版重测](../benchmarks/results/v0.5.6/tokens.md)固定指令输入；[四组容量](../benchmarks/results/v0.5.6/scaling.json)和[三类旧库迁移](../benchmarks/results/v0.5.6/migration.json)通过，不作为文学质量结论 |
| 本地 ZIP | [构建回执](../benchmarks/results/v0.5.6/package.json)：33 个技能文件，SHA-256 `d82f7cb7f195260f26b65ea155f515b579ff672cd9da03fbd6467775fffcaba2` |
| 本地 npm 包 | [构建](../benchmarks/results/v0.5.6/npm-package.json)与[载荷核验](../benchmarks/results/v0.5.6/npm-verify.json)通过，33 个技能文件与 ZIP 一致 |
| 固定标签与 Release | `v0.5.6` → `9026008907991c0c87eab86f97a9ceb6b8454e8c`；[Release](https://github.com/NingCui29/story-skill/releases/tag/v0.5.6) 的 ZIP/checksum 已回下载，服务端摘要与固定提交的 33 个技能文件一致。[发布核验](../benchmarks/results/v0.5.6/release/release.json) |
| 官方固定标签安装 | [隔离安装](../benchmarks/results/v0.5.6/release/remote-install.json) 7 技能、33 文件与 Release 一致；版本、帮助、初始化和状态四项通过，本机现有安装未改 |
| 远端 CI | [两次运行](../benchmarks/results/v0.5.6/release/ci.json)：Linux 全步骤成功；Windows 在报告导出时复现 `WinError 32`，后续步骤跳过，整体 CI 失败 |
| GitHub Packages | [工作流 35435102680](https://github.com/NingCui29/story-skill/actions/runs/35435102680) 成功发布公开包 旧名称 npm 包（v0.5.6） 并从注册表回下载；[工作流回执](../benchmarks/results/v0.5.6/release/packages.json)和回下载归档的[独立复核](../benchmarks/results/v0.5.6/release/packages-independent.json)确认 33 个载荷文件、2 个包装文件与本地构建一致 |

## v0.5.5 发布验证记录

v0.5.5 已于 **2026-09-12 19:53:27（北京时间）** 发布。Release 附件回下载、固定标签和官方隔离安装已核验；Linux 全步骤成功，Windows 因既有 WinError 32 失败，整体 CI 失败。Packages 已公开发布，工作流回下载产物的独立复核通过。[本版验证目录](../benchmarks/results/v0.5.5/README.md)保存各项回执；下列历史区段保留原版本、数字和链接。

| 环节 | 实际状态与证据 |
|---|---|
| 维护阶段本地验证 | Intel macOS／Python 3.12.14：543 项中 536 通过、7 跳过，20 项 CLI 冒烟通过；运行时仍为 0.5.4，不计为本版发布验收。[原始记录](../benchmarks/results/audit-branch-recovery-2026-09-12/validation.json) |
| v0.5.5 完整工程验收 | [本版完整回执](../benchmarks/results/v0.5.5/verification.json)：543 项中 536 通过、7 跳过，零失败或错误；12 项整包检查全部通过，162 份 Markdown 的 813 个本地文件链接核对通过 |
| 本版构建 | [ZIP](../benchmarks/results/v0.5.5/package.json)：33 文件、126,882 字节，SHA-256 `c3b621859256ef9cdd350bcfde1d09c959c7e4b45c423082b83b26133e1711db`；[npm](../benchmarks/results/v0.5.5/npm-package.json)：109,898 字节，33 个载荷文件与 ZIP 一致，另有 2 个包装文件 |
| 指令计数 | [重新测量](../benchmarks/results/v0.5.5/tokens.md)：普通／短篇 7,023、多线 9,610、深读 5,068、含示范 7,077、审稿 3,063、冲突审稿 4,528 tokens；仅固定指令 |
| 容量、升级及迁移 | [合成容量](../benchmarks/results/v0.5.5/scaling.json) strict／local 四组合通过；[v0.5.4→v0.5.5 含 ZIP 升级](../benchmarks/results/v0.5.5/upgrade.json) 18 项、[三类合成迁移](../benchmarks/results/v0.5.5/migration.json)通过 |
| 新增指令兼容复核 | [独立复核](../benchmarks/results/v0.5.5/instruction-review.json)发现的细纲默认范围表述不一致已对齐并定点复核；未遗留问题，不作文学质量结论 |
| 固定标签提交 | `v0.5.5` → `c53703bb57f42504022edb1c5d5011943e53cdf1`；[标签及源码复核](../benchmarks/results/v0.5.5/release/release.json)，不随后续文档更新移动 |
| 发布提交的 Linux／Windows CI | [运行 34692130624](https://github.com/NingCui29/story-skill/actions/runs/34692130624)：Linux 全步骤成功；Windows 首项报告导出复现已知 WinError 32，后续检查跳过，整体 CI 失败。[分项回执](../benchmarks/results/v0.5.5/release/ci.json) |
| GitHub Release 与附件 | [v0.5.5](https://github.com/NingCui29/story-skill/releases/tag/v0.5.5) 已发布；ZIP/checksum 回下载、服务端摘要与固定提交的 33 个技能文件一致。[发布核验](../benchmarks/results/v0.5.5/release/release.json) |
| 官方固定标签安装 | [隔离安装](../benchmarks/results/v0.5.5/release/remote-install.json) 7 技能、33 文件与固定提交／Release 一致；版本、帮助、初始化和状态 4 项检查通过，临时书库已清理 |
| GitHub Packages | [工作流 34692211772](https://github.com/NingCui29/story-skill/actions/runs/34692211772) 成功公开发布 旧名称 npm 包（v0.5.5） 并从注册表回下载；[工作流回执](../benchmarks/results/v0.5.5/release/packages.json)及其回下载归档产物的[独立复核](../benchmarks/results/v0.5.5/release/packages-independent.json)确认 33 个载荷文件、2 个包装文件和 4 项命令检查通过。本机直连注册表为 HTTP 403，未称本机直接回下载成功 |
| 本机安装 | 未更新：本次未请求安装，不作为发布验收阻塞项 |

历史修订新增 `history-saved` 完整断点回读和 `history-deps --chapter` 正式声明查询；卡片／世界变更按已记录关系扩大复核范围并清空旧审查。世界状态修正全局规则遗漏、同刻度与未知时间、物件归属／认知后续召回及状态版本混用。具体边界见 [本版说明](releases/v0.5.5.md)；依赖范围仍需人工核全，不宣称自动理解全部剧情或提升文学质量。

## v0.5.4 发布验证记录

v0.5.4 已于 **2026-09-11 21:21:30（北京时间）** 发布。固定标签、Release 附件回下载和官方隔离安装已核验；Linux 检查通过，Windows 因已知 WinError 32 失败，整体 CI 为失败。Packages 发布、注册表回下载及独立复核通过。完整工程与分发结果分别登记；[本版验证目录](../benchmarks/results/v0.5.4/README.md) 保存独立证据，v0.5.3 及更早发布段、标签、附件和旧测量不改。

| 环节 | 实际状态与证据 |
|---|---|
| 本地工程、打包与隔离安装 | Intel macOS／Python 3.12：443 项中 436 通过、7 按条件跳过，零失败或错误；12 项整包检查通过。[完整回执](../benchmarks/results/v0.5.4/verification.json) |
| 指令成本 | [本版计数](../benchmarks/results/v0.5.4/tokens.md)：普通／短篇 6,956、多线 9,143、深读 5,010、含示范 7,019、审稿 3,063、冲突审稿 4,528 tokens；只计固定指令 |
| 容量、升级及迁移 | [v0.5.3→v0.5.4 升级 18 项](../benchmarks/results/v0.5.4/upgrade.json)与[三类合成旧库迁移](../benchmarks/results/v0.5.4/migration.json)通过；四种合成容量组合通过 |
| 固定标签提交 | `v0.5.4` → `89945075e0917acff8f23b9fa356c3a8cbba658e`；[解析及复核](../benchmarks/results/v0.5.4/release/release.json)，不随后续文档更新移动 |
| 发布提交的 Linux／Windows CI | [CI 34603548095](https://github.com/NingCui29/story-skill/actions/runs/34603548095)：Linux 443 项中 431 项通过、12 项按平台跳过，全部步骤成功；Windows 首项报告导出因已知 WinError 32 失败，整体 CI 为失败。[分项回执](../benchmarks/results/v0.5.4/release/ci.json) |
| GitHub Release 与附件 | [v0.5.4](https://github.com/NingCui29/story-skill/releases/tag/v0.5.4) 已发布；ZIP 与校验文件回下载通过，GitHub 服务端摘要、33 个技能文件与固定提交、源码及本地构建一致。[发布核验](../benchmarks/results/v0.5.4/release/release.json) |
| 官方固定标签安装 | 从远端 `v0.5.4` 隔离安装 7 个技能、33 个文件，与 Release 和固定提交一致；版本、帮助、初始化、状态四项 CLI 通过。[安装核验](../benchmarks/results/v0.5.4/release/remote-install.json) |
| GitHub Packages | [工作流 34603844944](https://github.com/NingCui29/story-skill/actions/runs/34603844944) 成功公开发布 旧名称 npm 包（v0.5.4）；注册表回下载后在本机独立核对摘要、33 个技能文件、2 个包装文件、SHA-512 和四项 CLI，均通过。[工作流回执](../benchmarks/results/v0.5.4/release/packages.json) · [独立复核](../benchmarks/results/v0.5.4/release/package-check.json) |

本轮 [三题开发比较](../benchmarks/results/quality-focus-2026-09-11/README.md) 的实际结果为规划持平、正文修订略偏初版候选、跨章审稿略偏旧版。最终一句措辞只经过语义复核，没有重复生成或重新盲评。材料是用于开发的原创合成案例，没有独立留出、重复采样或人工读者；不证明文学吸引力稳定提升。

本版运行时仅更新版本标识，schema 2、七个技能及 33 文件布局不变；既有书库和正文不自动迁移。npm 包装为 v0.5.4 补上既有 Windows 平台限制提示，旧版本包装保持原样。规则变化及授权边界见 [v0.5.4 说明](releases/v0.5.4.md)，工程与分发验证不代替文学评阅。

本地 [ZIP 构建](../benchmarks/results/v0.5.4/package.json)含 33 个载荷文件、119,657 字节，SHA-256 为 `5f66a93a47178cf15a8f6ca4a8390885e7f1dd2dba0fe12dd4951fcd48d4a9af`；[npm 构建](../benchmarks/results/v0.5.4/npm-package.json)的 tarball 为 102,902 字节，SHA-256 为 `b006102ab73e0a5469faad3855f6aac2ef93a4a68bc207296ad6af7f472356f1`。Release ZIP 与注册表回下载 npm tarball 均与本地构建逐字节一致。[本机安装](../benchmarks/results/v0.5.4/release/local-install.json)已在完整备份旧版后由 0.5.3 升级到 0.5.4，7 个技能、33 个文件与 Release 和源码一致，四项 CLI 通过；旧版未发现本地修改或额外文件。它与官方固定标签隔离安装分别记录。固定标签保留发布前文档快照，main 记录后续分发状态，不移动标签或替换附件。

## v0.5.3 发布验证记录

v0.5.3 已于 **2026-09-11 17:18:19（北京时间）** 发布。固定标签、Release 附件回下载、官方隔离安装及 GitHub Packages 回下载均已核验；远端 Linux 检查通过，Windows 在已知 WinError 32 处失败，整体 CI 为失败。新书默认保存到 `写作父目录/书名/`，用户已指定最终书根或继续旧书时沿用原位置；运行时仅更新版本标识，CLI 的 `--title` 不自动创建书名目录。[本版验证目录](../benchmarks/results/v0.5.3/README.md) 与 [逐项状态](../benchmarks/results/v0.5.3/release/state.json) 保存独立结果，v0.5.2 及更早版本的标签与附件未更名，验证数字保留。

| 环节 | 实际结果与证据 |
|---|---|
| 本地工程、打包与隔离安装 | Intel macOS／Python 3.12：443 项中 436 项通过、7 项按条件跳过、零失败或错误；12 项整包检查通过。[完整回执](../benchmarks/results/v0.5.3/verification.json) |
| 新书与旧书选根试用 | [独立模型的两项选根试用](../benchmarks/results/v0.5.3/opening-trial.json) 通过：新建书名目录、沿用明确书根，均停留规划；执行时运行时尚报 0.5.2，三份指令与本版逐字节相同，不冒充固定标签新试用 |
| 指令成本 | [本版重新测量](../benchmarks/results/v0.5.3/tokens.md)：普通／多线写作 6,821／9,008 tokens，深读／含示范 5,010／7,019 tokens |
| 容量、升级及迁移 | [四种合成容量组合](../benchmarks/results/v0.5.3/scaling.json)、[v0.5.2→v0.5.3 升级 18 项](../benchmarks/results/v0.5.3/upgrade.json)及[三类合成旧库迁移](../benchmarks/results/v0.5.3/migration.json)通过 |
| 固定标签提交 | `v0.5.3` → `ccb186b94f121103b09f88cb4067f692bfc2a381`；[标签解析](../benchmarks/results/v0.5.3/release/tag.json)，不随后续发布文档更新移动 |
| 发布提交的 Linux／Windows CI | [CI 34583076198](https://github.com/NingCui29/story-skill/actions/runs/34583076198)：Linux 443 项中 431 项通过、12 项按平台跳过，全部步骤成功；Windows 首项报告导出因已知 WinError 32 失败后停止，整体 CI 为失败。[分项回执](../benchmarks/results/v0.5.3/release/ci.json) |
| GitHub Release 与附件 | [v0.5.3](https://github.com/NingCui29/story-skill/releases/tag/v0.5.3) 已发布；ZIP 与校验文件回下载通过，GitHub 服务端摘要、33 个技能文件与固定提交、源码及本地构建一致。[发布核验](../benchmarks/results/v0.5.3/release/release.json) |
| 官方固定标签安装 | 从远端 `v0.5.3` 隔离安装 7 个技能、33 个文件，与 Release 和源码一致；版本、帮助、初始化、状态四项 CLI 通过，临时数据已清理。[安装核验](../benchmarks/results/v0.5.3/release/remote-install.json) |
| GitHub Packages | [工作流 34583518477](https://github.com/NingCui29/story-skill/actions/runs/34583518477) 成功发布 旧名称 npm 包（v0.5.3）；注册表回下载后在本机独立核对归档摘要、33 个技能文件、2 个包装文件、SHA-512 和四项 CLI，均通过。[工作流回执](../benchmarks/results/v0.5.3/release/packages.json) · [独立复核](../benchmarks/results/v0.5.3/release/package-check.json) |

本版保留 schema 2，已有书不需重新导入，安装升级不自动搬动书目录。README 新增 [从拆书开始，新起一本书](../README.md#从拆书开始新起一本书)；拆书、方法提炼与原创规划按既有技能顺序推进，没有新增文学质量认证，也没有开展新一轮文学对照实验。

本地 [ZIP](../benchmarks/results/v0.5.3/package.json) 含 33 个载荷文件、118,923 字节，SHA-256 为 `6d5acf81f9d7d21dfa0719fad2dd50bf8ae0c2bed0c21fa85485924084768044`；[npm 构建](../benchmarks/results/v0.5.3/npm-package.json) 的 tarball SHA-256 为 `cd27f60476a8bb764c795192c0d9094f37840dc2371eb8913f63e491251e1d8b`。Release ZIP 和注册表回下载 tarball 均与本地构建逐字节一致。[本机安装](../benchmarks/results/v0.5.3/release/local-install.json) 已在保留完整旧版后更新到 0.5.3，33 个文件和四项 CLI 通过；它与官方固定标签隔离安装分别记录。固定标签保留发布前文档快照，main 记录后续完成的分发状态，不移动标签或替换附件。

## v0.5.2 发布验证记录

v0.5.2 已于 **2026-09-11 16:06:25（北京时间）** 发布。本版按 macOS／Linux 通过范围发布；Windows 在已知 WinError 32 处失败，整体 CI 为失败，不宣称全平台通过。源码、固定标签、附件、安装与注册表下载分别核验。[本版验证目录](../benchmarks/results/v0.5.2/README.md) 与 [逐项状态](../benchmarks/results/v0.5.2/release/state.json) 保存当次回执，历史版本的数字、附件和标签不变。

| 环节 | 实际结果与证据 |
|---|---|
| 本地工程、打包与隔离安装 | Intel macOS／Python 3.12：443 项中 436 项通过、7 项按条件跳过、零失败；12 项整包检查通过。[完整回执](../benchmarks/results/v0.5.2/verification.json) |
| 指令成本 | [v0.5.2 重新测量](../benchmarks/results/v0.5.2/tokens.md)：普通／多线写作 6,324／8,511 tokens，深读／含示范 4,959／6,968 tokens |
| 容量、升级及迁移 | [四种容量组合](../benchmarks/results/v0.5.2/scaling.json)、[v0.5.1→v0.5.2 升级 18 项](../benchmarks/results/v0.5.2/upgrade.json)及[三类合成旧库迁移](../benchmarks/results/v0.5.2/migration.json)全部通过 |
| 固定标签提交 | `v0.5.2` → `c50bd28b14b128e287dc18f7cf12a692c4da82be`；[标签解析](../benchmarks/results/v0.5.2/release/tag.json)，不随后续发布文档更新移动 |
| 发布提交的 Linux／Windows CI | [最终 CI 34577137667](https://github.com/NingCui29/story-skill/actions/runs/34577137667)：Linux 443 项中 431 项通过、12 项按平台跳过，全部步骤成功；Windows 执行 1 项、1 项失败，在已知报告导出 WinError 32 处停止，整体 CI 为失败。[分项结果](../benchmarks/results/v0.5.2/release/ci.json) |
| GitHub Release 与附件 | [v0.5.2](https://github.com/NingCui29/story-skill/releases/tag/v0.5.2) 已发布；ZIP 和校验文件回下载通过，7 个技能、33 个文件与固定提交、源码及本地包逐字节一致。[发布核验](../benchmarks/results/v0.5.2/release/release.json) |
| 官方固定标签安装 | 从远端 `v0.5.2` 真实隔离安装 7 个技能、33 个文件，与 Release 和源码一致；版本、帮助、初始化、状态四项 CLI 通过，临时数据已清理。[安装核验](../benchmarks/results/v0.5.2/release/remote-install.json) |
| GitHub Packages | [工作流 34577548793](https://github.com/NingCui29/story-skill/actions/runs/34577548793) 成功发布公开包 旧名称 npm 包（v0.5.2）；注册表回下载的 33 个技能文件和 2 个包装文件与本地构建一致，SHA-512 与四项 CLI 核验通过。[工作流回执](../benchmarks/results/v0.5.2/release/packages.json) · [本机独立复核](../benchmarks/results/v0.5.2/release/package-check.json) |

ZIP SHA-256 为 `eef07ccff5de52c73c86c1f60682535b6f49203331a3e4c04a37187884188c8a`；npm 回下载 tarball 为 `f58e3186b4d153295d4de2b0ef8e18dd04fb6f79dbd46444e62ebe2cd9891d66`，均与本地构建一致。公开包页面无需登录即可查看 0.5.2，GitHub npm 下载仍需认证。[Packages 工作流](../benchmarks/results/v0.5.2/release/packages-workflow.json) · [归档记录](../benchmarks/results/v0.5.2/release/packages-artifacts.json)。

固定标签中的文档保留发布前快照，main 上的本页与 [最终核对](../benchmarks/results/v0.5.2/release/final-check.json) 记录后续完成的分发状态；不移动标签或替换附件。本版不改变 schema 2，也不自动搬动已有正文或重新导入书库。分析逐块替换和首次报告定稿新增分析基线校验；自编脚本须采用新版指纹参数，步骤见 [恢复指南](recovery.md#分析稿修订与续跑)。验证分发和运行时不等于 技能界面 自动发现或文学质量验证。

## v0.5.1 发布验证记录

v0.5.1 已于 **2026-09-10 19:04:42（北京时间）** 发布。[逐项状态](../benchmarks/results/v0.5.1/release/state.json) 记录本地验证、固定标签、远端 CI、Release 附件、安装及 GitHub Packages。发布按 macOS／Linux 通过范围完成；Windows 保留已知失败，不将整个 CI 写成全平台通过。历史回执保留在下文。

| 环节 | 实际结果与证据 |
|---|---|
| 本地工程与安装 | Intel macOS／Python 3.12：397 项测试中 390 项通过、7 项按条件跳过、零失败或错误；12 项整包检查通过。[完整回执](../benchmarks/results/v0.5.1/verification.json) |
| 百万／千万字容量 | 400／4,000 章，2,000／20,000 张卡片，strict／local 四组合通过。[合成容量回执](../benchmarks/results/v0.5.1/scaling.json) |
| v0.5.0→v0.5.1 升级 | [升级回执](../benchmarks/results/v0.5.1/upgrade.json)，保留完整旧版、小说与其他技能 |
| schema 1 迁移 | [三类合成夹具](../benchmarks/results/v0.5.1/migration.json)，不冒充缺失历史实书重验 |
| 指令成本 | [重新测量](../benchmarks/results/v0.5.1/tokens.md)：普通／多线写作 5,330／7,097 tokens，深读／含示范 4,518／6,527 tokens |
| 固定标签提交 | `v0.5.1` → `4fd53f80e26efc0eadda50b62edaf34a046d363a`，[标签解析回执](../benchmarks/results/v0.5.1/release/tag.json)。固定标签不随后续发布文档更新移动 |
| 发布提交的 Linux／Windows CI | [CI 34469193018](https://github.com/NingCui29/story-skill/actions/runs/34469193018)：Linux 397 项中 385 通过、12 平台跳过，全部步骤成功；Windows 执行 8 项、7 通过、1 失败，在已知报告导出 WinError 32 处停止。[分项回执](../benchmarks/results/v0.5.1/release/ci.json) · [原始运行](../benchmarks/results/v0.5.1/release/ci-run.json) · [完整日志](../benchmarks/results/v0.5.1/release/ci-log.txt) |
| GitHub Release 与附件 | [v0.5.1](https://github.com/NingCui29/story-skill/releases/tag/v0.5.1) 已发布；ZIP 及校验文件回下载通过，7 个技能、33 个文件与源码和本地包逐字节一致。[发布核验](../benchmarks/results/v0.5.1/release/release.json) · [本地构建](../benchmarks/results/v0.5.1/package.json) |
| 官方固定标签安装 | 官方安装器固定 `v0.5.1`，隔离安装 7 个技能、33 个文件，与 Release 和源码一致；版本、帮助、初始化、状态四项 CLI 全通过，临时数据已清理。[安装回执](../benchmarks/results/v0.5.1/release/remote-install.json) |
| GitHub Packages | [工作流 34469387622](https://github.com/NingCui29/story-skill/actions/runs/34469387622) 成功发布公开包 旧名称 npm 包（v0.5.1），关联当前仓库；注册表回下载的 33 个技能文件及 2 个包装文件与预先构建包一致，四项 CLI 检查通过。[原始发布回执](../benchmarks/results/v0.5.1/release/packages.json) · [本地独立复核](../benchmarks/results/v0.5.1/release/package-check.json) |
| 作品深读 | [专项评估与限制](作品深读与评估.md)，程序核验和独立模型评阅分开，不作大师认证 |

ZIP SHA-256 为 `419aa4277c609c5626862cdf5a78c6d6f7d67bbef17d018f53ad64dea225900c`；npm 回下载 tarball 为 `e6bd3e5a28f69accfd559892dd2ef9e19b4a3eebc2e79575c99761e3aeee2b7d`，与本地构建字节一致。Actions 归档回执与 tarball 下载后又在本机独立核对了文件、SHA-512 及四项 CLI。[工作流记录](../benchmarks/results/v0.5.1/release/packages-workflow.json) · [归档信息](../benchmarks/results/v0.5.1/release/packages-artifacts.json)。GitHub npm 下载仍需认证，npm 本身不注册为技能。

安装与回下载验证没有更改全局技能或用户小说。它们验证分发和运行时，不代表 技能界面 自动发现、文学质量或 Windows 通过。[安装指引](../INSTALL.md) 已登记远端固定标签提交和校验摘要。标签内文档保留发布前快照，main 上的本页与验证回执记录随后完成的发布结果；技能和生产脚本与已验证发布提交保持一致。

## v0.5.0 发布验证记录

本节只记录 v0.5.0 的实际结果。历史 v0.4.0 的测试数、标签提交、附件摘要和平台结果不能代替本版验证。

| 环节 | 当前状态 |
|---|---|
| 本地软件与 CLI 验证 | 修复后 macOS／Python 3.12 回归：379 项测试中 372 项通过、7 项平台专用测试跳过、零失败或错误；同一代码的 12 项整包检查全部通过，[最终回执](../benchmarks/results/v0.5.0/verification.json) 已刷新 |
| 本地打包 | [打包回执](../benchmarks/results/v0.5.0/package.json) 已核验 7 个技能、31 个载荷文件；ZIP SHA-256 为 `d65504d907110ad59fa465566f7374991a216c03cdd42edc81b19ddcc87810a8` |
| 百万／千万字合成容量 | 修复后的 [本版回执](../benchmarks/results/v0.5.0/scaling.json) 已通过百万／千万字的 strict、local 四组合 |
| 开书初始化 | [单事务及本地测量](../benchmarks/results/v0.5.0/initialization.json)：88 条建表相关语句与初始元数据一次提交，保留持久化设置；测量仅含每阶段八次 macOS 样本 |
| 旧版升级 | [v0.4.0→v0.5.0 回执](../benchmarks/results/v0.5.0/upgrade.json)：18 项检查通过，完整旧版保留、七入口更新、重复执行、书籍与其他技能不变 |
| schema 1 迁移与回滚 | [合成迁移回执](../benchmarks/results/v0.5.0/migration.json)：固定 v0.2.0 生成长篇、短篇导入、拆文三类夹具；原三本历史书库缺失，未将合成结果称为实书重验 |
| 本地 npm 包 | [构建回执](../benchmarks/results/v0.5.0/npm-package.json)：身份为 旧名称 npm 包（v0.5.0），31 个技能文件与 ZIP 一致；注册表发布与回下载另见下行 |
| 指令 token | [本版测量](../benchmarks/results/v0.5.0/tokens.md) 已生成，普通／多线写作为 5,324／7,091 tokens；输入文件哈希核验通过 |
| 固定标签提交 | `v0.5.0` → `549e5c98b3f79e61d702cbdd8ddbbebf1619f41a`，[标签解析回执](../benchmarks/results/v0.5.0/release/tag.json)；固定标签不随后续文档提交移动 |
| 发布提交的 Linux／Windows CI | [CI 回执](../benchmarks/results/v0.5.0/release/ci.json)：Linux 379 项中 367 通过、12 平台跳过，全部步骤成功；Windows 执行 8 项、7 通过、1 失败，导出 WinError 32。该 CI 与发布包的 31 个技能文件一致，未宣称最终标签全平台通过 |
| GitHub Release 与附件 | [v0.5.0](https://github.com/NingCui29/story-skill/releases/tag/v0.5.0) 于 2026-09-10 14:37:55（北京时间）发布，ZIP 与校验文件已回下载，字节及 31 个载荷文件与本地包、源码一致。[发布回执](../benchmarks/results/v0.5.0/release/release.json) |
| 公共固定标签安装 | 官方安装器从固定 `v0.5.0` 下载 7 个技能、31 个文件，与 Release ZIP 及源码一致；版本、帮助、初始化、状态四项 CLI 全部通过，临时数据已清理。[安装回执](../benchmarks/results/v0.5.0/release/remote-install.json) |
| GitHub Packages | [工作流 34446075094](https://github.com/NingCui29/story-skill/actions/runs/34446075094) 成功发布公开包 旧名称 npm 包（v0.5.0）；注册表回下载的 31 个技能文件、2 个包装文件均匹配，四项 CLI 检查通过。[发布及回下载](../benchmarks/results/v0.5.0/release/packages.json) · [本地独立复核](../benchmarks/results/v0.5.0/release/package-check.json) |

本地检查、远端 CI、Release 附件、实际安装和注册表下载分别验收；只完成其中一项，不将其他项标为通过。[统一安装指引](../INSTALL.md) 已登记真实标签提交及与远端下载一致的 ZIP 摘要。

首轮候选提交 `56cb3d109734b8b261fee25ca389494d1d7bc8cb` 的 [CI 34442222281](https://github.com/NingCui29/story-skill/actions/runs/34442222281) 中，Linux 完成，Windows 达到 20 分钟时限，未输出完整单测汇总；进度中的 6 个失败标记来自 4 个测试方法，不能算作 6 项完整测试结果。[首轮未完成回执](../benchmarks/results/v0.5.0/release/ci-first-incomplete.json)。随后 [诊断 CI 34443655732](https://github.com/NingCui29/story-skill/actions/runs/34443655732) 在 Windows 首个失败处停止，确认大小写等价路径错误分类不符。[诊断失败回执](../benchmarks/results/v0.5.0/release/ci-diagnostic-failed.json)。这些候选均未据此创建正式发布。

修复保留三项具体变化：Windows 路径比较不再用自动忽略大小写的相等结果跳过别名检查；目录句柄补齐遍历权限以建立重命名保护；ZIP 检查原始成员名，防止路径规范化掩盖异常输入。另将开书的表结构与元数据初始化合并为一次事务，新增回滚与第二连接可见性回归；八次本地测量见 [初始化证据](../benchmarks/results/v0.5.0/initialization.json)，不将其作为 Windows 耗时或整场 CI 加速的证明。本地复验已完成；Windows 的最终现存限制见下段。

Windows 后续 [CI 34444622625](https://github.com/NingCui29/story-skill/actions/runs/34444622625) 确认目录句柄会使普通文件重命名返回 WinError 32，报告导出保留为待恢复状态。[导出失败回执](../benchmarks/results/v0.5.0/release/ci-windows-export-failed.json)。[原生 API 诊断](../benchmarks/results/v0.5.0/release/windows-api-probe.json) 只用于定位限制，没有作为本版的新实现或通过验收依据。v0.5.0 按当前 macOS／Linux 通过范围发布，停止本轮 Windows 修复与验证循环；Windows 安装指引继续固定 v0.4.0。

v0.5.0 注册表回下载 tarball 的 SHA-256 为 `f63bbddf2d0264ddcd1aa0e813fa4231596f959a923e66ffe7f81d941086cfc7`，与本地构建包字节一致；31 个技能文件及 2 个包装文件匹配，四项运行时检查通过，临时书库已删除。[工作流回执](../benchmarks/results/v0.5.0/release/packages-workflow.json) · [回下载与独立核对](../benchmarks/results/v0.5.0/release/package-check.json)。包可见性为 public；GitHub npm 下载仍需认证，npm 本身不注册为技能。实际隔离安装验证文件与 CLI，不代表 技能界面 自动发现或文学质量验证。

## 历史 v0.4.0 发布验证记录

v0.4.0 于 2026-09-10 发布，包含技能拆分、中文场景指导与当时的运行时修复。本地干净检出记录为 311 项测试、12 项整包检查通过，ZIP/npm 与源码的 31 个载荷文件一致；这组历史数字不代表 v0.5.0 已通过。[原修复与回执](全仓审查与优化.md)

固定标签指向提交 `c1b3c3377573610a191e165ceb6866ae35afe5a8`，不随后续文档更新移动。以下结果按发布环节分别记录：

| 环节 | 实际结果与证据 |
|---|---|
| GitHub Release | [v0.4.0](https://github.com/NingCui29/story-skill/releases/tag/v0.4.0) 于 2026-09-10 11:04:24（北京时间）发布，包含套件 ZIP 与 SHA-256 校验文件。[发布回执](../benchmarks/results/v0.4.0/release/release.json) |
| 发布提交的远端 CI | [运行 34431400166](https://github.com/NingCui29/story-skill/actions/runs/34431400166) 全步骤成功。Windows/Python 3.12：311 项通过、零跳过；Linux/Python 3.10：305 项通过、6 项 Windows 专用测试跳过。两端均完成打包、CLI 冒烟、中文稿件重放和多线历史修订演练。[CI 回执](../benchmarks/results/v0.4.0/release/ci.json) |
| 公共固定标签安装 | 用本机官方安装器从 `v0.4.0` 下载到隔离临时目录，7 个技能的 31 个文件与 Release ZIP、源码逐字节一致；安装后 `--version`、`--help`、`init`、`status` 全部成功。[安装回执](../benchmarks/results/v0.4.0/release/remote-install.json) |
| GitHub Packages | [运行 34431905475](https://github.com/NingCui29/story-skill/actions/runs/34431905475) 成功发布 旧名称 npm 包（v0.4.0），回下载 tarball 的 31 个技能文件与 Release 一致，4 项运行时检查通过。包可见性为 public，关联当前仓库；当时公开包页面显示 0.4.0 为 Latest；[该版 Release](https://github.com/NingCui29/story-skill/releases/tag/v0.4.0)可用于查找历史资源。[原始回执](../benchmarks/results/v0.4.0/release/packages.json) · [下载后再次核对](../benchmarks/results/v0.4.0/release/package-check.json) |

Release ZIP 的 SHA-256 为 `087ad76fe32714ea776853cab579c3b6087ed092ef0857c55a76ae20aff7d54b`。固定标签安装只操作临时技能与临时书库，不修改用户安装或小说；它验证文件安装与运行时启动，不等于 技能界面 自动发现或长程文学质量验证。

注册表回下载 tarball 的 SHA-256 为 `0cc37dcf98580b1ab7379ec7c54f23167b972e9e5d404679502fd975d982fa71`，与本地构建包逐字节一致；Actions 归档回执下载后也再次核对通过。[工作流与归档信息](../benchmarks/results/v0.4.0/release/packages-workflow.json)。公开包页面可浏览，GitHub npm 下载仍需要认证；npm 包本身不注册为技能。

首轮远端 Windows 检查曾因 5 个测试夹具没有规范化 TEMP 的 8.3 短路径而失败；Linux 当轮已通过。随后仅修正测试路径，将修改提交为上述发布提交，再重新运行两端 CI；没有修改技能载荷或生产脚本。[首轮失败回执](../benchmarks/results/v0.4.0/release/ci-first-failed.json) 保留，发布前的本地审查与文学评阅也不回写成远端结果。

## Story Skill 安装与升级

**本页新命名源码入口对应 v0.6.0。** [本版说明](releases/v0.6.0.md)登记已完成的本地核验；远程可用性以该版 Release 附件及回下载验证为准。v0.5.11 历史固定版仍是原标签与原分发身份；本文对历史名称的遮盖不会更改该标签的目录、附件或 npm 包。请从[对应版本 Release 页面](https://github.com/NingCui29/story-skill/releases/tag/v0.5.11)核对实际旧版资源，不要把下文的新技能名拼进旧标签安装命令。

v0.6.0 Release 附件齐全且回下载核验通过时，按 [INSTALL.md](../INSTALL.md) 的固定标签、附件与校验值安装完整套件。用户无需手填八个目录；也不要把文档页地址直接当技能目录传给官方安装器。若只在本地开发工作树试用，先核对本仓库安装器支持新布局，再在隔离项目中验证；源码修改不会自动同步本机已安装副本。

升级时先核对当前安装的完整目录、实际版本和本地修改。把旧目录与修改完整备份到技能扫描目录之外，再准备同一版本的八个新技能，核对 `SKILL.md`、共享脚本、载荷文件和运行时命令。旧入口仍留在扫描目录会造成两套技能同时显示；新旧目录切换前后都要核查。项目安装器管理的安装应按其回执处理，不能删托管清单来强行覆盖。仅复制新目录不等于完成旧入口迁移。

小说书根、正文和 `.story/` 状态库不随技能改名自动搬迁；已有导出继续按登记的实际路径读取。若需要回退历史固定版，应从其真实 Release/tag 准备完整旧套件，在扫描目录外保留当前完整副本，并按历史版自己的目录清单和运行时版本复验。历史版不具备新命名入口，不能将本页遮盖后的路径当作可执行的回退命令。[恢复指南](recovery.md)说明书库与导出问题。

## 维护者：验证并发布新布局

本节说明可复用的发布顺序；具体执行结果以对应提交的 Actions、Release 和 Packages 回执为准。先核对 8 份入口和相对引用、整套安装/更新/恢复、ZIP 与 npm 白名单、新 token 输入清单及下载后的 CLI 验证。保留已发布版本的固定 tag、附件和历史测量，不覆盖已有版本。

在开发工作树中逐条运行并检查结果；这些命令只生成本地证据，不表示 v0.6.0 已发布：

```bash
git status --short
git remote -v
python3 -B -X utf8 scripts/smoke.py --output dist/manual-smoke-v0.6.0.json
python3 -B -X utf8 scripts/long_acceptance.py --output dist/manual-long-v0.6.0.json
python3 -B -X utf8 scripts/package.py
```

迁移探针使用保留的 [schema 1 旧运行时夹具](../tests/fixtures/schema1_runtime.py)，经固定摘要核对后生成三类合成夹具，用 `--source` 复查历史实书时需另备相应旧数据库。`verify.py` 校验报告的当前哈希绑定。v0.6.0 新证据应按实际结果另存到新目录；历史 [v0.5.10 验证](../benchmarks/results/v0.5.10/README.md)与其他旧回执只作为经名称遮盖的历史参考。运行时或技能文本变化后，不能将旧版测试数及 token 百分比改名为新版结果。

提交前检查本版 `skills/` 改动、维护文档、验证回执与发布工具按计划进入暂存；不要把本地安装副本、虚拟环境、数据库或测试产物放入提交。先审查完整 diff，再提交和推送。只有推送完成，main 的多路径安装入口才具备远端源码。

准备发布固定版本时，在实际通过验证的提交上创建未使用的版本 tag。核对运行时版本、ZIP 文件名、Release tag 和 npm 版本全部一致后，再推送 tag；不要强制移动已经分发的 tag。

在 [创建 GitHub Release](https://github.com/NingCui29/story-skill/releases/new) 选择新 tag，填写本版变化与真实验证范围，上传本次构建的 `story-skill-<版本>.zip` 和 `.zip.sha256`，再发布。`dist/` 被 Git 忽略，普通 push 不会上传附件。GitHub 自动生成的 Source code ZIP 包含整个仓库，与套件附件用途不同。

发布后使用固定 tag 在独立临时目录真实安装整套，核对文件与 ZIP 的逐字节一致性、共享依赖和 CLI 启动结果，再记录验证回执和发布状态。技能在宿主应用中的发现和任务路由需要实际使用验证，文件复制和 CLI 测试不能代替这一层。

## GitHub Packages 同步

[同步工作流](../.github/workflows/packages.yml) 在正式 Release 发布后从该版本的 ZIP 和校验文件构建包；也可在 [Actions 页面](https://github.com/NingCui29/story-skill/actions/workflows/packages.yml) 对已发布标签手动补同步。工作流必须核对实际包身份、载荷清单、回下载摘要与临时工程中的版本、帮助、初始化和状态命令。已有版本若内容不同，应停止，不能删除或覆盖注册表文件。

v0.5.11 及更早版本使用历史包身份，名称在本页已遮盖；[v0.5.11 发布页](https://github.com/NingCui29/story-skill/releases/tag/v0.5.11)和上方工作流、回执保留当时验证结论。遮盖后的名称不能直接用于 `npm pack`。v0.6.0 包的状态必须以该版实际发布及回下载回执为准，不能将历史包的状态算作新包完成。GitHub npm 即使包页面公开，下载仍可能需要认证；[官方注册表说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry)给出认证要求。
