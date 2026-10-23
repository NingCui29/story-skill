using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace StoryWorkbench;

public sealed record PageKey(int Port, string Path);

public sealed record DocumentBuffer(string Id, string Title, string Path, bool Dirty, bool Saving,
    bool Recovering, bool NeedsRecovery, bool PendingReadback, bool FailedNewVersion,
    bool RecoveryCurrent, string? Text)
{
    public bool Pending => Dirty || NeedsRecovery || PendingReadback || FailedNewVersion;
}

public sealed record CloseState(string Kind, IReadOnlyList<DocumentBuffer> Documents,
    bool Busy, bool Composing, string Fingerprint, bool PendingForm = false)
{
    public IReadOnlyList<DocumentBuffer> Pending => Documents.Where(d => d.Pending).ToArray();
    public bool Saving => Documents.Any(d => d.Saving || d.Recovering);
}

public sealed record SafetySnapshot(long Generation, PageKey Key, CloseState State);

/// <summary>Pure validation and export logic, also exercised outside the Windows UI.</summary>
public static class BrowserSafety
{
    private static readonly Regex Controlled = new(@"\Ahttp://127\.0\.0\.1:([0-9]{1,5})(/|/[A-Za-z0-9_-]{20,128}/)(?:#[^\r\n]*)?\z", RegexOptions.CultureInvariant);
    public static PageKey? PageKeyFor(string? address)
    {
        if (address is null || address.Length > 32768) return null;
        var match = Controlled.Match(address);
        if (!match.Success || !int.TryParse(match.Groups[1].Value, out int port) || port is < 1 or > 65535)
            return null;
        if (!Uri.TryCreate(address, UriKind.Absolute, out var uri) || uri.Host != "127.0.0.1"
            || uri.Scheme != "http" || uri.Port != port || uri.UserInfo.Length != 0 || uri.Query.Length != 0)
            return null;
        return new(port, match.Groups[2].Value);
    }

