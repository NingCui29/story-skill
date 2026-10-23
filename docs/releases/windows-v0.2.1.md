# Windows 工作台 0.2.1

C# / WPF / WebView2 客户端，内置 Story Skill 0.6.22 Python 作品服务。该版本修复 Windows 使用审查中的九项问题。

## 下载

- [EXE 安装包](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe)
- [SHA256 校验文件](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.1/story-workbench-0.2.1-windows-x86_64-setup.exe.sha256)
- [安装和升级说明](../../desktop/windows/README.md)

从 0.2.0 或更早版本使用上述固定链接升级。0.2.1 起，应用内检查独立 Windows 正式发布，Story Skill 的 latest 入口保持独立。

## 修复内容

1. 安装、升级、卸载先核对所有相关进程和服务。保存确认后正常停止，静默操作不代填确认；完整旧目录先保留，失败时尝试恢复旧文件、卸载登记与快捷方式；恢复受阻时保留备份并提示处理。
2. 异常退出通过只读进程与现存锁核验恢复，保留原服务记录和作品。
3. 初次加载失败的空标签安全移除；通过保存核验后载入失败可重试。无法确认的编辑状态仍受保护。
4. 书架表单、备份恢复、状态保存与输入法组合纳入原生刷新/关闭保护。
5. 上次作品、阅读、布局与筛选偏好跨启动保存，仅持久化白名单数据。
6. 更新检查识别独立 `windows-v*` 正式发行。
7. 安装状态不再永久锁住重试入口，安装前完成保存保护并正常退出宿主。
8. 已下载完整包按官方元数据重新校验复用，定期清理可确认归属且未被占用的过期和多余缓存。
9. 原生窗口与网页共同响应浅色、深色和系统主题。

## 构建与验收

正式发布前补入本次固定提交、Windows 构建记录、安装包摘要和验收结果。测试安装器的编译期故障注入只用于隔离 CI，不进入正式 EXE 或 Release 附件。

验收区分 C# 合约、真实 WebView2、合成编辑状态、真实安装服务、跨版本安装、Inno 失败回退及完整分发清单。Windows Server runner 的结果不等于所有 Windows 10/11 设备的人工验收；缺少 WebView2 的首次离线补装仍单独列明。应用安装器未做代码签名。
