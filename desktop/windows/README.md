# 写作工作台 · Windows 独立应用候选

面向 Windows 10/11 x64。客户端使用 PySide6 / Qt WebEngine 桌面窗口，打包 Chromium 和 Python；使用者无需安装 Python、技能或浏览器扩展。当前为开发候选，尚未在真实 Windows 桌面验收，不属于已发布版本。

当前开发候选为 `0.1.3`。可运行 `story-workbench-0.1.3-windows-x86_64-setup.exe`，为当前用户安装并创建开始菜单快捷方式；安装向导可选创建桌面快捷方式，系统应用列表支持卸载。候选未做代码签名。

也可解压完整 ZIP 到可写目录，双击 `StoryWorkbench.exe`。请保留同目录的 `_internal`、`runtime`、`Scripts` 等文件。

书架和作品编辑页在应用标签中打开。点击“复制作品目录”选择目录后，将复制的路径粘贴到书架添加栏。下载使用系统保存面板。关闭、退出或重新载入时核对全部标签及文稿，有未保存文字时返回保存或完整导出，保存中或状态不明时阻止关闭；保存仍生成候选，正式采用沿用审稿流程。关闭应用后服务保留，重新打开会认证并复用既有书架。

书架的“外观”和编辑页的“设置 → 外观”支持浅色、深色及跟随系统。切换保留未保存的编辑，并记住选择；客户端只额外保存主题值，网页会话仍使用私密配置。

工具栏的“检查更新”在后台查询 GitHub 最新正式发布，比较 Windows x64 客户端安装包版本。有新版时可在应用内下载，查看百分比、大小、速度、预计剩余时间和 SHA256 校验状态，支持取消及失败重试。校验通过后启动安装向导前核对所有标签的编辑状态；便携 ZIP 只打开目录供手工解压替换。安装由系统向导接管，页面会提示保存、安装和重新打开，不虚报安装完成，也不结束作品服务。没有 Windows 正式安装包、校验信息不足、网络失败、超时或限流会明确提示，不据此判断已是最新。检查和下载只访问公开发布信息及安装文件，不上传书稿。详见[更新流程](../../docs/工作台更新流程.md)。

## 构建

在 Windows x64 的完整 Python 3.12 环境中，从仓库根目录运行：

```powershell
python -m venv .venv-desktop
.venv-desktop\Scripts\python -m pip install -r desktop/windows/requirements-build.txt
.venv-desktop\Scripts\python scripts/build_desktop_windows.py --output dist/story-workbench-windows-x86_64
```

也可运行 GitHub Actions 的 `Build Windows desktop candidate`，下载候选安装程序、便携 ZIP 与 SHA256。构建流程会执行运行时导入、CLI、安装、原生窗口、浅色／深色页面及卸载检查，并输出截图；这不代表所有用户环境的人工验收通过。安装构建依赖和手动检查更新需要联网，书稿服务在本机运行。安装程序由 Inno Setup 6 和 `installer.iss` 构建。

## 待完成的 Windows 验收

在未安装 Python 的 Windows 10/11 上验证首次启动、中文及空格目录、重复启动复用、添加和打开作品、候选保存及重开、版本对照、下载和取消、关闭提示、服务退出后重启，以及非管理员账户。核对 Qt/Python 依赖许可证和 Qt WebEngine 第三方声明后，才能作为公开分发附件发布。

Mac 独立应用继续使用 `desktop/macos` 的原生 Swift 客户端。

本轮候选内嵌运行时为 0.6.19，包含作品管理、回收站删除及整书备份恢复。关闭、退出、重新载入和跳转会核对全部标签及隐藏文稿；保存中或状态不明时阻止关闭，未保存文字可返回保存或完整导出 ZIP。重复打开作品复用原标签。操作和后续需求见[工作台需求与进展](../../docs/工作台需求与进展.md)。
