using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading.Tasks;

namespace TorBox;
public partial class TorBoxController
{
    // A ticket authorizes only this fixed playlist and its already selected settings.
    // Only durable playback settings are stored; stream URLs and runtime locks are not.
    class ExternalPlaylist {
        public SeriesSession State;
        public string Access, Uid, ClientKey;
        public string SourceUrl, Filename;
        public int Selected;
        public volatile bool Ready;
        public bool ServerMode;
        public volatile bool Revoked;
        public ConcurrentDictionary<int,(DateTime Until,string Url)> Streams=new();
    }
    static readonly ConcurrentDictionary<string,ExternalPlaylist> externalPlaylists=new();
    const string externalPath="database/torbox-external.json";
    // ponytail: one lock for the small playlist store; relays never hold it during upstream I/O.
    static readonly object externalGate=new();
    static bool externalLoaded;
    static void LoadExternal() {
        if(externalLoaded)return;
        try {
            var data=JObject.Parse(System.IO.File.ReadAllText(externalPath));
            var loaded=data.Properties().Select(p=>(Hash:p.Name,Playlist:p.Value.ToObject<ExternalPlaylist>())).ToArray();
            if(loaded.Any(x=>!System.Text.RegularExpressions.Regex.IsMatch(x.Hash,"^[A-F0-9]{64}$") ||
                string.IsNullOrEmpty(x.Playlist?.Uid) || (x.Playlist.SourceUrl!=null
                    ? !NamedTarget(x.Playlist.SourceUrl) || string.IsNullOrEmpty(x.Playlist.Filename)
                    : x.Playlist.State?.Episodes==null || x.Playlist.State.Episodes.Count==0 || x.Playlist.State.Episodes.Any(e=>e.Pick?.Id==null))))
                throw new InvalidDataException();
            foreach(var row in loaded) {
                // Json.NET represents a stored null JToken as JValue.Null, not a null reference.
                if(row.Playlist.State?.Audio?.Type==JTokenType.Null)row.Playlist.State.Audio=null;
                if(row.Playlist.State?.Subtitle?.Type==JTokenType.Null)row.Playlist.State.Subtitle=null;
                externalPlaylists[row.Hash]=row.Playlist;
            }
        }
        catch(FileNotFoundException) { }
        catch(DirectoryNotFoundException) { }
        catch { throw new TorBoxException(503,"Не удалось прочитать сохранённые внешние плейлисты"); }
        externalLoaded=true;
    }
    static JObject StoredExternal(ExternalPlaylist p) => p.SourceUrl!=null
        ? JObject.FromObject(new { p.Uid,p.SourceUrl,p.Filename,Ready=true })
        : JObject.FromObject(new {
        p.Uid,p.ClientKey,p.ServerMode,p.Selected,Ready=true,
        State=new { p.State.Owner,p.State.Title,p.State.Selection,p.State.KeyHash,p.State.FollowId,
            p.State.Resolution,p.State.Audio,p.State.Subtitle,
            Episodes=p.State.Episodes.Select(e=>new { e.File,e.Season,e.Episode,e.Numbered,e.Name,
                e.ExplicitAudio,e.ExplicitSub,Pick=new { e.Pick.Id,e.Pick.Hash,e.Pick.Name } }) }
    });
    static void WriteExternal(JObject data) {
        string temp=externalPath+".tmp-"+Guid.NewGuid().ToString("N");
        try {
            Directory.CreateDirectory("database");
            using(var stream=new FileStream(temp,new FileStreamOptions { Mode=FileMode.CreateNew,
                Access=FileAccess.Write,Share=FileShare.None,UnixCreateMode=UnixFileMode.UserRead|UnixFileMode.UserWrite })) {
                using(var writer=new StreamWriter(stream,System.Text.Encoding.UTF8,4096,leaveOpen:true)) {
                    writer.Write(data.ToString(Newtonsoft.Json.Formatting.None));
                }
                stream.Flush(true);
            }
            System.IO.File.Move(temp,externalPath,true);
        }
        catch { throw new TorBoxException(503,"Не удалось сохранить внешние плейлисты на диске"); }
        finally { if(System.IO.File.Exists(temp))System.IO.File.Delete(temp); }
    }
    static JObject ExternalSnapshot(string exceptUid=null) => new JObject(externalPlaylists
        .Where(x=>!x.Value.Revoked && x.Value.Uid!=exceptUid).Select(x=>new JProperty(x.Key,StoredExternal(x.Value))));
    public class ExternalInput { public string session {get;set;} public int index {get;set;} public string preparation {get;set;} }
    static string ExternalHash(string value) => SeriesKeyHash(value??"");
    ExternalPlaylist GetExternal(string access) {
        lock(externalGate) {
            LoadExternal();
            if(string.IsNullOrEmpty(access) || !System.Text.RegularExpressions.Regex.IsMatch(access,"^[a-f0-9]{64}$") ||
                !externalPlaylists.TryGetValue(ExternalHash(access),out var playlist) || playlist.Revoked)
                throw new TorBoxException(410,"Плейлист отозван или отсутствует. Откройте сериал в Lampa заново");
            playlist.Access=access;
            return playlist;
        }
    }
    static SeriesEpisode CopyEpisode(SeriesEpisode e) => new(){Pick=e.Pick,File=e.File,Season=e.Season,Episode=e.Episode,Numbered=e.Numbered,Name=e.Name,
        Metadata=e.Metadata?.DeepClone(),ExplicitAudio=e.ExplicitAudio,ExplicitSub=e.ExplicitSub};
    [HttpPost,Route("torbox/series/external"),RequestSizeLimit(4096)]
    public Task<ActionResult> PrepareExternal([FromBody] ExternalInput input) => Guard(async(c,key)=>{
        if(!externalPlaylistsAllowed)return Error(403,"Внешние плейлисты отключены администратором");
        if(input==null || input.index<0)return Error(400,"Некорректный плейлист");
        lock(externalGate)LoadExternal();
        ExternalPlaylist external;
        if(string.IsNullOrEmpty(input.preparation)) {
            var source=GetSeries(input.session,key);
            if(!await source.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Плейлист ещё подготавливается");
            try {
                if(input.index>=source.Episodes.Count)return Error(400,"Серия отсутствует в плейлисте");
                var selected=source.Episodes[input.index];
                var episodes=source.Episodes.Where(e=>e.Season==selected.Season).Select(CopyEpisode).ToList();
                var state=new SeriesSession{Owner=source.Owner,Title=source.Title,Selection=source.Selection,KeyHash=source.KeyHash,
                    Resolution=source.Resolution,Audio=source.Audio?.DeepClone(),Subtitle=source.Subtitle?.DeepClone(),Episodes=episodes,FollowId=source.FollowId};
                if(state.Episodes.Count>5000)return Error(409,"Слишком большой плейлист сериала");
                string access=Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
                external=new ExternalPlaylist{State=state,Access=access,Uid=requestInfo.user_uid,ClientKey=serverCredentials?null:key,ServerMode=serverCredentials,
                    Selected=state.Episodes.FindIndex(e=>e.Pick==selected.Pick && e.File==selected.File)};
            }finally{source.Gate.Release();}
        } else {
            external=GetExternal(input.preparation);
            if(external.State==null || external.State.Owner!=metadataOwner || external.State.KeyHash!=SeriesKeyHash(key) || external.Uid!=requestInfo.user_uid || external.ServerMode!=serverCredentials)
                return Error(403,"Плейлист принадлежит другому аккаунту");
        }
        if(!await external.State.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Плейлист ещё подготавливается");
        try {
            lock(externalGate) {
                if(external.Revoked)return Error(410,"Плейлист отозван");
                string hash=ExternalHash(external.Access);
                var data=ExternalSnapshot();data[hash]=StoredExternal(external);
                WriteExternal(data);
                external.Ready=true;
                externalPlaylists[hash]=external;
            }
            return Payload(new{success=true,pending=false,selected_index=external.Selected,
                playlist=external.State.Episodes.Select((e,i)=>new{index=i,title=EpisodeTitle(e),season=e.Season,episode=e.Episode,
                    url=host+"/torbox/series/external/"+external.Access+"/"+i+".m3u8"}).ToArray()});
        }finally{external.State.Gate.Release();}
    });
    public class NamedInput { public NamedLink[] links {get;set;} }
    public class NamedLink { public string url {get;set;} public string filename {get;set;} }
    static bool NamedTarget(string value) => value!=null && value.Length<=8192 &&
        !value.Any(char.IsControl) && Uri.TryCreate(value,UriKind.Absolute,out var uri) &&
        (uri.Scheme=="http" || uri.Scheme=="https") && string.IsNullOrEmpty(uri.UserInfo);
    // Shared InfuseSaver uses this for any provider; the server only redirects, never fetches URLs.
    [HttpPost,Route("torbox/infuse/named"),RequestSizeLimit(2097152)]
    public Task<ActionResult> PrepareNamedInfuse([FromBody] NamedInput input) => Guard((c,key)=>{
        if(!externalPlaylistsAllowed)return Task.FromResult(Error(403,"Внешние плейлисты отключены администратором"));
        if(input?.links==null || input.links.Length==0 || input.links.Length>5000 || input.links.Any(x=>x==null ||
            !NamedTarget(x.url) || string.IsNullOrWhiteSpace(x.filename) || x.filename.Length>512))
            return Task.FromResult(Error(400,"Некорректные ссылки Infuse"));
        var created=input.links.Select(x=>new ExternalPlaylist {
            Access=Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant(),Uid=requestInfo.user_uid,
            SourceUrl=x.url,Filename=System.Text.RegularExpressions.Regex.Replace(x.filename,@"[\\/:*?""<>|\x00-\x1f\x7f]","_").Trim(),Ready=true
        }).ToArray();
        if(created.Any(p=>p.Filename=="." || p.Filename==".."))return Task.FromResult(Error(400,"Некорректное имя файла"));
        lock(externalGate) {
            LoadExternal();
            var data=ExternalSnapshot();
            foreach(var p in created)data[ExternalHash(p.Access)]=StoredExternal(p);
            WriteExternal(data);
            foreach(var p in created)externalPlaylists[ExternalHash(p.Access)]=p;
        }
        return Task.FromResult(Payload(new{success=true,links=created.Select(p=>new {
            url=host+"/torbox/series/external/"+p.Access+"/file/"+Uri.EscapeDataString(p.Filename),filename=p.Filename
        }).ToArray()}));
    });
    [HttpGet,HttpHead,AllowAnonymous,Route("torbox/series/external/{access}/file/{filename}")]
    public ActionResult NamedInfuseStream(string access,string filename) {
        Response.Headers["Cache-Control"]="no-store";
        Response.Headers["Referrer-Policy"]="no-referrer";
        try {
            var p=GetExternal(access);
            if(p.SourceUrl==null || p.Filename!=filename)return Error(404,"Ссылка отсутствует");
            var c=Config();
            if(!externalPlaylistsAllowed || (serverCredentials &&
                (c["allowed_uids"] as JArray)?.Any(x=>string.Equals((string)x,p.Uid,StringComparison.OrdinalIgnoreCase))!=true))
                return Error(403,"Нет доступа к внешней ссылке");
            if(p.Revoked)return Error(410,"Ссылка отозвана");
            return Redirect(p.SourceUrl);
        }
        catch(TorBoxException ex){return Error(ex.Status,ex.Message);}
        catch{return Error(503,"Не удалось открыть сохранённую ссылку");}
    }
    // Authenticated account-wide revocation also works after changing a personal key.
    [HttpPost,Route("torbox/series/external/revoke"),RequestSizeLimit(4096)]
    public Task<ActionResult> RevokeExternal() => Guard((c,key)=>{
        lock(externalGate) {
            LoadExternal();
            var entries=externalPlaylists.Where(x=>x.Value.Uid==requestInfo.user_uid).ToArray();
            // Persist removal before confirming revocation, so a restart cannot revive links.
            if(entries.Length>0)WriteExternal(ExternalSnapshot(requestInfo.user_uid));
            // Invalidate every selected ticket before cleanup; a busy relay cannot delay others.
            foreach(var entry in entries)entry.Value.Revoked=true;
            foreach(var entry in entries) {
                externalPlaylists.TryRemove(entry.Key,out _);
                entry.Value.ClientKey=null;
                entry.Value.Streams.Clear();
            }
            return Task.FromResult(Payload(new{success=true,revoked=entries.Length}));
        }
    });
    [HttpGet,HttpHead,AllowAnonymous,Route("torbox/series/external/{access}/{index:int}.m3u8")]
    public async Task<ActionResult> ExternalSeriesStream(string access,int index) {
        Response.Headers["Cache-Control"]="no-store";
        Response.Headers["Referrer-Policy"]="no-referrer";
        try {
            var external=GetExternal(access);
            if(external.State==null)return Error(404,"Серия отсутствует в плейлисте");
            if(!external.Ready)return Error(409,"Плейлист ещё подготавливается");
            if(index<0 || index>=external.State.Episodes.Count)return Error(404,"Серия отсутствует в плейлисте");
            var c=Config(external.ServerMode ? "file" : "client");
            if(!externalPlaylistsAllowed)return Error(403,"Внешние плейлисты отключены администратором");
            if(serverCredentials && (c["allowed_uids"] as JArray)?.Any(x=>string.Equals((string)x,external.Uid,StringComparison.OrdinalIgnoreCase))!=true)
                return Error(403,"Нет доступа к TorBox для этого аккаунта Lampac");
            if(serverCredentials!=external.ServerMode)return Error(410,"Настройки TorBox изменились. Откройте плейлист заново");
            string key=serverCredentials?((string)c["api_key"]??"").Trim():external.ClientKey;
            if(string.IsNullOrEmpty(key) || SeriesKeyHash(key)!=external.State.KeyHash)return Error(410,"Ключ TorBox изменился. Откройте плейлист заново");
            if(!await external.State.Gate.WaitAsync(TimeSpan.FromSeconds(10),HttpContext.RequestAborted))return Error(429,"Поток ещё подготавливается");
            try {
                if(external.Revoked)return Error(410,"Плейлист отозван");
                if(!external.Streams.TryGetValue(index,out var cached) || cached.Until<DateTime.UtcNow) {
                    var stream=await ResolveSeriesStream(external.State,index,key);
                    cached=(DateTime.UtcNow.AddSeconds(30),stream.Url);external.Streams[index]=cached;
                }
                if(external.Revoked)return Error(410,"Плейлист отозван");
                if(Request.Method!="HEAD")SaveSeriesPosition(external.State,index);
                return Redirect(cached.Url);
            }finally{if(external.Revoked)external.Streams.Clear();external.State.Gate.Release();}
        }
        catch(TorBoxException ex){return Error(ex.Status,ex.Message);}
        catch(System.Net.Http.HttpRequestException){return Error(502,"Не удалось связаться с TorBox");}
        catch(TaskCanceledException){return Error(504,"TorBox не ответил вовремя");}
        catch{return Error(502,"Не удалось подготовить поток TorBox");}
    }
}
