# Windows 写作工作台 0.2.3

Windows x64 独立应用，C# / WPF / WebView2，内嵌 Story Skill 0.6.24。安装包包含 .NET、Python 和微软 WebView2 离线安装器。

本版修复工作台草稿同步的三个问题，并让正式稿清单随本地正式状态刷新：

- **章名和正文一起更新**：在“保存平台草稿”中选择“同步更新已有草稿（章名和正文）”。按所选章节范围读取最新正式稿，核对已有番茄草稿后更新；修改前保留备份，保存后独立回读。
- **识别错字标记中的原文**：读取已知纠错标记时保持作者原文，完整重填正文清除标记；出现未知结构或回读仍含标记时停止，避免误报成功。
- **同步状态及时刷新**：读取最新持久化记录，区分历史记录与本次核对，旧失败任务不再长期覆盖新结果；正式稿有变化时刷新预览和章节范围。

## 平台草稿的实际范围

已有草稿同步目前限番茄受控 Chrome 通路。默认保存和只读对账不会覆盖；未知结果先对账，缺失草稿跳过，已发布章节和身份不明确的条目停止处理。软件测试使用隔离样例，本次发版没有执行作者的真实平台同步。

## 安装

1. 下载 [EXE 安装包](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.3/story-workbench-0.2.3-windows-x86_64-setup.exe) 和[校验文件](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.3/story-workbench-0.2.3-windows-x86_64-setup.exe.sha256)。
2. 用 PowerShell `Get-FileHash .\story-workbench-0.2.3-windows-x86_64-setup.exe -Algorithm SHA256` 核对摘要。
3. 保存所有工作台及外部浏览器编辑，正常退出旧客户端，再运行安装包，按向导为当前用户安装。
4. 从开始菜单启动“写作工作台”，书稿保存在应用目录之外。

建议使用受支持的 Windows 11 x64。安装器未做代码签名；不包含 Qt 系列或 PHP。更多信息见 [Windows 安装说明](../../desktop/windows/README.md)。

## 本轮验证

[原生构建与验收](https://github.com/NingCui29/story-skill/actions/runs/40081716567)通过：39 项 C# 测试、1125 个便携和实际安装文件、运行时安全探针、原生明暗主题、合成下载进度、未保存保护、安装回滚、卸载和上一正式版 0.2.2 升级。合成进度验收不等于下载真实更新。

[三环境完整源码 CI](https://github.com/NingCui29/story-skill/actions/runs/40082295219)和实际打包源的对应关系见 [v0.6.24 发布记录](v0.6.24.md)。安装器 SHA256 为 `5c2f5d49e00de9e3242618d40bf75d49c11e7b6c25d5327e42868ab4bf9a9903`。

公开下载检查完整下载校验文件，对 EXE 与便携 ZIP 做匿名首尾采样，并核对原生完整验证和 GitHub 全文件摘要；没有声称在本机完整重新下载 Windows 二进制。见[下载回执](../../benchmarks/results/v0.6.24/release/windows-public-downloads.json)。
