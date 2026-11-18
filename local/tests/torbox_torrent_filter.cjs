const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const start = source.indexOf('  function torrentHash(');
const end = source.indexOf('  var arrangingMenu', start);
assert(start >= 0 && end > start);

let payload, Component;
const Lampa = {
  Component: {
    get: () => function () { this.filtred = () => this.results.Results.map(row => row.Title); },
    add: (name, ctor) => { assert.equal(name, 'torrents'); Component = ctor; }
  },
  Parser: { get: (object, done) => done(payload) },
  Torrent: { start() {} },
  Listener: { follow() {} },
  Utils: { hash: (value) => value },
  Storage: { get: () => true }
};
vm.runInNewContext(source.slice(start, end) + 'installTorrentCache();', {
  Lampa, $: { extend: Object.assign }, keyIdentity: () => 'fixture', setTimeout() {}, clearTimeout() {}
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
