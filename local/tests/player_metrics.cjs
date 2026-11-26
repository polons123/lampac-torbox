const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../wwwroot/plugins/player-metrics.js'), 'utf8');

function events() {
    const map = new Map();
    return {
        follow(name, fn) { map.set(name, fn); },
        emit(name, value) { map.get(name)?.(value); },
        remove(name, fn) { if (map.get(name) === fn) map.delete(name); }
    };
}

function media() {
    const eventsOnVideo = events();
    return { paused: false, buffered: { length: 0 }, currentTime: 0,
        addEventListener: eventsOnVideo.follow, removeEventListener: eventsOnVideo.remove, fire: eventsOnVideo.emit,
        requestVideoFrameCallback(fn) { this.frame = fn; return 42; },
        cancelVideoFrameCallback(id) { assert.equal(id, 42); this.frame = null; }
    };
}

let time = 0, stats = '', settings = null, video = media();
const L = {Player: {listener: events()}, Listener: events(), PlayerVideo: {listener: events(), video: () => video},
    SettingsApi: {addParam(param) { settings = param; }}, PlayerInfo: {set() {}, render() { return null; }},
    Noty: {show(s) { stats = s; }}};
const window = {Lampa: L, performance: {now: () => time}};
vm.runInNewContext(script, {window, document: {createElement: () => ({}), head: {appendChild() {}}}, setInterval() {}, clearInterval() {}});
const api = window.lampacPlaybackMetrics;
const sample = () => { settings.onChange(); return stats; };
const event = (e, cell) => L.Player.listener.emit(e, cell);
const cell = {url: 'https://private.invalid/secret'};
const offered = api.choosing();
time = 1500;
const t = api.begin(offered);
time = 2000; api.resolved(t, cell);
event('create', {data:cell}); event('start', cell); event('ready', cell);
time = 2200; video.fire('waiting'); video.fire('playing');
time = 2300; video.frame(time); // successful frame, so startup waits cannot count as rebuffering
time = 2400; video.fire('waiting'); video.fire('waiting');
time = 2800; video.fire('playing');
time = 2900; video.paused = true; video.fire('waiting'); video.paused = false;
time = 3000; video.seeking = true; video.fire('waiting'); video.seeking = false;
assert.match(sample(), /Выбор файла 1,50 с · Ссылка 0,50 с · До первого кадра 0,30 с · Буферизация 1 \/ 0,40 с/);
assert.ok(!stats.includes('private') && !stats.includes('secret'));
event('destroy');
assert.match(sample(), /Буферизация 1 \/ 0,40 с/);

const older = api.begin(); time += 250; const other = {};
api.resolved(older, other);
event('external', {});
assert.match(sample(), /Выбор файла — · Ссылка — · Внешний плеер/);
event('external', other);
assert.match(sample(), /Ссылка 0,25 с · Внешний плеер/);
event('start', other); // consumed metrics never replay
video = media();
delete video.requestVideoFrameCallback;
event('ready', other);
time += 150; video.fire('playing');
assert.match(sample(), /Ссылка — · Старт \(playing\) 0,15 с/);

let click;
const item = {on(name, fn) { assert.equal(name, 'hover:enter'); click = fn; }};
const tor = {};
L.Listener.emit('torrent_file', {type:'list_open'});
L.Listener.emit('torrent_file', {type:'render', item, element:tor});
time += 100; click();
time += 200; event('create', {data:tor}); event('start', tor);
assert.match(sample(), /Выбор файла 0,10 с · Ссылка 0,20 с/);
L.Listener.emit('torrent_file', {type:'list_close'});
event('destroy');

video = media(); event('start', {}); event('ready', {});
const staleFrame = video.frame;
event('destroy'); assert.equal(video.frame, null);
video = media(); event('start', {}); event('ready', {});
staleFrame(time + 20000);
assert.match(sample(), /До первого кадра —/);
console.log('PASS playback selection, stream lookup, first frame, stall accounting, external launches, TorrServer preload and callback isolation');
