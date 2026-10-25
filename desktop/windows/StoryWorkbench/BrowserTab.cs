using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Microsoft.Win32;

namespace StoryWorkbench;

public sealed class BrowserTab : IDisposable
{
    private readonly MainWindow owner;
    private readonly string address;
    private string? themeScript;
    private string? approvedNavigation;
    private ulong? approvedNavigationId;
    private bool guardedNavigation;
    private bool directoryPicking;
    private TaskCompletionSource? navigationCompletion;
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly PageLifecycle lifecycle = new();
    public WebView2 View { get; } = new() { IsEnabled = false };
    public TabItem Item { get; } = new();
    public PageKey Key { get; }
    public long Generation { get; private set; }
    public bool LoadComplete { get; private set; }
    public bool Disposed { get; private set; }
    public bool CanDiscardFailed => !LoadComplete && lifecycle.CanDiscardFailed;
    public string EntryAddress => address;
    public string Title { get; private set; } = "工作台";
    public bool IsInPrivate => View.CoreWebView2?.Profile.IsInPrivateModeEnabled == true;

    public BrowserTab(MainWindow owner, string address, bool shelf)
    {
        this.owner = owner; this.address = address;
        Key = BrowserSafety.PageKeyFor(address) ?? throw new ArgumentException("仅能打开已验证的本机工作台地址。");
        var header = new StackPanel { Orientation = Orientation.Horizontal };
        var label = new TextBlock { Text = shelf ? "书架" : "作品", Margin = new Thickness(8, 3, 8, 3) };
        header.Children.Add(label);
        if (!shelf)
        {
            var close = new Button { Content = "×", Padding = new Thickness(6, 0, 6, 0), ToolTip = "关闭作品页面" };
            close.Click += async (_, e) => { e.Handled = true; await owner.CloseTabAsync(this); };
            header.Children.Add(close);
        }
        Item.Header = header; Item.Content = View; Item.Tag = this;
        View.NavigationCompleted += (_, e) =>
        {
            // Cancelled link/navigation attempts never invalidate the document that is still displayed.
            if (approvedNavigationId != e.NavigationId) return;
            LoadComplete = e.IsSuccess && BrowserSafety.PageKeyFor(View.Source?.AbsoluteUri) == Key;
            lifecycle.Complete(LoadComplete);
            guardedNavigation = false;
            if (LoadComplete) ready.TrySetResult();
            else ready.TrySetException(new IOException("页面未能载入；当前作品服务未被关闭。"));
            if (LoadComplete) navigationCompletion?.TrySetResult();
            else navigationCompletion?.TrySetException(new IOException("页面重新载入失败，作品服务仍保留。"));
            if (CanDiscardFailed) ShowRecoverableFailure();
        };
        View.CoreWebView2InitializationCompleted += (_, e) =>
        {
            if (!e.IsSuccess) ready.TrySetException(new IOException("无法初始化 WebView2 Runtime。"));
        };
    }

