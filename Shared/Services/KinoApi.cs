using Microsoft.Extensions.Caching.Memory;
using Newtonsoft.Json.Linq;
using Shared.Services.Hybrid;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.RateLimiting;

namespace Shared.Services;

public static class KinoApi
{
    static readonly HttpClient client = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromSeconds(2),
        MaxResponseContentBufferSize = 1024 * 1024
    };

    static readonly ConcurrentDictionary<string, Lazy<Task<JToken>>> pending = new();

    static readonly string key = ReadKey();
    static long retryAfterTicks;
    static readonly SlidingWindowRateLimiter rate = new(new SlidingWindowRateLimiterOptions
    {
        PermitLimit = 8,
        Window = TimeSpan.FromSeconds(1),
        SegmentsPerWindow = 4,
        QueueLimit = 0
    });

    static string ReadKey()
    {
        string directory = Environment.GetEnvironmentVariable("CREDENTIALS_DIRECTORY");
        if (string.IsNullOrEmpty(directory))
            return null;

        try
        {
            string value = File.ReadAllText(Path.Combine(directory, "kinoapi")).Trim();
            return Regex.IsMatch(value, @"\A[A-Za-z0-9_-]{12,256}\z") ? value : null;
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    public static bool Enabled => !string.IsNullOrWhiteSpace(key);

    static Task<JToken> Get(string path)
    {
        if (!Enabled)
            return Task.FromResult<JToken>(null);

        var cache = HybridCache.GetMemory();
        if (cache != null && cache.TryGetValue("kinoapi:" + path, out JToken data))
            return Task.FromResult(data);

        return pending.GetOrAdd(path, p => new Lazy<Task<JToken>>(() => Fetch(p))).Value;
    }

    static async Task<JToken> Fetch(string path)
    {
        try
        {
            if (DateTime.UtcNow.Ticks < Interlocked.Read(ref retryAfterTicks))
                return null;
            using var lease = rate.AttemptAcquire();
            if (!lease.IsAcquired)
                return null;

            using var request = new HttpRequestMessage(HttpMethod.Get, "https://kinoapi.lampac.dev/api/v1/" + path);
            request.Headers.Add("X-API-Key", key);
            using var response = await client.SendAsync(request);
            var cache = HybridCache.GetMemory();
            if (response.StatusCode == HttpStatusCode.NotFound)
            {
                cache?.Set<JToken>("kinoapi:" + path, null, TimeSpan.FromMinutes(10));
                return null;
            }
            if (response.StatusCode == HttpStatusCode.TooManyRequests)
            {
                var now = DateTimeOffset.UtcNow;
                var retry = response.Headers.RetryAfter;
                var next = retry?.Date ?? now.Add(retry?.Delta ?? TimeSpan.FromSeconds(2));
                Interlocked.Exchange(ref retryAfterTicks, next.UtcTicks);
            }
            if (!response.IsSuccessStatusCode)
                return null;

            var data = JObject.Parse(await response.Content.ReadAsStringAsync())["data"];
            if (data is JObject or JArray)
            {
                cache?.Set("kinoapi:" + path, data, TimeSpan.FromHours(12));
                return data;
            }
            return null;
        }
        catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException or Newtonsoft.Json.JsonException)
        {
            return null;
        }
        finally
        {
            pending.TryRemove(path, out _);
        }
    }

    public static async Task<JObject> Title(long tmdb, string imdb, long kinopoisk, bool series)
    {
        string type = series ? "tv" : "movie";
        if (!Regex.IsMatch(imdb ?? "", @"\Att\d{1,12}\z"))
            imdb = null;

        string lookup = tmdb > 0 ? $"tmdb/{tmdb}" : imdb != null ? $"imdb/{imdb}" : kinopoisk > 0 ? $"kinopoisk/{kinopoisk}" : null;
        if (lookup == null)
            return null;

        var data = await Get($"titles/by/{lookup}?type={type}&include=images") as JObject;
        if (data?["id"] is not JObject ids || Text(data["type"]) != type ||
            !ValidIdNumber(ids["tmdb"]) || !ValidIdNumber(ids["kinopoisk"]) ||
            (ids["imdb"] != null && ids["imdb"].Type is not (JTokenType.Null or JTokenType.String)) ||
            (tmdb > 0 && (long?)ids["tmdb"] != tmdb) ||
            (imdb != null && (string)ids["imdb"] is string returnedImdb && returnedImdb != imdb) ||
            (kinopoisk > 0 && (long?)ids["kinopoisk"] is long returnedKp && returnedKp != kinopoisk) ||
            (tmdb <= 0 && imdb != null && (string)ids["imdb"] != imdb) ||
            (tmdb <= 0 && imdb == null && (long?)ids["kinopoisk"] != kinopoisk))
            return null;

        return data;
    }

    public static async Task<(string Title, string Original, int Year)?> Alternative(
        long tmdb, string imdb, long kinopoisk, bool series, string title, string original, int year)
    {
        var data = await Title(tmdb, imdb, kinopoisk, series);
        var names = data?["title"] as JObject;
        string ru = Text(names?["ru"]);
        string canonical = Text(names?["original"]);
        var date = data?["release"] as JObject;
        int.TryParse(Text(date?["year"]) ?? date?["year"]?.ToString(), out int canonicalYear);
        if (string.IsNullOrWhiteSpace(ru) || ru.Length > 300)
            ru = title;
        if (string.IsNullOrWhiteSpace(canonical) || canonical.Length > 300)
            canonical = original;
        if (canonicalYear < 1870 || canonicalYear > 2200 || (year > 0 && Math.Abs(canonicalYear - year) > 1))
            canonicalYear = year;
        if (data == null || (string.Equals(title?.Trim(), ru?.Trim(), StringComparison.OrdinalIgnoreCase) &&
            string.Equals(original?.Trim(), canonical?.Trim(), StringComparison.OrdinalIgnoreCase) && year == canonicalYear))
            return null;

        return (ru, canonical, canonicalYear);
    }

    public static async Task<JArray> Episodes(long tmdb, int season)
        => tmdb > 0 && season > 0 ? await Get($"tv/by/tmdb/{tmdb}/seasons/{season}/episodes?type=tv") as JArray : null;

    static bool ValidIdNumber(JToken value) => value == null || value.Type == JTokenType.Null ||
        (value.Type == JTokenType.Integer && long.TryParse(value.ToString(), out _));

    static string Text(JToken value) => value?.Type == JTokenType.String ? (string)value : null;

    public static string Poster(JObject title)
    {
        var images = title?["images"] as JObject;
        var kinopoisk = images?["kinopoisk"] as JObject;
        string poster = Text(kinopoisk?["poster"]);
        return Uri.TryCreate(poster, UriKind.Absolute, out var url) && url.Scheme == Uri.UriSchemeHttps &&
            url.IsDefaultPort && url.UserInfo.Length == 0 && url.Host == "kinopoiskapiunofficial.tech" ? poster : null;
    }
}
