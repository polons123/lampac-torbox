using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace TorBox;
public partial class TorBoxController
{
    static bool InfuseVideo(JToken file) => System.Text.RegularExpressions.Regex.IsMatch(FileName(file),@"\.(mkv|mp4|avi|m4v|mov|ts|m2ts|webm|mpg|mpeg|wmv|flv|vob|ogv)$",System.Text.RegularExpressions.RegexOptions.IgnoreCase);
    public class InfuseInput { public long id {get;set;} public long[] files {get;set;} }
    [HttpPost,Route("torbox/infuse/links"),RequestSizeLimit(4096)]
    public Task<ActionResult> InfuseLinks([FromBody] InfuseInput input) => Guard(async(c,key)=>{
        if(input==null || input.id<0 || input.files==null || input.files.Length<1 || input.files.Length>8 ||
            input.files.Any(f=>f<0) || input.files.Distinct().Count()!=input.files.Length)return Error(400,"Некорректный список файлов Infuse");
        var torrent=Rows(await Call("mylist?id="+input.id+"&bypass_cache=true&files=true",key)).FirstOrDefault(t=>(long?)t["id"]==input.id);
        if(torrent==null)return Error(404,"Раздача отсутствует в вашем TorBox");
        bool ready=torrent["download_present"]?.Type==JTokenType.Boolean ? (bool)torrent["download_present"] : (bool?)torrent["download_finished"]==true;
        if(!ready)return Error(409,"Раздача ещё не готова в TorBox");
        var files=torrent["files"] as JArray??new JArray();
        if(input.files.Any(id=>!files.Any(f=>(long?)f["id"]==id && InfuseVideo(f))))return Error(400,"Выберите видеофайлы этой раздачи");
        var film=ReadFilm(torrent);
        bool serial=(string)film?["media_type"]=="tv" || SeasonHint((string)torrent["name"])>0 ||
            files.Any(f=>System.Text.RegularExpressions.Regex.IsMatch((string)f["name"]??FileName(f),@"(?<![\p{L}\p{N}])S\d{1,2}[ ._-]*E\d{1,3}|(?<!\d)\d{1,2}x\d{1,3}",System.Text.RegularExpressions.RegexOptions.IgnoreCase));
        var episodes=serial?EpisodeFiles(new Pick{Name=(string)torrent["name"]},torrent):null;
        Dictionary<long,string> urls;
        try { urls=ParseBatchLinks(await RequestDownloadBatch(key,input.id,input.files),input.files); }
        catch(TorBoxException ex) when(ex.UnsupportedEndpoint) { urls=null; }
        object[] links;
        if(urls==null)links=await InfuseEach(key,input,files,episodes);
        else links=input.files.Select(id=>InfuseLink(id,urls[id],files,episodes)).ToArray();
        return Payload(new{success=true,data=links});
    });
    string DownloadUser() => !requestInfo.IsLocalIp && !string.IsNullOrEmpty(requestInfo.IP) ? "&user_ip="+Uri.EscapeDataString(requestInfo.IP) : "";
    async Task<JToken> RequestDownloadBatch(string key,long torrentId,long[] fileIds) {
        string query="requestdl/batch?token="+Uri.EscapeDataString(key)+"&torrent_id="+torrentId+DownloadUser();
        try {
            var body=new StringContent(Newtonsoft.Json.JsonConvert.SerializeObject(new{file_ids=fileIds}),Encoding.UTF8);
            body.Headers.ContentType=new MediaTypeHeaderValue("application/json");
            return await Call(query,key,body,method:HttpMethod.Post);
        } catch(TorBoxException ex) when(ex.UnsupportedEndpoint) {
            string get=query;
            foreach(var id in fileIds)get+="&file_ids="+id;
            return await Call(get,key,method:HttpMethod.Get);
        }
    }
    // Live batch returns [{file_id,success,url,error}]; older ID maps remain supported.
    static Dictionary<long,string> ParseBatchLinks(JToken data,long[] requested) {
        var map=new Dictionary<long,string>();
        void Add(long id,string url) {
            if(map.ContainsKey(id))throw new TorBoxException(502,"TorBox вернул повторяющийся файл в пакетном ответе");
            if(!Uri.TryCreate(url,UriKind.Absolute,out var uri) || uri.Scheme!="https")throw new TorBoxException(502,"TorBox вернул некорректную ссылку файла");
            map[id]=url;
        }
        if(data is JArray arr) {
            foreach(var item in arr) {
                if(item is not JObject o)throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
                if((o["success"]!=null && (bool?)o["success"]!=true) || (o["error"]!=null && o["error"].Type!=JTokenType.Null))
                    throw new TorBoxException(502,"TorBox не выдал ссылку одного из файлов");
                long? id=(long?)o["file_id"]??(long?)o["id"];
                string url=(string)o["url"]??(string)o["link"]??(string)o["download_url"];
                if(id==null || string.IsNullOrEmpty(url))throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
                Add(id.Value,url);
            }
        } else if(data is JObject obj) {
            foreach(var property in obj.Properties()) {
                if(!long.TryParse(property.Name,out var id))throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
                if(property.Value is JObject item && ((item["success"]!=null && (bool?)item["success"]!=true) || (item["error"]!=null && item["error"].Type!=JTokenType.Null)))
                    throw new TorBoxException(502,"TorBox не выдал ссылку одного из файлов");
                string url=property.Value.Type==JTokenType.String ? (string)property.Value : (string)property.Value?["url"]??(string)property.Value?["link"]??(string)property.Value?["download_url"];
                if(string.IsNullOrEmpty(url))throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
                Add(id,url);
            }
        } else throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
        if(map.Count!=requested.Length || requested.Any(id=>!map.ContainsKey(id)))throw new TorBoxException(502,"TorBox вернул неполный список ссылок");
        return map;
    }
    object InfuseLink(long id,string url,JArray files,IEnumerable<SeriesEpisode> episodes) {
        var file=files.First(f=>(long?)f["id"]==id);
        var episode=episodes?.FirstOrDefault(e=>e.File==id);
        return new{id,url,name=(string)file["name"]??FileName(file),season=(int?)episode?.Season,episode=(int?)episode?.Episode};
    }
    async Task<object[]> InfuseEach(string key,InfuseInput input,JArray files,IEnumerable<SeriesEpisode> episodes) {
        using var gate=new SemaphoreSlim(2,2);
        return await Task.WhenAll(input.files.Select(async id=>{
            await gate.WaitAsync(HttpContext.RequestAborted);
            try {
                string path="requestdl?token="+Uri.EscapeDataString(key)+"&torrent_id="+input.id+"&file_id="+id+"&redirect=false"+DownloadUser();
                string url=(string)await Call(path,key);
                if(!Uri.TryCreate(url,UriKind.Absolute,out var uri) || uri.Scheme!="https")throw new TorBoxException(502,"TorBox вернул некорректную ссылку файла");
                return InfuseLink(id,url,files,episodes);
            } finally { gate.Release(); }
        }));
    }
}
