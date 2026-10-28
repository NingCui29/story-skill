using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace StoryWorkbench;

public sealed record UpdateBlock(long Offset, int Size, string Sha256);
public sealed record UpdateDownloadPlan(long Size, string Sha256, IReadOnlyList<UpdateBlock> Blocks)
{
    public const int MaxBytes = 1024 * 1024;
    public const int MaxBlockBytes = 4 * 1024 * 1024;
    public static string Digest(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    public static UpdateDownloadPlan Parse(byte[] data, UpdateResult package)
    {
        try
        {
            if (data.Length == 0 || data.Length > MaxBytes || data.Length != package.PlanSize
                || Digest(data) != package.PlanSha256) throw new FormatException();
            using var document = JsonDocument.Parse(data, new JsonDocumentOptions { MaxDepth = 8 });
            var root = document.RootElement;
            if (root.GetProperty("schema").GetInt32() != 1 || root.GetProperty("file").GetString() != package.AssetName
                || root.GetProperty("size").GetInt64() != package.AssetSize
                || root.GetProperty("sha256").GetString() != package.Sha256) throw new FormatException();
            var chunks = root.GetProperty("chunks");
            if (chunks.GetArrayLength() is < 1 or > 4096) throw new FormatException();
            var blocks = new List<UpdateBlock>(); long offset = 0;
            foreach (var chunk in chunks.EnumerateArray())
            {
                var block = new UpdateBlock(chunk.GetProperty("offset").GetInt64(), chunk.GetProperty("size").GetInt32(), chunk.GetProperty("sha256").GetString() ?? "");
                if (block.Offset != offset || block.Size is < 1 or > MaxBlockBytes || block.Size > package.AssetSize - offset
                    || !Regex.IsMatch(block.Sha256, @"\A[0-9a-f]{64}\z")) throw new FormatException();
                blocks.Add(block); offset += block.Size;
            }
            if (offset != package.AssetSize) throw new FormatException();
            return new(offset, package.Sha256, blocks);
        }
        catch (Exception error) when (error is JsonException or FormatException or InvalidOperationException or KeyNotFoundException or OverflowException)
        { throw new UpdateException("更新分块清单与安装包不一致，已停止。", error); }
    }
}

public sealed class UpdateRangeUnsupportedException : Exception { }
public interface IUpdateBlockCache
{
    byte[]? Read(UpdateBlock block);
    void Save(UpdateBlock block, byte[] bytes);
}

public sealed partial class UpdateService
{
    private static bool HasWebViewRuntime()
    {
        if (!OperatingSystem.IsWindows()) return false;
        const string key = @"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
        foreach (var hive in new[] { Microsoft.Win32.RegistryHive.CurrentUser, Microsoft.Win32.RegistryHive.LocalMachine })
        foreach (var view in new[] { Microsoft.Win32.RegistryView.Registry32, Microsoft.Win32.RegistryView.Registry64 })
        {
            try
            {
                using var root = Microsoft.Win32.RegistryKey.OpenBaseKey(hive, view);
                using var runtime = root.OpenSubKey(key);
                if (runtime?.GetValue("pv") is string version && Regex.IsMatch(version, @"\A[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\z")
                    && Version.TryParse(version, out var parsed) && parsed > new Version(0, 0, 0, 0)) return true;
            }
            catch (Exception error) when (error is System.Security.SecurityException or UnauthorizedAccessException or IOException) { }
        }
        return false;
    }

