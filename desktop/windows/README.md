# 写作工作台 · Windows 独立应用候选

面向 Windows 10/11 x64。客户端使用 PySide6 / Qt WebEngine 桌面窗口，打包 Chromium 和 Python；使用者无需安装 Python、技能或浏览器扩展。当前为开发候选，尚未在真实 Windows 桌面验收，不属于已发布版本。

可运行 `story-workbench-windows-x86_64-setup.exe`，为当前用户安装并创建开始菜单快捷方式；安装向导可选创建桌面快捷方式，系统应用列表支持卸载。候选未做代码签名。

也可解压完整 ZIP 到可写目录，双击 `StoryWorkbench.exe`。请保留同目录的 `_internal`、`runtime`、`Scripts` 等文件。

书架和作品编辑页在应用标签中打开。点击“复制作品目录”选择目录后，将复制的路径粘贴到书架添加栏。下载使用系统保存面板。关闭标签或窗口会提示先保存；保存仍生成候选，正式采用沿用审稿流程。关闭应用后服务保留，重新打开会认证并复用既有书架。

## 构建

在 Windows x64 的完整 Python 3.12 环境中，从仓库根目录运行：

```powershell
python -m venv .venv-desktop
.venv-desktop\Scripts\python -m pip install -r desktop/windows/requirements-build.txt
.venv-desktop\Scripts\python scripts/build_desktop_windows.py --output dist/story-workbench-windows-x86_64
```

也可运行 GitHub Actions 的 `Build Windows desktop candidate`，下载候选安装程序、ZIP 与 SHA256。构建内含运行时导入、CLI、安装和卸载检查；这不代表原生界面验收通过。安装构建依赖需要联网，应用本身只连接本机工作台服务。安装程序由 Inno Setup 6 和 `installer.iss` 构建。

## 待完成的 Windows 验收

在未安装 Python 的 Windows 10/11 上验证首次启动、中文及空格目录、重复启动复用、添加和打开作品、候选保存及重开、版本对照、下载和取消、关闭提示、服务退出后重启，以及非管理员账户。核对 Qt/Python 依赖许可证和 Qt WebEngine 第三方声明后，才能作为公开分发附件发布。

Mac 独立应用继续使用 `desktop/macos` 的原生 Swift 客户端。
