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
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token) {
            Calls++;
            throw new Exception("Unexpected upstream request in offline playlist checks");
        }
    }
    public static async Task Main() {
        CoreInit.conf=new CoreInit();
        var mock=new MockHttp();
        var handler=typeof(HttpMessageInvoker).GetField("_handler",BindingFlags.Instance|BindingFlags.NonPublic)
            ?? throw new Exception("Cannot install offline HTTP handler");
        handler.SetValue(client,mock);handler.SetValue(publicClient,mock);
        await ExternalChecks(mock);
        Console.WriteLine($"TorBox external playlists: {checks} checks passed (offline).");
    }

    static async Task ExternalChecks(MockHttp mock) {
        Policy();seriesSessions.Clear();externalPlaylists.Clear();
        var c=Controller();await Probe(c);
        var state=new SeriesSession{Owner=testUid,KeyHash=SeriesKeyHash(testKey),Episodes=new List<SeriesEpisode>{new(){Season=1,Episode=1,Name="One",Pick=new Pick(),File=1}}};
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
        Check("external lifetime has idle and absolute bounds",playlist.Expires>DateTime.UtcNow.AddHours(11) && playlist.Limit<DateTime.UtcNow.AddDays(7).AddSeconds(1));
        playlist.Streams[0]=(DateTime.UtcNow.AddSeconds(30),"https://stream.example/offline");
        var relay=Controller(anonymous:true);
        Check("valid bearer can resolve without client headers",await relay.ExternalSeriesStream(access,0) is RedirectResult);
        Check("relay suppresses caching and referrers",relay.Response.Headers["Cache-Control"]=="no-store" && relay.Response.Headers["Referrer-Policy"]=="no-referrer");
        Policy(external:false);
        Check("administrator can disable existing bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==403);
        Policy(external:true);
        Check("re-enabling policy restores unexpired links",await Controller(anonymous:true).ExternalSeriesStream(access,0) is RedirectResult);
        var changed=JObject.Parse(IOFile.ReadAllText(configPath));changed["api_key"]="other-offline-placeholder-key";IOFile.WriteAllText(configPath,changed.ToString());
        Check("server key change invalidates bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==410);
        Policy(external:true);
        changed=JObject.Parse(IOFile.ReadAllText(configPath));changed["allowed_uids"]=new JArray("other");IOFile.WriteAllText(configPath,changed.ToString());
        Check("server UID removal invalidates bearer links",Result(await Controller().ExternalSeriesStream(access,0)).Status==403);
        Policy(external:true);playlist.Expires=DateTime.UtcNow.AddSeconds(-1);
        Check("idle expiration invalidates bearer",Result(await Controller().ExternalSeriesStream(access,0)).Status==410);
        var another=Result(await Controller().PrepareExternal(input)).Json;
        string access2=((string)another["playlist"][0]["url"]).Split('/')[6];
        Check("new playlists receive different tokens",access!=access2);
        var p2=c.GetExternal(access2);p2.Limit=DateTime.UtcNow.AddSeconds(-1);
        Check("absolute expiration overrides renewed idle time",Result(await Controller().ExternalSeriesStream(access2,0)).Status==410);
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
        var revoking=Controller().RevokeExternal();
        Check("revocation marks an in-flight playlist before waiting",pending.Revoked && !revoking.IsCompleted);
        pending.State.Gate.Release();
        Check("queued relay cannot redirect after revocation",Result(await waiting).Status==410);
        await revoking;
        Check("revocation clears stream caches",pending.Streams.IsEmpty);

        for(int i=0;i<128;i++)externalPlaylists["offline-cap-"+i]=new ExternalPlaylist();
        Check("existing playlist cap is preserved",Result(await Controller().PrepareExternal(input)).Status==429);
        externalPlaylists.Clear();
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

    }

}
