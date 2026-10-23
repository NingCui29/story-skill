# 写作工作台 · Windows

Windows 客户端 **0.2.0** 使用 **C# / WPF / .NET 10 / Microsoft WebView2**，内嵌 Story Skill **0.6.22** Python 作品服务。项目禁止使用 Qt 系列及 PHP，构建和安装验收包含对应检查。界面沿用 HTML、CSS 与 JavaScript，作品数据无需转换。

## 获取和安装

本版本构建产物名为 `story-workbench-0.2.0-windows-x86_64-setup.exe`，校验文件为同名 `.sha256`。从对应的 [Windows 构建记录](https://github.com/NingCui29/story-skill/actions/workflows/desktop-windows.yml) 获取，正式 Release 仅以实际发布的附件为准；旧版 Windows 附件不是本 C# 版本。

适用 Windows 10/11 x64。安装前保存工作台及外部浏览器中的编辑，退出旧客户端。双击安装程序，按向导为当前用户安装并创建快捷方式；系统应用列表支持卸载。应用未做代码签名。应用目录不用于存放书稿。

安装包自带 .NET 与 Python，无需另行安装这两者。WebView2 运行时已有时复用，缺失时使用随包微软签名的 Evergreen x64 离线安装程序安装；安装失败会明确中止。便携 ZIP 需完整解压并保留 `runtime`、`Scripts`、`Prerequisites` 及其余文件；如系统缺少 WebView2，先运行 `Prerequisites` 中的微软安装程序。

## 日常使用

- 书架搜索、筛选、新建、添加、移出、重新定位、回收站删除、整书备份与恢复仍使用原作品服务。
- 编辑页在应用标签中打开；重复打开复用原标签。返回书架不会重新载入未保存的编辑页。
- “外观”支持浅色、深色、跟随系统并保存偏好；主题色为天空蓝。
- 关闭标签、退出、刷新及启动更新前核对所有页面及隐藏文稿。保存中、恢复中、输入法组合中或状态未知时阻止操作；待保存文字可返回保存或导出完整 ZIP。
- “复制作品目录”使用系统目录选择器，导出与下载使用系统保存面板。
- “检查更新”查询官方正式 Windows x64 附件，显示下载进度、大小、速度、预计剩余时间，支持取消和重试；大小、SHA256 与文件身份通过后才允许启动安装程序。

保存候选、自动恢复副本和导出文件不等于正式采用。更新不会自动停止原有书架或作品服务；已有旧服务须在保留编辑后按原流程更新。平台草稿流程及其外部助手依赖保持原边界，不执行提交审核或定时发布。

## 构建与验证

需要 Windows x64、完整 Python 3.12、.NET 10 SDK 和 Inno Setup 6。WebView2 SDK 版本固定在 `StoryWorkbench/StoryWorkbench.csproj`。

```powershell
python scripts/check_forbidden_technologies.py --source
dotnet run --project tests/windows/StoryWorkbench.Tests.csproj
python scripts/build_desktop_windows.py --output dist/story-workbench-windows-x86_64
```

GitHub Actions 完成 C# 测试、作品服务安全探针、独立发布、安装器构建、中文及空格目录安装、原生窗口与外观验证、完整清单校验及卸载。安装包、校验文件、便携 ZIP 和验收回执作为 Actions 产物保存；常规构建不创建或公开 Release。

随包第三方许可来自实际还原的 WebView2 SDK、.NET 与 Python 分发载荷，并记录来源与摘要。完整文件清单包含自带的运行库与先决条件安装器。验收截图中的合成更新进度不代表真实网络升级成功；Windows runner 验收与不同用户系统的人工验收分别记录。

[项目技术约束](../../docs/技术约束.md) · [更新流程](../../docs/工作台更新流程.md)
