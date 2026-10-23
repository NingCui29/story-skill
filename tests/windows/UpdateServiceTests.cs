using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using StoryWorkbench;

internal static class UpdateServiceTests
{
    private const string Name = "story-workbench-0.2.1-windows-x86_64-setup.exe";
    private const string Url = "https://github.com/NingCui29/story-skill/releases/download/v-test/" + Name;
    private const string Cdn = "https://release-assets.githubusercontent.com/github-production-release-asset/1/fixture?sig=test";
    private static readonly byte[] Payload = Encoding.UTF8.GetBytes(string.Concat(Enumerable.Repeat("MZ 合成安装文件，不执行。\r\n", 24000)));
    private static string Digest(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    private static UpdateResult Package() => new("available", "synthetic", "0.2.1", Url, Name, Payload.Length, Digest(Payload));

    private static JsonObject Release(string version = "0.2.1", string extension = "-setup.exe")
    {
        var name = $"story-workbench-{version}-windows-x86_64{extension}";
        return new JsonObject {
            ["draft"] = false, ["prerelease"] = false,
            ["tag_name"] = "v9.9.9-skill", ["html_url"] = "https://github.com/NingCui29/story-skill/releases/tag/v-test",
            ["assets"] = new JsonArray(new JsonObject { ["name"] = name, ["state"] = "uploaded", ["size"] = Payload.Length,
                ["digest"] = "sha256:" + Digest(Payload), ["browser_download_url"] = "https://github.com/NingCui29/story-skill/releases/download/v-test/" + name })
        };
    }
    private static JsonObject Asset(JsonObject release) => release["assets"]![0]!.AsObject();
    private static UpdateResult Evaluate(JsonObject release, string current = "0.2.0") => UpdateService.EvaluateReleaseResponse(200, Encoding.UTF8.GetBytes(release.ToJsonString()), current);
    private static JsonObject WindowsRelease(string version = "9.0.0")
    {
        var release = Release(version); string tag = "windows-v" + version;
        release["tag_name"] = tag;
        release["html_url"] = "https://github.com/NingCui29/story-skill/releases/tag/" + tag;
        Asset(release)["browser_download_url"] = "https://github.com/NingCui29/story-skill/releases/download/" + tag + "/" + Asset(release)["name"]!.GetValue<string>();
        return release;
    }

    public static void Register(Suite suite)
    {
        suite.Test("updates: desktop numeric versions, independent release tag and installer preference", () => {
            Suite.Check(Evaluate(Release()).State == "available");
            Suite.Check(Evaluate(Release()).Version == "0.2.1");
            Suite.Check(!Evaluate(Release()).Message.Contains("v9.9.9"));
            Suite.Check(Evaluate(Release("0.1.10"), "0.1.9").State == "available");
            Suite.Check(Evaluate(Release("0.2.0")).State == "current");
            Suite.Check(Evaluate(Release("0.1.9")).State == "ahead");
            Suite.Check(Evaluate(Release("0.1.9")).DownloadUrl == "");
            var packet = Release("0.2.1", ".zip"); packet["assets"]!.AsArray().Add(Asset(Release()).DeepClone());
            packet["assets"]!.AsArray().Add(Asset(Release("0.1.99")).DeepClone());
            Suite.Check(Evaluate(packet).AssetName == Name);
            foreach (var version in new[] { "v0.1.1", "0.1", "0.1.2.3", "0.1.1-rc1", "0.1.1\n" })
                Suite.Throws<FormatException>(() => UpdateService.CompareVersions(version, "0.2.0"));
        });
        suite.Test("updates: stable release flags, status and bounded malformed JSON", () => {
            foreach (var key in new[] { "draft", "prerelease" }) {
                var packet = Release(); packet[key] = true; Suite.Check(Evaluate(packet).State == "error");
                packet[key] = null; Suite.Check(Evaluate(packet).State == "error");
            }
            foreach (var text in new[] { "[]", "null", "not-json", "{}" })
                Suite.Check(UpdateService.EvaluateReleaseResponse(200, Encoding.UTF8.GetBytes(text)).State == "error");
            Suite.Check(UpdateService.EvaluateReleaseResponse(200, new byte[] { 255 }).State == "error");
            Suite.Check(UpdateService.EvaluateReleaseResponse(200, new byte[UpdateService.MaxResponseBytes + 1]).State == "error");
            Suite.Check(UpdateService.EvaluateReleaseResponse(404, Array.Empty<byte>()).State == "unavailable");
            Suite.Check(UpdateService.EvaluateReleaseResponse(403, Array.Empty<byte>()).Message.Contains("请求限额"));
            Suite.Check(UpdateService.EvaluateReleaseResponse(429, Array.Empty<byte>()).Message.Contains("请求限额"));
            Suite.Check(UpdateService.EvaluateReleaseResponse(500, Array.Empty<byte>()).Message.Contains("HTTP 500"));
            foreach (var name in new[] { "story-workbench-0.2.1-windows-arm64-setup.exe", "story-workbench-windows-x64.zip", "story-workbench-0.2.1-rc1-windows-x64.zip", "story-workbench-0.2.1-macos-arm64.dmg" }) {
                var packet = Release(); Asset(packet)["name"] = name; Suite.Check(Evaluate(packet).State == "unavailable");
            }
        });
        suite.Test("updates: exact official URLs and raw traversal boundaries", () => {
            Suite.Check(UpdateService.OfficialReleaseUrl(Url, true)); Suite.Check(UpdateService.PermittedTransferUrl(Cdn));
            foreach (var url in new[] { Url.Replace("https:", "http:"), Url.Replace("github.com", "github.com.evil.test"),
                Url.Replace("github.com", "user@github.com"), Url.Replace("github.com", "github.com:443"),
                Url.Replace("NingCui29", "other"), Url + "?redirect=evil", Url + "#fragment", Url.Replace("/v-test/", "/../"),
                Url.Replace("/v-test/", "/%2e%2e/"), Url.Replace("/v-test/", "/a%2fb/"), Url.Replace("/v-test/", "/a%5cb/"),
                Url.Replace("/v-test/", "/%252e%252e/"), Url.Replace("github.com", "git\nhub.com"), "file:///C:/evil.exe" })
                Suite.Check(!UpdateService.OfficialReleaseUrl(url, true), url);
            foreach (var url in new[] { Cdn.Replace("https:", "http:"), Cdn.Replace(".com/", ".com.evil.test/"),
                Cdn.Replace(".com/", ".com:443/"), Cdn.Replace("https://", "https://user:pass@"), Cdn + "#fragment",
                Cdn.Replace("/1/", "/../"), Cdn.Replace("/1/", "/%2e%2e/"), Cdn.Replace("/1/", "/a%2fb/"),
                Cdn.Replace("/github-production-release-asset/", "/arbitrary/") }) Suite.Check(!UpdateService.PermittedTransferUrl(url), url);
        });
        suite.Test("updates: metadata size, digest, release identity and sidecar binding", () => {
            foreach (var bad in new JsonNode?[] { 0, true, "42", UpdateService.MaxDownloadBytes + 1 }) {
                var packet = Release(); Asset(packet)["size"] = bad?.DeepClone(); Suite.Check(Evaluate(packet).State == "error");
            }
            foreach (var digest in new[] { "sha256:bad", "sha512:" + new string('a', 64) }) {
                var packet = Release(); Asset(packet)["digest"] = digest; Suite.Check(Evaluate(packet).State == "error");
            }
            foreach (var url in new[] { "https://evil.test/new.exe", Url.Replace("v-test", "other-tag"), Url.Replace(Name, "other.exe") }) {
                var packet = Release("0.2.0"); var invalid = Asset(Release()).DeepClone().AsObject(); invalid["browser_download_url"] = url;
                packet["assets"]!.AsArray().Add(invalid); Suite.Check(Evaluate(packet).State == "error");
            }
            var sidecar = Release(); Asset(sidecar).Remove("digest"); Suite.Check(Evaluate(sidecar).State == "error");
            sidecar["assets"]!.AsArray().Add(new JsonObject { ["name"] = Name + ".sha256", ["state"] = "uploaded", ["size"] = 128, ["browser_download_url"] = Url + ".sha256" });
            Suite.Check(Evaluate(sidecar).State == "available"); Suite.Check(Evaluate(sidecar).ChecksumUrl == Url + ".sha256");
            sidecar["assets"]!.AsArray().Add(sidecar["assets"]![1]!.DeepClone()); Suite.Check(Evaluate(sidecar).State == "error");
            foreach (var invalid in new[] { Package() with { AssetName = "../" + Name }, Package() with { AssetSize = 0 },
                Package() with { AssetSize = UpdateService.MaxDownloadBytes + 1 }, Package() with { Version = "0.3.0" },
                Package() with { Sha256 = "" }, Package() with { DownloadUrl = "https://evil.test/a.exe" } })
                Suite.Throws<UpdateException>(() => UpdateService.ValidateMetadata(invalid));
        });
        suite.Test("updates: checksum must name exactly one package, progress includes speed and ETA", () => {
            var sha = Digest(Payload);
            foreach (var separator in new[] { "  ", " *" })
                Suite.Check(UpdateService.ParseChecksum(Encoding.ASCII.GetBytes(sha + separator + Name + "\r\n"), Name) == sha);
            foreach (var text in new[] { sha, sha + "  other.exe\n", sha + "  " + Name + "\n" + sha + "  other.exe\n", sha + "  " + Name + "\n\n", "中文" })
                Suite.Throws<UpdateException>(() => UpdateService.ParseChecksum(Encoding.UTF8.GetBytes(text), Name));
            Suite.Throws<UpdateException>(() => UpdateService.ParseChecksum(new byte[UpdateService.MaxChecksumBytes + 1], Name));
            var progress = UpdateService.ProgressText(new("downloading", 6 * 1024 * 1024, 10 * 1024 * 1024, 1024 * 1024, 4));
            Suite.Check(progress.Contains("6.0 MiB / 10.0 MiB") && progress.Contains("1.0 MiB/秒") && progress.Contains("4 秒"));
        });
        suite.Test("updates: independent Windows channel ignores skill latest and rejects mismatched tags", () => {
            static UpdateResult EvaluateList(params JsonObject[] items) => UpdateService.EvaluateWindowsReleases(
                Encoding.UTF8.GetBytes(new JsonArray(items.Select(x => (JsonNode)x).ToArray()).ToJsonString()), "0.2.0");
            Suite.Check(EvaluateList(Release("99.0.0"), WindowsRelease("0.2.0")).State == "current");
            var draft = WindowsRelease("99.0.0"); draft["draft"] = true;
            var preview = WindowsRelease("98.0.0"); preview["prerelease"] = true;
            Suite.Check(EvaluateList(draft, preview, WindowsRelease("0.2.1")).Version == "0.2.1");
            Suite.Check(EvaluateList(WindowsRelease("0.2.1"), WindowsRelease("0.3.0")).Version == "0.3.0");
            Suite.Check(EvaluateList(Release()).State == "unavailable");
            var malformed = WindowsRelease(); malformed["draft"] = null;
            Suite.Check(EvaluateList(malformed, WindowsRelease("0.2.0")).State == "error");
            var missingAsset = WindowsRelease(); missingAsset["assets"] = new JsonArray();
            Suite.Check(EvaluateList(missingAsset, WindowsRelease("0.2.0")).State == "unavailable");
            var wrongPage = WindowsRelease(); wrongPage["html_url"] = "https://github.com/other/repo/releases/tag/windows-v9.0.0";
            Suite.Check(EvaluateList(wrongPage).State == "error");
            var wrongAsset = WindowsRelease(); wrongAsset["tag_name"] = "windows-v8.0.0";
            wrongAsset["html_url"] = "https://github.com/NingCui29/story-skill/releases/tag/windows-v8.0.0";
            Asset(wrongAsset)["browser_download_url"] = Asset(wrongAsset)["browser_download_url"]!.GetValue<string>().Replace("windows-v9.0.0", "windows-v8.0.0");
            Suite.Check(EvaluateList(wrongAsset).State == "error");
        });
        suite.TestAsync("updates: Windows release transport is bounded, unauthenticated and cancellable", async () => {
            using var handler = new UpdateFakeHandler((request, _) => {
                Suite.Check(request.RequestUri!.OriginalString == UpdateService.ReleasesApi + "1");
                Suite.Check(!request.Headers.Contains("Authorization") && !request.Headers.Contains("Cookie"));
                return Task.FromResult(Response(Encoding.UTF8.GetBytes(new JsonArray(WindowsRelease()).ToJsonString())));
            });
            using var service = new UpdateService(handler); Suite.Check((await service.CheckAsync()).State == "available");
            using var oversized = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(Response(new byte[UpdateService.MaxResponseBytes + 1]))));
            Suite.Check((await oversized.CheckAsync()).State == "error");
            using var redirected = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(Redirect("https://evil.test/"))));
            Suite.Check((await redirected.CheckAsync()).State == "error");
            using var stalled = new UpdateService(new UpdateFakeHandler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return Response([]); }));
            using var cancel = new CancellationTokenSource(TimeSpan.FromMilliseconds(30));
            await Suite.ThrowsAsync<OperationCanceledException>(() => stalled.CheckAsync(cancel.Token));
        });
        suite.TestAsync("updates: bounded pagination finds Windows release behind newer skill releases", async () => {
            int page = 0;
            using var service = new UpdateService(new UpdateFakeHandler((request, _) => {
                page++; Suite.Check(request.RequestUri!.OriginalString == UpdateService.ReleasesApi + page);
                var entries = page == 1 ? new JsonArray(Enumerable.Range(0, 20).Select(_ => (JsonNode)Release()).ToArray()) : new JsonArray(WindowsRelease());
                var response = Response(Encoding.UTF8.GetBytes(entries.ToJsonString()));
                if (page == 1) response.Headers.TryAddWithoutValidation("Link", "<https://api.github.com/repos/NingCui29/story-skill/releases?per_page=20&page=2>; rel=\"next\"");
                return Task.FromResult(response);
            }));
            Suite.Check((await service.CheckAsync()).State == "available" && page == 2);
            int bounded = 0;
            using var many = new UpdateService(new UpdateFakeHandler((_, _) => {
                bounded++; var response = Response(Encoding.UTF8.GetBytes(new JsonArray(Enumerable.Range(0, 20).Select(_ => (JsonNode)Release()).ToArray()).ToJsonString()));
                response.Headers.TryAddWithoutValidation("Link", "<https://evil.invalid/not-followed>; rel=\"next\"");
                return Task.FromResult(response);
            }));
            Suite.Check((await many.CheckAsync()).State == "error" && bounded == 5);
        });
        suite.TestAsync("updates: installer process exit restores retry state without killing the child", async () => {
            foreach (int code in new[] { 2, 0 })
            {
                var start = new System.Diagnostics.ProcessStartInfo(OperatingSystem.IsWindows() ? "cmd.exe" : "/bin/sh")
                { UseShellExecute = false, RedirectStandardInput = true, CreateNoWindow = true };
                start.ArgumentList.Add(OperatingSystem.IsWindows() ? "/c" : "-c");
                start.ArgumentList.Add(OperatingSystem.IsWindows() ? "set /p hold=& exit /b " + code : "read line; exit " + code);
                var child = System.Diagnostics.Process.Start(start)!;
                var session = new UpdateInstallerSession("synthetic-child", child);
                try
                {
                    Suite.Check(session.IsRunning);
                    await child.StandardInput.WriteLineAsync("continue"); await child.StandardInput.FlushAsync();
                    Suite.Check(await session.Completion.WaitAsync(TimeSpan.FromSeconds(10)) == code);
                    Suite.Check(!session.IsRunning);
                }
                finally
                {
                    if (!session.Completion.IsCompleted) { child.Kill(entireProcessTree: true); await session.Completion; }
                }
            }
        });
        suite.TestAsync("updates: ZIP can never be launched", async () => {
            using var service = new UpdateService();
            var name = "story-workbench-0.2.1-windows-x64.zip";
            var portable = new UpdateDownloaded(Path.Combine(Path.GetTempPath(), name), Digest(Payload), Payload.Length, "0.2.1", name, new(1, 1, Payload.Length, 0, 0));
            await Suite.ThrowsAsync<UpdateException>(() => service.LaunchInstallerAsync(portable));
        });
        suite.TestWindowsAsync("updates: verified transfer, exact sidecar and clean retry", async () => {
            using var area = new UpdateTempArea(); var sentinel = Path.Combine(area.Root, "keep-author-file.txt"); File.WriteAllText(sentinel, "keep");
            var events = new List<UpdateProgress>();
            using var handler = new UpdateFakeHandler((request, _) => {
                Suite.Check(!request.Headers.Contains("Authorization") && !request.Headers.Contains("Cookie"));
                Suite.Check(request.Headers.AcceptEncoding.ToString() == "identity");
                return Task.FromResult(request.RequestUri!.OriginalString.EndsWith(".sha256", StringComparison.Ordinal)
                    ? Response(Encoding.ASCII.GetBytes(Digest(Payload) + "  " + Name + "\n")) : Response(Payload));
            });
            using var service = new UpdateService(handler);
            var sidecar = Encoding.ASCII.GetBytes(Digest(Payload) + "  " + Name + "\n");
            var result = Package() with { Sha256 = "", ChecksumUrl = Url + ".sha256", ChecksumSize = sidecar.Length };
            var update = await service.DownloadAsync(result, area.Cache, new UpdateInlineProgress(events.Add));
            Suite.Check(File.ReadAllBytes(update.Path).SequenceEqual(Payload)); Suite.Check(update.Identity is not null);
            Suite.Check(await service.VerifyAsync(update) == update); Suite.Check(events[^1].Stage == "ready");
            Suite.Check(events.Any(e => e.Stage == "verifying")); Suite.Check(File.ReadAllText(sentinel) == "keep");
            Suite.Check(handler.Calls == 2);
        });
        suite.TestWindowsAsync("updates: reopening and retry reuse current-official bytes without another download", async () => {
            using var area = new UpdateTempArea(); using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Response(Payload)));
            UpdateDownloaded first;
            using (var service = new UpdateService(handler)) first = await service.DownloadAsync(Package(), area.Cache);
            using var noNetwork = new UpdateService(new UpdateFakeHandler((_, _) => throw new Exception("Verified cache must not download again")));
            var reopened = await noNetwork.ReuseCachedAsync(Package(), area.Cache);
            Suite.Check(reopened is not null && reopened.Path == first.Path && reopened.Identity == first.Identity);
            var retried = await noNetwork.DownloadAsync(Package(), area.Cache);
            Suite.Check(retried.Path == first.Path && Directory.GetDirectories(area.Cache).Length == 1);
            File.WriteAllBytes(first.Path, Payload.Select(x => (byte)(x ^ 1)).ToArray());
            Suite.Check(await noNetwork.ReuseCachedAsync(Package(), area.Cache) is null);
        });
        suite.TestWindowsAsync("updates: cache pruning retains protected downloads and never deletes foreign entries", async () => {
            using var area = new UpdateTempArea(); using var service = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(Response(Payload))));
            var first = await service.DownloadAsync(Package(), area.Cache);
            string second = Path.Combine(area.Cache, "verified-update-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(second);
            File.Copy(first.Path, Path.Combine(second, Name));
            string third = Path.Combine(area.Cache, "verified-update-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(third);
            File.Copy(first.Path, Path.Combine(third, Name));
            File.SetLastWriteTimeUtc(first.Path, DateTime.UtcNow.AddDays(-60));
            string foreign = Path.Combine(area.Cache, "verified-update-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(foreign);
            File.WriteAllText(Path.Combine(foreign, "author.txt"), "keep");
            UpdateService.PruneCache(area.Cache, first.Path);
            Suite.Check(File.Exists(first.Path));
            Suite.Check(Directory.GetFiles(area.Cache, Name, SearchOption.AllDirectories).Length == UpdateService.MaxCachedPackages);
            Suite.Check(File.ReadAllText(Path.Combine(foreign, "author.txt")) == "keep");
            string stale = Path.Combine(area.Cache, "verified-update-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(stale);
            string part = Path.Combine(stale, Name + ".part"); File.WriteAllBytes(part, Payload); File.SetLastWriteTimeUtc(part, DateTime.UtcNow.AddDays(-2));
            UpdateService.PruneCache(area.Cache, first.Path); Suite.Check(!Directory.Exists(stale));
        });
        suite.TestWindowsAsync("updates: truncated, excess and mismatched hashes never leave a package", async () => {
            foreach (var bytes in new[] { Payload[..^1], Payload.Concat(new byte[] { 1 }).ToArray(), Payload.Select(b => (byte)(b ^ 1)).ToArray() }) {
                using var area = new UpdateTempArea();
                using var service = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(Response(bytes, omitLength: true))));
                var events = new List<UpdateProgress>();
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package(), area.Cache, new UpdateInlineProgress(events.Add)));
                area.CheckEmpty(); Suite.Check(!events.Any(e => e.Stage == "ready"));
            }
        });
        suite.TestWindowsAsync("updates: reject invalid response headers and status before consuming body", async () => {
            foreach (var variant in new[] { "status", "gzip", "short-length", "negative-length", "invalid-length", "final-url" }) {
                using var area = new UpdateTempArea(); var stream = new UpdateTrackingStream(Payload);
                using var service = new UpdateService(new UpdateFakeHandler((_, _) => {
                    var response = new HttpResponseMessage(variant == "status" ? HttpStatusCode.PartialContent : HttpStatusCode.OK) { Content = new StreamContent(stream) };
                    if (variant == "final-url") response.RequestMessage = new(HttpMethod.Get, "https://evil.test/installer.exe");
                    if (variant == "gzip") response.Content.Headers.TryAddWithoutValidation("Content-Encoding", "gzip");
                    if (variant.EndsWith("length", StringComparison.Ordinal)) response.Content.Headers.TryAddWithoutValidation("Content-Length",
                        variant == "short-length" ? "1" : variant == "negative-length" ? "-1" : "NaN");
                    return Task.FromResult(response);
                }));
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package(), area.Cache));
                Suite.Check(stream.ReadCalls == 0, variant); Suite.Check(stream.Disposed, variant); area.CheckEmpty();
            }
        });
        suite.TestWindowsAsync("updates: controlled redirects stop unsafe targets and sixth hop", async () => {
            foreach (var target in new[] { "http://github.com/file", "https://evil.test/file", Cdn.Replace(".com/", ".com:443/"), Url.Replace("NingCui29", "another") }) {
                using var area = new UpdateTempArea(); using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Redirect(target)));
                using var service = new UpdateService(handler);
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package(), area.Cache)); Suite.Check(handler.Calls == 1); area.CheckEmpty();
            }
            using (var area = new UpdateTempArea()) {
                using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Redirect(Cdn)));
                using var service = new UpdateService(handler);
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package(), area.Cache)); Suite.Check(handler.Calls == 6); area.CheckEmpty();
            }
            using (var area = new UpdateTempArea()) {
                var calls = 0; using var service = new UpdateService(new UpdateFakeHandler((request, _) => {
                    Suite.Check(!request.Headers.Contains("Authorization") && !request.Headers.Contains("Cookie"));
                    return Task.FromResult(++calls <= 5 ? Redirect(Cdn) : Response(Payload));
                }));
                var update = await service.DownloadAsync(Package(), area.Cache); Suite.Check(calls == 6 && update.Sha256 == Digest(Payload));
            }
        });
        suite.TestWindowsAsync("updates: cancellation before start, transfer and hash cleans only this attempt", async () => {
            foreach (var stage in new[] { "before", "downloading", "verifying" }) {
                using var area = new UpdateTempArea(); var sentinel = Path.Combine(area.Root, "author.txt"); File.WriteAllText(sentinel, "preserve");
                using var cancel = new CancellationTokenSource(); if (stage == "before") cancel.Cancel();
                using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Response(Payload)));
                using var service = new UpdateService(handler); var events = new List<UpdateProgress>();
                var progress = new UpdateInlineProgress(p => { events.Add(p); if (p.Stage == stage && (stage != "downloading" || p.Received > 0)) cancel.Cancel(); });
                await Suite.ThrowsAsync<OperationCanceledException>(() => service.DownloadAsync(Package(), area.Cache, progress, cancel.Token));
                area.CheckEmpty(); Suite.Check(!events.Any(e => e.Stage == "ready")); Suite.Check(File.ReadAllText(sentinel) == "preserve");
                if (stage == "before") Suite.Check(handler.Calls == 0);
                var retried = await service.DownloadAsync(Package(), area.Cache); Suite.Check(retried.Identity is not null);
            }
        });
        suite.TestWindowsAsync("updates: disconnect cleans partial file and succeeds on retry", async () => {
            using var area = new UpdateTempArea(); var broken = new UpdateTrackingStream(Payload, failAfterFirstRead: true); var calls = 0;
            using var service = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(++calls == 1
                ? new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(broken) } : Response(Payload))));
            await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package(), area.Cache));
            Suite.Check(broken.Disposed); area.CheckEmpty();
            Suite.Check((await service.DownloadAsync(Package(), area.Cache)).Identity is not null);
        });
        suite.TestWindowsAsync("updates: cancellation interrupts an in-flight response read", async () => {
            using var area = new UpdateTempArea(); using var cancel = new CancellationTokenSource();
            var source = new UpdateTrackingStream(Payload, blockReads: true, onBlocked: () => cancel.CancelAfter(30));
            using var service = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(
                new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(source) })));
            var clock = System.Diagnostics.Stopwatch.StartNew();
            await Suite.ThrowsAsync<OperationCanceledException>(() => service.DownloadAsync(Package(), area.Cache, cancellationToken: cancel.Token));
            Suite.Check(clock.Elapsed < TimeSpan.FromSeconds(5)); Suite.Check(source.ReadCalls > 0 && source.Disposed); area.CheckEmpty();
        });
        suite.TestWindowsAsync("updates: sidecar rejects wrong name, extra data, and multiple records", async () => {
            foreach (var text in new[] { Digest(Payload) + "  other.exe\n", Digest(Payload), Digest(Payload) + "  " + Name + "\n" + Digest(Payload) + "  other.exe\n" }) {
                using var area = new UpdateTempArea(); var sidecar = Encoding.ASCII.GetBytes(text);
                using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Response(sidecar)));
                using var service = new UpdateService(handler);
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package() with { Sha256 = "", ChecksumUrl = Url + ".sha256", ChecksumSize = sidecar.Length }, area.Cache));
                Suite.Check(handler.Calls == 1); area.CheckEmpty();
            }
            using (var area = new UpdateTempArea()) {
                var correct = Encoding.ASCII.GetBytes(Digest(Payload) + "  " + Name + "\n");
                using var handler = new UpdateFakeHandler((_, _) => Task.FromResult(Response(correct.Concat(new byte[] { 1 }).ToArray(), omitLength: true)));
                using var service = new UpdateService(handler);
                await Suite.ThrowsAsync<UpdateException>(() => service.DownloadAsync(Package() with { Sha256 = "", ChecksumUrl = Url + ".sha256", ChecksumSize = correct.Length }, area.Cache));
                Suite.Check(handler.Calls == 1); area.CheckEmpty();
            }
        });
        suite.TestWindowsAsync("updates: changed bytes and same-size same-mtime file replacement cannot launch", async () => {
            using var area = new UpdateTempArea(); using var service = new UpdateService(new UpdateFakeHandler((_, _) => Task.FromResult(Response(Payload))));
            var update = await service.DownloadAsync(Package(), area.Cache);
            var originalWrite = File.GetLastWriteTimeUtc(update.Path); var originalCreate = File.GetCreationTimeUtc(update.Path);
            var replacement = Path.Combine(Path.GetDirectoryName(update.Path)!, "replacement.fixture");
            File.WriteAllBytes(replacement, Payload); File.SetLastWriteTimeUtc(replacement, originalWrite); File.SetCreationTimeUtc(replacement, originalCreate);
            File.Move(replacement, update.Path, true);
            await Suite.ThrowsAsync<UpdateException>(() => service.VerifyAsync(update));
            await Suite.ThrowsAsync<UpdateException>(() => service.LaunchInstallerAsync(update));
            var newIdentity = await service.VerifyAsync(update with { Identity = null });
            File.WriteAllBytes(update.Path, Payload.Select(b => (byte)(b ^ 1)).ToArray());
            File.SetLastWriteTimeUtc(update.Path, originalWrite);
            await Suite.ThrowsAsync<UpdateException>(() => service.VerifyAsync(newIdentity));
        });
    }

    private static HttpResponseMessage Response(byte[] data, bool omitLength = false)
    {
        var content = omitLength ? (HttpContent)new StreamContent(new UpdateTrackingStream(data)) : new ByteArrayContent(data);
        return new(HttpStatusCode.OK) { Content = content };
    }
    private static HttpResponseMessage Redirect(string target)
    {
        var response = new HttpResponseMessage(HttpStatusCode.Found); response.Headers.Location = new Uri(target); return response;
    }
    private sealed class UpdateFakeHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        public int Calls { get; private set; }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        { token.ThrowIfCancellationRequested(); Calls++; return send(request, token); }
    }
    private sealed class UpdateInlineProgress(Action<UpdateProgress> report) : IProgress<UpdateProgress>
    { public void Report(UpdateProgress value) => report(value); }
    private sealed class UpdateTempArea : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "story-update-tests-" + Guid.NewGuid().ToString("N"));
        public string Cache => Path.Combine(Root, "cache");
        public UpdateTempArea() => Directory.CreateDirectory(Root);
        public void CheckEmpty() => Suite.Check(!Directory.Exists(Cache) || !Directory.EnumerateFileSystemEntries(Cache).Any());
        public void Dispose() { if (Directory.Exists(Root)) Directory.Delete(Root, true); }
    }
    private sealed class UpdateTrackingStream(byte[] data, bool failAfterFirstRead = false, bool blockReads = false, Action? onBlocked = null) : Stream
    {
        private readonly MemoryStream inner = new(data);
        public int ReadCalls { get; private set; }
        public bool Disposed { get; private set; }
        public override bool CanRead => true; public override bool CanSeek => false; public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => inner.Position; set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) { ReadCalls++; if (failAfterFirstRead && ReadCalls > 1) throw new IOException("synthetic disconnect"); return inner.Read(buffer, offset, count); }
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default) {
            token.ThrowIfCancellationRequested(); ReadCalls++; if (failAfterFirstRead && ReadCalls > 1) throw new IOException("synthetic disconnect");
            if (blockReads) { onBlocked?.Invoke(); return HoldAsync(token); }
            return inner.ReadAsync(buffer, token);
        }
        private static async ValueTask<int> HoldAsync(CancellationToken token) { await Task.Delay(Timeout.Infinite, token); return 0; }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        protected override void Dispose(bool disposing) { if (disposing) { Disposed = true; inner.Dispose(); } base.Dispose(disposing); }
    }
}
