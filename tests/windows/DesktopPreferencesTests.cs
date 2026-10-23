using System.Diagnostics;
using System.Text.Json;
using StoryWorkbench;

public static class DesktopPreferencesTests
{
    public static void Register(Suite suite)
    {
        suite.Test("only unexposed or verified replacement failures can be removed", () =>
        {
            var first = new PageLifecycle(); Suite.Check(!first.CanDiscardFailed); first.Fail(); Suite.Check(first.CanDiscardFailed);
            first.BeginVerifiedNavigation(); first.Complete(true); first.Fail(); Suite.Check(!first.CanDiscardFailed, "crash after input must remain protected");
            first.BeginVerifiedNavigation(); first.Complete(false); Suite.Check(first.CanDiscardFailed);
            first.BeginVerifiedNavigation(); first.Complete(true); first.AllowInput(); first.Fail(); Suite.Check(!first.CanDiscardFailed);
            first.BeginVerifiedNavigation(); first.AllowInput(); first.Fail(); Suite.Check(!first.CanDiscardFailed, "input resumed after navigation exception must remain protected");
        });
        suite.Test("shelf busy composition and pending forms block destructive operations", () =>
        {
            foreach (string field in new[] { "busy", "composing", "pending_form" })
            {
                var fields = new Dictionary<string, object> { ["kind"] = "shelf", ["documents"] = Array.Empty<object>(), ["busy"] = false, ["composing"] = false, ["pending_form"] = false };
                fields[field] = true;
                var state = BrowserSafety.ParseState(JsonSerializer.Serialize(fields));
                Suite.Check(state is not null && !BrowserSafety.CanProceed(state), field);
                var snapshot = new SafetySnapshot(1, new PageKey(8765, "/"), state!);
                Suite.Check(!BrowserSafety.CanCommit(snapshot, snapshot), field);
            }
        });
        suite.Test("preferences survive restart while rejecting manuscript paths URLs and unknown fields", () =>
        {
            string dir = Path.Combine(Path.GetTempPath(), "story-prefs-" + Guid.NewGuid().ToString("N"));
            try
            {
                var first = new ThemePreferences(dir);
                var valid = new Dictionary<string, string>
                {
                    ["story-last-books"] = "[\"0123456789abcdef0123\"]",
                    ["story-reading"] = "{\"size\":\"22\",\"line\":\"2.2\",\"font\":\"sans\",\"focus\":true}",
                    ["story-columns"] = "{\"left\":280,\"right\":240,\"navClosed\":true,\"contextClosed\":false}",
                    ["story-shelf-view"] = "{\"filter\":\"short\",\"sort\":\"oldest\",\"status\":\"completed\"}"
                };
                foreach (var item in valid) Suite.Check(first.SaveStorage(item.Key, item.Value));
                first.Save("dark"); var next = new ThemePreferences(dir);
                Suite.Check(next.Theme == "dark" && next.Storage.Count == 4);
                foreach (var item in valid) Suite.Check(next.Storage[item.Key] == ThemePreferences.ValidateStorage(item.Key, item.Value));
                foreach (var item in new[] {
                    ("manuscript", "正文不许保存"), ("story-last-books", "[\"http://127.0.0.1:8765/private-token/\"]"),
                    ("story-last-books", "[\"C:\\\\Books\\\\private.md\"]"),
                    ("story-reading", "{\"size\":\"22\",\"line\":\"2.2\",\"font\":\"sans\",\"focus\":true,\"text\":\"正文不许保存\"}"),
                    ("story-columns", "{\"left\":999999,\"right\":240,\"navClosed\":true,\"contextClosed\":false}") })
                    Suite.Check(!first.SaveStorage(item.Item1, item.Item2));
                string disk = File.ReadAllText(Path.Combine(dir, "desktop-preferences.json"));
                Suite.Check(!disk.Contains("private") && !disk.Contains("正文") && !disk.Contains("manuscript"));
                Suite.Check(!Directory.EnumerateFiles(dir, "*.tmp").Any());
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        });
        suite.TestAsync("production shelf probe sees forms IME and all busy flags", async () =>
        {
            await Node("const probe=" + JsonSerializer.Serialize(BrowserSafety.ProbeScript()) + ";\n" + """
            const vm=require('node:vm'),assert=require('node:assert/strict'),fields=[];
            const path={value:''},window={__storyNativeComposing:false};window.top=window;
            const context={window,document:{getElementById:id=>id==='library-heading'?{}:id==='book-path'?path:null,querySelectorAll:()=>fields},busy:false,managerBusy:false,statusSaving:new Set()};
            const read=()=>vm.runInNewContext(probe,context);
            assert.equal(read().busy,false);assert.equal(read().pending_form,false);
            for(const flag of ['busy','managerBusy']){context[flag]=true;assert.equal(read().busy,true);context[flag]=false;}
            context.statusSaving.add('book');assert.equal(read().busy,true);context.statusSaving.clear();
            window.__storyNativeComposing=true;assert.equal(read().composing,true);window.__storyNativeComposing=false;
            path.value='C:\\SyntheticOnly\\未添加';assert.equal(read().pending_form,true);path.value='';
            fields.push({tagName:'INPUT',type:'text',value:'未创建书名',defaultValue:'',dataset:{}});assert.equal(read().pending_form,true);fields.length=0;
            fields.push({tagName:'SELECT',value:'completed',dataset:{bookStatusOriginal:'serializing'}});assert.equal(read().pending_form,true);
            fields[0].value='serializing';assert.equal(read().pending_form,false);
            """);
        });
        suite.TestAsync("production bootstrap restores allowed preferences and forwards only allowed storage keys", async () =>
        {
            var values = new Dictionary<string, string> { ["story-last-books"] = "[\"0123456789abcdef0123\"]", ["story-reading"] = "{\"size\":\"22\",\"line\":\"2.2\",\"font\":\"sans\",\"focus\":true}" };
            await Node("const script=" + JsonSerializer.Serialize(BrowserSafety.BootstrapScript("system", values)) + ",release=" + JsonSerializer.Serialize(BrowserSafety.ReleaseInitialInputScript) + ";\n" + """
            const vm=require('node:vm'),assert=require('node:assert/strict');
            class Storage{constructor(){this.data=new Map()}setItem(k,v){this.data.set(String(k),String(v))}getItem(k){return this.data.get(k)||null}}
            const localStorage=new Storage(),events=new Map(),messages=[],control={value:'system'},window={chrome:{webview:{postMessage:message=>messages.push(message)}}};window.top=window;
            const context={window,location:{protocol:'http:',hostname:'127.0.0.1'},document:{cookie:'',getElementById:()=>control},localStorage,Storage,addEventListener:(name,fn)=>{const rows=events.get(name)||[];rows.push(fn);events.set(name,rows)},removeEventListener:(name,fn)=>events.set(name,(events.get(name)||[]).filter(row=>row!==fn))};
            vm.runInNewContext(script,context);
            let prevented=0;events.get('beforeinput')[0]({preventDefault(){prevented++},stopImmediatePropagation(){prevented++}});assert.equal(prevented,2);
            assert.equal(vm.runInNewContext(release,context),true);assert.equal(events.get('beforeinput').length,0);assert.equal(events.get('compositionstart').length,1);
            assert.equal(localStorage.getItem('story-last-books'),'["0123456789abcdef0123"]');assert.equal(JSON.parse(localStorage.getItem('story-reading')).size,'22');
            localStorage.setItem('manuscript','不许转发正文');assert.equal(messages.length,0);
            localStorage.setItem('story-last-books','[]');assert.equal(messages[0].kind,'preference');assert.equal(messages[0].key,'story-last-books');
            messages.length=0;events.get('change')[0]({target:{id:'font-size'}});assert.equal(messages.length,0);
            events.get('change')[0]({target:{id:'workbench-theme'}});assert.equal(messages[0].value,'system');
            """);
        });
        suite.TestAsync("persisted preferences survive JavaScript result escaping without accepting wrong values", async () =>
        {
            const string keys = "[\"0123456789abcdef0123\"]";
            string dir = Path.Combine(Path.GetTempPath(), "story-prefs-wire-" + Guid.NewGuid().ToString("N"));
            try
            {
                Suite.Check(new ThemePreferences(dir).SaveStorage("story-last-books", keys));
                var restored = new ThemePreferences(dir);
                string wire = await Node("const script=" + JsonSerializer.Serialize(BrowserSafety.BootstrapScript("system", restored.Storage)) + ";\n" + """
                const vm=require('node:vm');
                class Storage{constructor(){this.data=new Map()}setItem(k,v){this.data.set(String(k),String(v))}getItem(k){return this.data.get(k)||null}}
                const localStorage=new Storage(),window={};window.top=window;
                const context={window,location:{protocol:'http:',hostname:'127.0.0.1'},document:{cookie:''},localStorage,Storage,addEventListener:()=>{}};
                vm.runInNewContext(script,context);
                process.stdout.write(JSON.stringify(localStorage.getItem('story-last-books')));
                """);
                Suite.Check(wire != JsonSerializer.Serialize(keys), "fixture must reproduce the differing JSON escape representations");
                Suite.Check(BrowserSafety.ParseScriptString(wire) == keys, "JavaScript result must restore the exact persisted value");
                Suite.Check(BrowserSafety.ParseScriptString(JsonSerializer.Serialize(keys)) == keys);
                foreach (string invalid in new[] { "null", "true", "123", "{}", "[]", "\"unterminated", "\"value\" trailing" })
                    Suite.Check(BrowserSafety.ParseScriptString(invalid) is null, "non-string or invalid result must be rejected");
                Suite.Check(BrowserSafety.ParseScriptString(JsonSerializer.Serialize("[]")) != keys, "different valid string must still fail");
                Suite.Check(BrowserSafety.ParseScriptString(JsonSerializer.Serialize("[\"ffffffffffffffffffff\"]")) != keys);
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        });
    }
    private static async Task<string> Node(string script)
    {
        var start = new ProcessStartInfo(Environment.GetEnvironmentVariable("STORY_TEST_NODE") ?? "node") { UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using var process = Process.Start(start)!; var stdout = process.StandardOutput.ReadToEndAsync(); var stderr = process.StandardError.ReadToEndAsync();
        await process.StandardInput.WriteAsync(script); process.StandardInput.Close(); await process.WaitForExitAsync();
        string output = await stdout;
        Suite.Check(process.ExitCode == 0, output + await stderr);
        return output;
    }
}
