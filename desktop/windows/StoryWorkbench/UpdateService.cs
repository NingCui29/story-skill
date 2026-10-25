using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Numerics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace StoryWorkbench;

public sealed record UpdateResult(string State, string Message, string Version = "",
    string DownloadUrl = "", string AssetName = "", long AssetSize = 0,
    string Sha256 = "", string ChecksumUrl = "", long ChecksumSize = 0);

public sealed record UpdateProgress(string Stage, long Received = 0, long Total = 0,
    double Speed = 0, double? Remaining = null);

public sealed record UpdateFileIdentity(uint Volume, ulong Index, long Length, long Created, long Written);

public sealed record UpdateDownloaded(string Path, string Sha256, long Size, string Version,
    string AssetName, UpdateFileIdentity? Identity = null);

/// <summary>A launched installer owns its lifetime. Closing the updater never kills it.</summary>
public sealed class UpdateInstallerSession
{
    public string Path { get; }
    public Task<int> Completion { get; }
    public bool IsRunning => !Completion.IsCompleted;
    public UpdateInstallerSession(string path, Process process)
    { Path = path; Completion = ObserveAsync(process); }
    private static async Task<int> ObserveAsync(Process process)
    {
        using (process) { await process.WaitForExitAsync().ConfigureAwait(false); return process.ExitCode; }
    }
}

public sealed class UpdateException(string message, Exception? inner = null) : Exception(message, inner) { }

/// <summary>Official-release parsing and bounded transfers. No manuscript or window access.</summary>
public sealed class UpdateService : IDisposable
{
    public const string ClientVersion = "0.2.5";
    public const string Repository = "NingCui29/story-skill";
    public const string ReleasesApi = "https://api.github.com/repos/" + Repository + "/releases?per_page=20&page=";
    public const int MaxResponseBytes = 2 * 1024 * 1024;
    public const int MaxChecksumBytes = 16 * 1024;
    public const long MaxDownloadBytes = 1024L * 1024 * 1024;
    private const int ChunkBytes = 128 * 1024;
    private static readonly TimeSpan NetworkTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan TotalTimeout = TimeSpan.FromMinutes(30);
    private static readonly Regex AssetPattern = new(
        @"\Astory-workbench-([0-9]+\.[0-9]+\.[0-9]+)-windows-(?:x86_64|x64)(-setup\.exe|\.zip)\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static readonly HashSet<string> CdnHosts = ["release-assets.githubusercontent.com",
        "objects.githubusercontent.com", "github-releases.githubusercontent.com"];
    private readonly HttpClient client;
    private static readonly SemaphoreSlim InstallerGate = new(1, 1);
    private static UpdateInstallerSession? activeInstaller;
    public static UpdateInstallerSession? ActiveInstaller => Volatile.Read(ref activeInstaller) is { IsRunning: true } value ? value : null;
    public const int MaxCachedPackages = 2;
    private static readonly Regex CacheFolder = new(@"\Averified-update-[0-9a-f]{32}\z", RegexOptions.CultureInvariant);

    // A handler is injectable for synthetic tests; production always disables automatic
    // redirects, cookies, credentials and content decoding, including on CDN hops.
    public UpdateService(HttpMessageHandler? handler = null)
    {
        client = new HttpClient(handler ?? new HttpClientHandler {
            AllowAutoRedirect = false, UseCookies = false, UseDefaultCredentials = false,
            AutomaticDecompression = DecompressionMethods.None
        }) { Timeout = Timeout.InfiniteTimeSpan };
    }

    public void Dispose() => client.Dispose();

