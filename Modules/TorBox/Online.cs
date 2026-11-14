using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using Shared.Models.Templates;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace TorBox;
public partial class TorBoxController
{
    class PickGroup {
        public DateTime LastUsed = DateTime.UtcNow, CacheUntil;
        public string KeyHash;
        public Dictionary<string,bool?> Cache;
        public List<string> Tickets = new();
        public SemaphoreSlim CacheGate = new(1,1);
    }
    class Pick {
        public PickGroup Group = new();
        public string Owner, Link, Hash, Name, Movie, Next, SourceKey;
        public int Quality, Seeds, RequestedSeason, RequestedEpisode;
        public bool Serial;
        public Pick[] SeriesPicks;
        public JToken Torrent,ReadyTorrent;
        public string ReadyTicket,ReadyKeyHash,BoundKeyHash;
        public DateTime ReadyUntil;
        public bool Remembered;
        public long? Id;
        public FilmInput Film;
        public DateTime Expires = DateTime.UtcNow.AddHours(6);
        public SemaphoreSlim Gate = new(1,1);
        public ConcurrentDictionary<long,JToken> Metadata = new();
    }
    static readonly ConcurrentDictionary<string,Pick> picks = new();
    static readonly object pickCacheGate = new();
    static readonly ConcurrentDictionary<string,(DateTime Expires,JArray Rows)> searchCache = new();
    static readonly object searchCacheGate=new();
    static void StoreSearch(string searchKey,JArray rows) {
        var now=DateTime.UtcNow;
        lock(searchCacheGate) {
            foreach(var expired in searchCache.Where(x=>x.Value.Expires<=now).Select(x=>x.Key).ToArray())searchCache.TryRemove(expired,out _);
            while(searchCache.Count>=256 && !searchCache.ContainsKey(searchKey)) {
                string oldest=searchCache.OrderBy(x=>x.Value.Expires).Select(x=>x.Key).FirstOrDefault();
                if(oldest==null || !searchCache.TryRemove(oldest,out _))break;
            }
            searchCache[searchKey]=(now.AddMinutes(5),rows);
        }
    }

