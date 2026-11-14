using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace TorBox;
public partial class TorBoxController
{
    const string followedPath="database/torbox-series.json";
    static readonly object followedGate=new();
    class FollowedSeason {
        public string Id,Owner,KeyHash,Source,Hash,Name,Selection,Variant;
        public FilmInput Film;
        public int Season,Episode,Resolution,Height;
        public int[] Episodes;
        public JToken Audio,Subtitle;
        public DateTime Updated;
    }
    class SeasonOffer {
        public FollowedSeason Watch;
        public Pick Pick;
        public DateTime Expires=DateTime.UtcNow.AddMinutes(10);
    }
    static readonly ConcurrentDictionary<string,SeasonOffer> seasonOffers=new();
    static readonly ConcurrentDictionary<string,SemaphoreSlim> seasonUpdateGates=new();
    static SemaphoreSlim SeasonUpdateGate(string id) {
        lock(followedGate) {
            if(!seasonUpdateGates.TryGetValue(id,out var gate))seasonUpdateGates[id]=gate=new SemaphoreSlim(1,1);
            return gate;
        }
    }
    static string ReleaseSource(JToken row) {
        // Store a digest, never the tracker URL, passkeys or parser credentials.
        if(!Uri.TryCreate((string)row?["Details"],UriKind.Absolute,out var uri) ||
            (uri.Scheme!="http" && uri.Scheme!="https") || !string.IsNullOrEmpty(uri.UserInfo))return null;
        return SeriesKeyHash(uri.Host.ToLowerInvariant()+uri.PathAndQuery);
    }
    static string ReleaseHash(JToken row) {
        string link=Uri.UnescapeDataString((string)row["MagnetUri"]??(string)row["Link"]??"");
        string hash=Regex.Match(link,@"urn:btih:([a-z0-9]+)",RegexOptions.IgnoreCase).Groups[1].Value;
        return NormalizeCacheHash(string.IsNullOrEmpty(hash)?(string)row["InfoHash"]??(string)row["Hash"]:hash);
    }
    static JObject ReadFollowed() {
        try { return JObject.Parse(System.IO.File.ReadAllText(followedPath)); }
        catch(FileNotFoundException) { return new JObject(); }
        catch(DirectoryNotFoundException) { return new JObject(); }
        // Corrupt/unreadable history must never be silently replaced with an empty file.
    }
    static void WriteFollowed(JObject data) {
        Directory.CreateDirectory("database");
        string temp=followedPath+".tmp-"+Guid.NewGuid().ToString("N");
        try {
            using(var stream=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,
                FileOptions.None)) {
                System.IO.File.SetUnixFileMode(temp,UnixFileMode.UserRead|UnixFileMode.UserWrite);
                using var writer=new StreamWriter(stream);
                writer.Write(data.ToString(Newtonsoft.Json.Formatting.None));
            }
            System.IO.File.Move(temp,followedPath,true);
        } finally { if(System.IO.File.Exists(temp))System.IO.File.Delete(temp); }
    }
    FollowedSeason GetFollowed(string id,string key) {
        if(!Regex.IsMatch(id??"",@"^[A-F0-9]{64}$"))throw new TorBoxException(400,"Некорректная подписка на сезон");
        lock(followedGate) {
            var watch=ReadFollowed()[id]?.ToObject<FollowedSeason>();
            if(watch==null || watch.Owner!=metadataOwner || watch.KeyHash!=SeriesKeyHash(key))
                throw new TorBoxException(404,"Сезон отсутствует в вашем списке");
            return watch;
        }
    }
    void FollowSeries(Pick pick,SeriesSession state,SeriesEpisode current,string key) {
        if(string.IsNullOrEmpty(pick.SourceKey) || CleanFilm(pick.Film)==null || string.IsNullOrEmpty(pick.Hash) || !current.Numbered)return;
        try {
            string id=SeriesKeyHash(metadataOwner+":"+SeriesKeyHash(key)+":"+pick.SourceKey+":"+current.Season);
            // Remember every episode number in the release. A stopped auto playlist must not make the rest look new.
            var known=EpisodeFiles(pick,pick.Torrent).Where(e=>e.Season==current.Season).Select(e=>e.Episode).Distinct().OrderBy(e=>e).ToArray();
            if(known.Length==0)known=state.Episodes.Select(e=>e.Episode).Distinct().OrderBy(e=>e).ToArray();
            var watch=new FollowedSeason{Id=id,Owner=metadataOwner,KeyHash=SeriesKeyHash(key),Source=pick.SourceKey,
                Hash=pick.Hash,Name=pick.Name,Film=pick.Film,Season=current.Season,Episode=current.Episode,
                Episodes=known,
                Resolution=state.Resolution,Height=(int?)current.Metadata?["metadata"]?["video"]?["height"]??0,
                Audio=state.Audio?.DeepClone(),Subtitle=state.Subtitle?.DeepClone(),Selection=state.Selection,
                Variant=EpisodeVariant(current.Name),Updated=DateTime.UtcNow};
            lock(followedGate) {
                var data=ReadFollowed();
                if(data[id]==null && data.Properties().Count(p=>(string)p.Value["Owner"]==metadataOwner)>=500)
                    throw new TorBoxException(409,"Слишком много сохранённых сезонов");
                data[id]=JObject.FromObject(watch);WriteFollowed(data);
            }
            state.FollowId=id;
        } catch(TorBoxException ex) { state.FollowNotice=ex.Message; MetadataWarning("series-follow",ex); }
          catch(Exception ex) { MetadataWarning("series-follow",ex); }
    }
    void SaveSeriesPosition(SeriesSession state,int index) {
        if(state.FollowId==null)return;
        try {
            lock(followedGate) {
                var data=ReadFollowed();var row=data[state.FollowId] as JObject;
                var episode=state.Episodes[index];
                // An older open player cannot roll back a newly selected pack.
                if((string)row?["Owner"]!=state.Owner || (string)row?["KeyHash"]!=state.KeyHash ||
                    (string)row?["Hash"]!=episode.Pick.Hash)return;
                row["Episode"]=episode.Episode;row["Updated"]=DateTime.UtcNow;WriteFollowed(data);
            }
        } catch(Exception ex) { MetadataWarning("series-position",ex); }
    }
    [HttpGet,Route("torbox/series/followed")]
    public Task<ActionResult> FollowedSeasons()=>Guard(async(c,key)=>{
        FollowedSeason[] watches;
        lock(followedGate)watches=ReadFollowed().Properties().Select(p=>p.Value.ToObject<FollowedSeason>())
            .Where(w=>w.Owner==metadataOwner && w.KeyHash==SeriesKeyHash(key)).OrderByDescending(w=>w.Updated).Take(500).ToArray();
        return Payload(new{success=true,items=watches.Select(w=>new{id=w.Id,title=w.Film.title,season=w.Season,episode=w.Episode,release=w.Name,selection=w.Selection}).ToArray()});
    });
    async Task<JArray> SearchUpdatedSeason(FollowedSeason watch,JObject config) {
        var parser=config["jacred"] as JObject;
        if(parser==null || !Uri.TryCreate((string)parser["base_url"],UriKind.Absolute,out var uri))
            throw new TorBoxException(503,"JacRed для TorBox не настроен");
        // year stays 0: this lookup is the saved tracker page, and a later season pack can have a newer catalog year.
        string url=uri.ToString().TrimEnd('/')+"/api/v2.0/indexers/all/results?title="+Uri.EscapeDataString(watch.Film.title)+
            "&title_original="+Uri.EscapeDataString(watch.Film.original_title??watch.Film.title)+"&year=0&is_serial=2&apikey="+Uri.EscapeDataString((string)parser["api_key"]??"");
        using var response=await ParserClient.GetAsync(url,HttpContext.RequestAborted);
        if(!response.IsSuccessStatusCode)throw new TorBoxException(502,"JacRed не ответил на проверку сезона");
        var data=JObject.Parse(await response.Content.ReadAsStringAsync());
        if(data["Results"] is not JArray rows)throw new TorBoxException(502,"JacRed вернул некорректный список раздач");
        return rows;
    }
    [HttpGet,Route("torbox/series/updates")]
    public Task<ActionResult> CheckSeasonUpdates(string id)=>Guard(async(c,key)=>{
        var watch=GetFollowed(id,key);
        var rows=await SearchUpdatedSeason(watch,c);
        var matches=rows.OfType<JObject>().Where(r=>ReleaseSource(r)==watch.Source && ReleaseHash(r)!=null).ToArray();
        // A tracker page is the identity; similarly named releases are never substituted.
        var hashes=matches.Select(ReleaseHash).Distinct().ToArray();
        if(hashes.Length!=1)return Payload(new{success=true,status=hashes.Length==0?"missing":"ambiguous",
            detail=hashes.Length==0?"Исходная страница раздачи не найдена в JacRed. Автоматическая замена невозможна":"Для страницы найдены разные версии. Выберите раздачу вручную"});
        var row=matches[0];string hash=hashes[0];
        var owned=await OwnedTorrents(key);
        var torrent=owned.FirstOrDefault(t=>NormalizeCacheHash((string)t["hash"])==hash && CachedReady(t));
        var pick=new Pick{Owner=metadataOwner,Hash=hash,SourceKey=watch.Source,Name=(string)row["Title"]??watch.Name,
            Movie=watch.Film.title,Serial=true,Film=watch.Film,Link="magnet:?xt=urn:btih:"+hash};
        var seasonFiles=torrent==null?null:EpisodeFiles(pick,torrent).Where(e=>e.Season==watch.Season && e.Numbered).ToArray();
        int[] episodes=seasonFiles?.Select(e=>e.Episode).Distinct().OrderBy(e=>e).ToArray();
        bool? cached=torrent!=null?true:CacheBadge((await CachedBadgeBatch(key,new[]{hash})).Cache,hash);
        foreach(var expired in seasonOffers.Where(x=>x.Value.Expires<DateTime.UtcNow).ToArray())seasonOffers.TryRemove(expired.Key,out _);
        if(seasonOffers.Count>=256)return Error(429,"Слишком много открытых проверок сезонов");
        string offer=Guid.NewGuid().ToString("N");seasonOffers[offer]=new SeasonOffer{Watch=watch,Pick=pick};
        return Payload(new{success=true,status=hash==watch.Hash?"unchanged":"updated",offer,cached,
            release=pick.Name,season=watch.Season,last_episode=watch.Episode,
            new_episodes=episodes?.Except(watch.Episodes??Array.Empty<int>()).OrderBy(e=>e).ToArray(),
            next_available=seasonFiles!=null && SelectStoredEpisode(seasonFiles,watch.Episode+1,watch.Variant)!=null,
            episodes_verified=episodes!=null,film=watch.Film});
    });
    public class SeasonUpdateInput { public string offer {get;set;} public bool next {get;set;} }
    public class ForgetSeasonInput { public string id {get;set;} }
    [HttpPost,Route("torbox/series/forget"),RequestSizeLimit(256)]
    public Task<ActionResult> ForgetSeason([FromBody] ForgetSeasonInput input)=>Guard(async(c,key)=>{
        if(!Regex.IsMatch(input?.id??"",@"^[A-F0-9]{64}$"))return Error(400,"Некорректная подписка на сезон");
        lock(followedGate) {
            var data=ReadFollowed();
            var row=data[input.id] as JObject;
            if((string)row?["Owner"]!=metadataOwner || (string)row?["KeyHash"]!=SeriesKeyHash(key))
                return Error(404,"Сезон отсутствует в вашем списке");
            data.Remove(input.id);WriteFollowed(data);
        }
        return Payload(new{success=true});
    });
    [HttpPost,Route("torbox/series/update-play"),RequestSizeLimit(2048)]
    public Task<ActionResult> PlaySeasonUpdate([FromBody]SeasonUpdateInput input)=>Guard(async(c,key)=>{
        if(input?.offer==null || !seasonOffers.TryGetValue(input.offer,out var offer) || offer.Expires<DateTime.UtcNow)
            return Error(410,"Результат проверки устарел. Проверьте сезон заново");
        var seasonGate=SeasonUpdateGate(offer.Watch.Id);
        if(!await seasonGate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Обновление сезона уже подготавливается");
        try {
            if(!seasonOffers.TryGetValue(input.offer,out offer) || offer.Expires<DateTime.UtcNow)
                return Error(410,"Результат проверки устарел. Проверьте сезон заново");
            var watch=GetFollowed(offer.Watch.Id,key);
            if(watch.Updated!=offer.Watch.Updated || watch.Hash!=offer.Watch.Hash)
                return Error(409,"Выбор сериала изменился. Проверьте сезон заново");
            // Do not follow the normal forward-fallback chain: use exactly the confirmed page/hash.
            var pick=offer.Pick;
            var torrent=(await OwnedTorrents(key)).FirstOrDefault(t=>NormalizeCacheHash((string)t["hash"])==pick.Hash && CachedReady(t));
            if(torrent==null) {
                if(CacheBadge((await CachedBadgeBatch(key,new[]{pick.Hash})).Cache,pick.Hash)!=true)
                    return Error(409,"Новая версия не готова в кэше TorBox. Загрузка не запускалась");
                torrent=await AttachCachedOnly(pick,c,key);
            }
            if(torrent==null || NormalizeCacheHash((string)torrent["hash"])!=pick.Hash)return Error(409,"TorBox не подтвердил выбранную версию");
            BindReady(pick,torrent,key);
            var episodes=EpisodeFiles(pick,torrent).Where(e=>e.Season==watch.Season && e.Numbered).ToArray();
            int number=input.next?watch.Episode+1:watch.Episode;
            var current=SelectStoredEpisode(episodes,number,watch.Variant);
            if(current==null)return Error(409,episodes.Count(e=>e.Episode==number)>1
                ? "У этой серии несколько файлов. Откройте нужный через Онлайн → TorBox"
                : input.next?"Следующей серии пока нет в новой версии":"Сохранённая серия отсутствует в новой версии");
            var metadata=await StreamData(key,pick.Id.Value,current.File,0,-1);
            var audios=metadata["metadata"]?["audios"] as JArray??new JArray();
            var subs=metadata["metadata"]?["subtitles"] as JArray??new JArray();
            int audio=MatchSeriesTrack(watch.Audio,audios,true),sub=watch.Subtitle==null?-1:MatchSeriesTrack(watch.Subtitle,subs,false);
            int height=(int?)metadata["metadata"]?["video"]?["height"]??0;
            if(audio<0 || (watch.Subtitle!=null && sub<0) || (watch.Resolution==0 && height!=watch.Height))
                return Error(409,"В новой версии изменились выбранные дорожки или исходное разрешение. Выберите параметры заново через Онлайн → TorBox");
            pick.Metadata[current.File]=metadata;
            // StartSeries/ResolveSeriesStream keeps the selected resolution ceiling and semantic tracks.
            int resolution=watch.Resolution;
            string selection=watch.Selection;
            var result=await StartSeries(pick,current.File,resolution,audio,sub,selection,key);
            RememberFilm(torrent,watch.Film);
            seasonOffers.TryRemove(input.offer,out _);
            return result;
        } finally { seasonGate.Release(); }
    });
}
