using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;

namespace StoryWorkbench;

public sealed class MainWindow : Window
{
    public const string ClientVersion = "0.2.0";
    private readonly LaunchOptions options;
    private readonly BackendResult backend;
    private readonly ThemePreferences preferences;
    private readonly TabControl tabs = new();
    private readonly StackPanel toolbar = new() { Orientation = Orientation.Horizontal, Margin = new Thickness(8) };
    private readonly TextBlock status = new() { Margin = new Thickness(12, 4, 12, 6), TextWrapping = TextWrapping.Wrap };
    private readonly List<BrowserTab> pages = [];
    private readonly SemaphoreSlim themeGate = new(1, 1);
    private readonly string profileDirectory = Path.Combine(Path.GetTempPath(), "StoryWorkbench-" + Guid.NewGuid().ToString("N"));
    private CoreWebView2Environment? environment;
    private bool closeApproved;
    private bool closingRequested;
    private int exitCode;
    public bool ClosePending { get; private set; }
    public string Theme => preferences.Theme;

    public MainWindow(LaunchOptions options, BackendResult backend)
    {
        this.options = options; this.backend = backend;
        preferences = new(options.SelfTestReport is null ? null : options.StateDirectory);
        Title = "写作工作台"; Width = 1280; Height = 860; MinWidth = 800; MinHeight = 560;
        Background = new SolidColorBrush(Color.FromRgb(244, 248, 252));
        Foreground = new SolidColorBrush(Color.FromRgb(20, 53, 75));
        var layout = new DockPanel(); Content = layout;
        DockPanel.SetDock(toolbar, Dock.Top); layout.Children.Add(toolbar);
        DockPanel.SetDock(status, Dock.Bottom); layout.Children.Add(status); layout.Children.Add(tabs);
        AddButton("书架", async () => { if (pages.Count != 0) { tabs.SelectedItem = pages[0].Item; await NotifyShelfAsync(); } });
        AddButton("复制作品目录", () => { CopyDirectory(); return Task.CompletedTask; });
        AddButton("刷新当前页面", RefreshAsync);
        AddButton("检查更新", () => UpdateWindow.ShowAsync(this));
        tabs.SelectionChanged += async (_, e) => { if (e.Source == tabs) await NotifyShelfAsync(); };
        PreviewKeyDown += async (_, e) =>
        {
            if (e.Key == Key.F5 || (e.Key == Key.R && Keyboard.Modifiers == ModifierKeys.Control))
            { e.Handled = true; await RefreshAsync(); }
        };
        Closing += OnClosing;
        Closed += (_, _) =>
        {
            foreach (var page in pages) page.Dispose();
            pages.Clear();
            // InPrivate prevents document/capability persistence; cleanup only targets this process's random profile.
            try { Directory.Delete(profileDirectory, recursive: true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            Application.Current.Shutdown(exitCode);
        };
    }

    private void AddButton(string label, Func<Task> action)
    {
        var button = new Button { Content = label, Margin = new Thickness(3), Padding = new Thickness(12, 5, 12, 5) };
        button.Click += async (_, _) =>
        {
            if (ClosePending) return;
            try { await action(); }
            catch { ShowStatus("操作未完成，当前页面仍保留。请稍后重试。"); }
        };
        toolbar.Children.Add(button);
    }

    public async Task InitializeAsync()
    {
        Directory.CreateDirectory(profileDirectory);
        var environmentOptions = new CoreWebView2EnvironmentOptions
        {
            // Passwords, history and capability-bearing addresses must not enter a persistent user profile.
            AdditionalBrowserArguments = "--disable-features=msEdgeSidebarV2"
        };
        environment = await CoreWebView2Environment.CreateAsync(null, profileDirectory, environmentOptions);
        await OpenPageAsync(backend.Url);
        if (options.SelfTestReport is not null)
            await SelfTestAsync().WaitAsync(TimeSpan.FromSeconds(70));
        else if (backend.Outdated)
            MessageBox.Show(this, backend.Message, "书架版本提示", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    public void ShowStatus(string text) => status.Text = text;

    public async Task<BrowserTab?> OpenPageAsync(string address)
    {
        if (ClosePending || environment is null) return null;
        var key = BrowserSafety.PageKeyFor(address);
        if (key is null) return null;
        var existing = pages.FirstOrDefault(p => p.Key == key);
        if (existing is not null)
        {
            tabs.SelectedItem = existing.Item;
            await existing.NavigateFragmentAsync(address);
            return existing;
        }
        var page = new BrowserTab(this, address, shelf: pages.Count == 0);
        pages.Add(page); tabs.Items.Add(page.Item); tabs.SelectedItem = page.Item;
        await page.InitializeAsync(environment);
        return page;
    }

    public async Task RememberThemeAsync(string? value)
    {
        if (value is not ("system" or "light" or "dark")) return;
        await themeGate.WaitAsync();
        try
        {
            preferences.Save(value);
            bool dark = value == "dark";
            Background = new SolidColorBrush(dark ? Color.FromRgb(21, 30, 37) : Color.FromRgb(244, 248, 252));
            Foreground = new SolidColorBrush(dark ? Color.FromRgb(224, 235, 244) : Color.FromRgb(20, 53, 75));
            foreach (var page in pages.ToArray()) await page.SetThemeSeedAsync(value);
        }
        catch (IOException) { ShowStatus("外观已切换，偏好未能写入磁盘；下次启动可重新选择。"); }
        catch (UnauthorizedAccessException) { ShowStatus("外观偏好无法写入，请检查用户目录权限。"); }
        finally { themeGate.Release(); }
    }

    private async Task NotifyShelfAsync()
    {
        if (ClosePending || tabs.SelectedItem is not TabItem { Tag: BrowserTab page } || page.Key.Path != "/" || !page.LoadComplete) return;
        try
        {
            await page.ExecuteAsync("window.dispatchEvent(new Event('story-shelf-return'));true");
        }
        catch { }
    }

    public async Task CloseTabAsync(BrowserTab page)
    {
        if (pages.IndexOf(page) <= 0) return;
        await RunProtectedAsync("关闭页面", () =>
        {
            tabs.Items.Remove(page.Item); pages.Remove(page); page.Dispose();
            return Task.CompletedTask;
        });
    }

    private async Task RefreshAsync()
    {
        if (tabs.SelectedItem is not TabItem { Tag: BrowserTab page } || page.Disposed) return;
        string? source = page.View.CoreWebView2?.Source;
        if (source is not null) await RunProtectedAsync("重新载入", () => page.NavigateApprovedAsync(source));
    }

    /// <summary>Execute only while all retained documents are frozen and their complete states are verified.</summary>
    public async Task<bool> RunProtectedAsync(string action, Func<Task> operation)
    {
        if (ClosePending) return false;
        ClosePending = true;
        toolbar.IsEnabled = false; tabs.IsEnabled = false;
        var capturedPages = pages.ToArray();
        string token = Guid.NewGuid().ToString("N");
        var frozen = new List<BrowserTab>();
        try
        {
            // Disable native interaction before awaiting the renderer; inert/capture handlers also cover WebView airspace.
            foreach (var page in capturedPages) page.View.IsEnabled = false;
            foreach (var page in capturedPages)
            {
                if (!page.LoadComplete || !await page.ExecuteBooleanAsync(BrowserSafety.FreezeScript(token)))
                    return Block(page, "暂时无法核对编辑状态", "页面尚未完整载入，本次" + action + "已取消。请返回作品检查。");
                frozen.Add(page);
            }
            var snapshots = await ReadAllAsync(capturedPages);
            if (snapshots is null) return false;
            int pending = snapshots.Sum(s => s.State.Pending.Count);
            if (pending > 0)
            {
                tabs.SelectedItem = capturedPages[Array.FindIndex(snapshots, s => s.State.Pending.Count > 0)].Item;
                if (!ConfirmExport(pending, action)) return false;
                var choose = new SaveFileDialog { Title = "导出全部待保存文字", FileName = "工作台待保存文字.zip", Filter = "ZIP 文件|*.zip", DefaultExt = ".zip", AddExtension = true, OverwritePrompt = true };
                if (choose.ShowDialog(this) != true) return false;
                // The user's decision may take time. Re-read every hidden buffer after the dialog.
                snapshots = await ReadAllAsync(capturedPages);
                if (snapshots is null) return false;
                BrowserSafety.ExportPendingBuffers(choose.FileName, snapshots.Select(s => s.State));
            }
            if (capturedPages.Length != pages.Count || !capturedPages.SequenceEqual(pages))
                return Block(null, "页面发生变化", "标签已变化，本次操作已取消。当前文字仍保留。");
            var final = await ReadAllAsync(capturedPages);
            if (final is null) return false;
            for (int i = 0; i < snapshots.Length; i++)
                if (!BrowserSafety.CanCommit(snapshots[i], final[i]))
                    return Block(capturedPages[i], "编辑状态发生变化", "核对期间有内容变化，本次操作已取消。请返回作品保存；已有导出文件仍保留。");
            // Keep both native input and renderer input frozen until the caller's destructive step finishes.
            await operation();
            return true;
        }
        catch (Exception error)
        {
            return Block(null, "本次操作未完成", error is IOException or ArgumentException or InvalidOperationException
                ? error.Message + " 当前页面仍保留。" : "无法完整核对全部文字。当前页面仍保留，请返回作品保存。");
        }
        finally
        {
            foreach (var page in frozen)
                if (!page.Disposed) { try { await page.ExecuteAsync(BrowserSafety.ThawScript(token)); } catch { } }
            foreach (var page in capturedPages) if (!page.Disposed) page.View.IsEnabled = true;
            ClosePending = false; toolbar.IsEnabled = true; tabs.IsEnabled = true;
        }
    }

    private async Task<SafetySnapshot[]?> ReadAllAsync(BrowserTab[] selected)
    {
        var snapshots = new List<SafetySnapshot>();
        foreach (var page in selected)
        {
            var snapshot = await page.ProbeAsync();
            if (snapshot is null)
            { Block(page, "暂时无法核对编辑状态", "页面未返回完整状态。为保留文字，本次操作已取消。"); return null; }
            if (snapshot.State.Busy || snapshot.State.Saving || snapshot.State.Composing)
            {
                Block(page, "编辑操作尚未完成", snapshot.State.Composing ? "请先完成中文输入，再重试。" : "页面正在载入、保存或写入恢复稿，请等待完成后重试。");
                return null;
            }
            snapshots.Add(snapshot);
        }
        return snapshots.ToArray();
    }

    private bool Block(BrowserTab? page, string title, string message)
    {
        if (page is not null && !page.Disposed) tabs.SelectedItem = page.Item;
        MessageBox.Show(this, message, title, MessageBoxButton.OK, MessageBoxImage.Warning);
        return false;
    }

    private bool ConfirmExport(int count, string action)
    {
        bool accepted = false;
        var dialog = new Window { Owner = this, Title = "还有待保存文字", Width = 500, SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner, ResizeMode = ResizeMode.NoResize, Background = Background, Foreground = Foreground };
        var panel = new StackPanel { Margin = new Thickness(20) }; dialog.Content = panel;
        panel.Children.Add(new TextBlock { Text = $"全部作品页面中有 {count} 份待保存文字。", TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(new TextBlock { Text = "请返回保存候选稿，或先导出文字再" + action + "。恢复副本和导出都不等于已保存候选，也未正式采用。",
            TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 16) });
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right }; panel.Children.Add(row);
        var back = new Button { Content = "返回作品保存", IsCancel = true, IsDefault = true, Padding = new Thickness(10, 6, 10, 6) };
        var export = new Button { Content = "导出文字后" + action, Margin = new Thickness(12, 0, 0, 0), Padding = back.Padding };
        back.Click += (_, _) => dialog.Close();
        export.Click += (_, _) => { accepted = true; dialog.Close(); };
        row.Children.Add(back); row.Children.Add(export); dialog.ShowDialog();
        return accepted;
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (closeApproved) return;
        e.Cancel = true;
        if (ClosePending || closingRequested) return;
        closingRequested = true;
        try
        {
            foreach (var update in OwnedWindows.OfType<UpdateWindow>().ToArray()) await update.CancelAndWaitAsync();
            await RunProtectedAsync("退出工作台", () => { closeApproved = true; Close(); return Task.CompletedTask; });
        }
        catch { ShowStatus("更新任务仍在收尾，当前窗口和作品已保留，请稍后退出。"); }
        finally { closingRequested = false; }
    }

    private void CopyDirectory()
    {
        var dialog = new OpenFolderDialog { Title = "选择作品目录", Multiselect = false };
        if (dialog.ShowDialog(this) == true)
        { Clipboard.SetText(dialog.FolderName); ShowStatus("目录已复制，可粘贴到书架的添加目录栏。"); }
    }

    public static void OpenExternal(string address)
    {
        if (!BrowserSafety.IsExternalUrl(address)) return;
        try { Process.Start(new ProcessStartInfo(address) { UseShellExecute = true }); } catch { }
    }

    private async Task SelfTestAsync()
    {
        string reportPath = options.SelfTestReport!;
        var first = pages[0];
        bool shelf = await first.ExecuteBooleanAsync("!!document.getElementById('library-heading')");
        var captures = new Dictionary<string, bool>();
        string original = Theme;
        foreach (string theme in new[] { "light", "dark" })
        {
            bool applied = await SelectThemeAsync(first, theme);
            await Task.Delay(400);
            string path = Path.ChangeExtension(reportPath, null) + "-" + theme + ".png";
            await first.CaptureAsync(path);
            captures[theme] = applied && new FileInfo(path).Length > 0;
            if (theme == "light") File.Copy(path, Path.ChangeExtension(reportPath, ".png"), overwrite: true);
        }
        bool restored = await SelectThemeAsync(first, original);
        bool progress = await UpdateWindow.CaptureSyntheticAsync(this, Path.ChangeExtension(reportPath, null) + "-update-progress.png");
        bool safety = await NativeSafetySelfTestAsync();
        bool passed = shelf && captures.Values.All(v => v) && restored && progress && safety;
        Directory.CreateDirectory(Path.GetDirectoryName(reportPath)!);
        await File.WriteAllTextAsync(reportPath, JsonSerializer.Serialize(new
        {
            ok = passed, native_window = IsVisible, shelf_rendered = shelf, title = first.Title,
            captured = captures.Values.All(v => v), client_version = ClientVersion, ui_stack = "wpf-webview2",
            self_contained = true, in_private = pages.All(p => p.IsInPrivate),
            update_check_available = true, update_progress_verified = progress, update_progress_synthetic = true,
            themes_verified = restored && captures.Values.All(v => v), theme_captures = captures,
            native_close_probe_verified = safety, hidden_buffers_verified = safety, ime_guard_verified = safety,
            buffer_export_verified = safety, synthetic_editor_fixture = true
        }), new UTF8Encoding(false));
        exitCode = passed ? 0 : 2; closeApproved = true; Close();
    }

    private static Task<bool> SelectThemeAsync(BrowserTab page, string theme) => page.ExecuteBooleanAsync("""
        (()=>{const value=__THEME__, control=document.getElementById('workbench-theme');
          if(!control)return false;control.value=value;control.dispatchEvent(new Event('change',{bubbles:true}));
          return control.value===value&&document.documentElement.getAttribute('data-theme-preference')===value
            &&(value==='system'||document.documentElement.getAttribute('data-theme')===value)})()
        """.Replace("__THEME__", JsonSerializer.Serialize(theme)));

    private async Task<bool> NativeSafetySelfTestAsync()
    {
        // This server and its document exist only in the explicit isolated native self-test.
        await using var synthetic = new SyntheticEditor();
        var page = await OpenPageAsync(synthetic.Url) ?? throw new IOException("合成编辑器未创建。");
        try
        {
            var initial = await page.ProbeAsync();
            if (initial is null || initial.State.Pending.Count != 2 || BrowserSafety.CanProceed(initial.State)) return false;
            await page.ExecuteAsync("window.dispatchEvent(new CompositionEvent('compositionstart'));true");
            var composing = await page.ProbeAsync();
            if (composing?.State.Composing != true || BrowserSafety.CanProceed(composing.State)) return false;
            await page.ExecuteAsync("window.dispatchEvent(new CompositionEvent('compositionend'));docs.get('hidden').saving=true;true");
            var saving = await page.ProbeAsync();
            if (saving?.State.Saving != true || BrowserSafety.CanProceed(saving.State)) return false;
            await page.ExecuteAsync("docs.get('hidden').saving=false;true");
            string token = Guid.NewGuid().ToString("N");
            if (!await page.ExecuteBooleanAsync(BrowserSafety.FreezeScript(token))) return false;
            var snapshot = await page.ProbeAsync();
            if (snapshot is null || snapshot.State.Pending.Count != 2) return false;
            string export = Path.Combine(options.StateDirectory!, "native-synthetic-unsaved.zip");
            BrowserSafety.ExportPendingBuffers(export, new[] { snapshot.State });
            var same = await page.ProbeAsync();
            if (same is null || !BrowserSafety.CanCommit(snapshot, same)) return false;
            await page.ExecuteAsync("docs.get('hidden').value+='变更';true");
            var changed = await page.ProbeAsync();
            if (changed is null || BrowserSafety.CanCommit(snapshot, changed)) return false;
            await page.ExecuteAsync(BrowserSafety.ThawScript(token));
            return File.Exists(export);
        }
        finally { pages.Remove(page); tabs.Items.Remove(page.Item); page.Dispose(); tabs.SelectedItem = pages[0].Item; }
    }

    private sealed class SyntheticEditor : IAsyncDisposable
    {
        private readonly TcpListener listener = new(IPAddress.Loopback, 0);
        private readonly CancellationTokenSource cancellation = new();
        private readonly Task serving;
        public string Url { get; }
        public SyntheticEditor()
        {
            listener.Start();
            Url = $"http://127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}/{Guid.NewGuid():N}/";
            serving = ServeAsync();
        }
        private async Task ServeAsync()
        {
            const string html = """
                <!doctype html><html><head><meta charset="utf-8"><title>隔离关闭保护自检</title></head>
                <body><div id="current-book-root">仅合成材料</div><textarea id="text">当前未保存中文</textarea>
                <script>const docs=new Map([['active',{editable:true,text:'原稿',value:'原稿',editing:true,title:'当前测试'}],
                  ['hidden',{editable:true,text:'旧文',value:'隐藏未保存中文',editing:false,title:'隐藏测试'}]]);let active='active';</script></body></html>
                """;
            byte[] body = Encoding.UTF8.GetBytes(html);
            try
            {
                while (!cancellation.IsCancellationRequested)
                {
                    using var client = await listener.AcceptTcpClientAsync(cancellation.Token);
                    using var stream = client.GetStream();
                    var buffer = new byte[8192];
                    await stream.ReadAsync(buffer, cancellation.Token);
                    byte[] header = Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {body.Length}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n");
                    await stream.WriteAsync(header, cancellation.Token); await stream.WriteAsync(body, cancellation.Token);
                }
            }
            catch (Exception e) when (e is OperationCanceledException or SocketException or IOException) { }
        }
        public async ValueTask DisposeAsync()
        { cancellation.Cancel(); listener.Stop(); await serving; cancellation.Dispose(); }
    }
}
