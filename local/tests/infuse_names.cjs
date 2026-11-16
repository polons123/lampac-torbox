const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../wwwroot/infuseSave.js'), 'utf8')
  .replace('    if (!window.plugin_its_ready)', '    window.testApi={sendPreparedLinks,addSingleSaveAction,addBulkSaveAction,createEditableLinks,getPreparedLinks};\n    if (!window.plugin_its_ready)');
const sent = [], requests = [], notices = [];
const context = { URL, console, setTimeout: f => f(),
  window: { plugin_its_ready: true, location: { assign: url => sent.push(new URL(url)) },
    lampac_infuse_named_links: (links, done, fail) => requests.push({ links, done, fail }) },
  Lampa: { Platform: { tv() {} }, Lang: { translate: x => x }, Noty: { show: x => notices.push(x) } } };
vm.runInNewContext(source, context);
const api = context.window.testApi;
const original = 'https://cdn.example/uuid?token=signed-fixture&x=2';
const series = [{ url: original, filename: 'Show (2026) S01E01 - One.mkv' },
                { url: original + '&second=1', filename: 'Show (2026) S01E02 - Two.mkv' }];
function complete(job) {
  job.done(job.links.map((link, i) => ({ url: 'https://lampac.example/torbox/series/external/' + 'a'.repeat(64) + '/file/' + encodeURIComponent(link.filename), filename: link.filename })));
}
api.sendPreparedLinks(series);
assert.equal(requests.length, 1);assert.equal(sent.length, 0);
assert.equal(requests[0].links[0].url, original);
api.sendPreparedLinks(series);assert.equal(requests.length, 1, 'duplicate click waits for pending preparation');
complete(requests[0]);
assert.equal(sent[0].protocol, 'infuse:');assert.equal(sent[0].hostname, 'x-callback-url');
assert.equal(sent[0].searchParams.getAll('url').length, 2);
for (const [i, url] of sent[0].searchParams.getAll('url').entries()) {
  assert.equal(decodeURIComponent(new URL(url).pathname.split('/').pop()), series[i].filename);
  assert(!url.includes('signed-fixture'));
}
assert.deepEqual(sent[0].searchParams.getAll('filename'), series.map(x => x.filename));
api.sendPreparedLinks(series);requests[1].fail();
assert.equal(sent.length, 1, 'failed preparation never sends raw UUID URLs');assert.equal(notices.length, 1);
api.sendPreparedLinks(series);assert.equal(requests.length, 3, 'failure releases pending guard');complete(requests[2]);
const movie = { url: original, filename: 'Movie (2026) {tmdb-55}.mkv' };
api.sendPreparedLinks([movie]);assert.equal(requests.length, 3);assert.equal(sent[2].searchParams.get('url'), original);
const tv = { id: 123, media_type: 'tv', original_name: 'Show', name: 'Show', first_air_date: '2026-01-01' };
const data = { element: { url: original, name: 'Show.S01E03.mkv', season: 1, episode: 3 }, params: { movie: tv }, menu: [] };
api.addSingleSaveAction(data);data.menu[0].onSelect();assert.equal(requests.length, 4);complete(requests[3]);
assert(sent[3].searchParams.get('url').includes('/file/'));
const bulk = { items: [data.element], params: { movie: tv }, menu: [] };
api.addBulkSaveAction(bulk);bulk.menu[0].onSelect();assert.equal(requests.length, 5);complete(requests[4]);
assert.equal(sent[4].searchParams.get('filename'), sent[3].searchParams.get('filename'));
const edited = api.createEditableLinks([data.element, data.element], tv);
edited[0].enabled = false;edited[1].editedName = 'Show (2026) S01E04 - Ручное имя';
api.sendPreparedLinks(api.getPreparedLinks(edited, tv));assert.equal(requests.length, 6);complete(requests[5]);
assert.equal(sent[5].searchParams.getAll('url').length, 1);
assert.equal(sent[5].searchParams.get('filename'), 'Show (2026) S01E04 - Ручное имя.mkv');
const bridgeSource = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const begin = bridgeSource.indexOf('  window.lampac_infuse_named_links =');
let identity = 'profile-one', request, completed = 0, failed = 0;
const bridgeContext = { window: {}, keyIdentity: () => identity, request: (path, body, done, fail) => { request = { path, body, done, fail }; } };
vm.runInNewContext(bridgeSource.slice(begin, bridgeSource.indexOf('  function size', begin)), bridgeContext);
bridgeContext.window.lampac_infuse_named_links(series, () => completed++, () => failed++);
assert.equal(request.path, 'infuse/named');assert.equal(request.body.links, series);
identity = 'profile-two';request.done({ links: series });assert.equal(failed, 1);assert.equal(completed, 0);
bridgeContext.window.lampac_infuse_named_links(series, () => completed++, () => failed++);
request.done({ links: [] });assert.equal(failed, 2);assert.equal(completed, 0);
request.done({ links: series });assert.equal(completed, 1);
console.log('PASS: Infuse named URL handoff, signed targets, pending/failure/profile guards, single/bulk and unchanged movies');