    public static bool IsLocalUrl(string? address) => PageKeyFor(address) is not null;
    public static bool IsExternalUrl(string? address)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")
            || uri.UserInfo.Length != 0 || uri.IsLoopback || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase))
            return false;
        return !IPAddress.TryParse(uri.Host, out var ip) || !IPAddress.IsLoopback(ip);
    }

    public static bool CanProceed(CloseState? state) => state is not null && !state.Busy && !state.Composing
        && !state.Saving && !state.PendingForm && state.Pending.Count == 0;
    public static bool SameSnapshot(CloseState before, CloseState after) => before.Fingerprint == after.Fingerprint;
    public static bool CanCommit(SafetySnapshot before, SafetySnapshot after) => before.Generation == after.Generation
        && before.Key == after.Key && !after.State.Busy && !after.State.Composing && !after.State.Saving && !after.State.PendingForm
        && SameSnapshot(before.State, after.State);

    public static string? ParseScriptString(string json)
    {
        // ExecuteScriptAsync returns JSON. Equivalent quote/Unicode escaping is not a value difference.
        if (json.Length > 16 * 1024 * 1024) return null;
        try
        {
            using var parsed = JsonDocument.Parse(json, new() { MaxDepth = 4 });
            return parsed.RootElement.ValueKind == JsonValueKind.String ? parsed.RootElement.GetString() : null;
        }
        catch (JsonException) { return null; }
    }

    public static CloseState? ParseState(string json, bool includeText = true)
    {
        try
        {
            if (json.Length > 64 * 1024 * 1024) return null;
            using var parsed = JsonDocument.Parse(json, new() { MaxDepth = 16 });
            var root = parsed.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !String(root, "kind", out var kind)
                || kind is not ("shelf" or "editor") || !Bool(root, "busy", out var busy)
                || !Bool(root, "composing", out var composing) || !root.TryGetProperty("documents", out var rows)
                || rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() > 10000) return null;
            var documents = new List<DocumentBuffer>();
            var ids = new HashSet<string>(System.StringComparer.Ordinal);
            foreach (var row in rows.EnumerateArray())
            {
                if (!String(row, "id", out var id) || !ids.Add(id) || !String(row, "title", out var title)
                    || !String(row, "path", out var path) || !Bool(row, "dirty", out var dirty)
                    || !Bool(row, "saving", out var saving) || !Bool(row, "recovering", out var recovering)
                    || !Bool(row, "needs_recovery", out var recovery) || !Bool(row, "pending_readback", out var readback)
                    || !Bool(row, "failed_new_version", out var newVersion) || !Bool(row, "recovery_current", out var current)) return null;
                string? text = null;
                if (row.TryGetProperty("text", out var textValue) && textValue.ValueKind == JsonValueKind.String)
                    text = textValue.GetString();
                else if (!row.TryGetProperty("text", out textValue) || textValue.ValueKind != JsonValueKind.Null) return null;
                if (includeText && (dirty || recovery || readback || newVersion) && text is null) return null;
                documents.Add(new(id, title, path, dirty, saving, recovering, recovery, readback, newVersion, current, text));
            }
            bool pendingForm = false;
            if (root.TryGetProperty("pending_form", out _) && !Bool(root, "pending_form", out pendingForm)) return null;
            if (kind == "shelf" && documents.Count != 0 || kind == "editor" && pendingForm) return null;
            // Canonical typed representation avoids property-order differences between probes.
            string canonical = JsonSerializer.Serialize(new { kind, busy, composing, documents, pendingForm });
            return new(kind!, documents, busy, composing, Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))), pendingForm);
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or ArgumentException) { return null; }
    }

    private static bool String(JsonElement row, string key, out string value)
    {
        value = "";
        if (row.ValueKind != JsonValueKind.Object || !row.TryGetProperty(key, out var item) || item.ValueKind != JsonValueKind.String) return false;
        value = item.GetString()!;
        return value.Length <= 16 * 1024 * 1024;
    }
    private static bool Bool(JsonElement row, string key, out bool value)
    {
        value = false;
        if (row.ValueKind != JsonValueKind.Object || !row.TryGetProperty(key, out var item)
            || item.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) return false;
        value = item.GetBoolean(); return true;
    }

    public static string BootstrapScript(string theme, IReadOnlyDictionary<string, string>? preferences = null)
    {
        theme = theme is "light" or "dark" ? theme : "system";
        return """
        (()=>{
          if(window!==window.top || location.protocol!=='http:' || location.hostname!=='127.0.0.1')return;
          const inputEvents=['beforeinput','keydown','keyup','paste','cut','drop','click','dblclick','pointerdown','mousedown','touchstart','submit','compositionstart','compositionupdate','compositionend'];
          const blockInitialInput=event=>{event.preventDefault();event.stopImmediatePropagation()};
          window.__storyNativeInitialInput={events:inputEvents,block:blockInitialInput};
          for(const name of inputEvents)addEventListener(name,blockInitialInput,{capture:true,passive:false});
          const theme=__THEME__;
          document.cookie='story_workbench_theme='+theme+'; Path=/; SameSite=Strict';
          try{localStorage.setItem('story-workbench-theme',theme);}catch{}
          const preferences=__PREFERENCES__,keys=['story-last-books','story-reading','story-columns','story-shelf-view'];
          try{for(const key of keys)if(typeof preferences[key]==='string')localStorage.setItem(key,preferences[key]);}catch{}
          if(typeof Storage!=='undefined'){
            const set=Storage.prototype.setItem;
            Storage.prototype.setItem=function(key,value){
              const result=set.call(this,key,value);
              if(this===localStorage&&keys.includes(key))window.chrome?.webview?.postMessage({kind:'preference',key,value:String(value)});
              return result;
            };
          }
          window.__storyNativeComposing=false;
          addEventListener('compositionstart',()=>{window.__storyNativeComposing=true},true);
          addEventListener('compositionend',()=>{window.__storyNativeComposing=false},true);
          const send=()=>{
            const value=document.getElementById('workbench-theme')?.value;
            if(['system','light','dark'].includes(value))window.chrome?.webview?.postMessage({kind:'theme',value});
          };
          addEventListener('change',event=>{if(event.target?.id==='workbench-theme')send()},true);
          addEventListener('DOMContentLoaded',send,{once:true});
        })();
        """.Replace("__THEME__", JsonSerializer.Serialize(theme)).Replace("__PREFERENCES__", JsonSerializer.Serialize(preferences ?? new Dictionary<string, string>()));
    }

    public static string ReleaseInitialInputScript => """
        (()=>{
          const state=window.__storyNativeInitialInput;
          if(!state)return true;
          for(const name of state.events)removeEventListener(name,state.block,true);
          delete window.__storyNativeInitialInput;
          return true;
        })()
        """;

    public static string FreezeScript(string token) => """
        (()=>{
          if(window.__storyNativeFreeze)return window.__storyNativeFreeze.token===__TOKEN__;
          if(!document.documentElement)return false;
          const events=['beforeinput','keydown','keyup','paste','cut','drop','click','dblclick','pointerdown','mousedown','touchstart','submit'];
          const block=e=>{e.preventDefault();e.stopImmediatePropagation()};
          const state={token:__TOKEN__,events,block,inert:document.documentElement.inert};
          window.__storyNativeFreeze=state;
          for(const name of events)addEventListener(name,block,{capture:true,passive:false});
          document.documentElement.inert=true;
          return true;
        })()
        """.Replace("__TOKEN__", JsonSerializer.Serialize(token));

    public static string ThawScript(string token) => """
        (()=>{
          const state=window.__storyNativeFreeze;
          if(!state || state.token!==__TOKEN__)return false;
          for(const name of state.events)removeEventListener(name,state.block,true);
          document.documentElement.inert=state.inert;
          delete window.__storyNativeFreeze;
          return true;
        })()
        """.Replace("__TOKEN__", JsonSerializer.Serialize(token));

    public static string ProbeScript(bool includeText = true) => """
        (()=>{
          if(window!==window.top)return null;
          if(document.getElementById('library-heading')){
            const shelfBusy=(typeof busy!=='undefined'&&!!busy)||(typeof managerBusy!=='undefined'&&!!managerBusy)
              ||(typeof statusSaving!=='undefined'&&statusSaving.size>0);
            const changed=element=>{
              if(element.dataset?.bookStatusOriginal!==undefined)return element.value!==element.dataset.bookStatusOriginal;
              if(element.type==='checkbox'||element.type==='radio')return element.checked!==element.defaultChecked;
              if(element.tagName==='SELECT'){
                const original=[...element.options].findIndex(option=>option.defaultSelected);
                return element.selectedIndex!==(original<0?0:original);
              }
              return element.value!==element.defaultValue;
            };
            const fields=[...(document.querySelectorAll('#book-manager[open] input,#book-manager[open] select,#book-manager[open] textarea,.book-status-editor select')||[])];
            const pendingForm=!!document.getElementById('book-path')?.value?.trim()||fields.some(changed);
            return {kind:'shelf',documents:[],busy:!!shelfBusy,composing:window.__storyNativeComposing===true,pending_form:pendingForm};
          }
          if(!document.getElementById('current-book-root') || typeof docs==='undefined' || !(docs instanceof Map))return null;
          if([...docs.values()].some(d=>!d || typeof d!=='object' || typeof d.editable!=='boolean'
              || typeof d.value!=='string' || (d.editable && typeof d.text!=='string')))return null;
          const editorBusy=(typeof loading!=='undefined' && !!loading)||(typeof reloading!=='undefined' && !!reloading)
              ||(typeof reloadPending!=='undefined' && !!reloadPending);
          const documents=[...docs].map(([id,d])=>{
            const textbox=document.getElementById('text');
            const value=typeof active!=='undefined' && id===active && d.editing && typeof textbox?.value==='string'?textbox.value:d.value;
            const dirty=!!(d.editable && value!==d.text);
            const pending_readback=!!d.pendingSaveReadback, failed_new_version=!!d.failedSaveOptions?.newVersion;
            const pending=dirty||!!d.needs_recovery||pending_readback||failed_new_version;
            return {id:String(id),title:String(d.title||'未命名文字'),path:String(d.path||''),dirty,
              saving:!!d.saving,recovering:!!d.recovering,needs_recovery:!!d.needs_recovery,
              pending_readback,failed_new_version,recovery_current:dirty&&d.recovered===value,
              text:__TEXT__&&pending?value:null};
          });
          return {kind:'editor',documents,busy:editorBusy,composing:window.__storyNativeComposing===true};
        })()
        """.Replace("__TEXT__", includeText ? "true" : "false");

    public static void ExportPendingBuffers(string destination, IEnumerable<CloseState> states)
    {
        var snapshots = states.ToArray();
        if (snapshots.Length == 0 || snapshots.Any(s => s is null || s.Busy || s.Composing || s.Saving))
            throw new InvalidOperationException("无法确认全部文字已读取；请返回作品保存。");
        var documents = snapshots.SelectMany(s => s.Pending).ToArray();
        if (documents.Length == 0 || documents.Any(d => d.Text is null))
            throw new InvalidOperationException("没有完整读取到待保存文字；请返回作品核对。");
        destination = System.IO.Path.GetFullPath(destination);
        if (!destination.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("请选择 ZIP 文件作为未保存文字导出位置。");
        if (File.Exists(destination) && File.GetAttributes(destination).HasFlag(FileAttributes.ReparsePoint))
            throw new IOException("导出目标不能是链接。");
        string temporary = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(destination)!, ".story-export-" + Guid.NewGuid().ToString("N") + ".zip");
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None))
            {
                using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
                {
                    var manifest = new List<object>();
                    for (int i = 0; i < documents.Length; i++)
                    {
                        var doc = documents[i];
                        string title = Regex.Replace(doc.Title, "[<>:\"/\\\\|?*\\x00-\\x1f]", "_").Trim(' ', '.');
                        if (title.Length > 80) title = title[..80];
                        if (title.Length > 0 && char.IsHighSurrogate(title[^1])) title = title[..^1];
                        string name = $"{i + 1:D3}-{(title.Length == 0 ? "未命名文字" : title)}.txt";
                        using (var writer = new StreamWriter(archive.CreateEntry(name).Open(), new UTF8Encoding(false, throwOnInvalidBytes: true))) writer.Write(doc.Text);
                        manifest.Add(new { id = doc.Id, title = doc.Title, path = doc.Path, dirty = doc.Dirty,
                            needs_recovery = doc.NeedsRecovery, recovery_current = doc.RecoveryCurrent,
                            pending_readback = doc.PendingReadback, failed_new_version = doc.FailedNewVersion, export_file = name });
                    }
                    using var note = new StreamWriter(archive.CreateEntry("导出说明.json").Open(), new UTF8Encoding(false));
                    note.Write(JsonSerializer.Serialize(new { version = 1, status = "unsaved_buffer_export",
                        note = "这是关闭前导出的待保存文字，尚未保存为候选或正式采用。原作品文件未改动。", documents = manifest }));
                }
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, destination, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
