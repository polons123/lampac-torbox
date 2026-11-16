using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Shared;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Infuse;
public class InfuseController : BaseController
{
    public class Link { public string url { get; set; } public string filename { get; set; } }
    public class Input { public Link[] links { get; set; } }
    class Entry { public string Uid, Url, Filename; }
    const string store = "database/infuse-links.json";
    // ponytail: one lock for the small local store; no upstream requests run here.
    static readonly object gate = new();
    static Dictionary<string, Entry> entries;
    static string Hash(string token) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(token)));
    static bool Target(string value) => value != null && value.Length <= 8192 && !value.Any(char.IsControl) &&
        Uri.TryCreate(value, UriKind.Absolute, out var uri) && (uri.Scheme == "https" || uri.Scheme == "http") && string.IsNullOrEmpty(uri.UserInfo);
    static bool Active(string uid) {
        var user = string.IsNullOrEmpty(uid) ? null : CoreInit.conf.accsdb.findUser(uid);
        return user != null && !user.ban && user.expires > DateTime.Now;
    }
    void Headers() {
        Response.Headers["Cache-Control"] = "no-store";
        Response.Headers["Referrer-Policy"] = "no-referrer";
    }
    ActionResult Error(int status, string message) => new ContentResult {
        StatusCode = status, ContentType = "application/json; charset=utf-8",
        Content = JsonConvert.SerializeObject(new { success = false, detail = message })
    };
    static void Load() {
        if (entries != null) return;
        Dictionary<string, Entry> loaded;
        try { loaded = JsonConvert.DeserializeObject<Dictionary<string, Entry>>(System.IO.File.ReadAllText(store)); }
        catch (FileNotFoundException) { loaded = new(); }
        catch (DirectoryNotFoundException) { loaded = new(); }
        if (loaded == null || loaded.Any(x => !Regex.IsMatch(x.Key, "^[A-F0-9]{64}$") ||
            x.Value == null || string.IsNullOrEmpty(x.Value.Uid) || !Target(x.Value.Url) || string.IsNullOrWhiteSpace(x.Value.Filename)))
            throw new InvalidDataException();
        entries = loaded;
    }
    static void Write(Dictionary<string, Entry> data) {
        string temp = store + ".tmp-" + Guid.NewGuid().ToString("N");
        try {
            Directory.CreateDirectory("database");
            using (var stream = new FileStream(temp, new FileStreamOptions { Mode = FileMode.CreateNew,
                Access = FileAccess.Write, Share = FileShare.None, UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite })) {
                using (var writer = new StreamWriter(stream, Encoding.UTF8, 4096, leaveOpen: true))
                    writer.Write(JsonConvert.SerializeObject(data));
                stream.Flush(true);
            }
            System.IO.File.Move(temp, store, true);
            entries = data;
        }
        finally { if (System.IO.File.Exists(temp)) System.IO.File.Delete(temp); }
    }
    [HttpPost, Route("infuse/named"), RequestSizeLimit(2097152)]
    public ActionResult Prepare([FromBody] Input input) {
        Headers();
        if (!Active(requestInfo?.user_uid)) return Error(403, "Нет доступа к аккаунту Lampac");
        if (input?.links == null || input.links.Length == 0 || input.links.Length > 5000 || input.links.Any(x =>
            x == null || !Target(x.url) || string.IsNullOrWhiteSpace(x.filename) || x.filename.Length > 512))
            return Error(400, "Некорректные ссылки Infuse");
        var created = input.links.Select(x => new {
            Token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant(),
            Value = new Entry { Uid = requestInfo.user_uid, Url = x.url,
                Filename = Regex.Replace(x.filename, @"[\\/:*?""<>|\x00-\x1f\x7f]", "_").Trim() }
        }).ToArray();
        if (created.Any(x => x.Value.Filename == "." || x.Value.Filename == "..")) return Error(400, "Некорректное имя файла");
        try {
            lock (gate) {
                Load();
                var data = new Dictionary<string, Entry>(entries);
                foreach (var x in created) data.Add(Hash(x.Token), x.Value);
                Write(data);
            }
            return Content(JsonConvert.SerializeObject(new { success = true, links = created.Select(x => new {
                // token query parameters are already masked by the installed access-log filters.
                url = host + "/infuse/file/" + Uri.EscapeDataString(x.Value.Filename) + "?token=" + x.Token,
                filename = x.Value.Filename
            }) }), "application/json; charset=utf-8");
        }
        catch { return Error(503, "Не удалось сохранить ссылки Infuse на диске"); }
    }
    [HttpGet, HttpHead, AllowAnonymous, Route("infuse/file/{filename}")]
    public ActionResult Stream(string filename, string token) {
        Headers();
        if (token == null || !Regex.IsMatch(token, "^[a-f0-9]{64}$")) return Error(410, "Ссылка отозвана или отсутствует");
        try {
            lock (gate) {
                Load();
                if (!entries.TryGetValue(Hash(token), out var entry)) return Error(410, "Ссылка отозвана или отсутствует");
                if (entry.Filename != filename) return Error(404, "Имя файла не совпадает");
                if (!Active(entry.Uid)) return Error(403, "Нет доступа к аккаунту Lampac");
                return Redirect(entry.Url);
            }
        }
        catch { return Error(503, "Не удалось прочитать ссылки Infuse"); }
    }
    [HttpPost, Route("infuse/revoke"), RequestSizeLimit(4096)]
    public ActionResult Revoke() {
        Headers();
        if (!Active(requestInfo?.user_uid)) return Error(403, "Нет доступа к аккаунту Lampac");
        try {
            lock (gate) {
                Load();
                var data = entries.Where(x => x.Value.Uid != requestInfo.user_uid).ToDictionary(x => x.Key, x => x.Value);
                Write(data);
            }
            return Content("{\"success\":true}", "application/json; charset=utf-8");
        }
        catch { return Error(503, "Не удалось отозвать ссылки Infuse"); }
    }
}
