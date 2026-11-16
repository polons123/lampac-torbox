const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../wwwroot/infuseSave.js'), 'utf8')
  .replace('    if (!window.plugin_its_ready)', '    window.testApi={sendPreparedLinks,addSingleSaveAction,addBulkSaveAction,createEditableLinks,getPreparedLinks,infuseRequest};\n    if (!window.plugin_its_ready)');
const sent = [], requests = [], notices = [];
let identity = 'profile-one';
const context = { URL, console, document: {currentScript:{src:'https://lampac.example/infuseSave.js'}}, setTimeout: f => f(),
  $: {ajax: job => requests.push({links:JSON.parse(job.data).links, done: links => job.success({success:true,links}), fail:job.error, job})},
  window: { plugin_its_ready: true, location: {href:'https://lampac.example/', assign: url => sent.push(new URL(url))} },
  Lampa: { Storage:{get: (key, fallback) => key === 'account_email' ? identity : fallback}, Platform: { tv() {} }, Lang: { translate: x => x }, Noty: { show: x => notices.push(x) } } };
vm.runInNewContext(source, context);
const api = context.window.testApi;
const original = 'https://cdn.example/uuid?token=signed-fixture&x=2';
const series = [{ url: original, filename: 'Show (2026) S01E01 - One.mkv' },
                { url: original + '&second=1', filename: 'Show (2026) S01E02 - Two.mkv' }];
function complete(job) {
  job.done(job.links.map(link => ({ url: 'https://lampac.example/infuse/file/' + encodeURIComponent(link.filename) + '?token=' + 'a'.repeat(64), filename: link.filename })));
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
api.sendPreparedLinks([movie]);assert.equal(requests.length, 4);complete(requests[3]);
assert(sent[2].searchParams.get('url').includes('/infuse/file/'));
assert.equal(sent[2].searchParams.get('filename'), movie.filename);
const tv = { id: 123, media_type: 'tv', original_name: 'Show', name: 'Show', first_air_date: '2026-01-01' };
const data = { element: { url: original, name: 'Show.S01E03.mkv', season: 1, episode: 3 }, params: { movie: tv }, menu: [] };
api.addSingleSaveAction(data);data.menu[0].onSelect();assert.equal(requests.length, 5);complete(requests[4]);
assert(sent[3].searchParams.get('url').includes('/infuse/file/'));
const bulk = { items: [data.element], params: { movie: tv }, menu: [] };
api.addBulkSaveAction(bulk);bulk.menu[0].onSelect();assert.equal(requests.length, 6);complete(requests[5]);
assert.equal(sent[4].searchParams.get('filename'), sent[3].searchParams.get('filename'));
const edited = api.createEditableLinks([data.element, data.element], tv);
edited[0].enabled = false;edited[1].editedName = 'Show (2026) S01E04 - Ручное имя';
api.sendPreparedLinks(api.getPreparedLinks(edited, tv));assert.equal(requests.length, 7);complete(requests[6]);
assert.equal(sent[5].searchParams.getAll('url').length, 1);
assert.equal(sent[5].searchParams.get('filename'), 'Show (2026) S01E04 - Ручное имя.mkv');
api.sendPreparedLinks(series);
const last = requests[7];
assert.equal(new URL(last.job.url).pathname, '/infuse/named');
assert.equal(new URL(last.job.url).searchParams.get('account_email'), 'profile-one');
assert(!Object.keys(context.window).some(x => x.includes('torbox')), 'no TorBox client loaded');
identity = 'profile-two';complete(last);assert.equal(sent.length, 6, 'profile switch cancels handoff');
api.sendPreparedLinks(series);requests[8].done([]);assert.equal(sent.length, 6, 'incomplete response is rejected');
console.log('PASS: independent Infuse named handoff without TorBox, profile/failure guards, single/bulk/editor and named movies');
