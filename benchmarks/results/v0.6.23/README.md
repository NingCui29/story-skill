# v0.6.23 公开验证证据

本目录记录 Story Skill **v0.6.23**、Mac 工作台 **0.1.26（build 27）**和 Windows 工作台 **0.2.2** 的来源、测试、安装与发布证据。两个 Release 已发布，发布后下载与 Packages 的核验状态见末节。软件测试使用合成资料，不证明真实作者平台操作、模型写作质量或文学验收。

## 来源与构建关系

| 身份 | 固定提交 / 内容 | 证据 |
| --- | --- | --- |
| 两个发布标签的最终来源 | `6a7c203d61eca70a5c4c1ad6607420ef91b985df` | [来源总表](release/source.json)、[主标签](release/tag.json)、[Windows 标签](release/windows-tag.json) |
| 技能、npm、Mac 与 Windows 实际构建来源 | `2f81943e4d6b2f1092f6f06daf143aa532308989` | [构建输入](build-inputs.json)、[本地构建与验收](local-package-verification.json)、[Windows 原生 CI](release/windows-ci.json) |
| 构建来源到最终标签的差异 | 仅 9 份测试文件；发行输入的 Git 模式、对象与字节一致 | [构建来源等价证明](build-source-equivalence.json)、[测试夹具修正](release/test-fixture-repairs.json) |
| 分发内容 | 8 个技能、69 份技能载荷、20 份桌面运行时文件 | [构建输入](build-inputs.json)、[来源 CI 与标签交叉核验](release/ci-tag-crosscheck.json) |

构建输入快照包含 117 项保守检查项，其中实际构建输入并集为 113 项；本地构建使用 87 项，Windows 使用 50 项，存在重叠，不相加。原始构建与 QA 回执继续保留实际构建提交。[交叉核验](release/ci-tag-crosscheck.json)重新检查了 20 份原始回执，未改写其来源字段；后续测试修正和最终标签由独立证据连接。

## 测试历史与最终来源 CI

| 阶段 | 实际结果与边界 | 证据 |
| --- | --- | --- |
| 本地历史全量基线：`2f81943` | Python 3.12.14：运行 2684 项，跳过 25 项，失败与错误均为 0 | [完整本地回执](release/local-full-tests.json)、[阶段汇总](release/local-regression.json) |
| 第一次来源 CI：`2f81943` | Run `40067618165` 失败；Windows 在前置检查失败，未执行其全量 discovery | [第一次失败记录](release/source-ci-attempt-2f81943.json) |
| 第二次来源 CI：`8216d32` | Run `40069965469` 失败；Linux 通过，Windows 全量流程失败 | [第二次失败记录](release/source-ci-attempt-8216d32.json) |
| 9 份测试夹具修正 | 158 个不同测试方法定向通过：首轮 108 项，第二轮 50 项；生产代码未改动 | [修正与复验总表](release/test-fixture-repairs.json)、[浏览器夹具](release/browser-fixture-fix.json)、[恢复夹具](release/recovery-fixture-fix.json) |
| 最终来源 CI：`6a7c203` | Run `40072675192` 的三组矩阵均完整通过，详见下表 | [最终来源 CI](release/source-ci.json)、[独立交叉核验](release/ci-tag-crosscheck.json) |

`local-regression.json` 中的 `final_test_source_commit=8216d32` 是首次收证时的阶段目标。其本地全量结果属于 `2f81943`，不能归到最终 `6a7c203`，也不能与定向复验相加。两次失败回执保留原结论，最终成功有独立回执。

| 最终 CI 矩阵 | 全量运行 | 跳过 | 结论 |
| --- | ---: | ---: | --- |
| Ubuntu / Python 3.10 | 2680 | 46 | 通过 |
| Windows / Python 3.10 | 2680 | 70 | 通过 |
| Windows / Python 3.12 | 2680 | 70 | 通过 |

