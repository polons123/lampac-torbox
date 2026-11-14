using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Shared;
using Shared.Attributes;
using Shared.Services;
using Shared.Services.Utilities;
using System;
using System.Threading;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web;

namespace Tracks;

public class TracksController : BaseController
{
    // ponytail: 64 permanent stripes bound memory; collisions serialize unrelated probes.
    static readonly SemaphoreSlim[] ffprobeGates = Enumerable.Range(0, 64).Select(_ => new SemaphoreSlim(1, 1)).ToArray();
    static readonly System.Net.Http.HttpClient statHttp = FriendlyHttp.MessageClient("base", null, out _);

    [HttpGet, AllowAnonymous]
    [Staticache(cacheMinutes: 10, always: true, setHeadersNoCache: true)]
    [Route("tracks.js")]
    [Route("tracks/js/{token}")]
    public ActionResult Tracks(string token)
    {
        string plugin = FileCache.ReadAllText($"{ModInit.modpath}/plugin.js", "tracks.js")
            .Replace("{localhost}", host)
            .Replace("{token}", HttpUtility.UrlEncode(token));

        return ContentTo(plugin, "application/javascript; charset=utf-8");
    }


    [HttpGet]
    [Route("ffprobe")]
    async public Task<ActionResult> Ffprobe(string media, bool showerror)
    {
        if (string.IsNullOrWhiteSpace(media) || !media.StartsWith("http") || media.Contains("/transcoding/"))
            return ContentTo("{}");

        string magnethash = null;

        if (media.Contains("/dlna/stream"))
        {
            string path = Regex.Match(media, "\\?path=([^&]+)").Groups[1].Value;
            if (string.IsNullOrWhiteSpace(path))
                return ContentTo(showerror ? "path" : "{}");

            magnethash = HttpUtility.UrlDecode(path);
        }
        else if (media.Contains("/stream/") || media.Contains("/lite/pidtor/"))
        {
            media = Regex.Replace(media, "[^a-z0-9_:\\-\\/\\.\\=\\?\\&\\%\\@]+", "", RegexOptions.IgnoreCase);

            if (media.Contains("/stream/"))
            {
                if (ModInit.conf.tsuri != null)
                    media = Regex.Replace(media, "^https?://[^/]+", ModInit.conf.tsuri, RegexOptions.IgnoreCase);

                // TorrServer returns an empty body for preload/stat/m3u; ffprobe needs the play stream
                int q = media.IndexOf('?');
                if (q > 0)
                {
                    var args = media[(q + 1)..].Split('&', StringSplitOptions.RemoveEmptyEntries)
                        .Where(a => !Regex.IsMatch(a, "^(preload|stat|m3u|play)(=|$)", RegexOptions.IgnoreCase));

                    media = media[..q] + "?" + string.Join("&", args.Append("play"));
                }
            }
        }
        else if (media.Contains("/proxy/") && media.Contains(".mkv"))
        {
            string hash = Regex.Match(media, "/proxy/([^\n\r]+\\.mkv)").Groups[1].Value;
            media = ProxyLink.Decrypt(hash.AsSpan(), null)?.uri;
            if (string.IsNullOrWhiteSpace(media))
                return ContentTo(showerror ? "media" : "{}");
        }

        string argumentList = string.Empty;

        string memKey = $"tracks:ffprobe:{media}";
        string outPut = null;
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
        budget.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(ModInit.conf.ffprobe_timeout_seconds, 1, 120)));
        var gate = ffprobeGates[(StringComparer.Ordinal.GetHashCode(memKey) & int.MaxValue) % ffprobeGates.Length];
        try { await gate.WaitAsync(budget.Token); }
        catch (OperationCanceledException) { return StatusCode(HttpContext.RequestAborted.IsCancellationRequested ? 499 : 504, "{}"); }
        try
        {
            if (!hybridCache.TryGetValue(memKey, out outPut))
            {
                #region getFolder
                static string getFolder(string magnethash)
                {
                    return $"database/tracks/{magnethash}";
                }
                #endregion

                if (media.Contains("/stream/"))
                {
                    magnethash = Regex.Match(media, "link=([a-z0-9]+)").Groups[1].Value;
                    string index = Regex.Match(media, @"index=([0-9]+)", RegexOptions.IgnoreCase).Groups[1].Value;

                    if (!string.IsNullOrWhiteSpace(magnethash))
                        magnethash = $"{magnethash}_{index}";
                }
                else if (media.Contains("/lite/pidtor/"))
                {
                    magnethash = Regex.Match(media, "/lite/pidtor/s([a-z0-9]+)").Groups[1].Value;
                    string index = Regex.Match(media, @"tsid=([0-9]+)", RegexOptions.IgnoreCase).Groups[1].Value;

                    if (!string.IsNullOrWhiteSpace(magnethash))
                        magnethash = $"{magnethash}_{index}";
                }

                if (string.IsNullOrEmpty(magnethash))
                    magnethash = media;

                // A cache key must never remain a user-controlled filesystem path.
                magnethash = CrypTo.md5(magnethash);

                if (System.IO.File.Exists(getFolder(magnethash)))
                    outPut = await BrotliTo.DecompressAsync(getFolder(magnethash));

                if (string.IsNullOrWhiteSpace(outPut))
                {
                    if (!Uri.TryCreate(media, UriKind.Absolute, out var uri) ||
                        (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
                        return ContentTo(showerror ? "uri" : "{}");

                    // retry quick failures (transient TorrServer 5xx): up to 3 attempts, 500ms/1000ms backoff;
                    // a slow empty result (dead torrent) is not retried
                    for (int attempt = 1; attempt <= 3; attempt++)
                    {
                        var started = DateTime.UtcNow;
                        using var process = new System.Diagnostics.Process();
                        process.StartInfo.UseShellExecute = false;
                        process.StartInfo.RedirectStandardOutput = true;
                        process.StartInfo.RedirectStandardError = true;
                        process.StartInfo.StandardOutputEncoding = Encoding.UTF8;
                        process.StartInfo.FileName = CoreInit.Win32NT ? "data/ffprobe.exe" : System.IO.File.Exists("data/ffprobe") ? "data/ffprobe" : "ffprobe";

                        process.StartInfo.ArgumentList.Add("-v");
                        process.StartInfo.ArgumentList.Add("quiet");
                        process.StartInfo.ArgumentList.Add("-print_format");
                        process.StartInfo.ArgumentList.Add("json");
                        process.StartInfo.ArgumentList.Add("-show_format");
                        process.StartInfo.ArgumentList.Add("-show_streams");
                        process.StartInfo.ArgumentList.Add(AccsDbInvk.Args(uri.AbsoluteUri, HttpContext));

                        argumentList = process.StartInfo.FileName + " " + string.Join(" ", process.StartInfo.ArgumentList);

                        process.Start();

                        try
                        {
                            var stdout = process.StandardOutput.ReadToEndAsync(budget.Token);
                            var stderr = process.StandardError.ReadToEndAsync(budget.Token);
                            await Task.WhenAll(stdout, stderr, process.WaitForExitAsync(budget.Token));
                            outPut = await stdout;
                        }
                        catch
                        {
                            if (!process.HasExited)
                            {
                                try { process.Kill(entireProcessTree: true); }
                                catch (InvalidOperationException) { }
                                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                            }
                            throw;
                        }

                        if (!string.IsNullOrWhiteSpace(outPut) && Regex.Replace(outPut, "[\n\r\t ]+", "") != "{}")
                            break;

                        if (attempt == 3 || (DateTime.UtcNow - started).TotalSeconds > 5)
                            break;

                        await Task.Delay(500 * attempt, budget.Token);
                    }

                    if (outPut == null)
                        outPut = string.Empty;

                    if (Regex.Replace(outPut, "[\n\r\t ]+", "") == "{}")
                        outPut = string.Empty;

                    if (!string.IsNullOrWhiteSpace(outPut) && !string.IsNullOrWhiteSpace(magnethash))
                    {
                        await BrotliTo.CompressAsync(getFolder(magnethash), outPut);
                    }
                    else
                    {
                        // заглушка
                        hybridCache.Set(memKey, outPut, DateTime.Now.AddMinutes(5), inmemory: true);
                    }
                }
            }
        }
        catch (OperationCanceledException)
        {
            return StatusCode(HttpContext.RequestAborted.IsCancellationRequested ? 499 : 504, "{}");
        }
        finally
        {
            gate.Release();
        }

        return ContentTo(string.IsNullOrEmpty(outPut) ? (showerror ? argumentList : "{}") : outPut);
    }

    [HttpGet]
    [Route("tracks/stat")]
    async public Task<ActionResult> Stat(string media, string hash = null)
    {
        hash = string.IsNullOrEmpty(hash)
            ? Regex.Match(media ?? string.Empty, @"(?:[?&]link=|/lite/pidtor/(?:serial/|s))([a-f0-9]{40})(?![a-f0-9])", RegexOptions.IgnoreCase).Groups[1].Value
            : hash;
        if (!Regex.IsMatch(hash, "^[a-fA-F0-9]{40}$"))
            return ContentTo("{}");
        hash = hash.ToLowerInvariant();
        if (hash == string.Empty || string.IsNullOrWhiteSpace(ModInit.conf.tsstat))
            return ContentTo("{}");

        string memKey = $"tracks:stat:{ModInit.conf.tsstat}:{CrypTo.md5(ModInit.conf.tsauth ?? string.Empty)}:{hash}";
        if (hybridCache.TryGetValue(memKey, out string cached))
            return ContentTo(cached);

        try
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
            deadline.CancelAfter(TimeSpan.FromSeconds(12));
            using var request = new System.Net.Http.HttpRequestMessage(System.Net.Http.HttpMethod.Post, ModInit.conf.tsstat.TrimEnd('/') + "/torrents");
            request.Content = new System.Net.Http.StringContent(System.Text.Json.JsonSerializer.Serialize(new { action = "get", hash }), Encoding.UTF8, "application/json");
            if (!string.IsNullOrWhiteSpace(ModInit.conf.tsauth))
                request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Basic", Convert.ToBase64String(Encoding.UTF8.GetBytes(ModInit.conf.tsauth)));
            using var response = await statHttp.SendAsync(request, deadline.Token);
            response.EnsureSuccessStatusCode();
            string json = await response.Content.ReadAsStringAsync(deadline.Token);

            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var r = doc.RootElement;
            long num(string n) => r.TryGetProperty(n, out var v) && v.ValueKind == System.Text.Json.JsonValueKind.Number ? (long)v.GetDouble() : 0;

            string outPut = System.Text.Json.JsonSerializer.Serialize(new
            {
                seeders = num("connected_seeders"),
                active = num("active_peers"),
                total = num("total_peers"),
                speed = (long)(r.TryGetProperty("download_speed", out var sp) && sp.ValueKind == System.Text.Json.JsonValueKind.Number ? sp.GetDouble() : 0)
            });

            hybridCache.Set(memKey, outPut, DateTime.Now.AddSeconds(10), inmemory: true);
            return ContentTo(outPut);
        }
        catch
        {
            if (!HttpContext.RequestAborted.IsCancellationRequested)
                hybridCache.Set(memKey, "{}", DateTime.Now.AddSeconds(2), inmemory: true);
            return ContentTo("{}");
        }
    }
}