    // The web adapter requests nojson; native clients request the standard Lampac JSON templates.
    bool OnlineJson => !Request.Query.ContainsKey("nojson") && bool.TryParse(Request.Query["rjson"],out bool json) && json;
    ActionResult JsonMenu(ITplResult template) => Content(template.ToJson(),"application/json; charset=utf-8");
    ActionResult JsonStream(string url,string title) => Content(VideoTpl.ToJson("play",url,title,hls_manifest_timeout:60000),"application/json; charset=utf-8");
    ActionResult Similar(SimilarTpl t, params (int Season,int Episode)[] frames) {
        if(OnlineJson)return JsonMenu(t);
        string html=t.ToHtml();
        if(frames.Length>0){
            int index=0;
            html=Regex.Replace(html,"data-json='\\{",m=>{
                var frame=frames[Math.Min(index++,frames.Length-1)];
                return "data-json='{\"torbox_preview\":true,\"season\":"+frame.Season+",\"episode\":"+frame.Episode+",";
            });
        }
        return Content(html,"text/html; charset=utf-8");
    }
    string OnlineUrl(string path) => host + "/lite/torbox/" + path + (OnlineJson ? (path.Contains('?')?"&":"?")+"rjson=true" : "");
    string NativeMediaUrl(string pick,long file=-1,int audio=-1,int sub=-2,int resolution=0) => accsArgs(OnlineUrl("stream.m3u8?pick="+pick+"&file="+file+"&audio="+audio+"&sub="+sub+"&resolution="+resolution));
    string OriginalFileUrl(string pick,long file=-1) => accsArgs(OnlineUrl("file?pick="+pick+"&file="+file));
    VoiceTpl NativeReleases(Pick selected) {
        var voices=new VoiceTpl();int index=0;
        foreach(string ticket in selected.Group.Tickets) {
            if(!picks.TryGetValue(ticket,out var entry))continue;
            voices.Append("Раздача "+(++index)+": "+entry.Name,entry==selected,OnlineUrl("options?pick="+ticket));
        }
        return voices;
    }
    StreamQualityTpl NativeQualities(string ticket,long file,int audio,int sub,int height) {
        var qualities=new StreamQualityTpl();
        qualities.Append(NativeMediaUrl(ticket,file,audio,sub),height>0?height+"p":"auto");
        foreach(int resolution in new[]{1440,1080,720,480,360}.Where(r=>r<height))
            qualities.Append(NativeMediaUrl(ticket,file,audio,sub,resolution),resolution+"p");
        qualities.Append(OriginalFileUrl(ticket,file),"Файл без изменений");
        return qualities;
    }
    ActionResult OriginalFileMenu(string ticket,Pick pick,JToken[] files,long file) {
        var voices=NativeReleases(pick);
        voices.Append("Поток TorBox: качество, аудио и субтитры",false,OnlineUrl("options?pick="+ticket+"&file="+file));
        foreach(var entry in files)
            voices.Append("Файл без изменений: "+FileName(entry),false,OnlineUrl("options?pick="+ticket+"&file="+entry["id"]+"&original=true"));
        var movie=new MovieTpl(pick.Movie,pick.Movie,voices);
        movie.Append("Файл без изменений — все исходные дорожки",OriginalFileUrl(ticket,file),voice_name:pick.Name+" · "+FileName(files.First(f=>(long?)f["id"]==file)),quality:"original");
        return JsonMenu(movie);
    }
    ActionResult NativeOptionsMenu(string ticket,Pick pick,JToken[] files,long file,JArray audios,JArray subtitles,int height,int audio,int sub) {
        var voices=NativeReleases(pick);
        string path="options?pick="+ticket+"&file="+file;
        voices.Append("Файл без изменений — прямая ссылка",false,OnlineUrl(path+"&original=true"));
        if(files.Length>1)foreach(var entry in files)
            voices.Append("Файл: "+FileName(entry),false,OnlineUrl("options?pick="+ticket+"&file="+entry["id"]));
        foreach(var entry in audios.Select((track,index)=>new{Track=track,Index=index}))
            voices.Append("Аудио: "+TrackLabel(entry.Track),false,OnlineUrl(path+"&audio="+entry.Index+"&sub="+sub));
        voices.Append("Субтитры: выключены",false,OnlineUrl(path+"&audio="+audio+"&sub=-1"));
        foreach(var entry in subtitles.Select((track,index)=>new{Track=track,Index=index}))
            voices.Append("Субтитры: "+TrackLabel(entry.Track),false,OnlineUrl(path+"&audio="+audio+"&sub="+entry.Index));
        string selection=(audios.Count>audio?TrackLabel(audios[audio]):"Звук по умолчанию")+" · "+(sub<0?"Без субтитров":TrackLabel(subtitles[sub]));
        var movie=new MovieTpl(pick.Movie,pick.Movie,voices);
        movie.Append(selection,NativeMediaUrl(ticket,file,audio,sub),streamquality:NativeQualities(ticket,file,audio,sub,height),voice_name:pick.Name+" · "+FileName(files.First(f=>(long?)f["id"]==file)),hls_manifest_timeout:60000);
        return JsonMenu(movie);
    }
    [HttpGet,Route("lite/torbox/options")]
    public Task<ActionResult> NativeOptions(string pick,long file=-1,int audio=-1,int sub=-2,bool original=false) => Choose(pick,file,audio,sub,nativeOptions:true,original:original);
    ActionResult MediaRedirect(ActionResult action) {
        if(action is ContentResult content && (content.StatusCode??200)==200) {
            var video=JObject.Parse(content.Content);string url=(string)video["url"];
            if(Uri.TryCreate(url,UriKind.Absolute,out var uri) && uri.Scheme=="https")return Redirect(url);
        }
        return action;
    }
    [HttpGet,Route("lite/torbox/file")]
    public Task<ActionResult> OriginalFile(string pick,long file=-1) => Guard(async(c,key)=>{
        var requested=GetPick(pick);
        if(requested.Serial && file<0)return Error(400,"Выберите конкретный файл серии");
        if(!await requested.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Раздача ещё подготавливается");
        (string Ticket, Pick Selection, JToken Torrent)? ready;
        try { ready=await FindReadyFrom(pick,c,key); }
        finally { requested.Gate.Release(); }
        if(ready==null)return Error(404,"В кэше TorBox нет готового файла");
        var files=(ready.Value.Torrent["files"] as JArray??new JArray()).Where(f=>VideoName(FileName(f))).OrderByDescending(f=>(long?)f["size"]??0).ToArray();
        if(file<0)file=(long?)files.FirstOrDefault()?["id"]??-1;
        if(!files.Any(f=>(long?)f["id"]==file))return Error(404,"Видеофайл отсутствует в раздаче");
        return MediaRedirect(await Play(new PlayInput{id=ready.Value.Selection.Id.Value,file=file}));
    });
    [HttpGet,Route("lite/torbox/stream.m3u8")]
    public Task<ActionResult> NativeMedia(string pick,long file=-1,int audio=-1,int sub=-2,int resolution=0) => Guard(async(c,key)=>{
        if(!OnlineJson)return Error(400,"Некорректный формат ссылки TorBox");
        return MediaRedirect(await Choose(pick,file,audio,sub,resolution,native:true));
    });
    Pick GetPick(string ticket) {
        lock(pickCacheGate) {
        if (ticket == null || !picks.TryGetValue(ticket,out var p) || p.Expires < DateTime.UtcNow || p.Owner != metadataOwner)
            throw new TorBoxException(410,"Список устарел. Откройте TorBox заново из карточки фильма");
        p.Group.LastUsed=DateTime.UtcNow;
        return p;
        }
    }
    static readonly Regex releaseResolution=new(@"(?<!\d)(2160|1440|1080|720|576|480|360)(?:p|\b)",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseUhd=new(@"\b(?:4k|uhd(?:rip)?)\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseDv=new(@"\b(?:DV|DoVi|Dolby[ ._-]*Vision)\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseHdr10Plus=new(@"\bHDR10(?:\+|Plus\b)",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseHdr10=new(@"\bHDR10\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    // A trailing word boundary keeps HDRip out of HDR. HDR10 is matched by its own pattern.
    static readonly Regex releaseHdr=new(@"\bHDR\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseSdr=new(@"\bSDR\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseHevc=new(@"\b(?:HEVC|[xh][ ._-]?265)\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseAvc=new(@"\b(?:AVC|[xh][ ._-]?264)\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly Regex releaseAv1=new(@"\bAV1\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
    static readonly string[] resolutionLabels={"2160p","1440p","1080p","720p","576p","480p","360p","Не указано"};
    static readonly string[] hdrLabels={"Dolby Vision + HDR","Dolby Vision","HDR10+","HDR10","HDR","SDR","Не указано"};
    static readonly string[] codecLabels={"AV1","HEVC","AVC","Не указано"};
    static readonly string[] sizeLabels={"До 4 ГиБ","4–10 ГиБ","10–25 ГиБ","25–50 ГиБ","От 50 ГиБ","Не указан"};
    class ReleaseTags { public int Resolution; public string Res,Hdr,Codec; }
    static int TraitRank(string field,string value) {
        string[] order=field switch{"res"=>resolutionLabels,"hdr"=>hdrLabels,"codec"=>codecLabels,"size"=>sizeLabels,_=>Array.Empty<string>()};
        int index=Array.IndexOf(order,value);
        return index<0?order.Length:index;
    }
    static ReleaseTags ParseReleaseTags(string title) {
        title??="";
        int resolution=0;
        foreach(Match match in releaseResolution.Matches(title))resolution=Math.Max(resolution,int.Parse(match.Groups[1].Value));
        if(resolution==0 && releaseUhd.IsMatch(title))resolution=2160;
        bool dv=releaseDv.IsMatch(title),hdr10plus=releaseHdr10Plus.IsMatch(title),hdr10=releaseHdr10.IsMatch(title),hdr=releaseHdr.IsMatch(title);
        return new ReleaseTags{Resolution=resolution,Res=resolution>0?resolution+"p":"Не указано",
            Hdr=dv&&(hdr10plus||hdr10||hdr)?"Dolby Vision + HDR":dv?"Dolby Vision":hdr10plus?"HDR10+":hdr10?"HDR10":hdr?"HDR":releaseSdr.IsMatch(title)?"SDR":"Не указано",
            Codec=releaseHevc.IsMatch(title)?"HEVC":releaseAvc.IsMatch(title)?"AVC":releaseAv1.IsMatch(title)?"AV1":"Не указано"};
    }
    static ReleaseTags StoredTags(JToken row) {
        if(row["tb_resolution"]?.Type==JTokenType.Integer && row["tb_res"]?.Type==JTokenType.String && row["tb_hdr"]?.Type==JTokenType.String && row["tb_codec"]?.Type==JTokenType.String)
            return new ReleaseTags{Resolution=(int)row["tb_resolution"],Res=(string)row["tb_res"],Hdr=(string)row["tb_hdr"],Codec=(string)row["tb_codec"]};
        return ParseReleaseTags((string)row["Title"]);
    }
    static void StoreTags(JObject row,ReleaseTags tags) {
        row["tb_resolution"]=tags.Resolution;row["tb_res"]=tags.Res;row["tb_hdr"]=tags.Hdr;row["tb_codec"]=tags.Codec;
    }
    static ReleaseTags MergeTags(IEnumerable<(ReleaseTags Tags,int Seeds)> items) {
        var rows=items.ToArray();
        int resolution=rows.Max(x=>x.Tags.Resolution);
        string hdr=rows.Select(x=>x.Tags.Hdr).OrderBy(v=>TraitRank("hdr",v)).First();
        string codec=rows.Any(x=>x.Tags.Codec!="Не указано")?rows.Where(x=>x.Tags.Codec!="Не указано").OrderByDescending(x=>x.Seeds).First().Tags.Codec:"Не указано";
        return new ReleaseTags{Resolution=resolution,Res=resolution>0?resolution+"p":"Не указано",Hdr=hdr,Codec=codec};
    }
    // Unknown quality is 0 so it sorts below every labelled resolution.
    static int Resolution(JToken t) => StoredTags(t).Resolution;
    static string Normalize(string s) => Regex.Replace((s??"").Normalize(NormalizationForm.FormKC).ToLowerInvariant().Replace('ё','е'),"[^\\p{L}\\p{N}]+", " ").Trim();
    static bool VideoName(string name) => Regex.IsMatch(name??"",@"\.(mkv|mp4|avi|m4v|mov|ts|m2ts|webm|mpg|mpeg|wmv)$",RegexOptions.IgnoreCase) && !Regex.IsMatch(name??"",@"\b(sample|trailer|трейлер)\b",RegexOptions.IgnoreCase);
    static string FileName(JToken f) => (string)f["short_name"] ?? (string)f["name"] ?? "Видео";
    // Films keep a title only when it names this year. Series keep a later season year and drop an older namesake.
    static bool ReleaseYearAllowed(string title,int year,bool serial) {
        if(year<=0)return true;
        var years=Regex.Matches(title??"",@"\b(?:19|20)\d{2}\b").Select(m=>int.Parse(m.Value)).ToArray();
        if(years.Length==0)return true;
        return serial?years.Any(y=>y>=year-1):years.Contains(year);
    }

    [HttpGet, Route("lite/torbox")]
    public Task<ActionResult> Online(string title, string original_title, int year=0, byte serial=0, bool rjson=true, long id=0, long tmdb_id=0, string source=null, string tb_voice=null, int tb_season=0, int tb_episode=0, string tb_res=null, string tb_hdr=null, string tb_codec=null, string tb_size=null) => Guard(async(c,key)=>{
        if(string.IsNullOrWhiteSpace(title) && string.IsNullOrWhiteSpace(original_title))return Error(400,"Не указано название фильма");
        if(tb_season<0 || tb_season>99 || tb_episode<0 || tb_episode>999 ||
            (tb_season==0)!=(tb_episode==0) || (serial!=1 && tb_season>0))return Error(400,"Некорректный сезон или серия");
        string searchKey=SeriesKeyHash(metadataOwner+":"+c["jacred"]?.ToString(Newtonsoft.Json.Formatting.None))+":"+Normalize(title)+":"+Normalize(original_title)+":"+year+":"+serial;
        JArray rows;
        if(searchCache.TryGetValue(searchKey,out var hit) && hit.Expires>DateTime.UtcNow)rows=hit.Rows;
        else {
            var parser=c["jacred"] as JObject;
            if(parser==null || !Uri.TryCreate((string)parser["base_url"],UriKind.Absolute,out var baseUri))return Error(503,"JacRed для TorBox не настроен");
            string url=baseUri.ToString().TrimEnd('/')+"/api/v2.0/indexers/all/results?title="+Uri.EscapeDataString(title??original_title)+"&title_original="+Uri.EscapeDataString(original_title??title)+"&year="+year+"&is_serial="+(serial==1?2:1)+"&apikey="+Uri.EscapeDataString((string)parser["api_key"]??"");
            using var response=await ParserClient.GetAsync(url,HttpContext.RequestAborted);
            if(!response.IsSuccessStatusCode)return Error(502,"JacRed не ответил на поиск");
            var parsed=JObject.Parse(await response.Content.ReadAsStringAsync());
            rows=parsed["Results"] as JArray ?? new JArray();
            StoreSearch(searchKey,rows);
        }
        var eligible=rows.OfType<JObject>().Where(t=>!string.IsNullOrWhiteSpace((string)t["Title"]) &&
            (TitleMatches((string)t["Title"],title) || TitleMatches((string)t["Title"],original_title)) &&
            !Regex.IsMatch((string)t["Title"],@"\b(CAMRip|HDCAM|HDTS|TSRip|TELESYNC)\b",RegexOptions.IgnoreCase) &&
            ReleaseYearAllowed((string)t["Title"],year,serial==1))
            .OrderByDescending(Resolution).ThenByDescending(t=>(int?)t["Seeders"]??0);
        var releases=DeduplicateReleases(eligible).ToArray();
        if(releases.Length==0)return Error(404,"JacRed не нашёл подходящих раздач фильма");
        var voices=releases.SelectMany(ReleaseVoices).Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(v=>v,StringComparer.OrdinalIgnoreCase).ToArray();
        var filters=new JObject{["res"]=tb_res??"",["hdr"]=tb_hdr??"",["codec"]=tb_codec??"",["size"]=tb_size??""};
        if(filters.Properties().Any(f=>((string)f.Value).Length>32))return Error(400,"Некорректный фильтр TorBox");
        var options=new JObject();
        foreach(var field in filters.Properties())options[field.Name]=new JArray(releases.Select(t=>ReleaseTrait(t,field.Name)).Distinct().OrderBy(v=>TraitRank(field.Name,v)));
        string selected=(tb_voice??"").Trim();
        if(selected.Length>160)return Error(400,"Слишком длинное название озвучки");
        selected=voices.FirstOrDefault(v=>string.Equals(v,selected,StringComparison.OrdinalIgnoreCase))??selected;
        string search=host+"/lite/torbox?title="+Uri.EscapeDataString(title??"")+"&original_title="+Uri.EscapeDataString(original_title??"")+"&year="+year+"&serial="+serial+"&id="+id+"&tmdb_id="+tmdb_id+"&source="+Uri.EscapeDataString(source??"")+"&tb_season="+tb_season+"&tb_episode="+tb_episode;
        foreach(var field in filters.Properties())search+="&tb_"+field.Name+"="+Uri.EscapeDataString((string)field.Value);
        if(OnlineJson)search+="&rjson=true";
        var matching=releases.Where(t=>(selected.Length==0 || ReleaseVoices(t).Contains(selected,StringComparer.OrdinalIgnoreCase)) &&
            filters.Properties().All(f=>string.IsNullOrEmpty((string)f.Value) || ReleaseTrait(t,f.Name)==(string)f.Value)).ToArray();
        var states=await ReleaseCacheStates(key,matching);
        var episodeMatches=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if(tb_season>0) {
            // Known library files can resolve an exact episode before the menu cap, without attaching torrents.
            JArray owned=null;
            try { owned=await OwnedTorrents(key).WaitAsync(TimeSpan.FromSeconds(2)); }
            catch(Exception ex) { MetadataWarning("search-episode-library",ex); }
            foreach(var torrent in (owned??new JArray()).Where(CachedReady)) {
                string hash=NormalizeCacheHash((string)torrent["hash"]);
                if(hash==null)continue;
                foreach(var row in matching.Where(t=>ReleaseHash(t)==hash))
                    if(EpisodeFiles(new Pick{Name=(string)row["Title"]},torrent).Any(e=>e.Numbered && e.Season==tb_season && e.Episode==tb_episode))
                        episodeMatches.Add(hash);
            }
        }
        var candidates=matching.OrderByDescending(t=>episodeMatches.Contains(ReleaseHash(t)??""))
            .ThenByDescending(t=>states.TryGetValue(ReleaseHash(t)??"",out var cached) && cached==true)
            .ThenByDescending(Resolution).ThenByDescending(t=>(int?)t["Seeders"]??0).Take(serial==1?96:12).ToArray();
        var seasons=releases.Select(t=>SeasonHint((string)t["Title"])).Where(s=>s>0).Distinct().OrderBy(s=>s).ToArray();
        if(candidates.Length==0) {
            var empty=new SimilarTpl();empty.Append("По выбранным фильтрам раздач нет. Измените фильтры или выберите «Все».",null,null,search);
            if(OnlineJson)return JsonMenu(empty);
            return Content(SimilarReleaseHtml(empty,voices,selected,search,true,tb_season,tb_episode,seasons,options,filters),"text/html; charset=utf-8");
        }
        lock(pickCacheGate) {
        foreach(var expired in picks.Where(p=>p.Value.Expires<DateTime.UtcNow).ToArray())picks.TryRemove(expired.Key,out _);
        // Evict whole least-recently-used menus, keeping release chains intact.
        // Allocation and eviction share a lock so concurrent searches stay bounded.
        while(picks.Count+candidates.Length>512) {
            var oldest=picks.Values.OrderBy(p=>p.Group.LastUsed).First().Group;
            foreach(var entry in picks.Where(p=>p.Value.Group==oldest).ToArray())picks.TryRemove(entry.Key,out _);
        }
        var group=new PickGroup{KeyHash=SeriesKeyHash(key),Cache=new Dictionary<string,bool?>(),
            CacheUntil=DateTime.UtcNow.AddSeconds(states.Values.Any(v=>v.HasValue)?30:10)};
        var tpl=new SimilarTpl();
        var nativeMovies=OnlineJson && serial!=1 ? new MovieTpl(title,original_title) : null;
        string previous=null;
        var seriesPicks=new List<Pick>();
        for(int i=0;i<candidates.Length;i++) {
            var t=candidates[i];string link=(string)t["MagnetUri"] ?? (string)t["Link"];
            if(string.IsNullOrWhiteSpace(link))continue;
            string hash=ReleaseHash(t)??"";
            string ticket=Guid.NewGuid().ToString("N");
            var p=new Pick{Group=group,Owner=metadataOwner,Link=link,Hash=hash,Name=(string)t["Title"],Movie=title??original_title,Serial=serial==1,Quality=Resolution(t),Seeds=(int?)t["Seeders"]??0,
                RequestedSeason=tb_season,RequestedEpisode=tb_episode,SourceKey=ReleaseSource(t),Film=new FilmInput{tmdb_id=tmdb_id>0 ? tmdb_id : (source=="tmdb" || source=="cub" ? id : 0),title=string.IsNullOrWhiteSpace(title)?original_title:title,original_title=original_title,year=year,media_type=serial==1?"tv":"movie"}};
            picks[ticket]=p;seriesPicks.Add(p);group.Tickets.Add(ticket);
            group.Cache[ticket]=states.TryGetValue(hash,out var cached)?cached:null;
            if(previous!=null)picks[previous].Next=ticket;
            previous=ticket;
            tpl.Append(p.Name,year.ToString(),ReleaseSummary(t)+" · Озвучка: "+ReleaseVoices(t)[0]+(ReleaseVoices(t).Length>1 ? " + "+(ReleaseVoices(t).Length-1) : ""),OnlineUrl("choose?pick="+ticket));
            nativeMovies?.Append(p.Name,NativeMediaUrl(ticket),streamquality:NativeQualities(ticket,-1,-1,-2,p.Quality),voice_name:ReleaseSummary(t)+" · "+string.Join(", ",ReleaseVoices(t)),hls_manifest_timeout:60000);
        }
        if(serial==1){var peers=seriesPicks.ToArray();foreach(var p in peers)p.SeriesPicks=peers;}
        if(nativeMovies!=null){if(seriesPicks.Count>0)nativeMovies.Append(NativeReleases(seriesPicks[0]));return JsonMenu(nativeMovies);}
        if(OnlineJson)return JsonMenu(tpl);
        return Content(SimilarReleaseHtml(tpl,voices,selected,search,false,tb_season,tb_episode,seasons,options,filters),"text/html; charset=utf-8");
        }
    });

    static double PositiveNumber(JToken value) => value!=null &&
        double.TryParse(value.ToString(),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out var number) &&
        double.IsFinite(number) && number>0 ? number : 0;
    static string BytesLabel(double size) => size<=0 ? "Размер не указан" :
        size>=1073741824 ? (size/1073741824).ToString("0.##",System.Globalization.CultureInfo.InvariantCulture)+" ГиБ" :
        (size/1048576).ToString("0.##",System.Globalization.CultureInfo.InvariantCulture)+" МиБ";
    static double DurationSeconds(JToken value) => PositiveNumber(value)>0 ? PositiveNumber(value) :
        TimeSpan.TryParse(value?.ToString(),System.Globalization.CultureInfo.InvariantCulture,out var duration) && duration.TotalSeconds>0 ? duration.TotalSeconds : 0;
    static string ReleaseTrait(JToken row,string field) {
        if(field=="res" || field=="hdr" || field=="codec") {
            var tags=StoredTags(row);
            return field=="res"?tags.Res:field=="hdr"?tags.Hdr:tags.Codec;
        }
        double size=PositiveNumber(row["Size"])/1073741824;
        return size<=0?"Не указан":size<4?"До 4 ГиБ":size<10?"4–10 ГиБ":size<25?"10–25 ГиБ":size<50?"25–50 ГиБ":"От 50 ГиБ";
    }
    static string ReleaseSummary(JToken row) => string.Join(" · ",new[]{ReleaseTrait(row,"res"),ReleaseTrait(row,"hdr"),ReleaseTrait(row,"codec")}
        .Where(v=>v!="Не указано").Append(BytesLabel(PositiveNumber(row["Size"]))));
    static JObject PlaybackTrack(JToken track) {
        if(track is not JObject)return null;
        var result=new JObject();
        foreach(string field in new[]{"language","title","codec","channels","forced","hearing_impaired"}) {
            var value=track[field];
            if(value is JValue && value.Type!=JTokenType.Null && value.ToString().Length<=160)result[field]=value.DeepClone();
        }
        return result;
    }
    static bool KnownPlaybackTrack(JToken track) => track is JObject &&
        (TrackLanguage(track).Length>0 || TrackPart(track,"title").Length>0);
    static JObject PlaybackPreference(JToken data,int resolution,int audio,int sub) {
        var selectedAudio=(data["metadata"]?["audios"] as JArray)?.ElementAtOrDefault(audio);
        var selectedSub=(data["metadata"]?["subtitles"] as JArray)?.ElementAtOrDefault(sub);
        if((selectedAudio!=null && !KnownPlaybackTrack(selectedAudio)) || (sub>=0 && !KnownPlaybackTrack(selectedSub)))return null;
        return new JObject{["resolution"]=resolution,["audio"]=PlaybackTrack(selectedAudio),["subtitle"]=sub<0?null:PlaybackTrack(selectedSub)};
    }
    JObject ReadPlaybackPreference() {
        var headers=Request.Headers["X-TorBox-Playback"];
        if(headers.Count!=1 || headers[0].Length>8192)return null;
        try {
            var saved=JObject.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(headers[0])));
            if(saved.Properties().Any(p=>p.Name!="resolution" && p.Name!="audio" && p.Name!="subtitle") ||
                saved["resolution"]?.Type!=JTokenType.Integer || !new[]{0,360,480,720,1080,1440}.Contains((int)saved["resolution"]))return null;
            foreach(string field in new[]{"audio","subtitle"}) {
                if(saved[field]==null || saved[field].Type==JTokenType.Null)continue;
                if(saved[field] is not JObject track || track.Properties().Any(p=>
                    !new[]{"language","title","codec","channels","forced","hearing_impaired"}.Contains(p.Name) ||
                    p.Value is not JValue || p.Value.ToString().Length>160))return null;
            }
            return saved;
        } catch(FormatException) { return null; } catch(Newtonsoft.Json.JsonException) { return null; }
          catch(OverflowException) { return null; }
    }
    static bool MatchPlaybackPreference(JObject saved,JToken data,out int resolution,out int audio,out int sub) {
        resolution=(int?)saved?["resolution"]??-1;audio=-1;sub=-2;
        if(saved==null)return false;
        if((saved["audio"] is JObject && !KnownPlaybackTrack(saved["audio"])) ||
            (saved["subtitle"] is JObject && !KnownPlaybackTrack(saved["subtitle"])))return false;
        var audios=data["metadata"]?["audios"] as JArray??new JArray();
        var subtitles=data["metadata"]?["subtitles"] as JArray??new JArray();
        audio=MatchSeriesTrack(saved["audio"] as JObject,audios,true);
        sub=saved["subtitle"] is JObject track?MatchSeriesTrack(track,subtitles,false):-1;
        int height=(int?)data["metadata"]?["video"]?["height"]??0;
        if(resolution>0 && height<=resolution)resolution=0;
        return audio>=0 && (saved["subtitle"] is not JObject || sub>=0);
    }
    static string FileDescription(Pick pick,JToken file) {
        pick.Metadata.TryGetValue((long?)file["id"]??-1,out var known);
        double duration=DurationSeconds(file["duration"]);
        if(duration==0)duration=DurationSeconds(known?["metadata"]?["video"]?["duration"]);
        string text=BytesLabel(PositiveNumber(file["size"]));
        if(duration>0 && duration<604800)text+=" · "+TimeSpan.FromSeconds(duration).ToString(duration>=3600?@"h\:mm\:ss":@"m\:ss");
        return text;
    }
    static string ChosenHtml(string html,Pick pick,Pick requested,string file=null,string notice=null,JObject preference=null) {
        var meta=new JObject{["torbox_chosen"]=new JObject{["release"]=pick.Name,["fallback"]=pick!=requested,["file"]=file},
            ["torbox_preference_notice"]=notice,["torbox_playback"]=preference};
        string json=meta.ToString(Newtonsoft.Json.Formatting.None);
        string prefix=json.Substring(0,json.Length-1).Replace("'","\\u0027").Replace("<","\\u003c").Replace(">","\\u003e").Replace("&","\\u0026")+",";
        bool first=true;
        string result=Regex.Replace(html,"data-json='\\{",m=>{if(!first)return m.Value;first=false;return "data-json='"+prefix;});
        if(first)throw new InvalidOperationException("TorBox selection template has no data-json attribute");
        return result;
    }

    static bool TitleMatches(string release,string title) {
        string name=Normalize(title),candidate=Normalize(release);
        return name.Length>0 && ((" "+candidate+" ").Contains(" "+name+" ",StringComparison.Ordinal) ||
            (Regex.IsMatch(name,@"[\p{IsCJKUnifiedIdeographs}\p{IsHiragana}\p{IsKatakana}\p{IsHangulSyllables}]") && candidate.Contains(name,StringComparison.Ordinal)));
    }
    static IEnumerable<JObject> DeduplicateReleases(IEnumerable<JObject> rows) {
        foreach(var group in rows.GroupBy(t=>ReleaseHash(t) is string hash ? "hash:"+hash :
            "link:"+((string)t["MagnetUri"]??(string)t["Link"]??Guid.NewGuid().ToString("N")))) {
            var ranked=group.Select(t=>(Row:t,Tags:ParseReleaseTags((string)t["Title"]),Seeds:(int?)t["Seeders"]??0)).ToArray();
            var row=(JObject)ranked.OrderByDescending(x=>x.Seeds).ThenByDescending(x=>PositiveNumber(x.Row["Size"])>0).First().Row.DeepClone();
            if(PositiveNumber(row["Size"])==0 && group.FirstOrDefault(t=>PositiveNumber(t["Size"])>0)?["Size"] is JToken size)row["Size"]=size.DeepClone();
            var voices=group.SelectMany(ReleaseVoices).Where(v=>v!="Не указана").Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            if(voices.Length>0) {
                if(row["info"] is not JObject)row["info"]=new JObject();
                row["info"]["voices"]=new JArray(voices);
            }
            // One indexer title may omit the resolution that another copy of the same hash states.
            StoreTags(row,MergeTags(ranked.Select(x=>(x.Tags,x.Seeds))));
            yield return row;
        }
    }
    async Task<Dictionary<string,bool?>> ReleaseCacheStates(string key,JObject[] rows) {
        var hashes=rows.Select(ReleaseHash).Where(h=>h!=null).Distinct(StringComparer.Ordinal).ToArray();
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
        deadline.CancelAfter(TimeSpan.FromSeconds(8));
        using var gate=new SemaphoreSlim(4,4);
        var batches=await Task.WhenAll(hashes.Chunk(100).Select(async batch=>{
            JObject cache=null;bool entered=false;
            try {
                await gate.WaitAsync(deadline.Token);entered=true;
                cache=(await CachedBadgeBatch(key,batch).WaitAsync(deadline.Token)).Cache;
            } catch(OperationCanceledException) { }
              catch(Exception ex) { MetadataWarning("search-cache-batch",ex); }
            finally { if(entered)gate.Release(); }
            return batch.ToDictionary(h=>h,h=>CacheBadge(cache,h));
        }));
        HttpContext.RequestAborted.ThrowIfCancellationRequested();
        return batches.SelectMany(b=>b).ToDictionary(p=>p.Key,p=>p.Value);
    }

    static string[] ReleaseVoices(JToken row) {
        var values=(row["info"] as JObject)?["voices"] as JArray;
        var names=(values??new JArray()).Where(v=>v.Type==JTokenType.String).Select(v=>((string)v).Trim()).Where(v=>v.Length>0 && v.Length<=160).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        return names.Length>0 ? names : new[]{"Не указана"};
    }
    static string SimilarReleaseHtml(SimilarTpl tpl,string[] voices,string selected,string search,bool empty=false,int season=0,int episode=0,int[] seasons=null,JObject options=null,JObject filters=null) {
        var meta=new JObject{["torbox_preview"]=!empty,["torbox_release"]=!empty,["torbox_voice_empty"]=empty,
            ["torbox_season"]=season,["torbox_episode"]=episode,["torbox_seasons"]=new JArray(seasons??Array.Empty<int>()),
            ["torbox_filter_options"]=options,["torbox_filter_selected"]=filters,
            ["torbox_voice_options"]=new JArray(voices),["torbox_voice_selected"]=selected,["torbox_voice_search"]=search};
        string json=meta.ToString(Newtonsoft.Json.Formatting.None).Replace("'", "\\u0027").Replace("<", "\\u003c").Replace(">", "\\u003e").Replace("&", "\\u0026");
        string prefix=json.Substring(0,json.Length-1)+",";
        string html=tpl.ToHtml();
        if(string.IsNullOrEmpty(html))return html??"";
        bool first=true;
        return Regex.Replace(html,"data-json='\\{",m=>{
            string flags="data-json='{\"torbox_preview\":"+(!empty?"true":"false")+",\"torbox_release\":"+(!empty?"true":"false")+",";
            if(!first)return flags;
            first=false;return "data-json='"+prefix;
        });
    }

    // Shared read-only cache batches used by search ranking and menu badges.
    public class CacheCheckInput { public string[] hashes {get;set;} }
    [HttpPost, Route("torbox/cache-check"),RequestSizeLimit(16384)]
    public Task<ActionResult> CacheCheck([FromBody] CacheCheckInput input) => Guard(async(c,key)=>{
        if(input?.hashes==null || input.hashes.Length==0 || input.hashes.Length>100 || input.hashes.Any(h=>!ValidCacheHash(h)))
            return Error(400,"Укажите от 1 до 100 корректных хешей раздач");
        var hashes=input.hashes.Select(NormalizeCacheHash).Distinct().OrderBy(h=>h,StringComparer.Ordinal).ToArray();
        var result=await CachedBadgeBatch(key,hashes);
        return Payload(new{success=true,retryable=result.Cache==null,retry_after=result.Cache==null?10:0,
            items=hashes.Select(hash=>new{hash,cached=CacheBadge(result.Cache,hash)}).ToArray()});
    });

    class BadgeBatch {
        public DateTime Until=DateTime.MaxValue;
        public TaskCompletionSource<JObject> Ready=new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
    static readonly object badgeBatchGate=new();
    static readonly Dictionary<string,BadgeBatch> badgeBatches=new();
    static readonly Dictionary<string,DateTime> badgeBackoff=new();
    static readonly Dictionary<string,(DateTime Until,bool Cached)> badgeHashes=new();
    async Task<(JObject Cache,bool Fresh)> CachedBadgeBatch(string key,string[] hashes) {
        string owner=SeriesKeyHash(key);
        var known=new JObject();var missing=new List<string>();
        lock(badgeBatchGate) {
            foreach(var expired in badgeHashes.Where(x=>x.Value.Until<DateTime.UtcNow).Select(x=>x.Key).ToArray())badgeHashes.Remove(expired);
            foreach(string hash in hashes.Distinct(StringComparer.Ordinal)) {
                if(badgeHashes.TryGetValue(owner+":"+hash,out var hit))known[hash]=new JObject{["cached"]=hit.Cached};
                else missing.Add(hash);
            }
        }
        if(missing.Count==0)return (known,false);
        var result=await FetchBadgeBatch(key,missing.ToArray());
        foreach(string hash in missing) {
            bool? cached=CacheBadge(result.Cache,hash);
            if(cached.HasValue) {
                known[hash]=new JObject{["cached"]=cached.Value};
                lock(badgeBatchGate) {
                    // A miss goes stale quickly so a new cache hit can show. A hit stays for a minute.
                    if(badgeHashes.Count<16384)badgeHashes[owner+":"+hash]=(DateTime.UtcNow.AddSeconds(cached.Value?60:10),cached.Value);
                }
            } else known[hash]=new JObject{["cached"]="unknown"};
        }
        return (result.Cache==null && !known.Properties().Any(p=>p.Value["cached"]?.Type==JTokenType.Boolean)?null:known,result.Fresh);
    }
    async Task<(JObject Cache,bool Fresh)> FetchBadgeBatch(string key,string[] hashes) {
        string owner=SeriesKeyHash(key),id=owner+":"+string.Join(",",hashes.OrderBy(h=>h,StringComparer.Ordinal));
        BadgeBatch batch;bool fresh=false;
        lock(badgeBatchGate) {
            foreach(var expired in badgeBatches.Where(x=>x.Value.Until<DateTime.UtcNow).Select(x=>x.Key).ToArray())badgeBatches.Remove(expired);
            foreach(var expired in badgeBackoff.Where(x=>x.Value<DateTime.UtcNow).Select(x=>x.Key).ToArray())badgeBackoff.Remove(expired);
            if(badgeBackoff.ContainsKey(owner))return (null,false);
            if(!badgeBatches.TryGetValue(id,out batch)) {
                if(badgeBatches.Count>=256)return (null,false);
                badgeBatches[id]=batch=new BadgeBatch();fresh=true;
            }
        }
        if(fresh) {
            JObject cache=null;
            try {
                using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(8));
                cache=await Call("checkcached?hash="+Uri.EscapeDataString(string.Join(",",hashes))+"&format=object",key,cancellationToken:deadline.Token,shared:true) as JObject;
            } catch(TorBoxException) { } catch(HttpRequestException) { } catch(OperationCanceledException) { }
              catch(Newtonsoft.Json.JsonException) { }
              catch(Exception ex) { MetadataWarning("cache-batch",ex); }
            finally {
                lock(badgeBatchGate) {
                    batch.Until=DateTime.UtcNow.AddSeconds(cache==null?10:30);
                    if(cache==null && badgeBackoff.Count<256)badgeBackoff[owner]=batch.Until;
                    batch.Ready.TrySetResult(cache);
                }
            }
        }
        return (await batch.Ready.Task,fresh);
    }

    [HttpGet, Route("torbox/menu-cache")]
    public Task<ActionResult> MenuCache(string pick) => Guard(async(c,key)=>{
        var first=GetPick(pick);var group=first.Group;
        if(group.KeyHash!=SeriesKeyHash(key))return Error(410,"Настройки TorBox изменились. Откройте список заново");
        if(!await group.CacheGate.WaitAsync(TimeSpan.FromSeconds(10),HttpContext.RequestAborted))
            return Payload(new{success=true,available=false,retry_after=1,items=new object[0]});
        try {
            (string Ticket,Pick Pick)[] menu;
            lock(pickCacheGate)menu=picks.Where(x=>x.Value.Group==group).Select(x=>(x.Key,x.Value)).ToArray();
            if(group.Cache==null || group.CacheUntil<DateTime.UtcNow) {
                var hashes=menu.Select(x=>x.Pick.Hash).Where(ValidCacheHash).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                JObject cache=hashes.Length>0 ? (await CachedBadgeBatch(key,hashes)).Cache : new JObject();
                group.Cache=menu.ToDictionary(x=>x.Ticket,x=>CacheBadge(cache,x.Pick.Hash));
                group.CacheUntil=DateTime.UtcNow.AddSeconds(cache==null?10:30);
            }
            return Payload(new{success=true,available=group.Cache.Values.Any(x=>x.HasValue),
                items=menu.Select(x=>new{pick=x.Ticket,cached=group.Cache.TryGetValue(x.Ticket,out var state)?state:null}).ToArray()});
        } finally { group.CacheGate.Release(); }
    });
    static string NormalizeCacheHash(string hash) {
        if(!ValidCacheHash(hash))return null;
        hash=hash.ToLowerInvariant();
        if(hash.Length==40)return hash;
        int bits=0,buffer=0;var hex=new StringBuilder(40);
        foreach(char c in hash) {
            buffer=(buffer<<5)|"abcdefghijklmnopqrstuvwxyz234567".IndexOf(c);bits+=5;
            if(bits>=8){bits-=8;hex.Append(((buffer>>bits)&255).ToString("x2"));}
        }
        return hex.ToString();
    }
    static bool ValidCacheHash(string hash) => Regex.IsMatch(hash??"","^([a-f0-9]{40}|[a-z2-7]{32})$",RegexOptions.IgnoreCase);
    static bool? CacheBadge(JObject cache,string hash) {
        if(cache==null || !ValidCacheHash(hash))return null;
        var entry=cache.Properties().FirstOrDefault(x=>string.Equals(x.Name,hash,StringComparison.OrdinalIgnoreCase))?.Value;
        if(entry==null)return false;
        if(entry is not JObject obj)return null;
        if(!obj.HasValues)return false;
        if(obj["cached"]?.Type==JTokenType.Boolean)return (bool)obj["cached"];
        // The object response omits misses; populated file/name records denote hits.
        return obj.HasValues && (obj["files"] is JArray || obj["name"]?.Type==JTokenType.String) ? true : null;
    }

    [HttpGet, Route("lite/torbox/choose")]
    public Task<ActionResult> Choose(string pick, long file=-1, int audio=-1, int sub=-2, int resolution=-1, bool native=false, bool nativeOptions=false, bool original=false) => Guard(async(c,key)=>{
        var requested=GetPick(pick);
        if(!await requested.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"TorBox уже обрабатывает эту раздачу");
        (string Ticket, Pick Selection, JToken Torrent)? ready;
        try {
            ready=await FindReadyFrom(pick,c,key);
            if(ready!=null && !ready.Value.Selection.Remembered)ready.Value.Selection.Remembered=RememberFilm(ready.Value.Torrent,ready.Value.Selection.Film);
        }
        finally { requested.Gate.Release(); }
        if(ready==null)return Error(404,requested.RequestedSeason>0 ? "В готовых раздачах не найдена выбранная серия с распознанным номером. Загрузки не запускались" : "В выбранной и следующих раздачах нет готового видео в кэше TorBox. Загрузки не запускались");
        var p=ready.Value.Selection;
        var t=ready.Value.Torrent;
        if(pick!=ready.Value.Ticket){file=-1;audio=-1;sub=-2;resolution=-1;}
        pick=ready.Value.Ticket;
        var files=(t["files"] as JArray??new JArray()).Where(f=>VideoName(FileName(f))).OrderByDescending(f=>(long?)f["size"]??0).ToArray();
        if(files.Length==0)return Error(404,"В раздаче нет видеофайлов");
        if((native || nativeOptions) && OnlineJson && !p.Serial && file<0)file=(long?)files[0]["id"]??-1;
        if(nativeOptions && (!OnlineJson || p.Serial))return Error(400,"Откройте параметры фильма через нативный список TorBox");
        string preferenceNotice=null;
        ActionResult Menu(SimilarTpl menu,params (int Season,int Episode)[] frames) {
            if(OnlineJson)return JsonMenu(menu);
            var result=(ContentResult)Similar(menu,frames);
            result.Content=ChosenHtml(result.Content,p,requested,files.FirstOrDefault(f=>(long?)f["id"]==file) is JToken selectedFile?FileName(selectedFile):null,preferenceNotice);
            return result;
        }
        if(p.Serial){
            p.Torrent=t.DeepClone();
            var episodes=EpisodeFiles(p,t);
            if(file<0 && p.RequestedSeason>0) {
                var target=episodes.FirstOrDefault(e=>e.Numbered && e.Season==p.RequestedSeason && e.Episode==p.RequestedEpisode);
                if(target==null)return Error(404,"Выбранная серия отсутствует или её номер не распознан");
                file=target.File;
            }
            if(file<0){
                var episodeTpl=new SimilarTpl();
                foreach(var episode in episodes)episodeTpl.Append(EpisodeTitle(episode),null,null,OnlineUrl("choose?pick="+pick+"&file="+episode.File));
                return Menu(episodeTpl,episodes.Select(e=>(e.Season,e.Episode)).ToArray());
            }
            if(!episodes.Any(e=>e.File==file))return Error(400,"Выбранный файл не распознан как серия");
        }
        if(!p.Serial && file<0 && files.Length>1) {
            var fileMenu=new SimilarTpl();
            foreach(var videoFile in files)fileMenu.Append(FileName(videoFile),null,FileDescription(p,videoFile),OnlineUrl("choose?pick="+pick+"&file="+videoFile["id"]));
            return Menu(fileMenu,(0,0));
        }
        if(file<0)file=(long?)files[0]["id"]??-1;
        var frame=p.Serial?EpisodeFiles(p,t).FirstOrDefault(e=>e.File==file):null;
        if(!files.Any(f=>(long?)f["id"]==file))return Error(404,"Видеофайл отсутствует в раздаче");
        if(nativeOptions && original)return OriginalFileMenu(pick,p,files,file);
        if(!p.Metadata.TryGetValue(file,out var data)){
            data=await StreamData(key,p.Id.Value,file,0,-1);p.Metadata[file]=data;
        }
        var audios=data["metadata"]?["audios"] as JArray??new JArray();
        var subtitles=data["metadata"]?["subtitles"] as JArray??new JArray();
        string route="choose?pick="+pick+"&file="+file;
        var tpl=new SimilarTpl();
        var video=data["metadata"]?["video"];
        int width=(int?)video?["width"]??0,height=(int?)video?["height"]??0;
        var resolutions=new[]{1440,1080,720,480,360}.Where(x=>x<height).ToArray();
        var saved=ReadPlaybackPreference();
        bool matched=MatchPlaybackPreference(saved,data,out int savedResolution,out int savedAudio,out int savedSub);
        if((native || nativeOptions) && OnlineJson && !p.Serial) {
            if(native && resolution<0)resolution=matched?savedResolution:0;
            if(audio<0)audio=matched?savedAudio:audios.Select((a,i)=>new{Track=a,Index=i}).Where(x=>((string)x.Track["language"]??"").StartsWith("ru",StringComparison.OrdinalIgnoreCase)).Select(x=>x.Index).FirstOrDefault();
            if(sub==-2)sub=matched?savedSub:-1;
        }
        if(nativeOptions) {
            if(audio<0 || audio>=Math.Max(1,audios.Count) || sub < -1 || sub>=subtitles.Count)return Error(400,"Дорожка отсутствует в выбранном файле");
            return NativeOptionsMenu(pick,p,files,file,audios,subtitles,height,audio,sub);
        }
        if(resolution==-1){
            if(matched && (int?)saved["resolution"]>0 && height<=0)preferenceNotice=UnknownHeightNotice;
            if(matched)tpl.Append("Сохранённые параметры",null,
                (savedResolution==0?"Исходное разрешение":savedResolution+"p")+" · "+(savedAudio<audios.Count?TrackLabel(audios[savedAudio]):"Звук по умолчанию")+" · "+(savedSub<0?"Без субтитров":TrackLabel(subtitles[savedSub])),
                OnlineUrl(route+"&resolution="+savedResolution+"&audio="+savedAudio+"&sub="+savedSub));
            else if(saved!=null)preferenceNotice="Сохранённые параметры недоступны или неоднозначны. Выберите качество и дорожки вручную.";
            tpl.Append("Разрешение: исходное",null,width+" × "+height,OnlineUrl(route+"&resolution=0"));
            foreach(int res in resolutions)tpl.Append("Разрешение: "+res+"p",null,"TorBox",OnlineUrl(route+"&resolution="+res));
            if(!p.Serial && files.Length>1)foreach(var f in files.Where(f=>(long?)f["id"]!=file))tpl.Append("Другой файл: "+FileName(f),null,FileDescription(p,f),OnlineUrl("choose?pick="+pick+"&file="+f["id"]));
            return Menu(tpl,(frame?.Season??0,frame?.Episode??0));
        }
        if(resolution!=0 && !resolutions.Contains(resolution))return Error(400,"Разрешение недоступно для этого файла");
        route+="&resolution="+resolution;
        if(audio==-1){
            var choices=audios.Select((a,i)=>new{Track=a,Index=i}).OrderByDescending(x=>((string)x.Track["language"]??"").StartsWith("ru")).ThenByDescending(x=>(bool?)x.Track["default"]==true);
            foreach(var x in choices)tpl.Append("Аудио: "+TrackLabel(x.Track),null,(string)x.Track["codec"]+" · каналов: "+(string)x.Track["channels"],OnlineUrl(route+"&audio="+x.Index));
            if(audios.Count==0)tpl.Append("Аудио: по умолчанию",null,null,OnlineUrl(route+"&audio=0"));
            return Menu(tpl,(frame?.Season??0,frame?.Episode??0));
        }
        if(audio<0 || audio>=Math.Max(1,audios.Count))return Error(400,"Звуковая дорожка отсутствует");
        route+="&audio="+audio;
        if(sub==-2){
            tpl.Append("Без субтитров",null,null,OnlineUrl(route+"&sub=-1"));
            for(int i=0;i<subtitles.Count;i++)tpl.Append("Субтитры: "+TrackLabel(subtitles[i]),null,(string)subtitles[i]["codec"],OnlineUrl(route+"&sub="+i));
            return Menu(tpl,(frame?.Season??0,frame?.Episode??0));
        }
        if(sub < -1 || sub>=subtitles.Count)return Error(400,"Дорожка субтитров отсутствует");
        if(native && OnlineJson && !p.Serial)return await OnlinePlay(pick,file,audio,sub,resolution);
        var movie=new MovieTpl(p.Movie,p.Movie);
        string selection=(resolution==0?"Исходное разрешение":resolution+"p")+" · "+(audios.Count>audio?TrackLabel(audios[audio]):"Звук по умолчанию")+" · "+(sub<0?"Без субтитров":TrackLabel(subtitles[sub]));
        movie.Append(string.IsNullOrWhiteSpace(p.Movie)?p.Name:p.Movie,OnlineUrl("play?pick="+pick+"&file="+file+"&resolution="+resolution+"&audio="+audio+"&sub="+sub),method:"call",voice_name:selection+" · Раздача: "+p.Name,hls_manifest_timeout:60000);
        movie.Append("Файл без изменений — прямая ссылка",OriginalFileUrl(pick,file),voice_name:"Все исходные дорожки · "+p.Name,quality:"original");
        if(OnlineJson)return JsonMenu(movie);
        return Content(ChosenHtml(movie.ToHtml(),p,requested,FileName(files.First(f=>(long?)f["id"]==file)),preference:PlaybackPreference(data,resolution,audio,sub)),"text/html; charset=utf-8");
    });
    static bool CachedReady(JToken t) =>
        (t?["download_present"]?.Type==JTokenType.Boolean ? (bool)t["download_present"] : (bool?)t?["download_finished"]==true) &&
        (t?["files"] as JArray)?.Any(f=>VideoName(FileName(f)))==true;

    async Task<(string Ticket, Pick Selection, JToken Torrent)?> FindReadyFrom(string ticket,JObject config,string key)
    {
        var requested=GetPick(ticket);string digest=SeriesKeyHash(key);
        if(requested.ReadyKeyHash==digest && requested.ReadyUntil>DateTime.UtcNow && requested.ReadyTorrent!=null) {
            var selected=GetPick(requested.ReadyTicket);
            if(selected.BoundKeyHash==digest && selected.Id==(long?)requested.ReadyTorrent["id"])return(requested.ReadyTicket,selected,requested.ReadyTorrent);
        }
        var chain=new List<(string Ticket,Pick Selection)>();
        for(int i=0;ticket!=null && i<96;i++) {
            var candidate=GetPick(ticket);var first=chain.FirstOrDefault().Selection;
            if(first==null || first.RequestedSeason>0 || !first.Serial || SeasonHint(first.Name)==0 || SeasonHint(candidate.Name)==SeasonHint(first.Name))chain.Add((ticket,candidate));
            ticket=candidate.Next;
        }
        var owned=await OwnedTorrents(key);
        var hashes=chain.Select(x=>x.Selection.Hash).Where(h=>Regex.IsMatch(h??"","^([a-f0-9]{40}|[a-z2-7]{32})$",RegexOptions.IgnoreCase)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        JObject cache=null;bool checkedCache=false;
        foreach(var entry in chain) {
            var p=entry.Selection;
            var existing=owned.FirstOrDefault(t=>((p.Id.HasValue && (long?)t["id"]==p.Id) ||
                (!string.IsNullOrEmpty(p.Hash) && string.Equals((string)t["hash"],p.Hash,StringComparison.OrdinalIgnoreCase))) && CachedReady(t));
            JToken torrent=existing;
            if(torrent==null) {
                if(!string.IsNullOrEmpty(p.Hash)) {
                    if(!checkedCache) {cache=hashes.Length>0?await Call("checkcached?hash="+Uri.EscapeDataString(string.Join(",",hashes))+"&format=object",key) as JObject:null;checkedCache=true;}
                    if(CacheBadge(cache,p.Hash)!=true)continue;
                }
                torrent=await AttachCachedOnly(p,config,key);
            }
            if(torrent==null)continue;
            if(requested.RequestedSeason>0 && !EpisodeFiles(p,torrent).Any(e=>e.Numbered &&
                e.Season==requested.RequestedSeason && e.Episode==requested.RequestedEpisode))continue;
            BindReady(p,torrent,key);
            requested.ReadyTicket=entry.Ticket;requested.ReadyKeyHash=digest;requested.ReadyTorrent=torrent.DeepClone();requested.ReadyUntil=DateTime.UtcNow.AddSeconds(30);
            return(entry.Ticket,p,requested.ReadyTorrent);
        }
        return null;
    }

    async Task<JToken> AttachCachedOnly(Pick p,JObject config,string key)
    {
        using var form=new MultipartFormDataContent();
        form.Add(new StringContent("true"),"add_only_if_cached");
        form.Add(new StringContent("false"),"as_queued");
        form.Add(new StringContent("false"),"allow_zip");
        if(!string.IsNullOrEmpty(p.Hash))form.Add(new StringContent("magnet:?xt=urn:btih:"+p.Hash),"magnet");
        else if(p.Link.StartsWith("magnet:?",StringComparison.OrdinalIgnoreCase))form.Add(new StringContent(p.Link),"magnet");
        else{
            if(!Uri.TryCreate(p.Link,UriKind.Absolute,out var source) || !HostAllowed(config,source))return null;
            var file=new ByteArrayContent(await FetchTorrent(config,source));
            file.Headers.ContentType=new System.Net.Http.Headers.MediaTypeHeaderValue("application/x-bittorrent");
            form.Add(file,"file","cached.torrent");
        }
        using var request=new HttpRequestMessage(HttpMethod.Post,api+"createtorrent");
        request.Headers.Authorization=new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer",key);
        request.Headers.UserAgent.ParseAdd("Lampac-TorBox/1.0");request.Content=form;
        using var response=await client.SendAsync(request,HttpContext.RequestAborted);
        if((int)response.StatusCode==429)throw new TorBoxException(429,"TorBox ограничил частоту запросов. Попробуйте позже");
        JObject result;
        try { result=JObject.Parse(await response.Content.ReadAsStringAsync()); }
        catch { throw new TorBoxException(502,"TorBox не подтвердил наличие готового кэша. Загрузка не запускалась"); }
        if((bool?)result["success"]!=true){
            string code=(string)result["error"]??"",detail=(string)result["detail"]??"";
            if(code.IndexOf("CACH",StringComparison.OrdinalIgnoreCase)>=0 || code=="ITEM_NOT_FOUND" || detail.IndexOf("not cached",StringComparison.OrdinalIgnoreCase)>=0)return null;
            throw new TorBoxException(502,"TorBox не подтвердил готовую раздачу. Загрузка не запускалась");
        }
        if(!response.IsSuccessStatusCode)throw new TorBoxException(502,"TorBox не подтвердил готовую раздачу. Загрузка не запускалась");
        long? id=(long?)result["data"]?["torrent_id"]??(long?)result["data"]?["id"];
        if(!id.HasValue)return null;
        InvalidateOwned(key,id);
        var row=Rows(await Call("mylist?id="+id+"&bypass_cache=true",key)).FirstOrDefault(t=>(long?)t["id"]==id);
        return CachedReady(row)?row:null;
    }

    static string TrackLabel(JToken t) => ((string)t["language_full"]??(string)t["language"]??"Не указан язык")+(!string.IsNullOrWhiteSpace((string)t["title"])?" · "+(string)t["title"]:"");
    // TorBox expects a preset index, while the UI uses pixel heights.
    static int? ResolutionIndex(int resolution) => resolution switch {
        360 => 2, 480 => 3, 720 => 4, 1080 => 5, 1440 => 6, _ => null
    };
    async Task<JToken> StreamData(string key,long id,long file,int audio,int sub,int? resolutionIndex=null){
        return await Call("../stream/createstream?id="+id+"&file_id="+file+"&type=torrent&scrobbling_enabled=false&chosen_audio_index="+audio+"&chosen_subtitle_index="+(sub<0?"null":sub.ToString())+(resolutionIndex.HasValue?"&chosen_resolution_index="+resolutionIndex.Value:""),key);
    }

    [HttpGet, Route("lite/torbox/play")]
    public Task<ActionResult> OnlinePlay(string pick,long file,int audio=-1,int sub=-2,int resolution=-1) => Guard(async(c,key)=>{
        var p=GetPick(pick);
        if(p.Id==null || !p.Metadata.TryGetValue(file,out var known))return Error(400,"Сначала выберите параметры видео");
        int ac=(known["metadata"]?["audios"] as JArray)?.Count??0,sc=(known["metadata"]?["subtitles"] as JArray)?.Count??0;
        int height=(int?)known["metadata"]?["video"]?["height"]??0;
        if(audio<0 || audio>=Math.Max(1,ac) || sub < -1 || sub>=sc)return Error(400,"Выберите аудиодорожку и субтитры");
        if(resolution!=0 && !(new[]{1440,1080,720,480,360}.Contains(resolution) && resolution<height))return Error(400,"Выберите доступное разрешение");
        if(p.Serial){
            string selection=(resolution==0?"Исходное разрешение":resolution+"p")+" · "+(ac>audio?TrackLabel(known["metadata"]["audios"][audio]):"Звук по умолчанию")+" · "+(sub<0?"Без субтитров":TrackLabel(known["metadata"]["subtitles"][sub]));
            return await StartSeries(p,file,resolution,audio,sub,selection,key);
        }
        var data=await StreamData(key,p.Id.Value,file,audio,sub,resolution==0?null:ResolutionIndex(resolution));
        string url=(string)data["hls_url"];
        if(!Uri.TryCreate(url,UriKind.Absolute,out var uri)||uri.Scheme!="https")return Error(502,"TorBox не вернул HLS-поток");
        if(OnlineJson)return JsonStream(url,p.Movie??p.Name);
        // One TorBox-issued URL for the exact preselected combination, for either player.
        // Empty quality map prevents client defaults from overriding the selected resolution.
        // No runtime TorBox descriptor: selections are made before playback.
        return Payload(new{success=true,torbox_stream=true,url,quality=new JObject(),hls_manifest_timeout=60000});
    });
}