    public async Task InitializeAsync(CoreWebView2Environment environment)
    {
        var controller = environment.CreateCoreWebView2ControllerOptions();
        controller.ProfileName = "StoryWorkbench";
        controller.IsInPrivateModeEnabled = true;
        await View.EnsureCoreWebView2Async(environment, controller);
        var core = View.CoreWebView2;
        if (!core.Profile.IsInPrivateModeEnabled) throw new IOException("浏览器未进入隐私模式，已停止载入作品。");
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.AreBrowserAcceleratorKeysEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsBuiltInErrorPageEnabled = false;
        core.Settings.AreDefaultScriptDialogsEnabled = false;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.NavigationStarting += NavigationStarting;
        core.FrameNavigationStarting += (_, e) => e.Cancel = true;
        core.SourceChanged += (_, _) => Generation++;
        core.DocumentTitleChanged += (_, _) =>
        {
            Title = core.DocumentTitle;
            if (Item.Header is StackPanel panel && panel.Children[0] is TextBlock label)
                label.Text = string.IsNullOrWhiteSpace(Title) ? "工作台" : Title[..Math.Min(Title.Length, 32)];
        };
        core.ProcessFailed += (_, _) => { Generation++; LoadComplete = false; lifecycle.Fail(); owner.ShowStatus("页面进程已停止。未确认的编辑状态会阻止关闭，请保留当前窗口。"); };
        core.NewWindowRequested += async (_, e) =>
        {
            e.Handled = true;
            if (owner.ClosePending) return;
            try
            {
                if (BrowserSafety.IsLocalUrl(e.Uri)) await owner.OpenPageAsync(e.Uri);
                else if (e.IsUserInitiated) MainWindow.OpenExternal(e.Uri);
            }
            catch { owner.ShowStatus("新页面未能打开，原作品页面仍保留。"); }
        };
        core.WebMessageReceived += async (_, e) =>
        {
            if (BrowserSafety.PageKeyFor(e.Source) != Key) return;
            try
            {
                using var message = JsonDocument.Parse(e.WebMessageAsJson);
                var value = message.RootElement;
                if (value.GetProperty("kind").GetString() == "theme")
                    await owner.RememberThemeAsync(value.GetProperty("value").GetString());
                else if (value.GetProperty("kind").GetString() == "preference")
                    await owner.RememberPreferenceAsync(value.GetProperty("key").GetString(), value.GetProperty("value").GetString());
                else if (value.GetProperty("kind").GetString() == "choose-directory")
                    ChooseDirectory(value);
            }
            catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException) { }
        };
        core.PermissionRequested += (_, e) => { e.State = CoreWebView2PermissionState.Deny; e.Handled = true; };
        core.DownloadStarting += DownloadStarting;
        core.ScriptDialogOpening += (_, e) =>
        {
            if (e.Kind == CoreWebView2ScriptDialogKind.Beforeunload)
            {
                if (guardedNavigation && owner.ClosePending) e.Accept();
                return;
            }
            if (owner.ClosePending) return;
            if (e.Kind == CoreWebView2ScriptDialogKind.Alert)
            { MessageBox.Show(owner, e.Message, "写作工作台", MessageBoxButton.OK); e.Accept(); }
            else if (e.Kind == CoreWebView2ScriptDialogKind.Confirm
                && MessageBox.Show(owner, e.Message, "写作工作台", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
                e.Accept();
        };
        await SetThemeSeedAsync(owner.Theme);
        approvedNavigation = address;
        core.Navigate(address);
        try
        {
            await ready.Task.WaitAsync(TimeSpan.FromSeconds(35));
            if (!await ExecuteBooleanAsync(BrowserSafety.ReleaseInitialInputScript)) throw new IOException("页面初始化保护尚未完成。");
            View.IsEnabled = true;
        }
        catch { lifecycle.Fail(); throw; }
    }

    public async Task SetThemeSeedAsync(string theme)
    {
        if (Disposed || View.CoreWebView2 is null) return;
        if (themeScript is not null) View.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(themeScript);
        themeScript = await View.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(BrowserSafety.BootstrapScript(theme, owner.StoredPreferences));
    }

    private void ChooseDirectory(JsonElement request)
    {
        if (Disposed || !LoadComplete || Key.Path != "/" || owner.ClosePending
            || BrowserSafety.PageKeyFor(View.Source?.AbsoluteUri) != Key) return;
        string? id = request.GetProperty("id").GetString(), purpose = request.GetProperty("purpose").GetString();
        if (!Guid.TryParseExact(id, "D", out _) || purpose is not ("existing-book" or "create-parent")) return;
        long generation = Generation;
        void Reply(object value)
        {
            if (!Disposed && LoadComplete && Generation == generation && BrowserSafety.PageKeyFor(View.Source?.AbsoluteUri) == Key)
                View.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(value));
        }
        if (directoryPicking) { Reply(new { kind = "directory-result", id, error = "请先完成当前文件夹选择。" }); return; }
        directoryPicking = true;
        try
        {
            var dialog = new OpenFolderDialog { Title = purpose == "create-parent" ? "选择新作品的存放位置" : "选择已有作品文件夹", Multiselect = false };
            bool selected = dialog.ShowDialog(owner) == true;
            Reply(new { kind = "directory-result", id, cancelled = !selected, path = selected ? dialog.FolderName : null });
        }
        catch (Exception error) when (error is InvalidOperationException or System.Runtime.InteropServices.COMException)
        { Reply(new { kind = "directory-result", id, error = "文件夹选择器无法打开，请重试或填写完整路径。" }); }
        finally { directoryPicking = false; }
    }

    public void MarkInitializationFailed()
    {
        lifecycle.Fail();
        try { View.CoreWebView2?.Stop(); } catch (Exception error) when (error is InvalidOperationException or System.Runtime.InteropServices.COMException) { }
    }
    public void ResumeInteraction()
    {
        if (Disposed) return;
        if (LoadComplete) lifecycle.AllowInput();
        View.IsEnabled = !CanDiscardFailed;
    }

