using System.Text.Json;

var suite = new Suite();
BrowserSafetyTests.Register(suite);
DesktopPreferencesTests.Register(suite);
UpdateServiceTests.Register(suite);
return await suite.RunAsync(args);

public sealed class Suite
{
    private readonly List<(string Name, Func<Task> Test, bool Windows)> tests = [];
    public void Test(string name, Action action) => TestAsync(name, () => { action(); return Task.CompletedTask; });
    public void TestAsync(string name, Func<Task> action) => tests.Add((name, action, false));
    public void TestWindowsAsync(string name, Func<Task> action) => tests.Add((name, action, true));
    public static void Check(bool value, string message = "assertion failed")
    { if (!value) throw new Exception(message); }
    public static T Throws<T>(Action action) where T : Exception
    {
        try { action(); } catch (T error) { return error; }
        throw new Exception($"Expected {typeof(T).Name}");
    }
    public static async Task<T> ThrowsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); } catch (T error) { return error; }
        throw new Exception($"Expected {typeof(T).Name}");
    }
    public async Task<int> RunAsync(string[] args)
    {
        int passed = 0, skipped = 0; var failed = new List<object>();
        foreach (var test in tests)
        {
            if (test.Windows && !OperatingSystem.IsWindows())
            { skipped++; Console.WriteLine("SKIP Windows: " + test.Name); continue; }
            try { await test.Test().WaitAsync(TimeSpan.FromSeconds(60)); passed++; Console.WriteLine("PASS " + test.Name); }
            catch (Exception error) { failed.Add(new { name = test.Name, error = error.ToString() }); Console.Error.WriteLine("FAIL " + test.Name + "\n" + error); }
        }
        var report = JsonSerializer.Serialize(new { ok = failed.Count == 0, total = tests.Count, passed, skipped, failed }, new JsonSerializerOptions { WriteIndented = true });
        Console.WriteLine(report);
        int index = Array.IndexOf(args, "--report");
        if (index >= 0 && index + 1 < args.Length)
        {
            string path = Path.GetFullPath(args[index + 1]); Directory.CreateDirectory(Path.GetDirectoryName(path)!); await File.WriteAllTextAsync(path, report);
        }
        return failed.Count == 0 ? 0 : 1;
    }
}
