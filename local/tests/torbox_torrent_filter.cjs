const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const start = source.indexOf('  function torrentHash(');
const end = source.indexOf('  var arrangingMenu', start);
assert(start >= 0 && end > start);
const choose = source.slice(source.indexOf('  function choose('), source.indexOf('  function state('));
const add = source.slice(source.indexOf('  function torrentLink('), source.indexOf('  function watch('));

let payload, Component, active, menu, torrserverURL = 'https://torrserver.example', failAdd = false;
const requests = [], saved = [], notifications = [], playback = [];
const Lampa = {
  Component: {
    get: () => function () { this.filtred = () => this.results.Results.map(row => row.Title); },
    add: (name, ctor) => { assert.equal(name, 'torrents'); Component = ctor; }
  },
  Parser: { get: (object, done) => done(payload) },
  Activity: { active: () => active },
  Controller: { enabled: () => ({ name: 'content' }), toggle: name => assert.equal(name, 'content') },
  Select: { show: selection => { menu = selection; }, hide: () => { menu = null; } },
  Noty: { show: message => notifications.push(message) },
  Torrent: { start: () => playback.push('TorrServer') },
  Torserver: { url: () => torrserverURL, add: (data, done, fail) => { saved.push(data); (failAdd ? fail : done)(); } },
  Listener: { follow() {} },
  Utils: { hash: (value) => value },
  Storage: { get: () => true }
};
vm.runInNewContext('var busy = false;\n' + choose + add + source.slice(start, end) + 'installTorrentCache();', {
  Lampa, $: { extend: Object.assign }, keyIdentity: () => 'fixture', setTimeout() {}, clearTimeout() {},
  currentFilm: () => ({ title: 'Example' }), watch: () => playback.push('TorBox'),
  request: (route, body, done) => { requests.push({ route, body }); done({ existing: false }); }
});

const hash = ch => ch.repeat(40);
function verify(data, expected) {
  payload = data;
  const object = { page: 3 };
  const component = new Component(object);
  const session = object.lampacTorboxCache;
  Lampa.Parser.get(object, results => { component.results = results; });
  session.states[hash('a')] = { cached: true };
  session.states[hash('b')] = { cached: true };
  session.states[hash('c')] = { cached: false };

  assert.deepEqual(Array.from(component.filtred()), expected);
  assert.equal(object.page, 1);
  session.only = false;
  assert.deepEqual(Array.from(component.filtred()), data.Results.map(row => row.Title));
  assert(data.Results.every(row => !('lampacJackett' in row)));
}

verify({ Results: [
  { Title: 'JacRed duplicate', Source: 'JacRed', InfoHash: hash('a') },
  { Title: 'Jackett duplicate', Source: 'Jackett', InfoHash: hash('a') },
  { Title: 'Jackett download', Link: 'https://index.example/dl/fixture/?jackett_apikey=fixture&path=fixture', InfoHash: hash('b') },
  { Title: 'Uncached JacRed', Source: 'JacRed', InfoHash: hash('c') },
  { Title: 'Unknown cache', InfoHash: hash('d') }
]}, ['JacRed duplicate']);

const magnetOnly = { Title: 'Magnet-only result', TrackerId: 'fixture', InfoHash: hash('a') };
const Indexers = [{ ID: 'fixture' }];
verify({ Indexers, Results: [magnetOnly] }, []);
verify({ Indexers, Results: [magnetOnly], jacred: true }, ['Magnet-only result']);
console.log('PASS: Jackett rows are hidden in cache-only mode and restored when it is disabled');

const movie = { title: 'Example', original_title: 'Original', img: 'poster' };
const object = {};
const component = new Component(object);
component.activity = {};
active = { activity: component.activity };
payload = { Results: [{ InfoHash: hash('a') }] };
let row;
Lampa.Parser.get(object, data => { row = data.Results[0]; });
const session = object.lampacTorboxCache;
session.states[hash('a')] = { cached: true, until: Date.now() + 30000 };
function select(title) {
  Lampa.Torrent.start(row, movie);
  const item = menu.items.find(entry => entry.title === title);
  assert(item, title);
  menu.onSelect(item);
  assert.equal(menu, null);
}
select('Добавить в TorBox');
assert.equal(requests.length, 1);
assert.equal(requests[0].route, 'add');
assert.equal(requests[0].body.allow_download, true);
assert.equal(requests[0].body.hash, hash('a'));
assert.equal(requests[0].body.film.title, movie.title);
assert.equal(notifications.at(-1), 'Раздача добавлена в TorBox');
select('Добавить в TorrServer');
assert.equal(saved[0].link, 'magnet:?xt=urn:btih:' + hash('a'));
assert.equal(saved[0].data.movie, movie);
assert.equal(saved[0].title, 'Example / Original');
assert.equal(notifications.at(-1), 'Раздача добавлена в TorrServer');
failAdd = true;
select('Добавить в TorrServer');
assert.equal(notifications.at(-1), 'Не удалось добавить раздачу в TorrServer');
torrserverURL = '';
select('Добавить в TorrServer');
assert.equal(saved.length, 2);
assert.equal(playback.length, 0);
select('Воспроизвести из кэша TorBox');
select('Воспроизвести через TorrServer');
session.states[hash('a')].cached = false;
Lampa.Torrent.start(row, movie);
assert.equal(menu, null);
assert.deepEqual(playback, ['TorBox', 'TorrServer', 'TorrServer']);
console.log('PASS: cached-release menu adds to either service without starting playback');
