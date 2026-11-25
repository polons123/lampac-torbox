using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using Shared;
using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace SeasonWatch;
public class SeasonWatchController : BaseController
{
    const string report = "/var/lib/season-watch/report.json";
    static readonly SemaphoreSlim gate = new(4,4);

    static readonly object accountsLock = new();
    static DateTime accountsModified;
    static HashSet<string> accounts;
    bool Allowed() {
        var uid = requestInfo?.user_uid;
        if (string.IsNullOrEmpty(uid) || requestInfo.IsAnonymousRequest) return false;
        var user = CoreInit.conf.accsdb.findUser(uid);
        if (user == null || user.ban || user.expires <= DateTime.Now) return false;
        const string path = "/etc/season-watch/accounts.json";
        lock (accountsLock) {
            try {
                var modified = System.IO.File.GetLastWriteTimeUtc(path);
                if (accounts == null || modified != accountsModified) {
                    var entries = JArray.Parse(System.IO.File.ReadAllText(path));
                    if (entries.Any(x => x.Type != JTokenType.String)) throw new FormatException();
                    accounts = new HashSet<string>(entries.Values<string>(), StringComparer.OrdinalIgnoreCase);
                    accountsModified = modified;
                }
                return accounts.Contains(uid);
            } catch (Exception e) when (e is IOException || e is UnauthorizedAccessException ||
                                        e is Newtonsoft.Json.JsonException || e is FormatException) {
                accounts = null;
                throw new IOException("SeasonWatch account list unavailable", e);
            }
        }
    }
    ActionResult Error(int code,string message) => new JsonResult(new { detail=message }) {StatusCode=code};

    [HttpGet, AllowAnonymous, Route("season-watch.js")]
    public ActionResult Plugin() {
        Response.Headers["Cache-Control"]="no-cache";
        return Content(System.IO.File.ReadAllText("mods/SeasonWatch/plugin.js").Replace("{localhost}",host),"application/javascript; charset=utf-8");
    }
    [HttpGet,Route("season-watch/list")]
    public ActionResult List() {
        Response.Headers["Cache-Control"]="private, no-store";
        try {
            if (!Allowed()) return Error(403,"Нет доступа к библиотекам");
            return Content(System.IO.File.ReadAllText(report),"application/json; charset=utf-8");
        } catch (IOException) { return Error(503,"Результат проверки пока недоступен"); }
    }
    public class Input {
        public string place {get;set;}
        public string hash {get;set;}
        public int season {get;set;}
        public string choice {get;set;}
        public string confirmation {get;set;}
        public int identity {get;set;}
    }
    static bool Valid(string place,string hash) =>
        (place=="home" || place=="peer" || place=="box") && Regex.IsMatch(hash??"", @"\A[a-f0-9]{40}\z");

    [HttpGet, Route("season-watch/catalog")]
    public Task<ActionResult> Catalog() => Run("catalog");

    [HttpGet, Route("season-watch/media")]
    public Task<ActionResult> Media(string place,string hash) =>
        Valid(place,hash) ? Run("media",place,hash) : Task.FromResult(Error(400,"Некорректная раздача"));

    [HttpPost, Route("season-watch/update"), RequestSizeLimit(1024)]
    public Task<ActionResult> Update([FromBody] Input input) {
        if (Request.Headers["X-Season-Watch"]!="1" || input==null || !Valid(input.place,input.hash) ||
            (!string.IsNullOrEmpty(input.confirmation) && !Regex.IsMatch(input.confirmation,@"\A[a-f0-9]{64}\z")) ||
            input.season<0 || input.season>999 || !string.IsNullOrEmpty(input.choice) && !Regex.IsMatch(input.choice,@"\A[a-f0-9]{64}\z"))
            return Task.FromResult(Error(400,"Некорректный выбор"));
        return Run("update",input.place,input.hash,input.season.ToString(),input.choice??"",input.confirmation??"");
    }
    [HttpPost, Route("season-watch/identity"), RequestSizeLimit(1024)]
    public Task<ActionResult> Identity([FromBody] Input input) {
        if (Request.Headers["X-Season-Watch"]!="1" || input==null || !Valid(input.place,input.hash) || input.identity<1)
            return Task.FromResult(Error(400,"Некорректный выбор TMDB"));
        return Run("identity",input.place,input.hash,input.identity.ToString());
    }
    public class Removal {
        public string place {get;set;}
        public string hash {get;set;}
        public long? box_id {get;set;}
        public bool confirmed {get;set;}
    }

    [HttpPost, Route("season-watch/remove"), RequestSizeLimit(1024)]
    public Task<ActionResult> Remove([FromBody] Removal input) {
        if (Request.Headers["X-Season-Watch"]!="1" || input==null || !input.confirmed || !Valid(input.place,input.hash) ||
            (input.place=="box" ? input.box_id==null || input.box_id<0 : input.box_id!=null))
            return Task.FromResult(Error(400,"Подтвердите удаление выбранной раздачи"));
        return Run("remove",input.place,input.hash,input.box_id?.ToString()??"");
    }
    [HttpPost, Route("season-watch/remove/check"), RequestSizeLimit(1024)]
    public Task<ActionResult> CheckRemoval([FromBody] Removal input) {
        if (Request.Headers["X-Season-Watch"]!="1" || input==null || !Valid(input.place,input.hash))
            return Task.FromResult(Error(400,"Некорректная раздача"));
        return Run("verify-remove",input.place,input.hash);
    }
    async Task<ActionResult> Run(params string[] args) {
        Response.Headers["Cache-Control"]="private, no-store";
        try { if (!Allowed()) return Error(403,"Нет доступа к библиотекам"); }
        catch (IOException) { return Error(503,"Список доступа временно недоступен"); }
        if (!await gate.WaitAsync(TimeSpan.FromSeconds(2))) return Error(429,"Источник занят. Повторите запрос");
        try {
            var info=new ProcessStartInfo("/usr/bin/python3") { UseShellExecute=false,RedirectStandardOutput=true };
            if (args[0]=="remove" || args[0]=="update") info.Environment["SEASON_WATCH_ACTOR"]=requestInfo.user_uid;
            info.ArgumentList.Add("/usr/local/lib/season-watch/worker.py");
            foreach (var arg in args) info.ArgumentList.Add(arg);
            using var process=Process.Start(info);
            var reading=process.StandardOutput.ReadToEndAsync();
            using var deadline=new CancellationTokenSource(TimeSpan.FromMinutes(4));
            try { await process.WaitForExitAsync(deadline.Token); }
            catch (OperationCanceledException) {
                try { process.Kill(true); } catch (InvalidOperationException) { }
                return Error(504,"Источник не успел ответить");
            }
            string output=(await reading).Trim();
            if (process.ExitCode!=0) return Error(409,output.Length>0 && output.Length<=160 ? output : "Источник временно недоступен");
            return Content(output,"application/json; charset=utf-8");
        } finally { gate.Release(); }
    }
}