    public static int CompareVersions(string left, string right)
    {
        static BigInteger[] Parts(string value)
        {
            if (value.Length > 128 || !Regex.IsMatch(value, @"\A[0-9]+\.[0-9]+\.[0-9]+\z"))
                throw new FormatException("Invalid desktop version");
            return value.Split('.').Select(x => BigInteger.Parse(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
        }
        var a = Parts(left); var b = Parts(right);
        for (var i = 0; i < 3; i++) { var c = a[i].CompareTo(b[i]); if (c != 0) return c; }
        return 0;
    }

    private static bool RawUrl(string? value, out string host, out string path, out string query)
    {
        host = path = query = "";
        if (value is null || value.Length > 16384 || value.Any(c => c < 33 || c == 127 || c == '\\')) return false;
        // Inspect unnormalised path segments: System.Uri would otherwise remove /../.
        var match = Regex.Match(value, @"\Ahttps://([^/?#]+)(/[^?#]*)?(\?[^#]*)?\z", RegexOptions.CultureInvariant);
        if (!match.Success) return false;
        host = match.Groups[1].Value; path = match.Groups[2].Value; query = match.Groups[3].Value;
        return !host.Contains(':') && !host.Contains('@') && Uri.TryCreate(value, UriKind.Absolute, out _);
    }

    private static bool SafeSegment(string part)
    {
        if (part.Length == 0 || Regex.IsMatch(part, @"%(?![0-9a-fA-F]{2})")) return false;
        var decoded = Uri.UnescapeDataString(part);
        return decoded is not "." and not ".." && !decoded.Any(c => c < 33 || c == 127 || c is '/' or '\\' or '%');
    }

    public static bool OfficialReleaseUrl(string? value, bool download = false)
    {
        if (!RawUrl(value, out var host, out var path, out var query) || host != "github.com" || query.Length != 0) return false;
        var prefix = "/" + Repository + "/releases/";
        if (!path.StartsWith(prefix, StringComparison.Ordinal)) return false;
        var parts = path[prefix.Length..].Split('/');
        return parts.Length == (download ? 3 : 2) && parts[0] == (download ? "download" : "tag")
            && parts.Skip(1).All(SafeSegment);
    }

    public static bool PermittedTransferUrl(string? value)
    {
        if (!RawUrl(value, out var host, out var path, out _)) return false;
        if (host == "github.com") return OfficialReleaseUrl(value, true);
        return CdnHosts.Contains(host)
            && (path.StartsWith("/github-production-release-asset/", StringComparison.Ordinal)
                || path.StartsWith("/github-production-release-asset-2e65be/", StringComparison.Ordinal))
            && path.Split('/').Skip(1).All(SafeSegment);
    }

    private static string Text(JsonElement item, string key) => item.ValueKind == JsonValueKind.Object
        && item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString()! : "";
    private static long Size(JsonElement item, string key) => item.TryGetProperty(key, out var value)
        && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var result) ? result : 0;
    private static UpdateResult Error(string message) => new("error", message);

    public static UpdateResult EvaluateReleaseResponse(int status, ReadOnlyMemory<byte> data, string currentVersion = ClientVersion)
    {
        CompareVersions(currentVersion, currentVersion);
        if (status == 404) return new("unavailable", "官方仓库尚未发布正式版本，暂时无法检查 Windows 工作台更新。");
        if (status is 403 or 429) return Error("GitHub 暂时拒绝了更新检查，可能达到请求限额。请稍后重试。");
        if (status != 200) return Error($"更新检查失败（HTTP {status}），请稍后重试。");
        if (data.Length > MaxResponseBytes) return Error("更新信息超过大小限制，请稍后重试。");
        try
        {
            using var document = JsonDocument.Parse(data, new JsonDocumentOptions { MaxDepth = 32 });
            var release = document.RootElement;
            if (release.ValueKind != JsonValueKind.Object
                || !release.TryGetProperty("draft", out var draft) || draft.ValueKind != JsonValueKind.False
                || !release.TryGetProperty("prerelease", out var prerelease) || prerelease.ValueKind != JsonValueKind.False
                || !OfficialReleaseUrl(Text(release, "html_url"))
                || !release.TryGetProperty("assets", out var assets) || assets.ValueKind != JsonValueKind.Array)
                return Error("无法验证正式版本信息，请稍后重试。");
            var releaseTag = Uri.UnescapeDataString(Text(release, "html_url").Split('/')[^1]);
            var candidates = new List<(string Version, bool Installer, string Url, JsonElement Asset)>();
            foreach (var asset in assets.EnumerateArray())
            {
                if (Text(asset, "state") != "uploaded") continue;
                var name = Text(asset, "name"); var match = AssetPattern.Match(name);
                if (!match.Success) continue;
                var url = Text(asset, "browser_download_url");
                if (!OfficialReleaseUrl(url, true) || Uri.UnescapeDataString(url.Split('/')[^1]) != name
                    || Uri.UnescapeDataString(url.Split('/')[^2]) != releaseTag)
                    return Error("无法验证 Windows 工作台安装包下载信息，请稍后重试。");
                CompareVersions(match.Groups[1].Value, currentVersion);
                candidates.Add((match.Groups[1].Value, match.Groups[2].Value.Equals("-setup.exe", StringComparison.OrdinalIgnoreCase), url, asset));
            }
            if (candidates.Count == 0) return new("unavailable", $"最新正式发布中没有可验证版本的 Windows x64 工作台安装包。当前客户端：{currentVersion}。");
            candidates.Sort((a, b) => { var c = CompareVersions(a.Version, b.Version); if (c != 0) return c;
                c = a.Installer.CompareTo(b.Installer); return c != 0 ? c : string.CompareOrdinal(a.Url, b.Url); });
            var best = candidates[^1]; var comparison = CompareVersions(best.Version, currentVersion);
            if (comparison == 0) return new("current", $"当前 Windows 工作台 {currentVersion} 已是最新正式版本。", best.Version);
            if (comparison < 0) return new("ahead", $"当前 Windows 工作台 {currentVersion} 高于最新正式安装包 {best.Version}，无需降级。", best.Version);
            var assetSize = Size(best.Asset, "size");
            if (assetSize <= 0 || assetSize > MaxDownloadBytes) return Error("官方安装包大小无效或超出 1 GiB 限制，无法安全下载。");
            string sha = "", checksumUrl = ""; long checksumSize = 0;
            if (best.Asset.TryGetProperty("digest", out var digest) && digest.ValueKind != JsonValueKind.Null)
            {
                if (digest.ValueKind != JsonValueKind.String || !Regex.IsMatch(digest.GetString()!, @"\Asha256:[0-9a-fA-F]{64}\z"))
                    return Error("官方安装包 SHA256 信息无效，无法安全下载。");
                sha = digest.GetString()![7..].ToLowerInvariant();
            }
            else
            {
                var companions = assets.EnumerateArray().Where(a => Text(a, "name") == Text(best.Asset, "name") + ".sha256").ToArray();
                if (companions.Length != 1) return Error("官方发布缺少此安装包的 SHA256 校验信息，无法安全下载。");
                var companion = companions[0]; checksumUrl = Text(companion, "browser_download_url"); checksumSize = Size(companion, "size");
                if (Text(companion, "state") != "uploaded" || checksumSize <= 0 || checksumSize > MaxChecksumBytes
                    || checksumUrl != best.Url + ".sha256" || !OfficialReleaseUrl(checksumUrl, true))
                    return Error("无法验证此安装包的 SHA256 校验文件，无法安全下载。");
            }
            return new("available", $"发现 Windows 工作台 {best.Version}，当前版本 {currentVersion}。可在应用内下载并校验。",
                best.Version, best.Url, Text(best.Asset, "name"), assetSize, sha, checksumUrl, checksumSize);
        }
        catch (Exception error) when (error is JsonException or FormatException or InvalidOperationException)
        { return Error("更新信息格式无效，请稍后重试。"); }
    }

    public static UpdateResult EvaluateWindowsReleases(ReadOnlyMemory<byte> data, string currentVersion = ClientVersion)
    {
        if (data.Length > MaxResponseBytes) return Error("更新信息超过大小限制，请稍后重试。");
        try
        {
            using var document = JsonDocument.Parse(data, new JsonDocumentOptions { MaxDepth = 32 });
            if (document.RootElement.ValueKind != JsonValueKind.Array || document.RootElement.GetArrayLength() > 100)
                return Error("Windows 发布列表格式无效。");
            JsonElement? selected = null; string version = "";
            foreach (var release in document.RootElement.EnumerateArray())
            {
                var tag = Text(release, "tag_name");
                var match = Regex.Match(tag, @"\Awindows-v([0-9]+\.[0-9]+\.[0-9]+)\z");
                if (!match.Success) continue;
                if (!release.TryGetProperty("draft", out var draft) || draft.ValueKind is not (JsonValueKind.False or JsonValueKind.True)
                    || !release.TryGetProperty("prerelease", out var preview) || preview.ValueKind is not (JsonValueKind.False or JsonValueKind.True))
                    return Error("Windows 发布状态无法验证，已停止。");
                if (draft.ValueKind == JsonValueKind.True || preview.ValueKind == JsonValueKind.True) continue;
                var candidate = match.Groups[1].Value;
                if (selected is null || CompareVersions(candidate, version) > 0) { selected = release; version = candidate; }
            }
            if (selected is not JsonElement item) return new("unavailable", "尚未找到正式 Windows 独立发行版，请稍后重试或查看官方下载页。");
            if (Text(item, "html_url") != $"https://github.com/{Repository}/releases/tag/windows-v{version}")
                return Error("Windows 发布标签与官方页面不一致，已停止。");
            var result = EvaluateReleaseResponse(200, Encoding.UTF8.GetBytes(item.GetRawText()), currentVersion);
            if ((result.State is "available" or "current" or "ahead") && result.Version != version)
                return Error("Windows 发布标签与安装包版本不一致，已停止。");
            return result;
        }
        catch (Exception error) when (error is JsonException or FormatException or InvalidOperationException)
        { return Error("Windows 发布列表格式无效，请稍后重试。"); }
    }

    public async Task<UpdateResult> CheckAsync(CancellationToken cancellationToken = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            var releases = new List<JsonElement>(); int received = 0;
            for (int page = 1; page <= 5; page++)
            {
                using var request = Request(ReleasesApi + page, true);
                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token).ConfigureAwait(false);
                if (response.RequestMessage?.RequestUri is Uri final && final != request.RequestUri)
                    return Error("更新检查发生了非预期重定向，已停止。");
                var data = await ReadBoundedAsync(response, MaxResponseBytes, null, timeout.Token).ConfigureAwait(false);
                if (response.StatusCode != HttpStatusCode.OK) return EvaluateReleaseResponse((int)response.StatusCode, data);
                received += data.Length;
                if (received > MaxResponseBytes) return Error("更新信息超过大小限制，请稍后重试。");
                using var document = JsonDocument.Parse(data, new JsonDocumentOptions { MaxDepth = 32 });
                if (document.RootElement.ValueKind != JsonValueKind.Array || document.RootElement.GetArrayLength() > 20)
                    return Error("Windows 发布列表格式无效。");
                releases.AddRange(document.RootElement.EnumerateArray().Select(item => item.Clone()));
                if (document.RootElement.GetArrayLength() < 20
                    || !response.Headers.TryGetValues("Link", out var links) || !links.Any(link => link.Contains("rel=\"next\"", StringComparison.Ordinal)))
                    return EvaluateWindowsReleases(JsonSerializer.SerializeToUtf8Bytes(releases));
            }
            return Error("发布记录超出自动检查范围，请查看官方下载页。");
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        { return Error("更新检查超时，请检查网络后重试。"); }
        catch (UpdateException error) { return Error(error.Message); }
        catch (JsonException) { return Error("Windows 发布列表格式无效，请稍后重试。"); }
        catch (Exception error) when (error is HttpRequestException or IOException)
        { return Error("无法连接 GitHub，请检查网络后重试。"); }
    }