    private async void NavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (approvedNavigation == e.Uri)
        {
            approvedNavigation = null; approvedNavigationId = e.NavigationId; LoadComplete = false; Generation++; return;
        }
        e.Cancel = true;
        if (owner.ClosePending) return;
        var key = BrowserSafety.PageKeyFor(e.Uri);
        try
        {
            if (key is null)
            {
                if (e.IsUserInitiated) MainWindow.OpenExternal(e.Uri);
            }
            else if (key != Key) await owner.OpenPageAsync(e.Uri);
            else await owner.RunProtectedAsync("重新载入", () => NavigateApprovedAsync(e.Uri));
        }
        catch { owner.ShowStatus("本次导航未完成，原页面仍保留。"); }
    }

    public async Task NavigateApprovedAsync(string uri)
    {
        if (BrowserSafety.PageKeyFor(uri) != Key) throw new ArgumentException("作品地址发生变化。");
        if (!owner.ClosePending) throw new InvalidOperationException("页面须先完成保存核验才能重新载入。");
        lifecycle.BeginVerifiedNavigation();
        Item.Content = View;
        guardedNavigation = true;
        approvedNavigation = uri;
        navigationCompletion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        View.CoreWebView2.Navigate(uri);
        try
        {
            await navigationCompletion.Task.WaitAsync(TimeSpan.FromSeconds(35));
            if (!await ExecuteBooleanAsync(BrowserSafety.ReleaseInitialInputScript)) throw new IOException("页面初始化保护尚未完成。");
        }
        catch
        {
            // A late navigation must not become interactive after a failed/timed-out protected operation.
            View.CoreWebView2.Stop();
            if (!LoadComplete) { lifecycle.Fail(); ShowRecoverableFailure(); }
            throw;
        }
    }

    private void ShowRecoverableFailure()
    {
        if (!CanDiscardFailed || Disposed) return;
        var panel = new StackPanel { Margin = new Thickness(28) };
        panel.Children.Add(new TextBlock { Text = "页面未能载入。此前文字已完成保存检查；可以重试或关闭此标签。作品服务没有被停止。", TextWrapping = TextWrapping.Wrap });
        var retry = new Button { Content = "重试载入", HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 16, 0, 0), Padding = new Thickness(12, 6, 12, 6) };
        retry.Click += async (_, _) => await owner.RetryPageAsync(this);
        panel.Children.Add(retry); Item.Content = panel;
    }

    public async Task NavigateFragmentAsync(string uri)
    {
        if (BrowserSafety.PageKeyFor(uri) != Key || !LoadComplete || owner.ClosePending) return;
        string hash = new Uri(uri).Fragment;
        if (hash.Length != 0 && new Uri(View.CoreWebView2.Source).Fragment != hash)
            await ExecuteAsync("location.hash=" + JsonSerializer.Serialize(hash));
    }

    private void DownloadStarting(object? sender, CoreWebView2DownloadStartingEventArgs e)
    {
        using var deferral = e.GetDeferral();
        e.Handled = true;
        string uri = e.DownloadOperation.Uri;
        if (owner.ClosePending || !LoadComplete || !DownloadOriginAllowed(uri)) { e.Cancel = true; return; }
        var dialog = new SaveFileDialog { Title = "保存文件", FileName = Path.GetFileName(e.ResultFilePath), OverwritePrompt = true };
        if (dialog.ShowDialog(owner) != true) { e.Cancel = true; return; }
        e.ResultFilePath = dialog.FileName;
        var operation = e.DownloadOperation;
        operation.StateChanged += (_, _) => owner.ShowStatus(operation.State switch
        {
            CoreWebView2DownloadState.Completed => "文件下载完成。",
            CoreWebView2DownloadState.Interrupted => "文件下载未完成，请重新下载。",
            _ => "正在保存文件…"
        });
    }

    private bool DownloadOriginAllowed(string value)
    {
        if (value.StartsWith("blob:", StringComparison.Ordinal)) value = value[5..];
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == "http" && uri.Host == "127.0.0.1"
            && uri.Port == Key.Port && uri.UserInfo.Length == 0;
    }

    public async Task<string> ExecuteAsync(string script)
    {
        if (Disposed || View.CoreWebView2 is null) throw new InvalidOperationException("页面尚未准备好。");
        return await View.CoreWebView2.ExecuteScriptAsync(script).WaitAsync(TimeSpan.FromSeconds(8));
    }
    public async Task<bool> ExecuteBooleanAsync(string script) => await ExecuteAsync(script) == "true";

    public async Task<SafetySnapshot?> ProbeAsync()
    {
        var core = View.CoreWebView2;
        if (Disposed || !LoadComplete || core is null || BrowserSafety.PageKeyFor(core.Source) != Key) return null;
        long generation = Generation;
        try
        {
            var state = BrowserSafety.ParseState(await ExecuteAsync(BrowserSafety.ProbeScript()));
            if (state is null || Disposed || !LoadComplete || Generation != generation
                || !ReferenceEquals(core, View.CoreWebView2) || BrowserSafety.PageKeyFor(core.Source) != Key
                || state.Kind != (Key.Path == "/" ? "shelf" : "editor")) return null;
            return new(generation, Key, state);
        }
        catch { return null; }
    }

    public async Task CaptureAsync(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await using var output = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None);
        await View.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, output);
        await output.FlushAsync();
    }

    public void Dispose()
    {
        if (Disposed) return;
        Disposed = true; Generation++; LoadComplete = false;
        View.Dispose(); Item.Content = null;
    }
}
