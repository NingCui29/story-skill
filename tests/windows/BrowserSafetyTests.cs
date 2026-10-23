using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using System.Text.Json;
using StoryWorkbench;

public static class BrowserSafetyTests
{
    private static readonly string Editor = "http://127.0.0.1:49152/" + new string('a', 32) + "/";
    private static Dictionary<string, object?> Buffer(string id = "hidden", string text = "隐藏的未保存文字") => new()
    {
        ["id"] = id, ["title"] = "../../同名:稿件", ["path"] = "原稿.md", ["dirty"] = true,
        ["saving"] = false, ["recovering"] = false, ["needs_recovery"] = false,
        ["pending_readback"] = false, ["failed_new_version"] = false, ["recovery_current"] = false, ["text"] = text
    };
    private static string State(object[] documents, string kind = "editor", bool busy = false, bool composing = false) =>
        JsonSerializer.Serialize(new { kind, documents, busy, composing });
    private static CloseState Parsed(params object[] documents) => BrowserSafety.ParseState(State(documents))!;
    private static string Temporary() { string path = Path.Combine(Path.GetTempPath(), "story-csharp-tests-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(path); return path; }

    public static void Register(Suite suite)
    {
        suite.Test("exact local page identities and fragment reuse", () =>
        {
            Suite.Check(BrowserSafety.PageKeyFor(Editor) == BrowserSafety.PageKeyFor(Editor + "#hidden.md"));
            Suite.Check(BrowserSafety.PageKeyFor(Editor) != BrowserSafety.PageKeyFor(Editor.Replace("49152", "49153")));
            Suite.Check(BrowserSafety.PageKeyFor("http://127.0.0.1:8765/") == new PageKey(8765, "/"));
            foreach (string address in new[] { "https://127.0.0.1:8765/", "http://localhost:8765/", "http://user@127.0.0.1:8765/", "http://127.0.0.1:65536/", "http://127.0.0.1:0/", "http://127.0.0.1/", "http://127.0.0.1:8765/../", Editor + "?other=1", Editor.Replace("aaaa", "%61%61%61%61"), "file:///manuscript", "javascript:alert(1)" })
                Suite.Check(BrowserSafety.PageKeyFor(address) is null, address);
        });
        suite.Test("external dispatch excludes executable and loopback URLs", () =>
        {
            Suite.Check(BrowserSafety.IsExternalUrl("https://github.com/NingCui29/story-skill"));
            foreach (string address in new[] { "file:///a.exe", "shell:AppsFolder", "javascript:alert(1)", "http://localhost/", "http://127.0.0.2/", "http://[::1]/", "https://user:pass@example.com/" })
                Suite.Check(!BrowserSafety.IsExternalUrl(address), address);
        });
        suite.Test("unknown, malformed and duplicate buffers are never clean", () =>
        {
            foreach (string json in new[] { "null", "{}", "[]", "not json", State([Buffer()], "shelf"), State([Buffer(), Buffer()]), State([], "remote") })
                Suite.Check(BrowserSafety.ParseState(json) is null);
            var bad = Buffer(); bad["dirty"] = 1;
            Suite.Check(BrowserSafety.ParseState(State([bad])) is null);
            bad = Buffer(); bad.Remove("saving");
            Suite.Check(BrowserSafety.ParseState(State([bad])) is null);
            bad = Buffer(); bad["text"] = null;
            Suite.Check(BrowserSafety.ParseState(State([bad])) is null);
            Suite.Check(!BrowserSafety.CanProceed(null));
        });
        suite.Test("recovery, readback and failed new-version requests stay pending", () =>
        {
            foreach (string field in new[] { "dirty", "needs_recovery", "pending_readback", "failed_new_version" })
            {
                var row = Buffer(); row["dirty"] = false; row[field] = true;
                var state = Parsed(row); Suite.Check(state.Pending.Count == 1 && !BrowserSafety.CanProceed(state), field);
            }
        });
        suite.Test("active save, recovery, busy and IME composition block transitions", () =>
        {
            foreach (string field in new[] { "saving", "recovering" })
            {
                var row = Buffer(); row["dirty"] = false; row[field] = true;
                Suite.Check(!BrowserSafety.CanProceed(Parsed(row)), field);
            }
            Suite.Check(!BrowserSafety.CanProceed(BrowserSafety.ParseState(State([], busy: true))));
            Suite.Check(!BrowserSafety.CanProceed(BrowserSafety.ParseState(State([], composing: true))));
            Suite.Check(BrowserSafety.CanProceed(Parsed()));
        });
        suite.Test("final snapshot prevents navigation and concurrent-text loss", () =>
        {
            var initial = new SafetySnapshot(7, BrowserSafety.PageKeyFor(Editor)!, Parsed(Buffer()));
            Suite.Check(BrowserSafety.CanCommit(initial, initial));
            Suite.Check(!BrowserSafety.CanCommit(initial, initial with { Generation = 8 }));
            Suite.Check(!BrowserSafety.CanCommit(initial, initial with { Key = new PageKey(49153, "/") }));
            Suite.Check(!BrowserSafety.CanCommit(initial, initial with { State = Parsed(Buffer(text: "同时到达的新文字")) }));
            var saving = Buffer(); saving["saving"] = true;
            Suite.Check(!BrowserSafety.CanCommit(initial, initial with { State = Parsed(saving) }));
        });
        suite.Test("ZIP exports all hidden text with safe unique names and explicit status", () =>
        {
            string folder = Temporary();
            try
            {
                string original = Path.Combine(folder, "原稿.md"), target = Path.Combine(folder, "全部.zip");
                File.WriteAllText(original, "原稿仍在"); File.WriteAllText(target, "previous export");
                BrowserSafety.ExportPendingBuffers(target, [Parsed(Buffer("one", "第一份\r\n原样")), Parsed(Buffer("two", "第二份😀"))]);
                using var archive = ZipFile.OpenRead(target);
                using var doc = JsonDocument.Parse(new StreamReader(archive.GetEntry("导出说明.json")!.Open()).ReadToEnd());
                Suite.Check(doc.RootElement.GetProperty("status").GetString() == "unsaved_buffer_export");
                var names = doc.RootElement.GetProperty("documents").EnumerateArray().Select(x => x.GetProperty("export_file").GetString()!).ToArray();
                Suite.Check(names.Length == 2 && names.Distinct().Count() == 2 && names.All(n => !n.Contains('/') && !n.Contains('\\')));
                Suite.Check(new StreamReader(archive.GetEntry(names[0])!.Open()).ReadToEnd() == "第一份\r\n原样");
                Suite.Check(new StreamReader(archive.GetEntry(names[1])!.Open()).ReadToEnd() == "第二份😀");
                Suite.Check(File.ReadAllText(original) == "原稿仍在");
                Suite.Check(!Directory.EnumerateFiles(folder, ".story-export-*").Any());
            }
            finally { Directory.Delete(folder, true); }
        });
        suite.Test("export errors preserve destination and do not leave temporary files", () =>
        {
            string folder = Temporary();
            try
            {
                string target = Path.Combine(folder, "blocked.zip"); Directory.CreateDirectory(target);
                File.WriteAllText(Path.Combine(target, "sentinel"), "keep");
                var blocked = Suite.Throws<Exception>(() => BrowserSafety.ExportPendingBuffers(target, [Parsed(Buffer())]));
                Suite.Check(blocked is IOException or UnauthorizedAccessException);
                Suite.Check(File.ReadAllText(Path.Combine(target, "sentinel")) == "keep");
                Suite.Check(!Directory.EnumerateFiles(folder, ".story-export-*").Any());
                Suite.Throws<InvalidOperationException>(() => BrowserSafety.ExportPendingBuffers(Path.Combine(folder, "empty.zip"), [Parsed()]));
                var busy = Buffer(); busy["saving"] = true;
                Suite.Throws<InvalidOperationException>(() => BrowserSafety.ExportPendingBuffers(Path.Combine(folder, "busy.zip"), [Parsed(busy)]));
                Suite.Throws<ArgumentException>(() => BrowserSafety.ExportPendingBuffers(Path.Combine(folder, "original.md"), [Parsed(Buffer())]));
            }
            finally { Directory.Delete(folder, true); }
        });
        suite.Test("theme preferences persist only the three supported values", () =>
        {
            string folder = Temporary();
            try
            {
                var settings = new ThemePreferences(folder); Suite.Check(settings.Theme == "system"); settings.Save("dark");
                Suite.Check(new ThemePreferences(folder).Theme == "dark"); settings.Save("http://127.0.0.1:8765/private-capability/");
                Suite.Check(new ThemePreferences(folder).Theme == "dark");
                Suite.Check(!File.ReadAllText(Path.Combine(folder, "desktop-preferences.json")).Contains("private-capability"));
            }
            finally { Directory.Delete(folder, true); }
        });
        suite.Test("self-test launcher requires an isolated state and valid port", () =>
        {
            Suite.Check(LaunchOptions.Parse([]).Port == 8765);
            foreach (string[] args in new[] { new[] { "--self-test-report", "receipt.json" }, new[] { "--port", "0" }, new[] { "--port", "70000" }, new[] { "--port" }, new[] { "--unknown", "x" } })
                Suite.Throws<ArgumentException>(() => LaunchOptions.Parse(args));
        });
        suite.TestAsync("production JavaScript sees hidden buffers, IME text, and save readback", async () =>
        {
            string fixture = """
            const assert=require('node:assert/strict'),vm=require('node:vm');
            const window={__storyNativeComposing:false};window.top=window;
            const box={value:'输入法刚提交的文字'};
            const docs=new Map([
              ['active',{title:'当前',editable:true,text:'原稿',value:'旧输入',editing:true}],
              ['hidden',{title:'隐藏',path:'隐藏.md',editable:true,text:'原稿',value:'未保存文字',recovered:'未保存文字'}],
              ['readback',{editable:true,text:'已存',value:'已存',pendingSaveReadback:{}}],
              ['version',{editable:true,text:'已存',value:'已存',failedSaveOptions:{newVersion:true}}],
              ['recover',{editable:true,text:'恢复',value:'恢复',recovering:true}]
            ]);
            const context={window,docs,active:'active',document:{getElementById:id=>id==='current-book-root'?{}:id==='text'?box:null},Map};
            const result=vm.runInNewContext(script,context);
            assert.equal(result.documents[0].text,'输入法刚提交的文字');
            assert.equal(result.documents[1].text,'未保存文字');assert.equal(result.documents[1].recovery_current,true);
            assert.equal(result.documents[2].pending_readback,true);assert.equal(result.documents[3].failed_new_version,true);
            assert.equal(result.documents[4].recovering,true);
            window.__storyNativeComposing=true;assert.equal(vm.runInNewContext(script,context).composing,true);
            docs.set('bad',{editable:1,value:3});assert.equal(vm.runInNewContext(script,context),null);
            """;
            await RunNodeAsync("const script=" + JsonSerializer.Serialize(BrowserSafety.ProbeScript()) + ";\n" + fixture);
        });
        suite.TestAsync("production input freeze and thaw require the owning token", async () =>
        {
            string fixture = """
            const assert=require('node:assert/strict'),vm=require('node:vm');
            const listeners=new Map(),window={},document={documentElement:{inert:false}};
            const context={window,document,addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:(n,f)=>{assert.equal(listeners.get(n),f);listeners.delete(n)}};
            assert.equal(vm.runInNewContext(freeze,context),true);assert.equal(document.documentElement.inert,true);
            assert.ok(listeners.has('beforeinput'));let blocked=0;listeners.get('beforeinput')({preventDefault(){blocked++},stopImmediatePropagation(){blocked++}});assert.equal(blocked,2);
            assert.equal(vm.runInNewContext(wrong,context),false);assert.ok(listeners.size>0);
            assert.equal(vm.runInNewContext(thaw,context),true);assert.equal(document.documentElement.inert,false);assert.equal(listeners.size,0);
            """;
            await RunNodeAsync("const freeze=" + JsonSerializer.Serialize(BrowserSafety.FreezeScript("owner")) + ",thaw=" + JsonSerializer.Serialize(BrowserSafety.ThawScript("owner")) + ",wrong=" + JsonSerializer.Serialize(BrowserSafety.ThawScript("other")) + ";\n" + fixture);
        });
    }
    private static async Task RunNodeAsync(string script)
    {
        string node = Environment.GetEnvironmentVariable("STORY_TEST_NODE") ?? "node";
        var start = new ProcessStartInfo(node) { UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using var process = Process.Start(start)!;
        var stdout = process.StandardOutput.ReadToEndAsync(); var stderr = process.StandardError.ReadToEndAsync();
        await process.StandardInput.WriteAsync(script); process.StandardInput.Close();
        await process.WaitForExitAsync(); Suite.Check(process.ExitCode == 0, await stdout + await stderr);
    }
}
