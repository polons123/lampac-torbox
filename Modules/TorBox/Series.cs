using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace TorBox;
public partial class TorBoxController
{
    class SeriesEpisode {
        public Pick Pick;
        public long File;
        public int Season, Episode;
        public bool Numbered;
        public string Name;
        public JToken Metadata;
        public int ExplicitAudio=-1, ExplicitSub=-2;
    }
    const string UnknownHeightNotice="TorBox не сообщил разрешение. Открыто исходное качество.";
    class SeriesSession {
        public string Owner, Title, Selection, KeyHash, FollowId, FollowNotice;
        public int Resolution;
        public int Version=1;
        public JToken Audio, Subtitle;
        public List<SeriesEpisode> Episodes = new();
        public List<SeriesEpisode> Choice;
        public SemaphoreSlim Gate = new(1,1);
        public DateTime Expires = DateTime.UtcNow.AddHours(12);
    }
    static readonly ConcurrentDictionary<string,SeriesSession> seriesSessions = new();
    static int SeasonHint(string text) {
        var match=Regex.Match(text??"",@"(?:(?<![\p{L}\p{N}])S|\bSeason[ ._-]*|\bСезон[ ._:-]*)(\d{1,2})(?!\d)|(?<!\d)(\d{1,2})[ ._-]*(?:сезон|season)",RegexOptions.IgnoreCase);
        return match.Success ? int.Parse(match.Groups[1].Success?match.Groups[1].Value:match.Groups[2].Value) : 0;
    }
    static string NaturalOrder(string text) => Regex.Replace((text??"").ToLowerInvariant(),@"\d+",m=>m.Value.PadLeft(12,'0'));
    static List<SeriesEpisode> EpisodeFiles(Pick pick,JToken torrent) {
        var result=new List<SeriesEpisode>();
        int hint=SeasonHint(pick.Name);
        foreach(var file in (torrent?["files"] as JArray??new JArray()).Where(f=>VideoName(FileName(f))).OrderBy(f=>NaturalOrder((string)f["name"]??FileName(f)))) {
            string name=(string)file["name"]??FileName(file);
            var match=Regex.Match(name,@"(?<![\p{L}\p{N}])S(\d{1,2})[ ._-]*E(\d{1,3})(?!\d)|(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)",RegexOptions.IgnoreCase);
            int season=0,episode=0;
            if(match.Success) {
                season=int.Parse(match.Groups[1].Success?match.Groups[1].Value:match.Groups[3].Value);
                episode=int.Parse(match.Groups[2].Success?match.Groups[2].Value:match.Groups[4].Value);
                if(episode<1)continue;
            } else {
                season=SeasonHint(name);if(season==0)season=hint>0?hint:1;
                match=Regex.Match(name,@"(?:\bE(?:p(?:isode)?)?[ ._-]*|\bСерия[ ._:-]*)(\d{1,3})(?!\d)|(?<!\d)(\d{1,3})[ ._-]*(?:серия|серии|эпизод)",RegexOptions.IgnoreCase);
                if(match.Success)episode=int.Parse(match.Groups[1].Success?match.Groups[1].Value:match.Groups[2].Value);
                else {
                    string leaf=name.Replace('\\','/').Split('/').Last();
                    match=Regex.Match(leaf,@"^(\d{1,3})(?:[ ._-]|\.[a-z0-9]+$)",RegexOptions.IgnoreCase);
                    if(match.Success)episode=int.Parse(match.Groups[1].Value);
                    else if(Regex.IsMatch(name,@"\b(extras?|bonus|featurette|specials?|бонус|дополнения)\b",RegexOptions.IgnoreCase))continue;
                }
            }
            if(season<1)continue;
            result.Add(new SeriesEpisode{Pick=pick,File=(long?)file["id"]??-1,Season=season,Episode=episode,Numbered=episode>0,Name=FileName(file)});
        }
        foreach(var group in result.GroupBy(e=>e.Season)) {
            if(group.All(e=>e.Episode==0)){int ordinal=0;foreach(var e in group)e.Episode=++ordinal;}
        }
        // The manual list keeps every file. Automatic playback uses one determined file per episode number.
        return result.Where(e=>e.File>=0 && e.Episode>0).OrderBy(e=>e.Season).ThenBy(e=>e.Episode).ThenBy(e=>NaturalOrder(e.Name)).ToList();
    }
    static string EpisodeVariant(string name) {
        string text=Regex.Replace(name??"",@"\.[a-z0-9]{2,4}$","",RegexOptions.IgnoreCase);
        text=Regex.Replace(text,@"(?<![\p{L}\p{N}])S\d{1,2}[ ._-]*E\d{1,3}(?!\d)"," ",RegexOptions.IgnoreCase);
        text=Regex.Replace(text,@"(?<!\d)\d{1,2}x\d{1,3}(?!\d)"," ",RegexOptions.IgnoreCase);
        text=Regex.Replace(text,@"(?:\bE(?:p(?:isode)?)?[ ._-]*|\bСерия[ ._:-]*)\d{1,3}(?!\d)|(?<!\d)\d{1,3}[ ._-]*(?:серия|серии|эпизод)"," ",RegexOptions.IgnoreCase);
        return Normalize(text);
    }
    static SeriesEpisode MatchEpisodeVariant(IReadOnlyList<SeriesEpisode> files,string variant) {
        if(files.Count==1)return files[0];
        if(string.IsNullOrEmpty(variant))return null;
        var same=files.Where(e=>EpisodeVariant(e.Name)==variant).ToList();
        return same.Count==1?same[0]:null;
    }
    static List<SeriesEpisode> AutoPlaylist(IEnumerable<SeriesEpisode> episodes,SeriesEpisode chosen,out List<SeriesEpisode> choice) {
        // An unresolved episode ends the playlist. Later episodes stay unreachable until the user picks a file.
        choice=null;
        string variant=EpisodeVariant(chosen.Name);
        var groups=episodes.Where(e=>e.Season==chosen.Season && e.Episode>0).GroupBy(e=>e.Episode).OrderBy(g=>g.Key).ToList();
        int at=groups.FindIndex(g=>g.Key==chosen.Episode);
        var picked=new List<SeriesEpisode>();
        if(at<0){picked.Add(chosen);return picked;}
        for(int i=at-1;i>=0;i--) {
            var take=MatchEpisodeVariant(groups[i].ToList(),variant);
            if(take==null)break;
            picked.Insert(0,take);
        }
        var chosenFiles=groups[at].ToList();
        picked.Add(chosenFiles.FirstOrDefault(e=>e.File==chosen.File)??chosen);
        for(int i=at+1;i<groups.Count;i++) {
            var files=groups[i].ToList();
            var take=MatchEpisodeVariant(files,variant);
            if(take==null){if(files.Count>1)choice=files;break;}
            picked.Add(take);
        }
        return picked;
    }
    static SeriesEpisode SelectStoredEpisode(IEnumerable<SeriesEpisode> episodes,int number,string variant) {
        var files=episodes.Where(e=>e.Episode==number).ToList();
        return files.Count==0?null:MatchEpisodeVariant(files,variant);
    }
    static string EpisodeTitle(SeriesEpisode e) => e.Season+" сезон · "+e.Episode+" серия · "+e.Name;
    static string TrackPart(JToken track,string field) => Regex.Replace(((string)track?[field]??"").Trim().ToLowerInvariant(),@"\s+"," ");
    static string TrackLanguage(JToken track) => TrackPart(track,"language") switch {
        "rus"=>"ru", "eng"=>"en", "ukr"=>"uk", "jpn"=>"ja", "deu" or "ger"=>"de",
        "fra" or "fre"=>"fr", "spa"=>"es", "ita"=>"it", "und"=>"", var language=>language
    };
    static int MatchSeriesTrack(JToken selected,JArray tracks,bool audio) {
        if(selected==null)return audio && tracks.Count==0 ? 0 : -1;
        var matches=tracks.Select((track,index)=>new{track,index}).Where(x=>
            TrackLanguage(x.track)==TrackLanguage(selected) &&
            TrackPart(x.track,"title")==TrackPart(selected,"title") &&
            TrackPart(x.track,"forced")==TrackPart(selected,"forced") &&
            TrackPart(x.track,"hearing_impaired")==TrackPart(selected,"hearing_impaired")).ToArray();
        if(matches.Length==1)return matches[0].index;
        var exact=matches.Where(x=>TrackPart(x.track,"codec")==TrackPart(selected,"codec") && TrackPart(x.track,"channels")==TrackPart(selected,"channels")).ToArray();
        return exact.Length==1 ? exact[0].index : -1;
    }
    static string SeriesKeyHash(string key) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(key)));
    SeriesSession GetSeries(string session,string key) {
        if(session==null || !seriesSessions.TryGetValue(session,out var state) || state.Owner!=metadataOwner || state.KeyHash!=SeriesKeyHash(key) || state.Expires<DateTime.UtcNow)
            throw new TorBoxException(410,"Плейлист устарел. Откройте сериал в TorBox заново");
        state.Expires=DateTime.UtcNow.AddHours(12);return state;
    }
    static object SeriesChoice(SeriesSession state) {
        if(state.Choice==null || state.Choice.Count==0)return null;
        var first=state.Choice[0];
        return new { season=first.Season, episode=first.Episode, title=first.Season+" сезон · "+first.Episode+" серия",
            files=state.Choice.Select(e=>new{file=e.File,name=e.Name,title=EpisodeTitle(e)}).ToArray() };
    }
    object SeriesInfo(string session,SeriesSession state,int index,int version=-1) => new {
        session, selected_index=index, version=state.Version, title=state.Title, selection=state.Selection,
        release=state.Episodes[index].Pick.Name, file=state.Episodes[index].Name,
        next_season=0, next_index=state.Episodes.Count, season_only=true, choice=SeriesChoice(state),
        items=version==state.Version?null:state.Episodes.Select((e,i)=>new{index=i,title=EpisodeTitle(e),season=e.Season,episode=e.Episode}).ToArray()
    };
    async Task<(string Url,string Notice)> ResolveSeriesStream(SeriesSession state,int index,string key) {
        if(index<0 || index>=state.Episodes.Count)throw new TorBoxException(400,"Серия отсутствует в плейлисте");
        var episode=state.Episodes[index];
        if(episode.Metadata==null)episode.Metadata=await StreamData(key,episode.Pick.Id.Value,episode.File,0,-1);
        var audios=episode.Metadata["metadata"]?["audios"] as JArray??new JArray();
        var subtitles=episode.Metadata["metadata"]?["subtitles"] as JArray??new JArray();
        int audio=episode.ExplicitAudio>=0?episode.ExplicitAudio:MatchSeriesTrack(state.Audio,audios,true);
        int sub=episode.ExplicitSub>=-1?episode.ExplicitSub:state.Subtitle==null?-1:MatchSeriesTrack(state.Subtitle,subtitles,false);
        if(audio<0 || (state.Subtitle!=null && sub<0))throw new TorBoxException(409,"В следующей серии отсутствует выбранный перевод или дорожка субтитров. Выберите параметры заново");
        int height=(int?)episode.Metadata["metadata"]?["video"]?["height"]??0;
        int resolution=state.Resolution;
        // Unknown height cannot prove a downscale. Open the original and say so, same as a film.
        string notice=null;
        if(resolution>0 && height<=0){resolution=0;notice=UnknownHeightNotice;}
        else if(resolution>0 && height<=resolution)resolution=0;
        var stream=await StreamData(key,episode.Pick.Id.Value,episode.File,audio,sub,resolution==0?null:ResolutionIndex(resolution));
        string url=(string)stream["hls_url"];
        if(!Uri.TryCreate(url,UriKind.Absolute,out var uri)||uri.Scheme!="https")throw new TorBoxException(502,"TorBox не вернул HLS-поток");
        if(notice==null && state.Resolution>height && height>0)notice="Исходное видео этой серии ниже выбранного разрешения; сохранено исходное качество";
        return (url,notice);
    }
    async Task<ActionResult> SeriesStream(string session,SeriesSession state,int index,string key,int version=-1) {
        var stream=await ResolveSeriesStream(state,index,key);
        if(state.FollowId==null)FollowSeries(state.Episodes[index].Pick,state,state.Episodes[index],key);
        else SaveSeriesPosition(state,index);
        string followNotice=state.FollowId==null && state.Episodes[index].Pick.SourceKey!=null
            ? (state.FollowNotice??"Не удалось сохранить сезон для проверки обновлений") : null;
        return Payload(new{success=true,torbox_stream=true,url=stream.Url,quality=new JObject(),hls_manifest_timeout=60000,
            torbox_series=SeriesInfo(session,state,index,version),selection_notice=stream.Notice??followNotice});
    }
    async Task<ActionResult> StartSeries(Pick pick,long file,int resolution,int audio,int sub,string selection,string key) {
        foreach(var expired in seriesSessions.Where(x=>x.Value.Expires<DateTime.UtcNow).ToArray())seriesSessions.TryRemove(expired.Key,out _);
        if(seriesSessions.Count>=256)return Error(429,"Слишком много открытых сериалов. Попробуйте позднее");
        var episodes=EpisodeFiles(pick,pick.Torrent);
        var current=episodes.FirstOrDefault(e=>e.File==file);
        if(current==null)return Error(400,"Не удалось определить выбранную серию");
        current.ExplicitAudio=audio;current.ExplicitSub=sub;
        var known=pick.Metadata[file];
        var audios=known["metadata"]?["audios"] as JArray??new JArray();
        var subtitles=known["metadata"]?["subtitles"] as JArray??new JArray();
        var playlist=AutoPlaylist(episodes,current,out var choice);
        var state=new SeriesSession{Owner=metadataOwner,KeyHash=SeriesKeyHash(key),Title=pick.Movie,Selection=selection,Resolution=resolution,
            Audio=audios.Count>audio?audios[audio].DeepClone():null,Subtitle=sub>=0?subtitles[sub].DeepClone():null,
            Episodes=playlist.Take(5000).ToList(),Choice=choice};
        foreach(var e in state.Episodes)if(pick.Metadata.TryGetValue(e.File,out var data))e.Metadata=data;
        string session=Guid.NewGuid().ToString("N");seriesSessions[session]=state;
        int index=state.Episodes.FindIndex(e=>e.File==current.File && e.Season==current.Season && e.Episode==current.Episode);
        if(index<0)return Error(400,"Не удалось определить выбранную серию");
        return await SeriesStream(session,state,index,key);
    }
    [HttpGet,Route("torbox/series/play")]
    public Task<ActionResult> PlaySeries(string session,int index,int version=-1) => Guard(async(c,key)=>{
        var state=GetSeries(session,key);
        if(!await state.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Серия ещё подготавливается");
        try{return await SeriesStream(session,state,index,key,version);}finally{state.Gate.Release();}
    });
    [HttpGet,Route("torbox/series/resume")]
    public Task<ActionResult> ResumeSeries(string session,long file) => Guard(async(c,key)=>{
        var state=GetSeries(session,key);
        if(!await state.Gate.WaitAsync(TimeSpan.FromSeconds(2),HttpContext.RequestAborted))return Error(429,"Серия ещё подготавливается");
        try {
            var offered=state.Choice?.FirstOrDefault(e=>e.File==file);
            if(offered==null)return Error(409,"Выберите один из файлов этой серии");
            var pick=offered.Pick;
            if(pick.Id==null)return Error(400,"Раздача серии больше недоступна");
            if(!pick.Metadata.TryGetValue(file,out var known)) {
                known=await StreamData(key,pick.Id.Value,file,0,-1);pick.Metadata[file]=known;
            }
            var audios=known["metadata"]?["audios"] as JArray??new JArray();
            var subs=known["metadata"]?["subtitles"] as JArray??new JArray();
            int audio=MatchSeriesTrack(state.Audio,audios,true);
            int sub=state.Subtitle==null?-1:MatchSeriesTrack(state.Subtitle,subs,false);
            if(audio<0 || (state.Subtitle!=null && sub<0))
                return Error(409,"В выбранном файле нет выбранного перевода или дорожки субтитров. Выберите параметры заново через Онлайн → TorBox");
            return await StartSeries(pick,file,state.Resolution,audio,sub,state.Selection,key);
        } finally { state.Gate.Release(); }
    });
    [HttpGet,Route("torbox/series/advance")]
    public Task<ActionResult> AdvanceSeries(string session,int after,int version=-1) => Guard(async(c,key)=>{
        var state=GetSeries(session,key);
        if(after!=state.Episodes.Count-1)return Error(400,"Некорректный переход после последней серии сезона");
        return Payload(new{success=true,ended=true,detail="Сезон завершён. Добавьте следующий сезон через TorBox и выберите озвучку и субтитры заново"});
    });
}