    private static HttpRequestMessage Request(string url, bool api = false)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, url);
        request.Headers.TryAddWithoutValidation("User-Agent", "StoryWorkbench/" + ClientVersion);
        request.Headers.TryAddWithoutValidation("Accept", api ? "application/vnd.github+json" : "application/octet-stream");
        request.Headers.TryAddWithoutValidation("Accept-Encoding", "identity");
        if (api) request.Headers.TryAddWithoutValidation("X-GitHub-Api-Version", "2022-11-28");
        return request;
    }

    private async Task<HttpResponseMessage> OpenAsync(string url, long size, CancellationToken token)
    {
        if (!OfficialReleaseUrl(url, true)) throw new UpdateException("下载必须从官方发布链接开始。");
        for (var redirects = 0; ; redirects++)
        {
            using var request = Request(url);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token); timeout.CancelAfter(NetworkTimeout);
            var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token).ConfigureAwait(false);
            try
            {
                if (response.RequestMessage?.RequestUri is Uri final && final != request.RequestUri)
                    throw new UpdateException("下载出现未受控重定向，已停止。");
                if ((int)response.StatusCode is 301 or 302 or 303 or 307 or 308)
                {
                    var location = response.Headers.Location;
                    var next = location is null ? "" : location.IsAbsoluteUri ? location.OriginalString : new Uri(new Uri(url), location).OriginalString;
                    if (redirects >= 5 || !PermittedTransferUrl(next)) throw new UpdateException("下载重定向不在官方 HTTPS 资源范围内，已停止。");
                    url = next; response.Dispose(); continue;
                }
                if (response.StatusCode != HttpStatusCode.OK || !PermittedTransferUrl(url))
                    throw new UpdateException("官方资源未返回有效下载内容，请稍后重试。");
                if (response.Content.Headers.NonValidated.TryGetValues("Content-Encoding", out var encodings)
                        && (encodings.Count() != 1 || !encodings.Single().Equals("identity", StringComparison.OrdinalIgnoreCase))
                    || DeclaredLength(response) is long length && length != size)
                    throw new UpdateException("下载大小或编码与官方发布信息不符，已停止。");
                return response;
            }
            catch { response.Dispose(); throw; }
        }
    }

    private static async Task<int> ReadAsync(Stream stream, Memory<byte> buffer, CancellationToken token)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token); timeout.CancelAfter(NetworkTimeout);
        return await stream.ReadAsync(buffer, timeout.Token).ConfigureAwait(false);
    }

    private static long? DeclaredLength(HttpResponseMessage response)
    {
        if (!response.Content.Headers.NonValidated.TryGetValues("Content-Length", out var values)) return null;
        var lengths = values.ToArray();
        if (lengths.Length != 1 || !Regex.IsMatch(lengths[0], @"\A[0-9]+\z")
            || !long.TryParse(lengths[0], System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture, out var length))
            throw new UpdateException("下载资源的 Content-Length 无效，已停止。");
        return length;
    }

    private static async Task<byte[]> ReadBoundedAsync(HttpResponseMessage response, int limit, long? expected, CancellationToken token)
    {
        if (DeclaredLength(response) is long size && size > limit)
            throw new UpdateException("响应内容超过大小限制，已停止。");
        using var source = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
        using var result = new MemoryStream(); var buffer = new byte[Math.Min(ChunkBytes, limit + 1)];
        int count;
        while ((count = await ReadAsync(source, buffer, token).ConfigureAwait(false)) != 0)
        {
            if (result.Length + count > limit || expected is long total && result.Length + count > total)
                throw new UpdateException("响应内容超过官方大小限制，已停止。");
            result.Write(buffer, 0, count);
        }
        if (expected is long required && result.Length != required) throw new UpdateException("官方校验信息下载不完整，请重试。");
        return result.ToArray();
    }

    public static string ParseChecksum(byte[] data, string assetName)
    {
        if (data.Length == 0 || data.Length > MaxChecksumBytes || data.Any(b => b > 127)) throw new UpdateException("SHA256 校验文件格式无效。");
        var match = Regex.Match(Encoding.ASCII.GetString(data), @"\A([0-9a-fA-F]{64}) [ *]" + Regex.Escape(assetName) + @"\r?\n?\z");
        if (!match.Success) throw new UpdateException("SHA256 校验文件与此安装包名称不符。");
        return match.Groups[1].Value.ToLowerInvariant();
    }

    public static void ValidateMetadata(UpdateResult result)
    {
        var match = AssetPattern.Match(result.AssetName);
        if (result.State != "available" || !match.Success || match.Groups[1].Value != result.Version
            || !OfficialReleaseUrl(result.DownloadUrl, true) || Uri.UnescapeDataString(result.DownloadUrl.Split('/')[^1]) != result.AssetName
            || result.AssetSize <= 0 || result.AssetSize > MaxDownloadBytes
            || (result.Sha256.Length != 0 ? !Regex.IsMatch(result.Sha256, @"\A[0-9a-f]{64}\z")
                : result.ChecksumUrl != result.DownloadUrl + ".sha256" || !OfficialReleaseUrl(result.ChecksumUrl, true)
                    || result.ChecksumSize <= 0 || result.ChecksumSize > MaxChecksumBytes))
            throw new UpdateException("缺少可验证的官方安装包信息，请重新检查更新。");
        CompareVersions(result.Version, ClientVersion);
    }

    public async Task<UpdateDownloaded> DownloadAsync(UpdateResult result, string cacheDirectory,
        IProgress<UpdateProgress>? progress = null, CancellationToken cancellationToken = default)
    {
        ValidateMetadata(result);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken); timeout.CancelAfter(TotalTimeout);
        var token = timeout.Token; string? directory = null, temporary = null, target = null; bool succeeded = false;
        UpdateDirectoryGuard? guard = null;
        try
        {
            token.ThrowIfCancellationRequested();
            var digest = await ResolveDigestAsync(result, progress, token).ConfigureAwait(false);
            var reused = await FindCachedAsync(result, digest, cacheDirectory, progress, token).ConfigureAwait(false);
            if (reused is not null) return reused;
            PruneCache(cacheDirectory, maximumPackages: MaxCachedPackages - 1);
            token.ThrowIfCancellationRequested();
            Directory.CreateDirectory(cacheDirectory);
            directory = Path.Combine(Path.GetFullPath(cacheDirectory), "verified-update-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory); guard = new UpdateDirectoryGuard(directory);
            temporary = Path.Combine(directory, result.AssetName + ".part"); target = Path.Combine(directory, result.AssetName);
            long received = 0; var clock = Stopwatch.StartNew(); var lastReport = TimeSpan.Zero;
            progress?.Report(new("downloading", 0, result.AssetSize));
            using (var response = await OpenAsync(result.DownloadUrl, result.AssetSize, token).ConfigureAwait(false))
            using (var source = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false))
            using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, ChunkBytes, FileOptions.Asynchronous))
            {
                var buffer = new byte[ChunkBytes]; int count;
                while ((count = await ReadAsync(source, buffer, token).ConfigureAwait(false)) != 0)
                {
                    token.ThrowIfCancellationRequested(); received += count;
                    if (received > result.AssetSize) throw new UpdateException("下载内容超出官方安装包大小，已停止。");
                    await output.WriteAsync(buffer.AsMemory(0, count), token).ConfigureAwait(false);
                    if (clock.Elapsed - lastReport >= TimeSpan.FromMilliseconds(100) || received == result.AssetSize)
                    {
                        var speed = received / Math.Max(clock.Elapsed.TotalSeconds, 0.001);
                        progress?.Report(new("downloading", received, result.AssetSize, speed, (result.AssetSize - received) / speed)); lastReport = clock.Elapsed;
                    }
                }
                if (received != result.AssetSize) throw new UpdateException("安装包下载不完整，请重试。");
                token.ThrowIfCancellationRequested(); await output.FlushAsync(token).ConfigureAwait(false); output.Flush(true);
            }
            File.Move(temporary, target);
            var verified = await VerifyAsync(new(target, digest, result.AssetSize, result.Version, result.AssetName), progress, token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested(); succeeded = true; progress?.Report(new("ready", result.AssetSize, result.AssetSize));
            PruneCache(cacheDirectory, target);
            return verified;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        { throw new UpdateException("下载超时，请检查网络后重试。"); }
        catch (Exception error) when (error is IOException or HttpRequestException or UnauthorizedAccessException)
        { throw new UpdateException("下载或保存失败，请检查网络、磁盘空间和权限后重试。", error); }
        finally
        {
            // Delete only the two exact files created by this operation, never a tree.
            if (!succeeded && guard is not null)
                foreach (var path in new[] { temporary, target }) if (path is not null) { try { File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { } }
            guard?.Dispose();
            if (!succeeded && directory is not null) { try { Directory.Delete(directory, false); } catch (IOException) { } catch (UnauthorizedAccessException) { } }
        }
    }

    private async Task<string> ResolveDigestAsync(UpdateResult result, IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        if (result.Sha256.Length != 0) return result.Sha256;
        progress?.Report(new("checksum"));
        using var checksum = await OpenAsync(result.ChecksumUrl, result.ChecksumSize, token).ConfigureAwait(false);
        return ParseChecksum(await ReadBoundedAsync(checksum, MaxChecksumBytes, result.ChecksumSize, token).ConfigureAwait(false), result.AssetName);
    }

    /// <summary>Cache metadata is never trusted: the current official release supplies size, name and SHA256.</summary>
    public async Task<UpdateDownloaded?> ReuseCachedAsync(UpdateResult result, string cacheDirectory,
        IProgress<UpdateProgress>? progress = null, CancellationToken cancellationToken = default)
    {
        ValidateMetadata(result);
        if (!Directory.Exists(cacheDirectory)) return null;
        var digest = await ResolveDigestAsync(result, progress, cancellationToken).ConfigureAwait(false);
        return await FindCachedAsync(result, digest, cacheDirectory, progress, cancellationToken).ConfigureAwait(false);
    }

    private async Task<UpdateDownloaded?> FindCachedAsync(UpdateResult result, string digest, string cacheDirectory,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        if (!Directory.Exists(cacheDirectory)) return null;
        using var cacheGuard = new UpdateDirectoryGuard(cacheDirectory);
        foreach (var directory in CacheDirectories(cacheDirectory))
        {
            token.ThrowIfCancellationRequested();
            var path = Path.Combine(directory, result.AssetName);
            if (!File.Exists(path)) continue;
            try
            {
                var verified = await VerifyAsync(new(path, digest, result.AssetSize, result.Version, result.AssetName), progress, token).ConfigureAwait(false);
                PruneCache(cacheDirectory, path);
                progress?.Report(new("ready", result.AssetSize, result.AssetSize));
                return verified;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or UpdateException or Win32Exception)
            { /* Corrupt, occupied or replaced entries cannot become installation candidates. */ }
        }
        return null;
    }

    private static string[] CacheDirectories(string cacheDirectory) => Directory.EnumerateDirectories(Path.GetFullPath(cacheDirectory))
        .Where(path => CacheFolder.IsMatch(Path.GetFileName(path))).Take(128)
        .OrderByDescending(Directory.GetLastWriteTimeUtc).ToArray();

    /// <summary>Retain at most two owned package directories; never recurse or delete unfamiliar entries.</summary>
    public static void PruneCache(string cacheDirectory, string? keepPath = null, int maximumPackages = MaxCachedPackages)
    {
        if (!Directory.Exists(cacheDirectory)) return;
        try
        {
            using var cacheGuard = new UpdateDirectoryGuard(cacheDirectory);
            int retained = 0;
            foreach (var directory in CacheDirectories(cacheDirectory).OrderByDescending(directory =>
                string.Equals(directory, Path.GetDirectoryName(keepPath), StringComparison.OrdinalIgnoreCase)
                || string.Equals(directory, Path.GetDirectoryName(ActiveInstaller?.Path), StringComparison.OrdinalIgnoreCase)))
            {
                try
                {
                    bool remove = false;
                    using (var guard = new UpdateDirectoryGuard(directory))
                    {
                        var files = Directory.GetFileSystemEntries(directory);
                        if (files.Length != 1) continue;
                        var file = files[0];
                        var name = Path.GetFileName(file);
                        bool partial = name.EndsWith(".part", StringComparison.Ordinal);
                        if (!AssetPattern.IsMatch(partial ? name[..^5] : name)) continue;
                        // Opening proves it is a regular, non-reparse file; an active .part writer denies this open.
                        long length;
                        using (var read = UpdateFileAccess.OpenRead(file)) { length = read.Length; }
                        bool protectedFile = string.Equals(file, keepPath, StringComparison.OrdinalIgnoreCase)
                            || string.Equals(file, ActiveInstaller?.Path, StringComparison.OrdinalIgnoreCase);
                        bool expired = File.GetLastWriteTimeUtc(file) < DateTime.UtcNow.AddDays(partial ? -1 : -30);
                        if (partial && !expired) continue;
                        if (length > MaxDownloadBytes) expired = true;
                        if (protectedFile || (!expired && retained < maximumPackages)) { retained++; continue; }
                        File.Delete(file); remove = true;
                    }
                    if (remove) Directory.Delete(directory, recursive: false);
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException or UpdateException or Win32Exception) { }
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or UpdateException or Win32Exception) { }
    }

    private static void ValidateDownloaded(UpdateDownloaded update)
    {
        var match = AssetPattern.Match(update.AssetName);
        if (!match.Success || match.Groups[1].Value != update.Version || Path.GetFileName(update.Path) != update.AssetName
            || !Path.IsPathFullyQualified(update.Path) || !Regex.IsMatch(update.Sha256, @"\A[0-9a-f]{64}\z")
            || update.Size <= 0 || update.Size > MaxDownloadBytes)
            throw new UpdateException("安装包验证信息无效，请重新下载。");
    }

    public Task<UpdateDownloaded> VerifyAsync(UpdateDownloaded update, IProgress<UpdateProgress>? progress = null,
        CancellationToken cancellationToken = default) => Task.Run(async () =>
    {
        ValidateDownloaded(update);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken); timeout.CancelAfter(TotalTimeout);
        using var guard = new UpdateDirectoryGuard(Path.GetDirectoryName(update.Path)!);
        using var stream = UpdateFileAccess.OpenRead(update.Path);
        var identity = await VerifyStreamAsync(update, stream, progress, timeout.Token).ConfigureAwait(false);
        return update with { Identity = identity };
    }, cancellationToken);

    private static async Task<UpdateFileIdentity> VerifyStreamAsync(UpdateDownloaded update, FileStream stream,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        token.ThrowIfCancellationRequested(); var before = UpdateFileAccess.Identity(stream.SafeFileHandle);
        if (before.Length != update.Size || update.Identity is not null && update.Identity != before)
            throw new UpdateException("下载文件已改变或被替换，请重新下载。");
        progress?.Report(new("verifying", 0, update.Size));
        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[ChunkBytes]; long received = 0; int count;
        var clock = Stopwatch.StartNew(); var lastReport = TimeSpan.Zero;
        while ((count = await stream.ReadAsync(buffer, token).ConfigureAwait(false)) != 0)
        {
            token.ThrowIfCancellationRequested(); received += count;
            if (received > update.Size) throw new UpdateException("下载文件大小改变，请重新下载。");
            digest.AppendData(buffer, 0, count);
            if (clock.Elapsed - lastReport >= TimeSpan.FromMilliseconds(100) || received == update.Size)
            { progress?.Report(new("verifying", received, update.Size)); lastReport = clock.Elapsed; }
        }
        var actual = Convert.ToHexString(digest.GetHashAndReset()).ToLowerInvariant();
        if (received != update.Size || actual != update.Sha256 || before != UpdateFileAccess.Identity(stream.SafeFileHandle))
            throw new UpdateException("SHA256 或文件身份校验失败，文件不可安装，请重新下载。");
        token.ThrowIfCancellationRequested(); return before;
    }

    /// <summary>Call only inside the host's all-tabs protected save operation.</summary>
    public Task<UpdateInstallerSession> LaunchInstallerAsync(UpdateDownloaded update, CancellationToken cancellationToken = default) => Task.Run(async () =>
    {
        await InstallerGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (ActiveInstaller is not null) throw new UpdateException("安装向导仍在运行，请先完成或取消该向导。");
            ValidateDownloaded(update);
            if (update.Identity is null || !update.AssetName.EndsWith("-setup.exe", StringComparison.OrdinalIgnoreCase))
                throw new UpdateException("仅可启动已经校验的安装程序；便携 ZIP 请手动解压。");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken); timeout.CancelAfter(TotalTimeout);
            using var guard = new UpdateDirectoryGuard(Path.GetDirectoryName(update.Path)!);
            using var stream = UpdateFileAccess.OpenRead(update.Path);
            await VerifyStreamAsync(update, stream, null, timeout.Token).ConfigureAwait(false);
            timeout.Token.ThrowIfCancellationRequested();
            // Keep the verified file and every parent pinned until process creation. The host
            // then closes through its protected operation; the installer manages service shutdown.
            var start = new ProcessStartInfo(update.Path) { WorkingDirectory = Path.GetDirectoryName(update.Path)!, UseShellExecute = true };
            var process = Process.Start(start) ?? throw new UpdateException("无法启动安装程序，请重试。");
            var session = new UpdateInstallerSession(update.Path, process);
            Volatile.Write(ref activeInstaller, session);
            return session;
        }
        finally { InstallerGate.Release(); }
    }, cancellationToken);

    public static string FormatBytes(double value)
    {
        var units = new[] { "B", "KiB", "MiB", "GiB" }; var index = 0;
        while (value >= 1024 && index < 3) { value /= 1024; index++; }
        return value.ToString(index == 0 ? "0" : "0.0", System.Globalization.CultureInfo.InvariantCulture) + " " + units[index];
    }

    public static string ProgressText(UpdateProgress progress)
    {
        var result = FormatBytes(progress.Received) + " / " + FormatBytes(progress.Total);
        if (progress.Stage == "downloading" && progress.Speed > 0)
        {
            var seconds = (long)Math.Clamp(progress.Remaining ?? 0, 0, 86400 * 365);
            result += " · " + FormatBytes(progress.Speed) + "/秒 · 预计剩余 "
                + (seconds >= 60 ? $"{seconds / 60} 分 {seconds % 60} 秒" : $"{seconds} 秒");
        }
        return result;
    }
}

