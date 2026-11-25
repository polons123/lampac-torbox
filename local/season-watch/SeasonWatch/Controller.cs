using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using Shared;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace SeasonWatch;
public class SeasonWatchController : BaseController
{
    const string report = "/var/lib/season-watch/report.json";
    static readonly SemaphoreSlim gate = new(1,1);

    bool Allowed() {
        var uid = requestInfo?.user_uid;
        if (string.IsNullOrEmpty(uid) || requestInfo.IsAnonymousRequest) return false;
        var user = CoreInit.conf.accsdb.findUser(uid);
        if (user == null || user.ban || user.expires <= DateTime.Now) return false;
        return JArray.Parse(System.IO.File.ReadAllText("/etc/season-watch/accounts.json"))
            .Values<string>().Contains(uid,StringComparer.OrdinalIgnoreCase);
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
        public string op {get;set;}
        public string id {get;set;}
        public string revision {get;set;}
        public string value {get;set;}
    }
    [HttpPost,Route("season-watch/action"),RequestSizeLimit(1024)]
    public async Task<ActionResult> Change([FromBody] Input input) {
        Response.Headers["Cache-Control"]="private, no-store";
        if (!Allowed()) return Error(403,"Нет доступа к библиотекам");
        if (Request.Headers["X-Season-Watch"]!="1" || input==null ||
            !Regex.IsMatch(input.id??"", @"\A[a-f0-9]{32}\z") || !Regex.IsMatch(input.revision??"", @"\A[a-f0-9]{32}\z") ||
            !(input.op=="apply" && string.IsNullOrEmpty(input.value) ||
              input.op=="bind" && Regex.IsMatch(input.value??"", @"\A[a-f0-9]{64}\z") ||
              input.op=="identity" && Regex.IsMatch(input.value??"", @"\A[1-9][0-9]{0,9}\z")))
            return Error(400,"Некорректный выбор");
        if (!await gate.WaitAsync(0)) return Error(409,"Уже выполняется обновление");
        try {
            var info=new ProcessStartInfo("/usr/bin/python3") { UseShellExecute=false,RedirectStandardOutput=true };
            foreach (var arg in new[]{"/usr/local/lib/season-watch/worker.py",input.op,input.id,input.revision,input.value??""})
                info.ArgumentList.Add(arg);
            using var process=Process.Start(info);
            var reading=process.StandardOutput.ReadToEndAsync();
            using var deadline=new CancellationTokenSource(TimeSpan.FromMinutes(4));
            try { await process.WaitForExitAsync(deadline.Token); }
            catch (OperationCanceledException) {
                try { process.Kill(true); } catch (InvalidOperationException) { }
                return Error(504,"Проверка заняла слишком много времени. Обновите список");
            }
            string detail=(await reading).Trim();
            if (process.ExitCode!=0) return Error(409,detail.Length>0 && detail.Length<=160 ? detail : "Не удалось обновить выбор");
            return new JsonResult(new {success=true});
        } finally { gate.Release(); }
    }
}
