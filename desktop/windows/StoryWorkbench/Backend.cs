using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace StoryWorkbench;

public sealed record BackendResult(string Url, bool Outdated, string Message);
public sealed record LaunchOptions(string? SelfTestReport, string? StateDirectory, int Port)
{
    public static LaunchOptions Parse(string[] args)
    {
        string? report = null, state = null;
        int port = 8765;
        for (int i = 0; i < args.Length; i++)
        {
            if (++i == args.Length) throw new ArgumentException("客户端启动参数缺少值。");
            switch (args[i - 1])
            {
                case "--self-test-report": report = Path.GetFullPath(args[i]); break;
                case "--state-dir": state = Path.GetFullPath(args[i]); break;
                case "--port":
                    if (!int.TryParse(args[i], out port) || port is < 1 or > 65535) throw new ArgumentException("书架端口无效。");
                    break;
                default: throw new ArgumentException("客户端启动参数无效。");
            }
        }
        if (report is not null && state is null) throw new ArgumentException("自检必须使用独立的 --state-dir。");
        return new(report, state, port);
    }
}

public static class Backend
{
    public static async Task<BackendResult> StartAsync(string root, LaunchOptions options)
    {
        var start = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"))
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
            RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = root
        };
        foreach (string name in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE" }) start.Environment.Remove(name);
        start.Environment["PYTHONNOUSERSITE"] = "1";
        start.Environment["PYTHONUTF8"] = "1";
        start.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        foreach (string item in new[] { "-s", "-B", Path.Combine(root, "backend_runner.py"), "--port", options.Port.ToString() })
            start.ArgumentList.Add(item);
        if (options.StateDirectory is not null)
        {
            start.ArgumentList.Add("--state-dir"); start.ArgumentList.Add(options.StateDirectory);
        }
        using var process = Process.Start(start) ?? throw new IOException("无法运行工作台内嵌服务。");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
        try
        {
            Task<string> stdout = ReadBoundedAsync(process.StandardOutput, timeout.Token);
            Task<string> stderr = ReadBoundedAsync(process.StandardError, timeout.Token);
            await Task.WhenAll(stdout, stderr, process.WaitForExitAsync(timeout.Token));
            using var parsed = JsonDocument.Parse(await stdout);
            var data = parsed.RootElement;
            if (process.ExitCode != 0 || !data.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True)
                throw new IOException(data.TryGetProperty("message", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() : "工作台服务启动失败。");
            string address = data.GetProperty("url").GetString() ?? "";
            if (BrowserSafety.PageKeyFor(address) is not { Path: "/" }) throw new IOException("工作台返回的本机书架地址无效。");
            return new(address, data.TryGetProperty("outdated", out var outdated) && outdated.ValueKind == JsonValueKind.True,
                data.TryGetProperty("message", out var message) && message.ValueKind == JsonValueKind.String ? message.GetString()! : "");
        }
        catch (OperationCanceledException)
        {
            // Only the short-lived launcher is owned here. Never terminate its bookshelf/editor children.
            if (!process.HasExited) process.Kill(entireProcessTree: false);
            throw new IOException("书架启动尚未确认。可能仍在启动的作品服务已保留，请稍后重试。");
        }
    }

    private static async Task<string> ReadBoundedAsync(StreamReader stream, CancellationToken token)
    {
        var result = new StringBuilder(); var buffer = new char[4096];
        int count;
        while ((count = await stream.ReadAsync(buffer.AsMemory(), token)) > 0)
        {
            if (result.Length + count > 1024 * 1024) throw new IOException("工作台启动信息超过限制。");
            result.Append(buffer, 0, count);
        }
        return result.ToString();
    }
}
