const assert = require('assert').strict;
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '../..');
const online = fs.readFileSync(path.join(root, 'Online/plugin.js'), 'utf8');
const parseSource = online.slice(online.indexOf('var pfParse ='), online.indexOf('    var pfApply ='));
const filters = {}; vm.createContext(filters);vm.runInContext(parseSource, filters);
const metadata = {res:'480p',hdr:'HDR10+',voices:["Studio 1+1, Team"],trackers:['tracker+literal']};
const got = JSON.parse(JSON.stringify(filters.pfParse({title:'2160p misleading title',url:'https://example.test/1080p',pidtor:metadata})));
assert.deepEqual(got,{res:'480p',hdr:'HDR10+',voice:['Studio 1+1, Team'],tr:['tracker+literal']});
assert.equal(filters.pfParse({maxquality:'1440',title:'2160p'}).res,'1440p');
assert.equal(filters.pfParse({title:'2160p'}).res,'');
assert(online.includes("pidtor: file.pidtor"));
console.log('PASS structured filters preserve resolution and literal voice/tracker punctuation');

const tracks = fs.readFileSync(path.join(root,'Modules/Tracks/plugin.js'),'utf8');
const source = tracks.slice(tracks.indexOf('  function appendSwarmStat'),tracks.indexOf('  function parseMetainfo'));
let requests=[],cleared=0,removed=0,next=0,timers=new Map();
function node(){return {find(){return this},text(){return this},append(){return this},prepend(){return this},empty(){return this},remove(){removed++;return this}}}
const context = {connect_host:'https://example.test', $:node,
 setTimeout(fn){let id=++next;timers.set(id,fn);return id},clearTimeout(id){timers.delete(id)},
 Lampa:{Template:{get:node},Lang:{translate:s=>s},Storage:{get:()=>''},Utils:{addUrlComponent:(url,q)=>url+'&'+q},
 Reguest:function(){this.timeout=()=>{};this.clear=()=>cleared++;this.native=(url,success,failure)=>requests.push({url,success,failure})}}};
vm.createContext(context);vm.runInContext(source,context);
let stop = context.appendSwarmStat(node(),{pidtor:{hash:'a'.repeat(40),index:2}},true);
assert.equal(requests.length,1);assert(requests[0].url.includes('hash='+'a'.repeat(40)));assert(!requests[0].url.includes('index='));
requests[0].success(JSON.stringify({total:12,active:7}));assert.equal(timers.size,1);
[...timers.values()][0]();timers.clear();assert.equal(requests.length,2);
stop();assert.equal(cleared,1);assert.equal(removed,1);requests[1].success(JSON.stringify({total:12,active:7}));assert.equal(timers.size,0);
let before=requests.length;context.appendSwarmStat(node(),{url:'https://example.test/lite/pidtor/serial/'+'b'.repeat(40)},false);
assert.equal(requests.length,before+1);
console.log('PASS live swarm polling uses torrent hash and cancels pending requests/timers');

const alias=fs.readFileSync(path.join(root,'local/wwwroot/its/its.js'),'utf8');
let scripts=[];let aliasContext={window:{},URL,document:{currentScript:{src:'https://example.test/its/its.js'},createElement:()=>({}),head:{appendChild:s=>scripts.push(s)}}};
vm.createContext(aliasContext);vm.runInContext(alias,aliasContext);vm.runInContext(alias,aliasContext);
assert.equal(scripts.length,1);assert.equal(scripts[0].src,'https://example.test/infuseSave.js');assert(alias.length<1000);
console.log('PASS legacy Infuse URL loads the single canonical implementation once');
