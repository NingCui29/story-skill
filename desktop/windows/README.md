# 写作工作台 · Windows

Windows 客户端 **0.2.1** 使用 **C# / WPF / .NET 10 / Microsoft WebView2**，内嵌 Story Skill **0.6.22** Python 作品服务。项目禁止使用 Qt 系列及 PHP，构建和安装验收包含对应检查。界面沿用 HTML、CSS 与 JavaScript，作品数据无需转换。

## 获取和安装

下载 [EXE 安装包](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe)及同名 [SHA256 校验文件](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe.sha256)。固定发行页为 [windows-v0.2.1](https://github.com/NingCui29/story-skill/releases/tag/windows-v0.2.1)。

建议使用受支持的 Windows 11 x64。实际原生验收在 Windows Server 2025 runner 完成，未逐一验证 Windows 10 各版本。安装前保存工作台及外部浏览器中的编辑，退出旧客户端。双击安装程序，按向导为当前用户安装，从开始菜单启动“写作工作台”；系统应用列表支持卸载。应用未做代码签名。应用目录不用于存放书稿。

在下载目录的 PowerShell 中执行以下命令，与 `.sha256` 文件中的摘要比较：

```powershell
Get-FileHash .\story-workbench-0.2.1-windows-x86_64-setup.exe -Algorithm SHA256
```

从 0.2.0 或更早版本升级，请使用固定下载链接。0.2.1 起，应用内检查独立的 `windows-v*` 正式发行版，并复用重新校验通过的已下载包。

安装包自带 .NET 与 Python，无需另行安装这两者。WebView2 运行时已有时复用，缺失时使用随包微软签名的 Evergreen x64 离线安装程序安装；安装失败会明确中止。便携 ZIP 需完整解压并保留 `runtime`、`Scripts`、`Prerequisites` 及其余文件；如系统缺少 WebView2，先运行 `Prerequisites` 中的微软安装程序。

## 日常使用

- 书架搜索、筛选、新建、添加、移出、重新定位、回收站删除、整书备份与恢复仍使用原作品服务。
- 编辑页在应用标签中打开；重复打开复用原标签。返回书架不会重新载入未保存的编辑页。
- “外观”支持浅色、深色、跟随系统并保存偏好；主题色为天空蓝。
- 关闭标签、退出、刷新及启动更新前核对所有页面及隐藏文稿。保存中、恢复中、输入法组合中或状态未知时阻止操作；待保存文字可返回保存或导出完整 ZIP。
- “复制作品目录”使用系统目录选择器，导出与下载使用系统保存面板。
- “检查更新”查询官方正式 Windows x64 附件，显示下载进度、大小、速度、预计剩余时间，支持取消和重试；大小、SHA256 与文件身份通过后才允许启动安装程序。

保存候选、自动恢复副本和导出文件不等于正式采用。平台草稿流程及外部助手依赖保持原边界，不执行提交审核或定时发布。

## 升级、卸载和异常恢复

安装或卸载前保存工作台及外部浏览器中的全部编辑，退出客户端。在向导中确认已保存后，安装器先认证相关后台服务、正常停止并等待文件释放；身份无法核对或仍有未知占用时停止操作。静默安装不会代替用户确认，须提前正常停止相关服务。

覆盖安装会将已核对的完整旧安装目录保留为同级 `<原目录>.previous-<唯一标识>`；安装失败时尝试恢复旧目录，未完成的新目录另行保留；若因占用、权限或身份核对失败无法自动恢复，会提示并保留备份。成功后备份继续保留，确认新版正常后可手工清理。书稿应始终存放在安装目录之外，卸载只处理当前应用。

异常退出后，客户端通过只读进程检查、原记录一致性和现存文件锁确认服务已退出，再重新启动。无法核对的状态会保留并提示，不按旧 PID 强制结束进程。首次打开失败的空标签可安全移除；已完成保存检查后加载失败的页面可重试。仍无法确认的编辑状态继续受到保护。

“上次打开作品”、阅读设置、布局和书架筛选跨启动保存；仅持久化经校验的作品标识和普通偏好，不保存正文、作品目录或含访问凭证的地址。网页与原生窗口共同响应浅色、深色及系统主题。

## 构建与验证

需要 Windows x64、完整 Python 3.12、.NET 10 SDK 和 Inno Setup 6。WebView2 SDK 版本固定在 `StoryWorkbench/StoryWorkbench.csproj`。

```powershell
python scripts/check_forbidden_technologies.py --source
dotnet run --project tests/windows/StoryWorkbench.Tests.csproj
python scripts/build_desktop_windows.py --output dist/story-workbench-windows-x86_64
```

GitHub Actions 完成 C# 测试、作品服务安全探针、独立发布、安装器构建、中文及空格目录安装、原生窗口与外观验证、完整清单校验及卸载。安装包、校验文件、便携 ZIP 和验收回执作为 Actions 产物保存；常规构建不创建或公开 Release。

每次正式发布的实际构建来源、Windows 执行记录和验证边界见 [0.2.1 发布记录](../../docs/releases/windows-v0.2.1.md)。

随包第三方许可来自实际还原的 WebView2 SDK、.NET 与 Python 分发载荷，并记录来源与摘要。完整文件清单包含自带的运行库与先决条件安装器。验收截图中的合成更新进度不代表真实网络升级成功；Windows runner 验收与不同用户系统的人工验收分别记录。

[项目技术约束](../../docs/技术约束.md) · [更新流程](../../docs/工作台更新流程.md)
