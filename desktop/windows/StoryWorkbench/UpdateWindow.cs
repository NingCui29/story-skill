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
    private readonly Border surface = new() { Padding = new Thickness(24) };
    private readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap, FontSize = 17, Margin = new(0, 0, 0, 14) };
    private readonly TextBlock details = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 10, 0, 20), LineHeight = 22 };
    private readonly ProgressBar bar = new() { Minimum = 0, Maximum = 100, Height = 8, Foreground = new SolidColorBrush(Color.FromRgb(56, 189, 248)) };
    private readonly Button checkButton = Button("重新检查");
    private readonly Button downloadButton = Button("下载更新");
    private readonly Button installButton = Button("校验后退出并安装");
    private readonly Button cancelButton = Button("取消");
    private readonly Button folderButton = Button("打开所在文件夹");
    private readonly Button closeButton = Button("关闭");
    private UpdateResult? result;
    private UpdateDownloaded? verified;
    private CancellationTokenSource? cancellation;
    private Task? activeTask;
    private bool closeWhenStopped;
    private UpdateInstallerSession? installer;
    private UpdateInstallerSession? observedInstaller;
    private bool closed;
    private int operationGeneration;
    public bool IsBusy => activeTask is { IsCompleted: false };
    public static IReadOnlyDictionary<string, object>? SyntheticCaptureDiagnostics { get; private set; }

    private static Button Button(string title) => new() { Content = title, Padding = new(14, 7, 14, 7), Margin = new(0, 4, 8, 0), MinHeight = 34 };

    public UpdateWindow(MainWindow owner)
    {
        host = owner; Owner = owner;
        Title = "Windows 工作台更新"; Width = 650; MinWidth = 570; SizeToContent = SizeToContent.Height;
        WindowStartupLocation = WindowStartupLocation.CenterOwner; ResizeMode = ResizeMode.CanMinimize;
        // Fluent windows default to a transparent Mica backdrop. Paint this actual content surface
        // with its dynamic theme brush so text remains legible without depending on the compositor.
        SetResourceReference(BackgroundProperty, "WindowBackground");
        SetResourceReference(ForegroundProperty, "WindowForeground");
        surface.SetResourceReference(Border.BackgroundProperty, "WindowBackground");
        var layout = new StackPanel();
        layout.Children.Add(new TextBlock { Text = "写作工作台 " + UpdateService.ClientVersion, Opacity = 0.65, Margin = new(0, 0, 0, 12) });
        layout.Children.Add(message); layout.Children.Add(bar); layout.Children.Add(details);
        var actions = new WrapPanel();
        foreach (var button in new[] { checkButton, downloadButton, installButton, folderButton, cancelButton, closeButton }) actions.Children.Add(button);
        layout.Children.Add(actions); surface.Child = layout; Content = surface;
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
        window.Show();
        if (UpdateService.ActiveInstaller is { } running) { window.WatchInstaller(running); return; }
        await window.CheckAsync();
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
            details.Text = "未完成的临时下载已清理；已校验的完整安装包仍可复用。可重新检查或重试。";
            bar.IsIndeterminate = false;
        }
        catch (Exception error)
        {
            if (closed) return;
            message.Text = "更新未完成：" + error.Message;
            details.Text = "已保留当前页面和完整下载。可重新校验后重试。";
            bar.IsIndeterminate = false;
            downloadButton.Content = "重试下载";
            if (result?.State == "available") downloadButton.Visibility = Visibility.Visible;
        }
    }

    private void SetBusy(bool busy)
    {
        checkButton.IsEnabled = downloadButton.IsEnabled = installButton.IsEnabled = !busy && UpdateService.ActiveInstaller is null;
        folderButton.IsEnabled = !busy;
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
            details.Text = "读取官方 Windows 独立正式发布。";
            bar.Visibility = Visibility.Visible; bar.IsIndeterminate = true;
            var found = await Task.Run(() => service.CheckAsync(token), token); token.ThrowIfCancellationRequested();
            result = found; message.Text = found.Message; bar.IsIndeterminate = false; bar.Visibility = Visibility.Collapsed;
            details.Text = found.State == "available"
                ? "安装包大小：" + UpdateService.FormatBytes(found.AssetSize) + "。下载完成后核对官方 SHA256。"
                : "当前客户端版本：" + UpdateService.ClientVersion;
            if (found.State == "available")
            {
                downloadButton.Content = "下载更新"; downloadButton.Visibility = Visibility.Visible;
                verified = await service.ReuseCachedAsync(found, CacheDirectory, Progress(token), token);
                token.ThrowIfCancellationRequested();
                if (verified is not null) { ShowReady(verified); message.Text = "已找到本地安装包，重新校验通过。"; }
            }
            else UpdateService.PruneCache(CacheDirectory);
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
            var progress = Progress(token);
            var download = await Task.Run(() => service.DownloadAsync(selected, CacheDirectory, progress, token), token);
            token.ThrowIfCancellationRequested(); verified = download; ShowReady(download);
        });
    }

    private static string CacheDirectory => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StoryWorkbench", "updates");

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
            ? "已核对官方大小及 SHA256。再次校验并检查全部标签的待保存文字后，将正常退出工作台并启动安装。取消安装后，重新打开工作台可复用此安装包。"
            : "便携 ZIP 已校验。请退出客户端后解压完整目录并手工替换；若文件被占用，请保存并正常停止对应作品服务。";
    }

    private async Task InstallAsync()
    {
        if (verified is null) return;
        if (UpdateService.ActiveInstaller is { } running) { WatchInstaller(running); return; }
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
                    installer = await service.LaunchInstallerAsync(checkedDownload, token);
                    if (installer.Completion.IsCompletedSuccessfully && installer.Completion.Result != 0)
                        throw new UpdateException("安装向导未能继续，已保留当前页面和下载，可重试。");
                    WatchInstaller(installer);
                    host.CloseForUpdate();
                }
                catch (Exception error) { launchError = error; }
            });
            bar.IsIndeterminate = false;
            if (launchError is not null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(launchError).Throw();
            if (!allowed || installer is null)
            { message.Text = "已保留当前页面。保存全部文字后可再次启动安装程序。"; return; }
            message.Text = "安装程序已启动，请查看安装向导。";
            details.Text = "工作台正在正常退出。安装向导会核对占用及服务状态；安装完成后重新打开工作台，核对运行版本。";
            bar.Visibility = installButton.Visibility = downloadButton.Visibility = Visibility.Collapsed;
            host.ShowStatus("更新安装向导已启动；当前运行版本仍为 " + UpdateService.ClientVersion + "。");
        });
    }

    private void WatchInstaller(UpdateInstallerSession session)
    {
        if (ReferenceEquals(observedInstaller, session)) return;
        observedInstaller = session;
        installer = session;
        message.Text = "安装向导正在运行，请完成或取消该向导。";
        SetBusy(IsBusy);
        _ = ObserveInstallerAsync(session);
    }

    private async Task ObserveInstallerAsync(UpdateInstallerSession session)
    {
        int? exit = null;
        try { exit = await session.Completion; } catch { }
        if (closed || !ReferenceEquals(installer, session)) return;
        installer = null;
        if (verified is not null) ShowReady(verified);
        message.Text = exit == 0 ? "安装向导已结束。重新打开工作台后核对版本。"
            : "安装向导已取消或未完成，可使用已下载文件重试。";
        details.Text = "当前运行版本仍为 " + UpdateService.ClientVersion + "。重新检查时会再次校验并复用完整下载。";
        SetBusy(IsBusy);
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
        SyntheticCaptureDiagnostics = new Dictionary<string, object> { ["ok"] = false, ["stage"] = "not_captured" };
        var window = new UpdateWindow(owner);
        try
        {
            window.checkButton.IsEnabled = window.downloadButton.IsEnabled = false;
            window.Show();
            window.ShowProgress(new("downloading", 6 * 1024 * 1024, 10 * 1024 * 1024, 1024 * 1024, 4));
            window.message.Text = "界面验证：合成下载进度，不在下载真实更新。";
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Render);
            window.UpdateLayout();
            var dpi = VisualTreeHelper.GetDpi(window.surface);
            // Capture only whole physical pixels; a ceiled partial edge is naturally translucent.
            var bitmap = new RenderTargetBitmap((int)Math.Floor(window.surface.ActualWidth * dpi.DpiScaleX),
                (int)Math.Floor(window.surface.ActualHeight * dpi.DpiScaleY), dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
            // Capture the displayed content itself, including its real background, not a synthetic canvas.
            bitmap.Render(window.surface);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var stream = new FileStream(pngPath, FileMode.Create, FileAccess.Write, FileShare.None)) encoder.Save(stream);
            return window.IsVisible && window.bar.Value == 60 && window.details.Text.Contains("MiB/秒", StringComparison.Ordinal)
                && window.details.Text.Contains("4 秒", StringComparison.Ordinal) && new FileInfo(pngPath).Length > 0
                && VerifyVisibleCapture(window, bitmap);
        }
        finally { window.Close(); }
    }

    private static bool VerifyVisibleCapture(UpdateWindow window, BitmapSource bitmap)
    {
        if (window.surface.Background is not SolidColorBrush background || background.Color.A != 255 || background.Opacity != 1
            || window.Foreground is not SolidColorBrush foreground || bitmap.PixelWidth < 10 || bitmap.PixelHeight < 10)
        {
            SyntheticCaptureDiagnostics = new Dictionary<string, object> { ["ok"] = false, ["stage"] = "visible_brush_required" };
            return false;
        }
        var pixels = new byte[bitmap.PixelWidth * bitmap.PixelHeight * 4];
        bitmap.CopyPixels(pixels, bitmap.PixelWidth * 4, 0);
        // WPF's 8-bit premultiplied antialiasing can round an opaque composited glyph to alpha 254.
        // Accept exactly that one quantization level; truly translucent pixels still fail the 99% gate.
        const byte alphaThreshold = 254;
        int opaque = 0, exactOpaque = 0, minimumAlpha = 255;
        for (int i = 3; i < pixels.Length; i += 4)
        {
            if (pixels[i] >= alphaThreshold) opaque++;
            if (pixels[i] == 255) exactOpaque++;
            minimumAlpha = Math.Min(minimumAlpha, pixels[i]);
        }
        double fraction = opaque / (double)(bitmap.PixelWidth * bitmap.PixelHeight);
        // (1,1) is inside the actual Border padding, outside all text and progress controls.
        int sample = (bitmap.PixelWidth + 1) * 4;
        bool backgroundMatches = pixels[sample] == background.Color.B && pixels[sample + 1] == background.Color.G
            && pixels[sample + 2] == background.Color.R && pixels[sample + 3] == 255;
        double alpha = foreground.Color.A / 255.0 * foreground.Opacity;
        double Linear(double channel) => channel <= 0.04045 ? channel / 12.92 : Math.Pow((channel + 0.055) / 1.055, 2.4);
        double Luminance(double r, double g, double b) => 0.2126 * Linear(r) + 0.7152 * Linear(g) + 0.0722 * Linear(b);
        double backgroundLuminance = Luminance(background.Color.R / 255.0, background.Color.G / 255.0, background.Color.B / 255.0);
        double textLuminance = Luminance((foreground.Color.R * alpha + background.Color.R * (1 - alpha)) / 255,
            (foreground.Color.G * alpha + background.Color.G * (1 - alpha)) / 255,
            (foreground.Color.B * alpha + background.Color.B * (1 - alpha)) / 255);
        double contrast = (Math.Max(backgroundLuminance, textLuminance) + 0.05) / (Math.Min(backgroundLuminance, textLuminance) + 0.05);
        bool passed = fraction >= 0.99 && backgroundMatches && contrast >= 4.5;
        SyntheticCaptureDiagnostics = new Dictionary<string, object> { ["ok"] = passed, ["background"] = background.Color.ToString(),
            ["foreground"] = foreground.Color.ToString(), ["opaque_fraction"] = fraction,
            ["alpha_threshold"] = alphaThreshold, ["minimum_alpha"] = minimumAlpha,
            ["exact_opaque_fraction"] = exactOpaque / (double)(bitmap.PixelWidth * bitmap.PixelHeight),
            ["background_pixel_matches"] = backgroundMatches, ["contrast_ratio"] = contrast };
        return passed;
    }
}
