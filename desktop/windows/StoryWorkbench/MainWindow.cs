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
    public const string ClientVersion = "0.2.1";
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
    private bool selfTestSuppressPrompts;
    private int exitCode;
    public bool ClosePending { get; private set; }
    public string Theme => preferences.Theme;
    public IReadOnlyDictionary<string, string> StoredPreferences => preferences.Storage;

    public MainWindow(LaunchOptions options, BackendResult backend)
    {
        this.options = options; this.backend = backend;
        preferences = new(options.SelfTestReport is null ? null : options.StateDirectory);
        Title = "写作工作台"; Width = 1280; Height = 860; MinWidth = 800; MinHeight = 560;
        ApplyNativeTheme();
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
            await SelfTestAsync().WaitAsync(TimeSpan.FromSeconds(120));
        else if (backend.Outdated)
            MessageBox.Show(this, backend.Message, "书架版本提示", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    public void ShowStatus(string text) => status.Text = text;

    public void CloseForUpdate()
    {
        if (!ClosePending) throw new InvalidOperationException("安装前必须完成全部页面保存核验。");
        closeApproved = true; Close();
    }

    private void ApplyNativeTheme()
    {
        // .NET's Fluent theme applies to controls, owned dialogs and subsequent Windows appearance changes.
#pragma warning disable WPF0001
        Application.Current.ThemeMode = Theme switch { "dark" => System.Windows.ThemeMode.Dark, "light" => System.Windows.ThemeMode.Light, _ => System.Windows.ThemeMode.System };
#pragma warning restore WPF0001
    }

    public async Task<BrowserTab?> OpenPageAsync(string address)
    {
        if (ClosePending || environment is null) return null;
        var key = BrowserSafety.PageKeyFor(address);
        if (key is null) return null;
        var existing = pages.FirstOrDefault(p => p.Key == key);
        if (existing is not null)
        {
            tabs.SelectedItem = existing.Item;
            if (existing.CanDiscardFailed) await RetryPageAsync(existing);
            else await existing.NavigateFragmentAsync(address);
            return existing;
        }
        var page = new BrowserTab(this, address, shelf: pages.Count == 0);
        pages.Add(page); tabs.Items.Add(page.Item); tabs.SelectedItem = page.Item;
        try { await page.InitializeAsync(environment); }
        catch
        {
            page.MarkInitializationFailed();
            if (page.CanDiscardFailed) { pages.Remove(page); tabs.Items.Remove(page.Item); page.Dispose(); }
            throw;
        }
        return page;
    }

    public async Task RememberThemeAsync(string? value)
    {
        if (value is not ("system" or "light" or "dark")) return;
        await themeGate.WaitAsync();
        try
        {
            preferences.Save(value);
            ApplyNativeTheme();
            foreach (var page in pages.ToArray()) await page.SetThemeSeedAsync(value);
        }
        catch (IOException) { ShowStatus("外观已切换，偏好未能写入磁盘；下次启动可重新选择。"); }
        catch (UnauthorizedAccessException) { ShowStatus("外观偏好无法写入，请检查用户目录权限。"); }
        catch (Exception error) when (error is InvalidOperationException or System.Runtime.InteropServices.COMException)
        { ShowStatus("个别页面外观同步未完成，页面和已保存的偏好仍保留。"); }
        finally { themeGate.Release(); }
    }

    public async Task RememberPreferenceAsync(string? key, string? value)
    {
        if (key is null) return;
        await themeGate.WaitAsync();
        try
        {
            if (!preferences.SaveStorage(key, value)) return;
            foreach (var page in pages.ToArray()) await page.SetThemeSeedAsync(Theme);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { ShowStatus("普通显示偏好未能写入磁盘，本次页面仍保留。"); }
        catch (Exception error) when (error is InvalidOperationException or System.Runtime.InteropServices.COMException)
        { ShowStatus("偏好已记录，个别页面的初始化设置将在重新打开后应用。"); }
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
        if (page.CanDiscardFailed) { await RetryPageAsync(page); return; }
        string? source = page.View.CoreWebView2?.Source;
        if (source is not null) await RunProtectedAsync("重新载入", () => page.NavigateApprovedAsync(source));
    }

    public Task<bool> RetryPageAsync(BrowserTab page) => RunProtectedAsync("重试载入", () => page.NavigateApprovedAsync(page.EntryAddress));

    /// <summary>Execute only while all retained documents are frozen and their complete states are verified.</summary>
    public async Task<bool> RunProtectedAsync(string action, Func<Task> operation)
    {
        if (ClosePending) return false;
        ClosePending = true;
        toolbar.IsEnabled = false; tabs.IsEnabled = false;
        var capturedPages = pages.ToArray();
        var emptyPages = capturedPages.Where(page => page.CanDiscardFailed).ToDictionary(page => page, page => page.Generation);
        var checkedPages = capturedPages.Where(page => !emptyPages.ContainsKey(page)).ToArray();
        string token = Guid.NewGuid().ToString("N");
        var frozen = new List<BrowserTab>();
        try
        {
            // Disable native interaction before awaiting the renderer; inert/capture handlers also cover WebView airspace.
            foreach (var page in capturedPages) page.View.IsEnabled = false;
            foreach (var page in checkedPages)
            {
                if (!page.LoadComplete || !await page.ExecuteBooleanAsync(BrowserSafety.FreezeScript(token)))
                    return Block(page, "暂时无法核对编辑状态", "页面尚未完整载入，本次" + action + "已取消。请返回作品检查。");
                frozen.Add(page);
            }
            var snapshots = await ReadAllAsync(checkedPages);
            if (snapshots is null) return false;
            int pending = snapshots.Sum(s => s.State.Pending.Count);
            if (pending > 0)
            {
                tabs.SelectedItem = checkedPages[Array.FindIndex(snapshots, s => s.State.Pending.Count > 0)].Item;
                if (!ConfirmExport(pending, action)) return false;
                var choose = new SaveFileDialog { Title = "导出全部待保存文字", FileName = "工作台待保存文字.zip", Filter = "ZIP 文件|*.zip", DefaultExt = ".zip", AddExtension = true, OverwritePrompt = true };
                if (choose.ShowDialog(this) != true) return false;
                // The user's decision may take time. Re-read every hidden buffer after the dialog.
                snapshots = await ReadAllAsync(checkedPages);
                if (snapshots is null) return false;
                BrowserSafety.ExportPendingBuffers(choose.FileName, snapshots.Select(s => s.State));
            }
            if (capturedPages.Length != pages.Count || !capturedPages.SequenceEqual(pages))
                return Block(null, "页面发生变化", "标签已变化，本次操作已取消。当前文字仍保留。");
            if (emptyPages.Any(pair => !pair.Key.CanDiscardFailed || pair.Key.Generation != pair.Value))
                return Block(null, "页面发生变化", "失败页面的状态已经变化，请重新核对后再试。");
            var final = await ReadAllAsync(checkedPages);
            if (final is null) return false;
            for (int i = 0; i < snapshots.Length; i++)
                if (!BrowserSafety.CanCommit(snapshots[i], final[i]))
                    return Block(checkedPages[i], "编辑状态发生变化", "核对期间有内容变化，本次操作已取消。请返回作品保存；已有导出文件仍保留。");
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
            foreach (var page in capturedPages) page.ResumeInteraction();
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
            if (snapshot.State.PendingForm)
            { Block(page, "书架还有未提交内容", "请返回书架完成或清空填写内容，或恢复尚未保存的作品状态选择，再重试。"); return null; }
            snapshots.Add(snapshot);
        }
        return snapshots.ToArray();
    }

    private bool Block(BrowserTab? page, string title, string message)
    {
        if (page is not null && !page.Disposed) tabs.SelectedItem = page.Item;
        if (selfTestSuppressPrompts && options.SelfTestReport is not null) return false;
        MessageBox.Show(this, message, title, MessageBoxButton.OK, MessageBoxImage.Warning);
        return false;
    }

    private bool ConfirmExport(int count, string action)
    {
        bool accepted = false;
        var dialog = new Window { Owner = this, Title = "还有待保存文字", Width = 500, SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner, ResizeMode = ResizeMode.NoResize };
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
        bool shelfGuard = await NativeShelfGuardSelfTestAsync(first);
        await RememberThemeAsync("system");
#pragma warning disable WPF0001
        bool systemTheme = Application.Current.ThemeMode.Equals(System.Windows.ThemeMode.System);
#pragma warning restore WPF0001
        await RememberThemeAsync(original);
        var preferenceDetails = new Dictionary<string, object>();
        bool persisted = await NativePreferenceSelfTestAsync(first, preferenceDetails);
        var lifecycle = await NativeLifecycleSelfTestAsync(); // Intentionally crashes only the isolated test renderer, last.
        bool passed = shelf && captures.Values.All(v => v) && restored && progress && safety && shelfGuard && systemTheme && persisted && lifecycle.recovered && lifecycle.crashProtected;
        Directory.CreateDirectory(Path.GetDirectoryName(reportPath)!);
        await File.WriteAllTextAsync(reportPath, JsonSerializer.Serialize(new
        {
            ok = passed, native_window = IsVisible, shelf_rendered = shelf, title = first.Title,
            captured = captures.Values.All(v => v), client_version = ClientVersion, ui_stack = "wpf-webview2",
            self_contained = true, in_private = pages.All(p => p.IsInPrivate),
            update_check_available = true, update_progress_verified = progress, update_progress_synthetic = true,
            themes_verified = restored && captures.Values.All(v => v), theme_captures = captures,
            native_close_probe_verified = safety, hidden_buffers_verified = safety, ime_guard_verified = safety,
            buffer_export_verified = safety, synthetic_editor_fixture = true,
            shelf_state_guard_verified = shelfGuard, native_system_theme_verified = systemTheme,
            preferences_restored_verified = persisted, preference_diagnostics = preferenceDetails,
            failed_tab_recovery_verified = lifecycle.recovered,
            unknown_crash_protected = lifecycle.crashProtected
        }), new UTF8Encoding(false));
        exitCode = passed ? 0 : 2; closeApproved = true; Close();
    }

    private async Task<bool> NativeShelfGuardSelfTestAsync(BrowserTab shelf)
    {
        selfTestSuppressPrompts = true;
        try
        {
            foreach (var pair in new[] {
                ("busy=true", "busy=false"), ("managerBusy=true", "managerBusy=false"),
                ("statusSaving.add('synthetic-only')", "statusSaving.clear()"),
                ("document.getElementById('book-path').value='C:\\\\SyntheticOnly'", "document.getElementById('book-path').value=''"),
                ("window.dispatchEvent(new CompositionEvent('compositionstart'))", "window.dispatchEvent(new CompositionEvent('compositionend'))"),
                ("showCreateBook();document.getElementById('manager-body').querySelector('input').value='未提交测试'", "manager.close()") })
            {
                await shelf.ExecuteAsync(pair.Item1 + ";true");
                bool ran = false;
                bool allowed = await RunProtectedAsync("自检", () => { ran = true; return Task.CompletedTask; });
                await shelf.ExecuteAsync(pair.Item2 + ";true");
                if (allowed || ran) return false;
            }
            var clean = await shelf.ProbeAsync();
            return BrowserSafety.CanProceed(clean?.State);
        }
        finally
        {
            try { await shelf.ExecuteAsync("busy=false;managerBusy=false;statusSaving.clear();document.getElementById('book-path').value='';manager.close();window.dispatchEvent(new CompositionEvent('compositionend'));true"); } catch { }
            selfTestSuppressPrompts = false;
        }
    }

    private async Task<bool> NativePreferenceSelfTestAsync(BrowserTab shelf, IDictionary<string, object> diagnostics)
    {
        const string keys = "[\"0123456789abcdef0123\"]";
        foreach (string stage in new[] { "bridge_received", "disk_reloaded", "new_origin_loaded", "new_origin_value_matches" }) diagnostics[stage] = false;
        try
        {
            await shelf.ExecuteAsync("localStorage.setItem('story-last-books'," + JsonSerializer.Serialize(keys) + ");true");
            for (int i = 0; i < 50 && (!StoredPreferences.TryGetValue("story-last-books", out var value) || value != keys); i++) await Task.Delay(100);
            bool received = StoredPreferences.TryGetValue("story-last-books", out var current) && current == keys;
            bool reloaded = new ThemePreferences(options.StateDirectory).Storage.TryGetValue("story-last-books", out var stored) && stored == keys;
            diagnostics["bridge_received"] = received; diagnostics["disk_reloaded"] = reloaded;
            if (!received || !reloaded) return false;
            await using var synthetic = new SyntheticEditor(clean: true);
            var page = await OpenPageAsync(synthetic.Url);
            if (page is null) return false;
            try
            {
                bool differentOrigin = page.LoadComplete && page.Key.Port != shelf.Key.Port;
                diagnostics["new_origin_loaded"] = differentOrigin;
                string raw = await page.ExecuteAsync("localStorage.getItem('story-last-books')");
                string? restored = BrowserSafety.ParseScriptString(raw);
                diagnostics["response_is_string"] = restored is not null;
                bool matched = restored == keys;
                diagnostics["new_origin_value_matches"] = matched;
                return differentOrigin && matched;
            }
            finally { pages.Remove(page); tabs.Items.Remove(page.Item); page.Dispose(); tabs.SelectedItem = shelf.Item; }
        }
        catch (Exception error)
        {
            // Record only the stage and exception type; never include page data or capability URLs.
            diagnostics["error_type"] = error.GetType().Name;
            return false;
        }
    }

    private async Task<(bool recovered, bool crashProtected)> NativeLifecycleSelfTestAsync()
    {
        await using var synthetic = new SyntheticEditor(clean: true);
        selfTestSuppressPrompts = true;
        BrowserTab? page = null;
        try
        {
            int before = pages.Count;
            synthetic.FailRequests = true;
            try { await OpenPageAsync(synthetic.Url); return (false, false); } catch (IOException) { } catch (TimeoutException) { }
            if (pages.Count != before) return (false, false);
            synthetic.FailRequests = false;
            page = await OpenPageAsync(synthetic.Url);
            if (page is null) return (false, false);
            synthetic.FailRequests = true;
            if (await RunProtectedAsync("自检重新载入", () => page.NavigateApprovedAsync(synthetic.Url)) || !page.CanDiscardFailed) return (false, false);
            synthetic.FailRequests = false;
            if (!await RetryPageAsync(page) || !page.LoadComplete || page.CanDiscardFailed) return (false, false);
            await page.ExecuteAsync("docs.set('crash-only',{editable:true,text:'原稿',value:'隔离自检未保存文字',editing:false,title:'崩溃保护测试'});true");
            if ((await page.ProbeAsync())?.State.Pending.Count != 1) return (true, false);
            var failed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            page.View.CoreWebView2.ProcessFailed += (_, _) => failed.TrySetResult();
            Task crash = page.View.CoreWebView2.CallDevToolsProtocolMethodAsync("Page.crash", "{}");
            try { await crash.WaitAsync(TimeSpan.FromSeconds(2)); } catch { }
            await failed.Task.WaitAsync(TimeSpan.FromSeconds(10));
            bool ran = false;
            bool allowed = await RunProtectedAsync("自检关闭", () => { ran = true; return Task.CompletedTask; });
            return (true, !allowed && !ran && !page.CanDiscardFailed);
        }
        finally
        {
            if (page is not null) { pages.Remove(page); tabs.Items.Remove(page.Item); page.Dispose(); }
            selfTestSuppressPrompts = false;
        }
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
        private readonly bool clean;
        public volatile bool FailRequests;
        public string Url { get; }
        public SyntheticEditor(bool clean = false)
        {
            this.clean = clean;
            listener.Start();
            Url = $"http://127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}/{Guid.NewGuid():N}/";
            serving = ServeAsync();
        }
        private async Task ServeAsync()
        {
            string html = """
                <!doctype html><html><head><meta charset="utf-8"><title>隔离关闭保护自检</title></head>
                <body><div id="current-book-root">仅合成材料</div><textarea id="text">当前未保存中文</textarea>
                <script>const docs=new Map([['active',{editable:true,text:'原稿',value:'原稿',editing:true,title:'当前测试'}],
                  ['hidden',{editable:true,text:'旧文',value:'隐藏未保存中文',editing:false,title:'隐藏测试'}]]);let active='active';</script></body></html>
                """;
            if (clean) html = html.Replace("</script>", "docs.clear();document.getElementById('text').value='';</script>");
            byte[] body = Encoding.UTF8.GetBytes(html);
            try
            {
                while (!cancellation.IsCancellationRequested)
                {
                    using var client = await listener.AcceptTcpClientAsync(cancellation.Token);
                    using var stream = client.GetStream();
                    using var request = CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token);
                    request.CancelAfter(TimeSpan.FromSeconds(8));
                    var buffer = new byte[8192];
                    int received = 0;
                    bool headersComplete = false;
                    try
                    {
                        while (received < buffer.Length)
                        {
                            int count = await stream.ReadAsync(buffer.AsMemory(received), request.Token);
                            if (count == 0) break;
                            received += count;
                            if (buffer.AsSpan(0, received).IndexOf("\r\n\r\n"u8) >= 0)
                            { headersComplete = true; break; }
                        }
                    }
                    catch (OperationCanceledException) when (!cancellation.IsCancellationRequested) { continue; }
                    if (!headersComplete) continue;
                    if (FailRequests) continue;
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
