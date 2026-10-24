# Windows 工作台 0.2.1

C# / WPF / WebView2 客户端，内置 Story Skill 0.6.22 Python 作品服务。该版本修复 Windows 使用审查中的九项问题。

## 下载

- [EXE 安装包](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe)
- [SHA256 校验文件](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe.sha256)
- [安装和升级说明](../../desktop/windows/README.md)

从 0.2.0 或更早版本使用上述固定链接升级。0.2.1 起，应用内检查独立 Windows 正式发布，Story Skill 的 latest 入口保持独立。

## 修复内容

1. 安装、升级、卸载先核对所有相关进程和服务。保存确认后正常停止，静默操作不代填确认；完整旧目录先保留，失败时尝试恢复旧文件、卸载登记与快捷方式；恢复受阻时保留备份并提示处理。
2. 异常退出通过只读进程与现存锁核验恢复，保留原服务记录和作品；Windows 临时目录句柄冲突只重试同一次原子重命名，保持备份、哈希核对和禁止覆盖。
3. 初次加载失败的空标签安全移除；通过保存核验后载入失败可重试。无法确认的编辑状态仍受保护。
4. 书架表单、备份恢复、状态保存与输入法组合纳入原生刷新/关闭保护。
5. 上次作品、阅读、布局与筛选偏好跨启动保存，仅持久化白名单数据。
6. 更新检查识别独立 `windows-v*` 正式发行。
7. 安装状态不再永久锁住重试入口，安装前完成保存保护并正常退出宿主。
8. 已下载完整包按官方元数据重新校验复用，下载或复用时清理可确认归属且未被占用的过期和多余缓存。
9. 原生窗口与网页共同响应浅色、深色和系统主题；更新窗口使用动态主题实色背景，并核验文字对比度。

## 构建与验收

安装包由固定提交 [`10fc6f0c09c6f35bbd814210d922cac664c61452`](https://github.com/NingCui29/story-skill/commit/10fc6f0c09c6f35bbd814210d922cac664c61452) 构建。

- [Windows 构建与安装验收](https://github.com/NingCui29/story-skill/actions/runs/40050935023)：通过。包含 39 项 C# 合约测试（无跳过）、Windows 跨进程文件占用与原子写入测试、真实 WebView2 原生窗口、明暗主题、偏好跨启动恢复、失败标签恢复及未保存状态保护。
- 实际安装到含中文和空格的目录，从正式 0.2.0 升级到 0.2.1，核对完整旧目录保留；运行真实服务交接、安装互斥、静默操作拒绝、Inno 安装失败回退、未登记文件保留及卸载后进程清理验收。
- 核对便携载荷和实际安装目录中的 1125 个清单文件，确认 C# / WPF / WebView2、自包含 .NET、Python 服务与构建源一致，并通过项目禁止技术检查。
- [源码全量验证](https://github.com/NingCui29/story-skill/actions/runs/40050935033)：Linux 3.10、Windows 3.10 和 Windows 3.12 最终均通过；每组运行 2528 项回归，Linux 按平台跳过 46 项，两个 Windows 环境各跳过 68 项。全部后续打包和端到端验收也已完成。

首次 Windows 3.12 全量执行中，`test_reconcile_mode_is_bound_to_preview_authorization` 触及模拟浏览器夹具的 6 秒等待上限；该用例在同一任务的前置检查已通过。[首次失败记录](https://github.com/NingCui29/story-skill/actions/runs/40050935033/job/120743224106)保留，未改变程序、测试或等待条件；[同一提交的完整复验](https://github.com/NingCui29/story-skill/actions/runs/40050935033/job/120751166180)通过，覆盖整个 Windows 3.12 任务，包含全部回归及后续打包、CLI、中文事务和历史修订验收。

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `story-workbench-0.2.1-windows-x86_64-setup.exe` | 299268857 | `736adaf46845bb72763a77cbe7bc11892f556d676703cdef38a70b69a7ef53dd` |

发行标签提交只在上述构建提交之后补充本发布记录；[源码等价核验](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/release-source-equivalence.json) 逐项证明程序、运行时、打包、工作流和验收输入没有变化。[分发核验](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/windows-distribution-verification.json)、[构建清单](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/windows-build-manifest.json)、[安装回退结果](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/windows-installation-lifecycle.json)及[跨版本升级结果](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/windows-previous-upgrade.json)随正式发行提供。

原生验收环境为 Windows Server 2025 runner，不等于所有 Windows 10/11 设备的人工验收。编辑保护使用合成编辑状态；更新窗口截图使用合成下载进度，不表示真实网络升级完成。缺少 WebView2 时的首次离线补装未在本次 runner 单独实测。应用安装器未做代码签名。测试安装器的编译期故障注入只用于隔离 CI，不进入正式 EXE 或 Release 附件。

## 安装和保留数据

安装前保存工作台及外部浏览器中的编辑并退出旧客户端，升级时选择原安装目录。安装向导在确认保存后核验并正常停止相关服务；遇到无法核验的进程会拒绝继续。成功升级后，相邻 `.previous-*` 完整旧版备份继续保留并占用磁盘，可在核对新版和书稿后自行清理。不要运行备份目录中的旧卸载器来回退当前安装。

卸载会保留这些备份、应用目录之外的作者书稿及共享 WebView2 运行时。便携包不会自动安装先决条件，缺少 WebView2 时需按安装说明运行随包的微软安装程序。