    /// <summary>Only verified complete blocks survive cancellation. Output is never an installed application.</summary>
    public async Task DownloadBlocksAsync(UpdateResult package, UpdateDownloadPlan plan, IUpdateBlockCache cache,
        Stream output, IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        using var group = CancellationTokenSource.CreateLinkedTokenSource(token);
        var stop = group.Token;
        var missing = new List<UpdateBlock>(); long reused = 0;
        foreach (var block in plan.Blocks)
        {
            stop.ThrowIfCancellationRequested();
            var bytes = cache.Read(block);
            if (bytes is not null && bytes.Length == block.Size && UpdateDownloadPlan.Digest(bytes) == block.Sha256) reused += block.Size;
            else missing.Add(block);
        }
        long downloaded = 0; var clock = Stopwatch.StartNew(); var reportLock = new object(); var lastReport = TimeSpan.Zero;
        void Report(int count)
        {
            lock (reportLock)
            {
                downloaded += count;
                if (count != 0 && downloaded < plan.Size - reused && clock.Elapsed - lastReport < TimeSpan.FromMilliseconds(100)) return;
                lastReport = clock.Elapsed;
                var speed = downloaded / Math.Max(clock.Elapsed.TotalSeconds, 0.001);
                progress?.Report(new("downloading", reused + downloaded, plan.Size, speed,
                    speed > 0 ? (plan.Size - reused - downloaded) / speed : null, reused));
            }
        }
        Report(0);
        // Three workers, not one task per block. Await every worker before fallback.
        int next = -1;
        async Task Worker()
        {
            try
            {
                while (true)
                {
                    int index = Interlocked.Increment(ref next);
                    if (index >= missing.Count) return;
                    var block = missing[index];
                    using var response = await OpenAsync(package.DownloadUrl, plan.Size, stop, block).ConfigureAwait(false);
                    using var source = await response.Content.ReadAsStreamAsync(stop).ConfigureAwait(false);
                    using var buffer = new MemoryStream(block.Size);
                    var bytes = new byte[128 * 1024]; int count;
                    while ((count = await ReadAsync(source, bytes, stop).ConfigureAwait(false)) != 0)
                    {
                        if (buffer.Length + count > block.Size) throw new UpdateException("更新分块超过清单大小，已停止。");
                        buffer.Write(bytes, 0, count); Report(count);
                    }
                    var complete = buffer.ToArray();
                    if (complete.Length != block.Size || UpdateDownloadPlan.Digest(complete) != block.Sha256)
                        throw new UpdateException("更新分块不完整或校验失败，请重试；已验证分块保留。");
                    cache.Save(block, complete);
                }
            }
            catch { group.Cancel(); throw; }
        }
        var workers = Enumerable.Range(0, Math.Min(3, missing.Count)).Select(_ => Worker()).ToArray();
        try { await Task.WhenAll(workers).ConfigureAwait(false); }
        catch
        {
            token.ThrowIfCancellationRequested();
            var failure = workers.Where(t => t.IsFaulted).SelectMany(t => t.Exception!.Flatten().InnerExceptions)
                .FirstOrDefault(e => e is not OperationCanceledException);
            if (failure is not null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
            throw;
        }
        progress?.Report(new("assembling", 0, plan.Size, Reused: reused));
        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        long written = 0;
        foreach (var block in plan.Blocks)
        {
            token.ThrowIfCancellationRequested();
            var bytes = cache.Read(block);
            if (bytes is null || bytes.Length != block.Size || UpdateDownloadPlan.Digest(bytes) != block.Sha256)
                throw new UpdateException("更新缓存已改变，请重新下载；当前应用保持原样。");
            await output.WriteAsync(bytes, token).ConfigureAwait(false); digest.AppendData(bytes); written += bytes.Length;
            progress?.Report(new("assembling", written, plan.Size, Reused: reused));
        }
        if (written != plan.Size || Convert.ToHexString(digest.GetHashAndReset()).ToLowerInvariant() != plan.Sha256)
            throw new UpdateException("分块合并后的完整 SHA256 不一致，文件不可安装。");
    }
}

internal sealed class UpdateDiskBlockCache : IUpdateBlockCache, IDisposable
{
    private readonly string directory;
    private readonly UpdateDirectoryGuard guard;
    private readonly FileStream lease;
    private readonly object gate = new();
    private static readonly Regex Name = new(@"\A[0-9a-f]{64}\.chunk\z");
    public UpdateDiskBlockCache(string directory)
    {
        this.directory = directory;
        Directory.CreateDirectory(directory);
        guard = new UpdateDirectoryGuard(directory);
        FileStream? acquired = null;
        try { lease = acquired = UpdateFileAccess.OpenExclusive(Path.Combine(directory, "cache.lock")); Prune(); }
        catch { acquired?.Dispose(); guard.Dispose(); throw; }
    }
    private string PathFor(UpdateBlock block)
    {
        if (!Regex.IsMatch(block.Sha256, @"\A[0-9a-f]{64}\z") || block.Size is < 1 or > UpdateDownloadPlan.MaxBlockBytes)
            throw new UpdateException("更新缓存标识无效。");
        return Path.Combine(directory, block.Sha256 + ".chunk");
    }
    public byte[]? Read(UpdateBlock block)
    {
        lock (gate)
        {
            try
            {
                using var stream = UpdateFileAccess.OpenRead(PathFor(block));
                if (stream.Length != block.Size) return null;
                var bytes = new byte[block.Size]; stream.ReadExactly(bytes);
                return UpdateDownloadPlan.Digest(bytes) == block.Sha256 ? bytes : null;
            }
            catch (Exception error) when (error is IOException or System.ComponentModel.Win32Exception or UnauthorizedAccessException or UpdateException) { return null; }
        }
    }
    public void Save(UpdateBlock block, byte[] bytes)
    {
        lock (gate)
        {
            if (bytes.Length != block.Size || UpdateDownloadPlan.Digest(bytes) != block.Sha256) throw new UpdateException("未校验分块不能缓存。");
            var target = PathFor(block); var temporary = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".part");
            try
            {
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                { file.Write(bytes); file.Flush(true); }
                File.Move(temporary, target, true);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
    private void Prune()
    {
        long retained = 0; int count = 0;
        foreach (var item in new DirectoryInfo(directory).EnumerateFiles().OrderByDescending(f => f.LastWriteTimeUtc))
        {
            if (!Name.IsMatch(item.Name) || (item.Attributes & FileAttributes.ReparsePoint) != 0) continue;
            retained += item.Length; count++;
            if (count > 2048 || retained > 512L * 1024 * 1024 || item.LastWriteTimeUtc < DateTime.UtcNow.AddDays(-30))
            { try { File.Delete(item.FullName); } catch (IOException) { } }
        }
    }
    public void Dispose() { try { Prune(); } finally { lease.Dispose(); guard.Dispose(); } }
}
