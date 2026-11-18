using Microsoft.Extensions.Caching.Memory;
using Newtonsoft.Json.Linq;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.RateLimiting;

namespace Shared.Services;

public static class KinoApi
{
    const int maxResponseBytes = 1024 * 1024;
    static readonly MemoryCache cache = new(new MemoryCacheOptions { SizeLimit = 8 * 1024 * 1024 });
    static readonly Uri baseUrl = ReadBaseUrl();
    static readonly string key = ReadKey();
    static readonly Lazy<HttpClient> client = new(CreateClient);

    static readonly ConcurrentDictionary<string, Lazy<Task<JToken>>> pending = new();

    static long retryAfterTicks;
    static long nextWarningTicks;
    static readonly SlidingWindowRateLimiter rate = new(new SlidingWindowRateLimiterOptions
    {
        PermitLimit = 8,
        Window = TimeSpan.FromSeconds(1),
        SegmentsPerWindow = 4,
        QueueLimit = 0
    });

    static Uri ReadBaseUrl()
    {
        string configured = Environment.GetEnvironmentVariable("LAMPAC_KINOAPI_BASE_URL");
        string url = string.IsNullOrWhiteSpace(configured) ? "https://kinoapi.lampac.dev/api/v1/" : configured.Trim();
        if (Uri.TryCreate(url.TrimEnd('/') + "/", UriKind.Absolute, out var uri) &&
            (uri.Scheme == Uri.UriSchemeHttps || (uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback)) &&
            uri.UserInfo.Length == 0 && uri.Query.Length == 0 && uri.Fragment.Length == 0)
            return uri;

        Serilog.Log.Warning("KinoAPI disabled: invalid base URL");
        return null;
    }

    static HttpClient CreateClient()
    {
        var handler = Http.HandlerOrNull(baseUrl.AbsoluteUri, null) ?? new HttpClientHandler();
        handler.AllowAutoRedirect = false;
        handler.UseCookies = false;
        handler.ServerCertificateCustomValidationCallback = null;
        return new HttpClient(handler)
        {
            Timeout = TimeSpan.FromSeconds(2),
            MaxResponseContentBufferSize = maxResponseBytes
        };
    }

    static string ReadKey()
    {
        string directory = Environment.GetEnvironmentVariable("CREDENTIALS_DIRECTORY");
        if (string.IsNullOrEmpty(directory))
            return null;

        try
        {
            string value = File.ReadAllText(Path.Combine(directory, "kinoapi")).Trim();
            if (Regex.IsMatch(value, @"\A[A-Za-z0-9_-]{12,256}\z"))
                return value;
            Serilog.Log.Warning("KinoAPI disabled: invalid credential format");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            if (ex is not FileNotFoundException || Environment.GetEnvironmentVariable("LAMPAC_KINOAPI_BASE_URL") != null)
                Serilog.Log.Warning("KinoAPI disabled: cannot read credential ({Kind})", ex.GetType().Name);
        }
        return null;
    }

    public static bool Enabled => key != null && baseUrl != null;

    static void Warn(string reason)
    {
        long now = DateTime.UtcNow.Ticks;
        long previous = Interlocked.Read(ref nextWarningTicks);
        if (now >= previous && Interlocked.CompareExchange(ref nextWarningTicks, now + TimeSpan.TicksPerMinute, previous) == previous)
            Serilog.Log.Warning("KinoAPI request failed: {Reason}", reason);
    }

    static Task<JToken> Get(string path)
    {
        if (!Enabled)
            return Task.FromResult<JToken>(null);

        if (cache.TryGetValue(path, out byte[] data))
            return Task.FromResult(data == null ? null : JToken.Parse(Encoding.UTF8.GetString(data)));

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

            using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(baseUrl, path));
            request.Headers.Add("X-API-Key", key);
            using var response = await client.Value.SendAsync(request);
            if (response.StatusCode == HttpStatusCode.NotFound)
            {
                Store(path, null, TimeSpan.FromMinutes(10));
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
            {
                Warn($"HTTP {(int)response.StatusCode}");
                return null;
            }

            var data = JObject.Parse(await response.Content.ReadAsStringAsync())["data"];
            var result = Project(data);
            if (result != null)
            {
                Store(path, Encoding.UTF8.GetBytes(result.ToString(Newtonsoft.Json.Formatting.None)), TimeSpan.FromHours(12));
                return result;
            }
            Warn("unexpected response shape");
            return null;
        }
        catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException or Newtonsoft.Json.JsonException)
        {
            Warn(ex.GetType().Name);
            return null;
        }
        finally
        {
            pending.TryRemove(path, out _);
        }
    }

    static void Store(string path, byte[] data, TimeSpan ttl)
        => cache.Set(path, data, new MemoryCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = ttl,
            Size = 256 + path.Length * sizeof(char) + (data?.Length ?? 0)
        });

    static JToken Project(JToken data)
    {
        static JObject Pick(JToken token, params string[] fields)
        {
            var result = new JObject();
            if (token is JObject obj)
                foreach (var field in fields)
                    if (obj[field] is JValue value)
                        result[field] = value.DeepClone();
            return result;
        }

        if (data is JArray episodes)
            return new JArray(episodes.OfType<JObject>().Select(episode => Pick(episode, "episode_number", "name_ru")));
        if (data is not JObject card || card["id"] is not JObject ids ||
            !ValidIdNumber(ids["tmdb"]) || !ValidIdNumber(ids["kinopoisk"]) ||
            (ids["imdb"] != null && ids["imdb"].Type is not (JTokenType.Null or JTokenType.String)))
            return null;

        var title = Pick(card, "type");
        title["id"] = Pick(ids, "tmdb", "imdb", "kinopoisk");
        title["title"] = Pick(card["title"], "ru", "original");
        title["release"] = Pick(card["release"], "year");
        title["images"] = new JObject
        {
            ["kinopoisk"] = Pick((card["images"] as JObject)?["kinopoisk"], "poster")
        };
        return title;
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
