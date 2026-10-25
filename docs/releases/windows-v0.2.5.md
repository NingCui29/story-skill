# Windows 写作工作台 0.2.5

Windows **0.2.5** 使用 C# / WPF / .NET 10 / Microsoft WebView2，内嵌 Story Skill **0.6.26**。

本版修复番茄章号、章名输入后被页面恢复的问题：通过真实键盘输入、失焦提交和分阶段逐字核对稳定字段。出现变化立即停止，保留已有草稿 ID，未知结果先只读对账。详见 [Story Skill 0.6.26](v0.6.26.md)。

## 下载

- [x64 EXE 安装程序](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.5/story-workbench-0.2.5-windows-x86_64-setup.exe) · [SHA256](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.5/story-workbench-0.2.5-windows-x86_64-setup.exe.sha256)
- [x64 便携 ZIP](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.5/story-workbench-0.2.5-windows-x86_64.zip) · [SHA256](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.5/story-workbench-0.2.5-windows-x86_64.zip.sha256)

升级前保存全部工作台及外部浏览器编辑，正常退出旧客户端，核对校验文件后安装。书稿、书架及历史状态沿用原有位置。安装器内置 .NET、Python 和微软签名的 WebView2 离线安装程序；工作台及安装程序本身未做代码签名。

## 验证

本次 Windows 原生构建验证 0.2.4 → 0.2.5 升级、原生界面、完整分发和安装目录、未保存编辑保护与回滚。结果与运行链接在发布完成后补录。原生隔离环境的通过不代表所有 Windows 设备已人工验收；同步仍须逐篇独立完整回读。
