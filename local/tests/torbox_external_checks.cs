using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json.Linq;
using Shared;
using Shared.Models.Base;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using IOFile = System.IO.File;

namespace TorBox;

// Compiled beside the unchanged dynamic manifest sources, never loaded by Lampac.
public partial class TorBoxController
{
    const string testUid="offline-account", testKey="offline-placeholder-key";
    static int checks;
    static void Check(string name,bool success) {
        if(!success)throw new Exception("FAIL: "+name);
        checks++;Console.WriteLine("PASS: "+name);
    }
    static (int Status,JObject Json) Result(ActionResult result) {
        var content=(ContentResult)result;
        return (content.StatusCode??200,JObject.Parse(content.Content));
    }
    static TorBoxController Controller(string uid=testUid,bool anonymous=false,string key=null,string mode=null,JObject config=null) {
        var context=new DefaultHttpContext();
        context.Request.Scheme="https";context.Request.Host=new HostString("lampac.example");
        context.Features.Set(new RequestModel{user_uid=uid,IsAnonymousRequest=anonymous});
        if(key!=null)context.Request.Headers["X-TorBox-Key"]=key;
        if(mode!=null)context.Request.Headers["X-TorBox-Mode"]=mode;
        if(config!=null)context.Request.Headers["X-TorBox-Config"]=Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(config.ToString()));
        return new TorBoxController{ControllerContext=new ControllerContext{HttpContext=context}};
    }
    static void Policy(bool clientMode=false,bool external=false) => IOFile.WriteAllText(configPath,new JObject{
        ["allow_client_credentials"]=clientMode,["allow_external_playlists"]=external,
        ["api_key"]=testKey,["allowed_uids"]=new JArray(testUid),
        ["torrent_origins"]=new JArray("https://server-only.example"),
        ["jacred"]=new JObject{["base_url"]="https://parser.example",["api_key"]="offline-parser-key"}
    }.ToString());
    static async Task<(int Status,JObject Json)> Probe(TorBoxController c) => Result(await c.Guard((config,key)=>
        Task.FromResult(c.Payload(new{key,config,owner=c.metadataOwner,external=c.externalPlaylistsAllowed}))));

    sealed class MockHttp : HttpMessageHandler {
        public int Calls;
        public bool Playback;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token) {
            Calls++;
            if(Playback) {
                Check("restored playlist requests the saved torrent and episode",request.RequestUri.Query.Contains("id=42&file_id=7"));
                return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new StringContent(
                    new JObject{["success"]=true,["data"]=new JObject{["hls_url"]="https://stream.example/fresh-after-restart",
                        ["metadata"]=new JObject{["audios"]=new JArray(),["subtitles"]=new JArray(),["video"]=new JObject{["height"]=1080}}}}.ToString())});
            }
            throw new Exception("Unexpected upstream request in offline playlist checks");
        }
    }
    public static async Task Main(string[] args) {
        CoreInit.conf=new CoreInit();
        var mock=new MockHttp();
        var handler=typeof(HttpMessageInvoker).GetField("_handler",BindingFlags.Instance|BindingFlags.NonPublic)
            ?? throw new Exception("Cannot install offline HTTP handler");
        handler.SetValue(client,mock);handler.SetValue(publicClient,mock);
        if(args.Length>0)await PersistenceChecks(args[0],args[1]=="client",mock);
        else { await ExternalChecks(mock);await NamedChecks(); }
        Console.WriteLine($"TorBox external playlists: {checks} checks passed (offline).");
    }

    static async Task ExternalChecks(MockHttp mock) {
        Policy();seriesSessions.Clear();externalPlaylists.Clear();
        var c=Controller();await Probe(c);
        var state=new SeriesSession{Owner=testUid,KeyHash=SeriesKeyHash(testKey),Episodes=new List<SeriesEpisode>{new(){Season=1,Episode=1,Name="One",Pick=new Pick{Id=42},File=1}}};
        seriesSessions["offline-session"]=state;
        var input=new ExternalInput{session="offline-session",index=0};
        Check("external playlist creation disabled by default",Result(await c.PrepareExternal(input)).Status==403 && externalPlaylists.IsEmpty);
        var invalidPolicy=JObject.Parse(IOFile.ReadAllText(configPath));invalidPolicy["allow_external_playlists"]="true";IOFile.WriteAllText(configPath,invalidPolicy.ToString());
        Check("external permission requires server boolean",Result(await Controller().PrepareExternal(input)).Status==503);
        Policy(external:true);
        var prepared=Result(await Controller().PrepareExternal(input)).Json;
        string url=(string)prepared["playlist"][0]["url"],access=url.Split('/')[6];
        // URL: https://lampac.example/torbox/series/external/<access>/0.m3u8
        Check("external token has 32 random bytes encoded as 64 hex characters",System.Text.RegularExpressions.Regex.IsMatch(access,"^[a-f0-9]{64}$"));
        Check("relay URL contains no account keys",!url.Contains(testKey) && !url.Contains("offline-parser-key"));
        var playlist=c.GetExternal(access);
        Check("external playlists have no expiry fields",typeof(ExternalPlaylist).GetField("Expires")==null && typeof(ExternalPlaylist).GetField("Limit")==null);
        playlist.Streams[0]=(DateTime.UtcNow.AddSeconds(30),"https://stream.example/offline");
        var relay=Controller(anonymous:true);
        Check("valid bearer can resolve without client headers",await relay.ExternalSeriesStream(access,0) is RedirectResult);
        Check("relay suppresses caching and referrers",relay.Response.Headers["Cache-Control"]=="no-store" && relay.Response.Headers["Referrer-Policy"]=="no-referrer");
        Policy(external:false);
        Check("administrator can disable existing bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==403);
        Policy(external:true);
        Check("re-enabling policy restores links that have not been revoked",await Controller(anonymous:true).ExternalSeriesStream(access,0) is RedirectResult);
        var changed=JObject.Parse(IOFile.ReadAllText(configPath));changed["api_key"]="other-offline-placeholder-key";IOFile.WriteAllText(configPath,changed.ToString());
        Check("server key change invalidates bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==410);
        Policy(external:true);
        changed=JObject.Parse(IOFile.ReadAllText(configPath));changed["allowed_uids"]=new JArray("other");IOFile.WriteAllText(configPath,changed.ToString());
        Check("server UID removal invalidates bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==403);
        Policy(external:true);playlist.State.Expires=DateTime.UtcNow.AddYears(-1);
        Check("external playlist survives expiration of browser session",await Controller().ExternalSeriesStream(access,0) is RedirectResult);
        state.Expires=DateTime.UtcNow.AddHours(1);
        var another=Result(await Controller().PrepareExternal(input)).Json;
        string access2=((string)another["playlist"][0]["url"]).Split('/')[6];
        Check("new playlists receive different tokens",access!=access2);
        await Controller().RevokeExternal();
        var third=Result(await Controller().PrepareExternal(input)).Json;
        string access3=((string)third["playlist"][0]["url"]).Split('/')[6];
        Policy(true,external:true);
        var cp=c.GetExternal(access3);cp.ServerMode=false;cp.ClientKey=testKey;
        cp.State.Owner=testUid+":torbox:"+SeriesKeyHash(testKey).ToLowerInvariant();
        Check("anonymous revocation denied",Result(await Controller(anonymous:true,key:testKey).RevokeExternal()).Status==403);
        await Controller(uid:"other",key:testKey).RevokeExternal();
        Check("revocation cannot affect other UID",!externalPlaylists.IsEmpty);
        Policy(true,external:false);
        var revoked=Result(await Controller(key:"changed-offline-placeholder-key").RevokeExternal());
        Check("owner can revoke after personal key change",(int?)revoked.Json["revoked"]==1 && externalPlaylists.IsEmpty && cp.ClientKey==null && cp.Revoked);
        Check("revocation works while external playlists are disabled",revoked.Status==200);
        Check("revoked bearer returns gone",Result(await Controller().ExternalSeriesStream(access3,0)).Status==410);

        Policy(external:true);
        state.FollowId="offline-followed-season";
        var fresh=Result(await Controller().PrepareExternal(input)).Json;
        string pendingAccess=((string)fresh["playlist"][0]["url"]).Split('/')[6];
        var pending=c.GetExternal(pendingAccess);
        Check("external playlist retains series continuation identity",pending.State.FollowId==state.FollowId);
        pending.Streams[0]=(DateTime.UtcNow.AddMinutes(1),"https://stream.example/offline");
        await pending.State.Gate.WaitAsync();
        var waiting=Controller(anonymous:true).ExternalSeriesStream(pendingAccess,0);
        var anotherPlaylist=new ExternalPlaylist{Uid=testUid,State=state,ClientKey=testKey};
        externalPlaylists["offline-other-playlist"]=anotherPlaylist;
        var revoking=Controller().RevokeExternal();
        Check("revocation completes without waiting for a busy relay",pending.Revoked && revoking.IsCompletedSuccessfully);
        Check("busy relay cannot delay revocation of other playlists",anotherPlaylist.Revoked && anotherPlaylist.ClientKey==null && externalPlaylists.IsEmpty);
        pending.State.Gate.Release();
        Check("queued relay cannot redirect after revocation",Result(await waiting).Status==410);
        await revoking;
        Check("revocation clears stream caches",pending.Streams.IsEmpty);

        for(int i=0;i<128;i++)externalPlaylists[ExternalHash("offline-cap-"+i)]=new ExternalPlaylist{Uid=testUid,State=state,ServerMode=true,Ready=true};
        var beyondCap=Result(await Controller().PrepareExternal(input));
        Check("creating a playlist beyond 128 succeeds",beyondCap.Status==200 && externalPlaylists.Count==129);
        string beyondAccess=((string)beyondCap.Json["playlist"][0]["url"]).Split('/')[6];
        externalPlaylists.Clear();externalLoaded=false;
        Check("more than 128 playlists survive reload",Controller().GetExternal(beyondAccess).Ready && externalPlaylists.Count==129);
        var allRevoked=Result(await Controller().RevokeExternal());
        Check("manual revoke removes every playlist beyond the old cap",(int?)allRevoked.Json["revoked"]==129 && externalPlaylists.IsEmpty && JObject.Parse(IOFile.ReadAllText(externalPath)).Count==0);
        var episodes=state.Episodes;
        state.Episodes=Enumerable.Repeat(episodes[0],5001).ToList();
        Check("existing episode cap is preserved",Result(await Controller().PrepareExternal(input)).Status==409);
        state.Episodes=episodes;

        Policy(true,external:true);
        var overrideResult=await Probe(Controller(key:testKey,config:new JObject{["allow_external_playlists"]=true}));
        Check("client cannot override administrator playlist permission",overrideResult.Status==400);
        Policy(external:true);
        var policy=JObject.Parse(IOFile.ReadAllText(configPath));policy.Remove("allow_external_playlists");IOFile.WriteAllText(configPath,policy.ToString());
        Check("missing external flag fails closed",!(bool)(await Probe(Controller())).Json["external"]);
        Policy(true);
        Check("client mode also defaults external playlists off",!(bool)(await Probe(Controller(key:testKey))).Json["external"]);
        var guarded=Controller(key:testKey);await Probe(guarded);
        Check("guard suppresses caching and referrers",guarded.Response.Headers["Cache-Control"]=="no-store" && guarded.Response.Headers["Referrer-Policy"]=="no-referrer");
        Check("offline checks never call upstream",mock.Calls==0);

        Policy(external:true);state.Expires=DateTime.UtcNow.AddHours(1);
        IOFile.Delete(externalPath);System.IO.Directory.CreateDirectory(externalPath);
        Check("failed disk write does not issue an ephemeral playlist",Result(await Controller().PrepareExternal(input)).Status==503 && externalPlaylists.IsEmpty);
        System.IO.Directory.Delete(externalPath);
        IOFile.WriteAllText(externalPath,"{broken");externalLoaded=false;
        Check("corrupt store fails closed without overwriting saved data",Result(await Controller().PrepareExternal(input)).Status==503 && IOFile.ReadAllText(externalPath)=="{broken");

    }

    const string namedFile="Infuse Sync Test (2026) S01E01 - Name.mkv";
    const string namedSource="https://cdn.example/uuid?token=private-signed-fixture&x=2";
    static async Task NamedChecks() {
        IOFile.Delete(externalPath);externalPlaylists.Clear();externalLoaded=false;
        Policy(external:true);
        var input=new NamedInput{links=new[]{new NamedLink{url=namedSource,filename=namedFile}}};
        Check("anonymous named creation denied",Result(await Controller(anonymous:true).PrepareNamedInfuse(input)).Status==403);
        Policy(external:false);
        Check("named creation obeys external policy",Result(await Controller().PrepareNamedInfuse(input)).Status==403);
        Policy(external:true);
        foreach(var url in new[]{"file:///etc/passwd","javascript:alert(1)","https://user:pass@example.com/video","https://example.com/\r\nX-Test:a"}) {
            Check("invalid named target rejected",Result(await Controller().PrepareNamedInfuse(new NamedInput{links=new[]{new NamedLink{url=url,filename=namedFile}}})).Status==400);
        }
        var result=Result(await Controller().PrepareNamedInfuse(input));
        string urlResult=(string)result.Json["links"][0]["url"],access=urlResult.Split('/')[6];
        Check("named URL contains encoded filename, no source credentials",result.Status==200 && Uri.UnescapeDataString(urlResult.Split('/').Last())==namedFile && !urlResult.Contains("private-signed"));
        var relay=Controller(anonymous:true);
        Check("named relay preserves exact signed source URL",(relay.NamedInfuseStream(access,namedFile) as RedirectResult)?.Url==namedSource);
        Check("named relay suppresses cache and referrers",relay.Response.Headers["Cache-Control"]=="no-store" && relay.Response.Headers["Referrer-Policy"]=="no-referrer");
        Check("named filename cannot be substituted",Result(await Task.FromResult(Controller().NamedInfuseStream(access,"Other.mkv"))).Status==404);
        Check("named ticket cannot be used as a series",Result(await Controller().ExternalSeriesStream(access,0)).Status==404);
        Check("named ticket cannot be used as series preparation",Result(await Controller().PrepareExternal(new ExternalInput{index=0,preparation=access})).Status==403);
        var changed=JObject.Parse(IOFile.ReadAllText(configPath));changed["allowed_uids"]=new JArray("other");IOFile.WriteAllText(configPath,changed.ToString());
        Check("named relay respects owner allowlist removal",Result(Controller().NamedInfuseStream(access,namedFile)).Status==403);
        Policy(external:true);
        var invalidBatch=new NamedInput{links=new[]{input.links[0],new NamedLink{url="file:///bad",filename=namedFile}}};
        int count=externalPlaylists.Count;
        Check("invalid named batch publishes no partial links",Result(await Controller().PrepareNamedInfuse(invalidBatch)).Status==400 && externalPlaylists.Count==count);
        await Controller().RevokeExternal();
        Check("manual revoke includes named links",Result(Controller(anonymous:true).NamedInfuseStream(access,namedFile)).Status==410);
    }
    static async Task PersistenceChecks(string phase,bool clientMode,MockHttp mock) {
        if(phase=="seed") {
            Policy(clientMode,external:true);
            var c=Controller(key:clientMode?testKey:null);await Probe(c);
            var state=new SeriesSession{Owner=c.metadataOwner,Title="Saved series",KeyHash=SeriesKeyHash(testKey),Resolution=720,
                Selection="Saved playback choice",FollowId="offline-follow",Episodes=new List<SeriesEpisode>{new(){
                    Pick=new Pick{Id=42,Hash="saved-release-hash",Link="https://tracker.example/private-passkey"},
                    File=7,Season=2,Episode=3,Numbered=true,Name="S02E03.mkv",ExplicitAudio=0,ExplicitSub=-1,
                    Metadata=new JObject{["hls_url"]="https://stream.example/old-private-stream"}}}};
            seriesSessions["persist-session"]=state;
            var result=Result(await c.PrepareExternal(new ExternalInput{session="persist-session",index=0}));
            Check("playlist persisted before successful response",result.Status==200 && IOFile.Exists(externalPath));
            string access=((string)result.Json["playlist"][0]["url"]).Split('/')[6];
            IOFile.WriteAllText("fixture-access",access);
            var named=Result(await Controller(key:clientMode?testKey:null).PrepareNamedInfuse(new NamedInput{links=new[]{new NamedLink{url=namedSource,filename=namedFile}}}));
            Check("named link is persisted before successful response",named.Status==200);
            IOFile.WriteAllText("fixture-named-access",((string)named.Json["links"][0]["url"]).Split('/')[6]);
            string saved=IOFile.ReadAllText(externalPath);
            Check("store omits raw bearer tokens and transient upstream URLs",!saved.Contains(access) && !saved.Contains("private-passkey") && !saved.Contains("old-private-stream"));
            Check("server credentials never copied into playlist store",clientMode || !saved.Contains(testKey));
            Check("playlist store is private",IOFile.GetUnixFileMode(externalPath)==(System.IO.UnixFileMode.UserRead|System.IO.UnixFileMode.UserWrite));
        } else {
            string access=IOFile.ReadAllText("fixture-access");
            string namedAccess=IOFile.ReadAllText("fixture-named-access");
            if(phase=="restore") {
                var c=Controller(anonymous:true);var saved=c.GetExternal(access);
                Check("named link restores across a fresh process",(c.NamedInfuseStream(namedAccess,namedFile) as RedirectResult)?.Url==namedSource);
                Check("new process restores selected playback settings",saved.Ready && saved.State.Resolution==720 && saved.State.Selection=="Saved playback choice" && saved.State.FollowId=="offline-follow" && saved.State.Episodes[0].Numbered);
                Check("new process restores client credentials when required",saved.ClientKey==(clientMode?testKey:null));
                Check("temporary metadata and stream cache are regenerated",saved.Streams.IsEmpty && saved.State.Episodes[0].Metadata==null);
                mock.Playback=true;
                var response=await c.ExternalSeriesStream(access,0);
                if(response is ContentResult error)Console.WriteLine("Fixture relay: "+error.Content+"; calls="+mock.Calls);
                var redirect=response as RedirectResult;
                Check("same external URL plays after process restart",redirect?.Url=="https://stream.example/fresh-after-restart" && mock.Calls==2);
                Policy(clientMode,external:false);
                // A failed durable revoke must report failure and leave the existing store intact.
                string original=IOFile.ReadAllText(externalPath);IOFile.Delete(externalPath);System.IO.Directory.CreateDirectory(externalPath);
                Check("failed revocation is not reported as successful",Result(await Controller(key:clientMode?testKey:null).RevokeExternal()).Status==503 && !saved.Revoked);
                System.IO.Directory.Delete(externalPath);IOFile.WriteAllText(externalPath,original);
                var revoked=Result(await Controller(key:clientMode?"changed-offline-placeholder-key":null).RevokeExternal());
                Check("restored playlist and named link revoke together with policy disabled",revoked.Status==200 && (int?)revoked.Json["revoked"]==2);
            } else {
                Check("revocation survives another fresh process",Result(await Controller(anonymous:true).ExternalSeriesStream(access,0)).Status==410);
                Check("named revocation survives another fresh process",Result(Controller(anonymous:true).NamedInfuseStream(namedAccess,namedFile)).Status==410);
                Check("revocation removes credentials from disk",!IOFile.ReadAllText(externalPath).Contains(testKey));
            }
        }
    }

}
