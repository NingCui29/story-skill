using System.Net;
using System.Text.Json.Nodes;
using StoryWorkbench;

internal static class UpdateBlockTests
{
    private const string Name = "story-workbench-0.3.0-windows-x86_64-setup.exe";
    private const string Url = "https://github.com/NingCui29/story-skill/releases/download/windows-v0.3.0/" + Name;
    private static byte[] Payload()
    { var bytes = new byte[8 * 131072 + 17]; new Random(17).NextBytes(bytes); return bytes; }
    private static (UpdateResult Package, UpdateDownloadPlan Plan, byte[] Json) Package(byte[] bytes)
    {
        var blocks = new JsonArray();
        for (int offset = 0; offset < bytes.Length; offset += 131072)
        {
            var part = bytes[offset..Math.Min(bytes.Length, offset + 131072)];
            blocks.Add(new JsonObject { ["offset"] = offset, ["size"] = part.Length, ["sha256"] = UpdateDownloadPlan.Digest(part) });
        }
        var document = new JsonObject { ["schema"] = 1, ["file"] = Name, ["size"] = bytes.Length,
            ["sha256"] = UpdateDownloadPlan.Digest(bytes), ["chunks"] = blocks };
        var encoded = System.Text.Encoding.UTF8.GetBytes(document.ToJsonString());
        var package = new UpdateResult("available", "fixture", "0.3.0", Url, Name, bytes.Length,
            UpdateDownloadPlan.Digest(bytes), PlanUrl: Url + ".download.json", PlanSize: encoded.Length,
            PlanSha256: UpdateDownloadPlan.Digest(encoded));
        return (package, UpdateDownloadPlan.Parse(encoded, package), encoded);
    }
    private sealed class Cache : IUpdateBlockCache
    {
        public readonly Dictionary<string, byte[]> Blocks = [];
        public Action? Saved;
        public byte[]? Read(UpdateBlock block) { lock (Blocks) return Blocks.GetValueOrDefault(block.Sha256); }
        public void Save(UpdateBlock block, byte[] bytes) { lock (Blocks) Blocks[block.Sha256] = bytes.ToArray(); Saved?.Invoke(); }
    }
    private sealed class Reports : IProgress<UpdateProgress>
    {
        public readonly List<UpdateProgress> Values = [];
        public void Report(UpdateProgress value) { lock (Values) Values.Add(value); }
    }
    private sealed class Server(byte[] payload, string mode = "good") : HttpMessageHandler
    {
        public readonly List<long> Offsets = [];
        public int Active, Peak;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            int active = Interlocked.Increment(ref Active);
            lock (Offsets) Peak = Math.Max(Peak, active);
            try
            {
                Suite.Check(request.Headers.Authorization is null && request.Headers.AcceptEncoding.ToString() == "identity");
                var range = request.Headers.Range!.Ranges.Single();
                long from = range.From!.Value, to = range.To!.Value;
                if (mode == "redirect" && request.RequestUri!.Host == "github.com")
                {
                    var redirect = new HttpResponseMessage(HttpStatusCode.Redirect);
                    redirect.Headers.Location = new Uri("https://release-assets.githubusercontent.com/github-production-release-asset/1/fixture?sig=test");
                    return redirect;
                }
                lock (Offsets) Offsets.Add(from);
                await Task.Delay(from == 0 ? 1 : 15, token);
                var bytes = payload[(int)from..((int)to + 1)];
                if (mode == "corrupt") bytes[0] ^= 0xff;
                var response = new HttpResponseMessage(mode == "unsupported" ? HttpStatusCode.OK : HttpStatusCode.PartialContent)
                    { Content = new ByteArrayContent(bytes) };
                response.Content.Headers.ContentRange = new(mode == "range" ? from + 1 : from, to, payload.Length);
                if (mode == "gzip") response.Content.Headers.ContentEncoding.Add("gzip");
                return response;
            }
            finally { Interlocked.Decrement(ref Active); }
        }
    }
    private sealed class PackageServer(byte[] payload, byte[] plan, bool ranges) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            if (request.RequestUri!.AbsolutePath.EndsWith(".download.json"))
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(plan) });
            var range = request.Headers.Range?.Ranges.Single();
            if (range is not null && ranges)
            {
                long from = range.From!.Value, to = range.To!.Value;
                var result = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(payload[(int)from..((int)to + 1)]) };
                result.Content.Headers.ContentRange = new(from, to, payload.Length);
                return Task.FromResult(result);
            }
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(payload) });
        }
    }

    public static void Register(Suite suite)
    {
        suite.Test("blocks: lean package selection requires existing WebView2 and official plan metadata", () => {
            JsonObject Asset(string name) => new() { ["name"] = name, ["state"] = "uploaded", ["size"] = 100,
                ["digest"] = "sha256:" + new string('a', 64),
                ["browser_download_url"] = "https://github.com/NingCui29/story-skill/releases/download/windows-v0.3.0/" + name };
            var lean = Name.Replace("-setup.exe", "-update-setup.exe");
            var plan = Asset(lean + ".download.json");
            var release = new JsonObject { ["draft"] = false, ["prerelease"] = false,
                ["html_url"] = "https://github.com/NingCui29/story-skill/releases/tag/windows-v0.3.0",
                ["assets"] = new JsonArray(Asset(Name), Asset(lean), plan) };
            UpdateResult Evaluate(bool ready) => UpdateService.EvaluateReleaseResponse(200,
                System.Text.Encoding.UTF8.GetBytes(release.ToJsonString()), "0.2.5", ready);
            Suite.Check(Evaluate(false).AssetName == Name && Evaluate(true).AssetName == lean);
            Suite.Check(Evaluate(true).PlanUrl.EndsWith(".download.json"));
            plan["digest"] = "unverified"; Suite.Check(Evaluate(true).State == "error");
            plan["digest"] = "sha256:" + new string('a', 64);
            plan["browser_download_url"] = "https://example.invalid/plan"; Suite.Check(Evaluate(true).State == "error");
        });
        suite.Test("blocks: exact plan identity, coverage, limits and official manifest digest", () => {
            var value = Package(Payload());
            Suite.Check(value.Plan.Size == value.Package.AssetSize && value.Plan.Blocks.Count == 9);
            Suite.Throws<UpdateException>(() => UpdateDownloadPlan.Parse(value.Json, value.Package with { PlanSha256 = new string('0', 64) }));
            foreach (var edit in new Action<JsonObject>[] {
                j => j["schema"] = 2, j => j["file"] = "another.exe", j => j["size"] = 9,
                j => j["chunks"]![1]!["offset"] = 0, j => j["chunks"]![0]!["size"] = 0,
                j => j["chunks"]![0]!["sha256"] = "bad", j => j["chunks"] = new JsonArray(),
                j => j["chunks"]![0]!["size"] = UpdateDownloadPlan.MaxBlockBytes + 1 })
            {
                var json = JsonNode.Parse(value.Json)!.AsObject(); edit(json);
                var encoded = System.Text.Encoding.UTF8.GetBytes(json.ToJsonString());
                Suite.Throws<UpdateException>(() => UpdateDownloadPlan.Parse(encoded,
                    value.Package with { PlanSize = encoded.Length, PlanSha256 = UpdateDownloadPlan.Digest(encoded) }));
            }
        });
        suite.TestAsync("blocks: at most three concurrent ranges and exact complete output", async () => {
            var bytes = Payload(); var value = Package(bytes); var cache = new Cache(); var reports = new Reports();
            var server = new Server(bytes); using var service = new UpdateService(server); using var output = new MemoryStream();
            await service.DownloadBlocksAsync(value.Package, value.Plan, cache, output, reports, default);
            Suite.Check(output.ToArray().SequenceEqual(bytes) && server.Peak == 3 && server.Active == 0);
            Suite.Check(reports.Values.Any(p => p.Stage == "assembling") && reports.Values.All(p => p.Received <= p.Total));
            int count = server.Offsets.Count; using var repeated = new MemoryStream();
            await service.DownloadBlocksAsync(value.Package, value.Plan, cache, repeated, reports, default);
            Suite.Check(server.Offsets.Count == count && repeated.ToArray().SequenceEqual(bytes));
            Suite.Check(reports.Values.Any(p => p.Reused == bytes.Length));
        });
        suite.TestAsync("blocks: pause waits for workers and resume skips verified cached data", async () => {
            var bytes = Payload(); var value = Package(bytes); var cache = new Cache();
            using var cancel = new CancellationTokenSource(); cache.Saved = () => cancel.Cancel();
            var first = new Server(bytes); using (var service = new UpdateService(first))
            using (var output = new MemoryStream())
            {
                await Suite.ThrowsAsync<OperationCanceledException>(() => service.DownloadBlocksAsync(value.Package, value.Plan, cache, output, null, cancel.Token));
                Suite.Check(first.Active == 0 && output.Length == 0 && cache.Blocks.Count > 0);
            }
            cache.Saved = null; var cachedOffsets = value.Plan.Blocks.Where(b => cache.Blocks.ContainsKey(b.Sha256)).Select(b => b.Offset).ToArray();
            var resumed = new Server(bytes); using var next = new UpdateService(resumed); using var complete = new MemoryStream();
            await next.DownloadBlocksAsync(value.Package, value.Plan, cache, complete, null, default);
            Suite.Check(complete.ToArray().SequenceEqual(bytes) && !resumed.Offsets.Intersect(cachedOffsets).Any());
        });
        suite.TestAsync("blocks: corrupt cache is redownloaded and bad network content never assembles", async () => {
            var bytes = Payload(); var value = Package(bytes); var cache = new Cache();
            foreach (var block in value.Plan.Blocks) cache.Blocks[block.Sha256] = bytes[(int)block.Offset..((int)block.Offset + block.Size)].ToArray();
            cache.Blocks[value.Plan.Blocks[0].Sha256][0] ^= 1;
            var server = new Server(bytes); using (var service = new UpdateService(server)) using (var output = new MemoryStream())
            { await service.DownloadBlocksAsync(value.Package, value.Plan, cache, output, null, default); Suite.Check(server.Offsets.SequenceEqual(new long[] { 0 })); }
            var bad = new Server(bytes, "corrupt"); using var rejected = new UpdateService(bad); using var empty = new MemoryStream();
            await Suite.ThrowsAsync<UpdateException>(() => rejected.DownloadBlocksAsync(value.Package, value.Plan, new Cache(), empty, null, default));
            Suite.Check(empty.Length == 0 && bad.Active == 0);
        });
        suite.TestAsync("blocks: unsupported range is distinct from wrong range and wrong encoding", async () => {
            var bytes = Payload(); var value = Package(bytes);
            foreach (var mode in new[] { "unsupported", "range", "gzip" })
            {
                var server = new Server(bytes, mode); using var service = new UpdateService(server); using var output = new MemoryStream();
                if (mode == "unsupported") await Suite.ThrowsAsync<UpdateRangeUnsupportedException>(() => service.DownloadBlocksAsync(value.Package, value.Plan, new Cache(), output, null, default));
                else await Suite.ThrowsAsync<UpdateException>(() => service.DownloadBlocksAsync(value.Package, value.Plan, new Cache(), output, null, default));
                Suite.Check(output.Length == 0 && server.Active == 0);
            }
        });
        suite.TestAsync("blocks: merging still requires the complete official package SHA256", async () => {
            var bytes = Payload(); var value = Package(bytes); var cache = new Cache();
            var server = new Server(bytes); using var service = new UpdateService(server); using var output = new MemoryStream();
            await Suite.ThrowsAsync<UpdateException>(() => service.DownloadBlocksAsync(value.Package,
                value.Plan with { Sha256 = new string('0', 64) }, cache, output, null, default));
        });
        suite.TestAsync("blocks: controlled redirects retain exact ranges on official HTTPS storage", async () => {
            var bytes = Payload(); var value = Package(bytes); var server = new Server(bytes, "redirect");
            using var service = new UpdateService(server); using var output = new MemoryStream();
            await service.DownloadBlocksAsync(value.Package, value.Plan, new Cache(), output, null, default);
            Suite.Check(output.ToArray().SequenceEqual(bytes));
        });
        suite.TestWindowsAsync("blocks: native cache and full fallback retain verified file identity", async () => {
            foreach (var ranges in new[] { true, false })
            {
                var folder = Path.Combine(Path.GetTempPath(), "story-update-blocks-" + Guid.NewGuid().ToString("N"));
                try
                {
                    var bytes = Payload(); var value = Package(bytes);
                    using var service = new UpdateService(new PackageServer(bytes, value.Json, ranges));
                    var result = await service.DownloadAsync(value.Package, folder);
                    Suite.Check(result.Identity is not null && File.ReadAllBytes(result.Path).SequenceEqual(bytes));
                    var repeated = await service.ReuseCachedAsync(value.Package, folder);
                    Suite.Check(repeated is not null && repeated.Identity == result.Identity);
                    Suite.Check(!Directory.EnumerateFiles(folder, "*.part", SearchOption.AllDirectories).Any());
                }
                finally { if (Directory.Exists(folder)) Directory.Delete(folder, true); }
            }
        });
    }
}