最终 CI 另逐项核对 413 个关键回归方法，其中已包含上述 158 个夹具测试，不重复累计；Windows 的前置测试组也不叠加到全量数量。源码技术约束、301 项指令载荷和发行包清单契约随最终矩阵核对。历史本地补充检查见[技术约束与指令预算](release/policy-and-budget.json)及[三条合成 CLI 工作流](release/local-cli-checks.json)，后者执行来源为 `8216d32`。

## 技能包与 Mac 原生验收

[本地构建与验收总表](local-package-verification.json)绑定实际构建来源 `2f81943`，汇总成员清单、CRC、固定 Git 字节、临时安装与运行结果；[本地附件清单](local-artifacts.json)记录全部附件与校验文件的摘要。

| 范围 | 结果 | 详细证据 |
| --- | --- | --- |
| 标准、Claude、Codex 与 npm 包 | 标准 ZIP 69 项；Claude ZIP 71 项；Codex marketplace 74 项、插件 72 项；npm 72 项（69 份载荷加 3 份包装文件） | [标准包](package.json)、[Claude 包](claude-package.json)、[Codex 包](codex-package.json)、[npm 本地包](npm-local.json) |
| 安装与宿主发现 | 官方安装器与 check 通过；隔离 Codex home 实际安装、启用并发现 8 个命名空间技能，插件 72 份文件，工作台 HTTP 200 | [固定来源核验](fixed-source-verification.json)、[Codex 插件验收](codex-plugin-verification.json) |
| Mac 0.1.26 / build 27 | 20 份运行时文件；原生 WebKit 书架、编辑器、缓存切换通过，隔离服务完成清理 | [构建](macos-build.json)、[载荷探针](macos-payload-probe.json)、[原生验收](macos-native-acceptance.json) |
| Mac 分发完整性 | 构建 app、ZIP 与只读 DMG 中的 1680 份文件及 9 个符号链接一致；原生测试前后严格 ad-hoc 签名校验通过 | [完整清单](macos-complete-inventory.json)、[分发核验](macos-distribution.json)、[签名与载荷核验](macos-verification.json) |
| Mac 画面 | 三张本次捕获的 WebKit 画面已独立目视审查 | [审查回执](macos-visual-review.json)、[书架](macos-native-shelf.png)、[编辑器](macos-native-editor.png)、[切换后](macos-native-switch.png) |

Mac 使用本地 ad-hoc 签名，未公证；本次原生验收未覆盖人工关闭警告、下载和已安装应用工作流。上述安装与原生检查均使用隔离合成资料，未替换用户安装，也未连接真实模型、SDK 或作者平台。

## Windows 原生构建、安装与画面

[Windows 原生 CI 总表](release/windows-ci.json)对应实际构建来源 `2f81943`、Run `40067852242`。Windows 宿主使用 C# / WPF / WebView2。

| 范围 | 结果 | 详细证据 |
| --- | --- | --- |
| C# 契约与分发清单 | 39 项通过、0 跳过；便携包与安装目录各核验 1125 份文件；完整发布和安装目录通过禁用技术扫描 | [C# 测试](release/windows/windows-csharp-tests.json)、[分发核验](release/windows/windows-distribution-verification.json)、[构建清单](release/windows/windows-build-manifest.json) |
| 来源输入 | 50 项全部匹配：20 项与 Git 字节直接一致，30 项符合声明的 CRLF checkout 映射 | [原生 CI 来源证明](release/windows-ci.json) |
| 生命周期与升级 | 安装互斥、未保存服务保护、阻止升级与卸载、授权停止、回滚、未知文件保留通过；实际 0.2.1 → 0.2.2 升级保留旧宿主 | [生命周期](release/windows/windows-installation-lifecycle.json)、[旧版升级](release/windows/windows-previous-upgrade.json)、[安装日志](release/windows/windows-install.log) |
| 原生 WebView2 与编辑保护 | 书架、主题与偏好恢复、失败页恢复、崩溃保护及编辑缓冲、IME、导出和关闭保护通过 | [原生冒烟](release/windows/windows-native-smoke.json)、[运行时保护](release/windows/windows-runtime-safety.json) |
| 画面审查 | 三张原生 runner 画面已独立目视审查 | [审查回执](release/windows-visual-review.json)、[浅色](release/windows/windows-native-smoke-light.png)、[深色](release/windows/windows-native-smoke-dark.png)、[进度](release/windows/windows-native-smoke-update-progress.png) |

