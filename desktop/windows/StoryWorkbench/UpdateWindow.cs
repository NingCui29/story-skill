using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace StoryWorkbench;

/// <summary>Modeless update UI. The host owns all manuscript save/close decisions.</summary>
public sealed class UpdateWindow : Window
{
    private static readonly Dictionary<MainWindow, UpdateWindow> Windows = [];
    private readonly MainWindow host;
    private readonly UpdateService service = new();
    private readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap, FontSize = 17, Margin = new(0, 0, 0, 14) };
    private readonly TextBlock details = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 10, 0, 20), LineHeight = 22 };
    private readonly ProgressBar bar = new() { Minimum = 0, Maximum = 100, Height = 8, Foreground = new SolidColorBrush(Color.FromRgb(56, 189, 248)) };
    private readonly Button checkButton = Button("重新检查");
    private readonly Button downloadButton = Button("下载更新");
    private readonly Button installButton = Button("校验并启动安装程序");
    private readonly Button cancelButton = Button("取消");
    private readonly Button folderButton = Button("打开所在文件夹");
    private readonly Button closeButton = Button("关闭");
    private UpdateResult? result;
    private UpdateDownloaded? verified;
    private CancellationTokenSource? cancellation;
    private Task? activeTask;
    private bool closeWhenStopped;
    private bool installerStarted;
    private bool closed;
    private int operationGeneration;
    public bool IsBusy => activeTask is { IsCompleted: false };

    private static Button Button(string title) => new() { Content = title, Padding = new(14, 7, 14, 7), Margin = new(0, 4, 8, 0), MinHeight = 34 };

    public UpdateWindow(MainWindow owner)
    {
        host = owner; Owner = owner;
        Title = "Windows 工作台更新"; Width = 650; MinWidth = 570; SizeToContent = SizeToContent.Height;
        WindowStartupLocation = WindowStartupLocation.CenterOwner; ResizeMode = ResizeMode.CanMinimize;
        Background = owner.Background; Foreground = owner.Foreground;
        var layout = new StackPanel { Margin = new(24) };
        layout.Children.Add(new TextBlock { Text = "写作工作台 " + UpdateService.ClientVersion, Opacity = 0.65, Margin = new(0, 0, 0, 12) });
        layout.Children.Add(message); layout.Children.Add(bar); layout.Children.Add(details);
        var actions = new WrapPanel();
        foreach (var button in new[] { checkButton, downloadButton, installButton, folderButton, cancelButton, closeButton }) actions.Children.Add(button);
        layout.Children.Add(actions); Content = layout;
        downloadButton.Background = new SolidColorBrush(Color.FromRgb(56, 189, 248));
        downloadButton.Foreground = Brushes.Black;
        message.Text = "检查 Windows 工作台正式更新";
        details.Text = "从官方发布读取版本信息，下载后核对大小与 SHA256。";
        downloadButton.Visibility = installButton.Visibility = folderButton.Visibility = cancelButton.Visibility = Visibility.Collapsed;
        bar.Visibility = Visibility.Collapsed;
        checkButton.Click += async (_, _) => await CheckAsync();
        downloadButton.Click += async (_, _) => await DownloadAsync();
        installButton.Click += async (_, _) => await InstallAsync();
        cancelButton.Click += (_, _) => Cancel();
        closeButton.Click += (_, _) => Close();
        folderButton.Click += (_, _) => OpenFolder();
        Closing += OnClosing;
        Closed += (_, _) => {
            closed = true;
            if (Windows.TryGetValue(host, out var current) && ReferenceEquals(current, this)) Windows.Remove(host);
            service.Dispose();
        };
    }

    public static async Task ShowAsync(MainWindow owner)
    {
        if (Windows.TryGetValue(owner, out var existing) && !existing.closed)
        { existing.Show(); existing.Activate(); return; }
        var window = new UpdateWindow(owner); Windows[owner] = window;
        window.Show(); await window.CheckAsync();
    }

    private async Task RunAsync(Func<CancellationToken, Task> work)
    {
        if (IsBusy || closed) return;
        var source = new CancellationTokenSource(); cancellation = source; operationGeneration++;
        SetBusy(true);
        // RunCore handles synchronous exceptions as well as failures after awaiting.
        activeTask = RunCoreAsync(work, source.Token);
        try { await activeTask; }
        finally
        {
            if (ReferenceEquals(cancellation, source)) { cancellation = null; activeTask = null; }
            source.Dispose();
            if (!closed) { SetBusy(false); if (closeWhenStopped) Close(); }
        }
    }

    private async Task RunCoreAsync(Func<CancellationToken, Task> work, CancellationToken token)
    {
        try { await work(token); }
        catch (OperationCanceledException)
        {
            if (closed) return;
            message.Text = "更新操作已取消。";
            details.Text = "未完成的临时下载已清理。可重新检查或重试下载。";
            bar.IsIndeterminate = false;
        }
        catch (Exception error)
        {
            if (closed) return;
            message.Text = "更新未完成：" + error.Message;
            details.Text = "可以重试。下载和安装操作不会结束作品服务。";
            bar.IsIndeterminate = false;
            downloadButton.Content = "重试下载";
            if (result?.State == "available") downloadButton.Visibility = Visibility.Visible;
        }
    }

    private void SetBusy(bool busy)
    {
        checkButton.IsEnabled = downloadButton.IsEnabled = installButton.IsEnabled = folderButton.IsEnabled = !busy;
        cancelButton.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        cancelButton.IsEnabled = busy;
        closeButton.IsEnabled = true;
    }

    private async Task CheckAsync()
    {
        await RunAsync(async token =>
        {
            verified = null; result = null;
            downloadButton.Visibility = installButton.Visibility = folderButton.Visibility = Visibility.Collapsed;
            message.Text = "正在检查 Windows 工作台更新…";
            details.Text = "读取官方仓库的最新正式发布。";
            bar.Visibility = Visibility.Visible; bar.IsIndeterminate = true;
            var found = await Task.Run(() => service.CheckAsync(token), token); token.ThrowIfCancellationRequested();
            result = found; message.Text = found.Message; bar.IsIndeterminate = false; bar.Visibility = Visibility.Collapsed;
            details.Text = found.State == "available"
                ? "安装包大小：" + UpdateService.FormatBytes(found.AssetSize) + "。下载完成后核对官方 SHA256。"
                : "当前客户端版本：" + UpdateService.ClientVersion;
            if (found.State == "available") { downloadButton.Content = "下载更新"; downloadButton.Visibility = Visibility.Visible; }
        });
    }

    private IProgress<UpdateProgress> Progress(CancellationToken token)
    {
        var generation = operationGeneration;
        return new Progress<UpdateProgress>(value => {
            if (!closed && IsBusy && generation == operationGeneration && !token.IsCancellationRequested) ShowProgress(value);
        });
    }

    private async Task DownloadAsync()
    {
        if (result?.State != "available") return;
        var selected = result;
        await RunAsync(async token =>
        {
            verified = null; installButton.Visibility = folderButton.Visibility = Visibility.Collapsed;
            bar.Visibility = Visibility.Visible; bar.Value = 0;
            var cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StoryWorkbench", "updates");
            var progress = Progress(token);
            var download = await Task.Run(() => service.DownloadAsync(selected, cache, progress, token), token);
            token.ThrowIfCancellationRequested(); verified = download; ShowReady(download);
        });
    }

    private void ShowProgress(UpdateProgress progress)
    {
        bar.Visibility = Visibility.Visible; bar.IsIndeterminate = progress.Stage == "checksum";
        if (progress.Stage == "checksum")
        { message.Text = "正在取得官方 SHA256 校验信息…"; details.Text = "核对与此安装包同名的官方校验文件。"; return; }
        bar.Value = progress.Total > 0 ? Math.Clamp(progress.Received * 100.0 / progress.Total, 0, 100) : 0;
        message.Text = progress.Stage switch {
            "verifying" => "正在校验已下载文件的 SHA256…",
            "ready" => "下载完成，SHA256 校验通过。",
            _ => "正在下载 Windows 工作台 " + (result?.Version ?? UpdateService.ClientVersion) + "…"
        };
        details.Text = UpdateService.ProgressText(progress);
    }

    private void ShowReady(UpdateDownloaded download)
    {
        ShowProgress(new("ready", download.Size, download.Size));
        downloadButton.Visibility = Visibility.Collapsed; folderButton.Visibility = Visibility.Visible;
        var executable = download.AssetName.EndsWith("-setup.exe", StringComparison.OrdinalIgnoreCase);
        installButton.Visibility = executable ? Visibility.Visible : Visibility.Collapsed;
        details.Text = executable
            ? "已核对官方大小及 SHA256。启动安装程序前会再次校验文件，并检查全部标签的待保存文字。"
            : "便携 ZIP 已校验。请退出客户端后解压完整目录并手工替换；若文件被占用，请保存并正常停止对应作品服务。";
    }

    private async Task InstallAsync()
    {
        if (verified is null || installerStarted) return;
        var download = verified;
        await RunAsync(async token =>
        {
            message.Text = "正在重新校验安装包…";
            var checkedDownload = await service.VerifyAsync(download, Progress(token), token);
            token.ThrowIfCancellationRequested(); verified = checkedDownload;
            message.Text = "等待全部标签完成保存检查…";
            bar.IsIndeterminate = true;
            Exception? launchError = null;
            var allowed = await host.RunProtectedAsync("启动更新安装", async () =>
            {
                try
                {
                    token.ThrowIfCancellationRequested();
                    message.Text = "正在最终核验安装包并启动安装向导…";
                    await service.LaunchInstallerAsync(checkedDownload, token);
                    installerStarted = true;
                }
                catch (Exception error) { launchError = error; }
            });
            bar.IsIndeterminate = false;
            if (launchError is not null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(launchError).Throw();
            if (!allowed || !installerStarted)
            { message.Text = "已保留当前页面。保存全部文字后可再次启动安装程序。"; return; }
            message.Text = "安装程序已启动，请查看安装向导。";
            details.Text = "请正常退出客户端后继续安装。若安装器提示文件占用，请保存并正常停止对应作品服务后重试。安装完成后重新打开工作台，核对运行版本。";
            bar.Visibility = installButton.Visibility = downloadButton.Visibility = Visibility.Collapsed;
            host.ShowStatus("更新安装向导已启动；当前运行版本仍为 " + UpdateService.ClientVersion + "。");
        });
    }

    private void OpenFolder()
    {
        if (verified is null || IsBusy) return;
        try
        {
            var start = new ProcessStartInfo("explorer.exe") { UseShellExecute = false };
            start.ArgumentList.Add(Path.GetDirectoryName(verified.Path)!);
            using var process = Process.Start(start);
        }
        catch (Exception error) { message.Text = "无法打开文件夹：" + error.Message; }
    }

    private void Cancel()
    {
        cancellation?.Cancel(); cancelButton.IsEnabled = false;
        message.Text = "正在取消，等待当前操作结束并清理临时文件…";
    }

    public async Task CancelAndWaitAsync()
    {
        var task = activeTask;
        if (task is null) return;
        Cancel(); await task;
    }

    private void OnClosing(object? sender, CancelEventArgs args)
    {
        if (!IsBusy) return;
        args.Cancel = true; closeWhenStopped = true; Cancel();
    }

    public static async Task<bool> CaptureSyntheticAsync(MainWindow owner, string pngPath)
    {
        var window = new UpdateWindow(owner);
        try
        {
            window.checkButton.IsEnabled = window.downloadButton.IsEnabled = false;
            window.Show();
            window.ShowProgress(new("downloading", 6 * 1024 * 1024, 10 * 1024 * 1024, 1024 * 1024, 4));
            window.message.Text = "界面验证：合成下载进度，不在下载真实更新。";
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Render);
            window.UpdateLayout();
            var dpi = VisualTreeHelper.GetDpi(window);
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(window.ActualWidth * dpi.DpiScaleX),
                (int)Math.Ceiling(window.ActualHeight * dpi.DpiScaleY), dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
            bitmap.Render(window);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var stream = new FileStream(pngPath, FileMode.Create, FileAccess.Write, FileShare.None)) encoder.Save(stream);
            return window.IsVisible && window.bar.Value == 60 && window.details.Text.Contains("MiB/秒", StringComparison.Ordinal)
                && window.details.Text.Contains("4 秒", StringComparison.Ordinal) && new FileInfo(pngPath).Length > 0;
        }
        finally { window.Close(); }
    }
}
