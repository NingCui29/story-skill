using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace StoryWorkbench;

/// <summary>Persist only bounded presentation values and opaque shelf keys; never document text, paths or URLs.</summary>
public sealed class ThemePreferences
{
    private readonly string path;
    private readonly Dictionary<string, string> storage = new(StringComparer.Ordinal);
    public string Theme { get; private set; } = "system";
    public IReadOnlyDictionary<string, string> Storage => new Dictionary<string, string>(storage);
    public ThemePreferences(string? isolatedState)
    {
        string directory = isolatedState ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StorySkill", "StoryWorkbench");
        path = Path.Combine(directory, "desktop-preferences.json");
        try
        {
            if (!File.Exists(path) || new FileInfo(path).Length > 16384) return;
            using var stored = JsonDocument.Parse(File.ReadAllText(path));
            if (stored.RootElement.TryGetProperty("theme", out var theme) && theme.GetString() is "system" or "light" or "dark") Theme = theme.GetString()!;
            if (stored.RootElement.TryGetProperty("storage", out var items) && items.ValueKind == JsonValueKind.Object)
                foreach (var item in items.EnumerateObject())
                    if (item.Value.ValueKind == JsonValueKind.String && ValidateStorage(item.Name, item.Value.GetString()) is { } value) storage[item.Name] = value;
        }
        catch (Exception e) when (e is IOException or JsonException or InvalidOperationException or UnauthorizedAccessException) { }
    }
    public void Save(string theme)
    {
        if (theme is not ("system" or "light" or "dark") || theme == Theme) return;
        Write(theme, storage); Theme = theme;
    }
    public bool SaveStorage(string key, string? value)
    {
        string? clean = ValidateStorage(key, value);
        if (clean is null) return false;
        if (storage.TryGetValue(key, out var old) && clean == old) return true;
        var next = new Dictionary<string, string>(storage) { [key] = clean };
        Write(Theme, next); storage[key] = clean; return true;
    }
    private void Write(string theme, IReadOnlyDictionary<string, string> values)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temporary, JsonSerializer.Serialize(new { theme, storage = values }), new UTF8Encoding(false));
            File.Move(temporary, path, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
    public static string? ValidateStorage(string key, string? text)
    {
        if (text is null || text.Length > 2048) return null;
        try
        {
            using var parsed = JsonDocument.Parse(text, new() { MaxDepth = 4 });
            var value = parsed.RootElement;
            bool Choice(string name, params string[] allowed) => value.TryGetProperty(name, out var field) && field.ValueKind == JsonValueKind.String && allowed.Contains(field.GetString());
            bool Flag(string name) => value.TryGetProperty(name, out var field) && field.ValueKind is JsonValueKind.True or JsonValueKind.False;
            bool Number(string name, int min, int max) => value.TryGetProperty(name, out var field) && field.TryGetInt32(out int number) && number >= min && number <= max;
            bool Fields(params string[] names) => value.ValueKind == JsonValueKind.Object && value.EnumerateObject().Select(p => p.Name).Order().SequenceEqual(names.Order());
            bool valid = key switch
            {
                "story-last-books" => value.ValueKind == JsonValueKind.Array && value.GetArrayLength() <= 8
                    && value.EnumerateArray().All(v => v.ValueKind == JsonValueKind.String && Regex.IsMatch(v.GetString()!, "\\A[0-9a-f]{20}\\z"))
                    && value.EnumerateArray().Select(v => v.GetString()).Distinct().Count() == value.GetArrayLength(),
                "story-reading" => Fields("size", "line", "font", "focus") && Choice("size", "16", "19", "22", "25")
                    && Choice("line", "1.6", "1.8", "1.9", "2.2") && Choice("font", "serif", "sans") && Flag("focus"),
                "story-columns" => Fields("left", "right", "navClosed", "contextClosed") && Number("left", 180, 360)
                    && Number("right", 200, 360) && Flag("navClosed") && Flag("contextClosed"),
                "story-shelf-view" => Fields("filter", "sort", "status") && Choice("filter", "all", "long", "short", "analysis")
                    && Choice("sort", "newest", "oldest") && Choice("status", "all", "serializing", "completed", "unknown"),
                _ => false
            };
            return valid ? JsonSerializer.Serialize(value) : null;
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or FormatException) { return null; }
    }
}
