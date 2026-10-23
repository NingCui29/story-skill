using System.Text;
using System.Text.Json;
using System.Windows;

namespace StoryWorkbench;

public sealed class App : Application
{
    [STAThread]
    public static int Main(string[] args)
    {
        var app = new App { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Startup += async (_, _) =>
        {
            LaunchOptions? options = null;
            try
            {
                options = LaunchOptions.Parse(args);
                if (options.StateDirectory is not null) Directory.CreateDirectory(options.StateDirectory);
                var backend = await Backend.StartAsync(AppContext.BaseDirectory, options);
                var window = new MainWindow(options, backend);
                app.MainWindow = window;
                window.Show();
                await window.InitializeAsync();
            }
            catch (Exception error)
            {
                if (options?.SelfTestReport is { } report)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(report)!);
                    File.WriteAllText(report, JsonSerializer.Serialize(new { ok = false, error = error.GetType().Name,
                        ui_stack = "wpf-webview2", client_version = MainWindow.ClientVersion }), new UTF8Encoding(false));
                }
                else MessageBox.Show(error is IOException or ArgumentException ? error.Message
                    : "工作台窗口无法启动，请检查安装目录和 Microsoft Edge WebView2 Runtime 是否完整。", "写作工作台", MessageBoxButton.OK, MessageBoxImage.Error);
                app.Shutdown(2);
            }
        };
        return app.Run();
    }
}