进度画面使用合成进度值，不能作为真实网络更新下载证据。Windows 原生 CI 的四份附件上传及服务端摘要核对记录在[草稿上传回执](release/windows/windows-draft-upload.json)；正式发布与公开下载状态分别列在末节。

## 固定标签与隔离安装

[主标签](release/tag.json)和 [Windows 标签](release/windows-tag.json)均为附注标签，解引用到最终来源 `6a7c203`。[来源 CI 与标签交叉核验](release/ci-tag-crosscheck.json)通过新的 GitHub 只读请求核对两标签、三组 CI checkout 与完整日志，保留实际构建来源。

[固定标签安装回执](release/remote-tag-install.json)记录真实 `v0.6.23` 安装：先前从固定构建提交匿名下载的 71 份文件，在标签阶段重新按标签树验证全部 71 个必需 Git 对象；69 份技能载荷由此复用，官方安装器另从固定标签 URL 重新匿名下载并执行。安装后 69 份文件、8 个技能及标记一致，4 项 CLI 和书架、编辑器两次 HTTP 200 通过；临时目录与服务已清理，前后标签对象和提交未变。公开回执已脱敏本地路径，原始回执另行保留。

## 正式发布、公开下载与 Packages

| 渠道 | 当前回执状态 | 证据 |
| --- | --- | --- |
| 主 Release `v0.6.23` | 已发布，10 份附件：3 个技能 ZIP、Mac ZIP / DMG 及其 5 份 SHA256 校验文件 | [发布回执](release/published.json) |
| Windows `windows-v0.2.2` | 已独立发布，4 份附件：安装器、便携 ZIP 及其 2 份 SHA256 校验文件 | [发布回执](release/windows-published.json) |
| 主 Release 匿名下载 | `ok=true`：10份附件全部完整匿名下载并核对完整SHA256；Mac ZIP与DMG通过连续、不重叠分段取得全部64870501字节，合并后核对完整摘要，不是首尾抽样 | [主下载回执](release/public-downloads.json) |
| Windows 匿名下载 | `ok=true`：两份校验文件完整下载；安装器与便携 ZIP 核对匿名首尾 Range，并绑定原生完整验收和 GitHub 服务端摘要。未声称完整匿名下载两个大包 | [Windows 下载回执](release/windows-public-downloads.json) |
| GitHub Packages | Release 事件触发 Run `40075465932` 成功；checkout 为最终标签提交，实际发布并从 registry 下载 `@ningcui29/story-skill@0.6.23`，核对 69 份载荷加 3 份包装文件、4 项 CLI、公有可见性及仓库关联 | [Packages CI](release/packages-ci.json)、[发布与回下载回执](release/packages/receipt.json)、[仓库关联](release/packages/package-association.json) |

[Packages 导出包独立核验](release/registry-independent-download.json)已通过：621827 字节、SHA256 与 SHA512 SRI 一致，72 个成员和 4 项 CLI 通过，导出 tarball 与本地构建逐字节一致。实际 registry HTTP 下载由 Packages 工作流执行，本地独立检查使用其导出文件，没有进行第二次本地 registry HTTP 下载。

以上发布渠道共 14 份附件，主 Release 保持 `latest`，Windows 更新器使用独立 `windows-v*` 渠道。各层回执分别证明对应检查，不以发布成功替代下载、安装或原生验收。