internal static class UpdateFileAccess
{
    [StructLayout(LayoutKind.Sequential)]
    private struct UpdateHandleInformation
    {
        public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh,
            VolumeSerial, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    internal static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out UpdateHandleInformation information);

    internal static UpdateFileIdentity Identity(SafeFileHandle handle, bool directory = false)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("安装文件身份校验需要 Windows。");
        if (!GetFileInformationByHandle(handle, out var value)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if ((value.Attributes & 0x400) != 0 || ((value.Attributes & 0x10) != 0) != directory)
            throw new UpdateException("更新路径包含链接或不是预期的普通文件，已停止。");
        return new(value.VolumeSerial, ((ulong)value.IndexHigh << 32) | value.IndexLow,
            ((long)value.SizeHigh << 32) | value.SizeLow, ((long)value.CreationHigh << 32) | value.CreationLow,
            ((long)value.WriteHigh << 32) | value.WriteLow);
    }

    internal static FileStream OpenRead(string path)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("安装文件身份校验需要 Windows。");
        var handle = CreateFileW(path, 0x80000000, 1, IntPtr.Zero, 3, 0x00200000 | 0x08000000, IntPtr.Zero);
        if (handle.IsInvalid) { var error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
        try { Identity(handle); return new FileStream(handle, FileAccess.Read, 128 * 1024, false); }
        catch { handle.Dispose(); throw; }
    }
}

internal sealed class UpdateDirectoryGuard : IDisposable
{
    private readonly List<SafeFileHandle> handles = [];
    public UpdateDirectoryGuard(string directory)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("安装文件身份校验需要 Windows。");
        var path = Path.GetFullPath(directory);
        if (path.StartsWith(@"\\", StringComparison.Ordinal)) throw new UpdateException("更新缓存必须位于本地磁盘。");
        try
        {
            var ancestors = new Stack<string>();
            for (var current = new DirectoryInfo(path); current is not null; current = current.Parent) ancestors.Push(current.FullName);
            foreach (var parent in ancestors)
            {
                // Deny delete-sharing, so no ancestor can be renamed during verification.
                var handle = UpdateFileAccess.CreateFileW(parent, 0x80, 3, IntPtr.Zero, 3, 0x02000000 | 0x00200000, IntPtr.Zero);
                if (handle.IsInvalid) { var error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
                handles.Add(handle); UpdateFileAccess.Identity(handle, true);
            }
        }
        catch { Dispose(); throw; }
    }
    public void Dispose() { foreach (var handle in handles.AsEnumerable().Reverse()) handle.Dispose(); handles.Clear(); }
}
