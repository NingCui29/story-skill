# Windows 工作台 0.2.2

**Windows 0.2.2 已正式发布。** [独立发行页](https://github.com/NingCui29/story-skill/releases/tag/windows-v0.2.2)提供本版文件，旧版[Windows 0.2.1](windows-v0.2.1.md)保留历史范围。0.2.2 沿用 C#／WPF／.NET 10／Microsoft WebView2 桌面层，内嵌 Story Skill 0.6.23 Python 作品服务；不要求转换既有作品数据。

本版与 [Story Skill v0.6.23](v0.6.23.md) 分别发布。Windows 使用独立 `windows-v0.2.2`，安装程序、便携 ZIP 与各自 `.sha256` 共四附件；主发行提供技能与 Mac 的十附件并保持 latest。Windows 0.2.1 起的应用内更新读取 `windows-v*`，旧版本继续按固定链接手动升级。

## 用户可见变化

- 恢复中心按作品分批读取，每页最多100条，可继续加载尚未扫描的目录。扫描未完成时保留提示，读取失败时已列记录仍可核对。
- 整书恢复保留文件、作品身份与原 Chrome 会话证据，但恢复副本不能继续使用旧执行端连接；执行平台草稿前须重新连接。
- 平台草稿在填入、点击和回读期间再次核对当前目标、草稿状态与正文；页面变化或结果不明时停止并先只读对账。番茄缺少独立只读预览配置时在启动或连接前阻断。
- 内嵌运行时提供采用新书名的 `book-title` 命令，保留书 ID、书根、章节与短篇全文路径；研究命令修正静态来源解析、重复标签抽样和旧研究材料兼容性。完整创作与研究规则需在所用助手中另行安装。

本版沿用 0.2.1 的安装交接、旧目录备份、失败回退、编辑保护、偏好保存与独立更新通道；本次新构建的原生结果见下文。试读设计、回报呈现与封面联动规则详见[主版本记录](v0.6.23.md)，不据此声称已验证真实读者或平台效果。

## 构建与验收状态

实际构建源码为 `2f81943e4d6b2f1092f6f06daf143aa532308989`。[原生工作流40067852242](https://github.com/NingCui29/story-skill/actions/runs/40067852242)的构建和草稿上传均已成功；原始回执继续绑定该实际来源。[原生汇总](../../benchmarks/results/v0.6.23/release/windows-ci.json)

- 39项C#合约测试通过，无失败或跳过；真实WebView2窗口、浅深与系统主题、跨启动偏好恢复、失败标签恢复、隐藏编辑缓冲、输入法和关闭保护已核对。
- 实际安装、从正式0.2.1升级、完整旧目录保留、安装互斥、占用阻止、认证后的服务停止、安装失败回退、未知文件保留和卸载清理通过。
- 便携包与实际安装目录各1125项文件完整核对，运行时、许可和项目禁止技术扫描通过。50项源码输入按20项原字节与30项声明的CRLF转换逐项核对；四份附件上传草稿并匹配服务端摘要。
- 三张浅色、深色和合成更新进度原图已目视检查，合成进度明确标记。[独立视觉记录](../../benchmarks/results/v0.6.23/release/windows-visual-review.json)

源码CI两次失败均保留：`2f81943` 的Windows前置失败，测试修正后 `8216d32` 的Windows完整回归仍失败；第二轮再修五份测试，累计只修改九份测试。最终标签来源 `6a7c203d61eca70a5c4c1ad6607420ef91b985df` 的[完整CI40072675192](https://github.com/NingCui29/story-skill/actions/runs/40072675192)三个矩阵作业全部通过；Linux／Python3.10运行2680项、跳过46项，Windows／Python3.10和3.12各运行2680项、跳过70项，均零失败和错误。413项关键回归包含158个修正测试方法，不重复累计。失败历史、测试原因和本地历史基线见[主版本记录](v0.6.23.md#来源与两次测试修正)。

Windows原生构建与原始回执保留实际来源 `2f81943`；最终标签的全部分发输入已按[等价证明](../../benchmarks/results/v0.6.23/build-source-equivalence.json)核对，安装程序和便携包未换成未验证的新产物。两次源码CI失败分别记录，没有改写成Windows原生构建失败。

两个注释标签均固定到最终 `6a7c203`。[Windows标签](../../benchmarks/results/v0.6.23/release/windows-tag.json)与[主标签](../../benchmarks/results/v0.6.23/release/tag.json)已实际回读；主标签的[真实隔离安装](../../benchmarks/results/v0.6.23/release/remote-tag-install.json)完成71对象、69载荷、八技能、4CLI与两HTTP200核对，并清理临时服务和目录，标签前后不变。

独立Release于2026-10-24 07:46:31 UTC正式发布，四附件身份与服务端摘要匹配。[发布回执](../../benchmarks/results/v0.6.23/release/windows-published.json)和[公开请求](../../benchmarks/results/v0.6.23/release/windows-public-downloads.json)均通过：两份校验文件完整匿名下载，安装程序和便携ZIP各核对头尾64KiB Range，结合原生完整验收与服务端SHA256，不称为两个大包完整匿名回下载。主发行继续保持latest，Windows更新读取独立 `windows-v*`。

原生 CI 运行在 Windows Server 2025 runner；它与 Windows 10/11 各用户设备的人工使用分别登记。合成更新进度截图不证明真实网络升级完成，WebView2 已存在时的安装也不能替代缺失时首次离线补装验收。应用安装器未做代码签名。

## 下载文件

| 文件 | 字节 | SHA256 |
|---|---:|---|
| [story-workbench-0.2.2-windows-x86_64-setup.exe](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.2/story-workbench-0.2.2-windows-x86_64-setup.exe) | 299,297,827 | `a1660d77af7748a6997f60b5ed30fc0b376e19506d5eb23c10e9755fd803d89d` |
| [story-workbench-0.2.2-windows-x86_64.zip](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.2/story-workbench-0.2.2-windows-x86_64.zip) | 320,739,469 | `b34e67518528083bb08ff29f2252349824468bb91f9aa58f01cbf28fbd401a1c` |

两个文件各有同名 `.sha256`，完整下载后核对再运行。GitHub Packages 属于主Story Skill发行，已由主Release自动工作流同步；其注册表下载与独立核验见[主版本记录](v0.6.23.md#本轮验收状态)。

## 安装和数据保留

按[安装说明](../../desktop/windows/README.md)下载并核对 SHA256。安装前保存所有工作台与外部浏览器编辑、退出客户端，书稿保存在应用目录之外。安装器经确认后核验并正常停止相关服务，无法确认的占用会阻止继续；静默安装不代填保存确认。

覆盖安装保留相邻 `.previous-*` 旧目录，失败时尝试恢复；恢复受阻时保留备份并提示。卸载保留应用目录之外的书稿与共享 WebView2 运行时。不要运行备份目录中的旧卸载器回退当前安装。便携包须完整解压，缺少 WebView2 时按说明运行随包微软安装程序。

[项目技术约束](../技术约束.md) · [更新流程](../工作台更新流程.md)
