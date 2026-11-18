const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../wwwroot/infuseSave.js'), 'utf8')
  .replace('    if (!window.plugin_its_ready)', '    window.testApi={sendPreparedLinks,addSingleSaveAction,addBulkSaveAction,createEditableLinks,normalizeEditableLinks,setEditedName,episodeFromFilename,sourceEpisodePattern,applyEpisodeTemplate,getPreparedLinks,infuseRequest};\n    if (!window.plugin_its_ready)');
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
assert.equal(notices.length, 1, 'pending preparation is explained');
complete(requests[0]);
assert.equal(sent[0].protocol, 'infuse:');assert.equal(sent[0].hostname, 'x-callback-url');
assert.equal(sent[0].searchParams.getAll('url').length, 2);
for (const [i, url] of sent[0].searchParams.getAll('url').entries()) {
  assert.equal(decodeURIComponent(new URL(url).pathname.split('/').pop()), series[i].filename);
  assert(!url.includes('signed-fixture'));
}
assert.deepEqual(sent[0].searchParams.getAll('filename'), series.map(x => x.filename));
api.sendPreparedLinks(series);requests[1].fail();
assert.equal(sent.length, 1, 'failed preparation never sends raw UUID URLs');assert.equal(notices.length, 2);
api.sendPreparedLinks(series);assert.equal(requests.length, 3, 'failure releases pending guard');complete(requests[2]);
const movie = { url: original, filename: 'Movie (2026) {tmdb-55}.mkv' };
api.sendPreparedLinks([movie]);assert.equal(requests.length, 4);
assert.equal(requests[3].links[0].url, original, 'movie request retains the exact signed source URL');
api.sendPreparedLinks([{url: original + '&other=1', filename: 'Other Movie.mkv'}]);
assert.equal(requests.length, 4, 'another file cannot replace a pending movie');
assert.equal(notices.length, 3, 'another file receives pending feedback');
complete(requests[3]);
assert(sent[2].searchParams.get('url').includes('/infuse/file/'));
assert.equal(decodeURIComponent(new URL(sent[2].searchParams.get('url')).pathname.split('/').pop()), movie.filename);
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

const fortitude = [];
const expected = [];
for (const [season, count, year] of [[1, 12, 2015], [2, 10, 2017], [3, 4, 2018]]) {
  for (let episode = 1; episode <= count; episode++) {
    const number = String(episode).padStart(2, '0');
    const filename = `Fortitude.S0${season}.(${number}.seriya).${year}.WEB-DL.(720p).ExKinoRay.mkv`;
    fortitude.push({ name: filename, url: original + '&file=' + fortitude.length });
    expected.push(`Fortitude S0${season}E${number}.mkv`);
  }
}
const fromTorBox = api.createEditableLinks(fortitude, null);
api.setEditedName(fromTorBox[0], 'Fortitude S01E01 - Custom');
fromTorBox[1].enabled = false;
fromTorBox[2].source.infuse_name = 'Unnormalized S01E03.mkv';
fromTorBox[2].editedName = 'Unnormalized S01E03';
api.normalizeEditableLinks(fromTorBox, null);
assert.equal(fromTorBox[0].editedName, 'Fortitude S01E01 - Custom');
assert.equal(fromTorBox[2].editedName, 'Fortitude S01E03');
api.normalizeEditableLinks(fromTorBox, null, true);
assert.equal(fromTorBox[0].editedName, 'Fortitude S01E01');
assert.equal(fromTorBox[0].nameEdited, false);
assert.equal(fromTorBox[1].enabled, false);
fromTorBox[1].enabled = true;
assert.deepEqual(Array.from(api.getPreparedLinks(fromTorBox, null), link => link.filename), expected);
assert.deepEqual(Array.from(api.getPreparedLinks(fromTorBox, null), link => link.url), fortitude.map(file => file.url));

const fromTorrServer = api.createEditableLinks([
  { title: fortitude[14].name, url: original, season: 2, episode: 1 }
], { id: 999, title: 'Incorrect movie match' });
api.normalizeEditableLinks(fromTorrServer, null);
assert.equal(api.getPreparedLinks(fromTorrServer, null)[0].filename, 'Fortitude S02E03.mkv');
const confirmed = { media_type: 'tv', original_name: 'Fortitude', first_air_date: '2015-01-29' };
const confirmedNames = api.createEditableLinks([fortitude[22]], confirmed);
api.normalizeEditableLinks(confirmedNames, confirmed);
assert.equal(api.getPreparedLinks(confirmedNames, confirmed)[0].filename, 'Fortitude (2015) S03E01.mkv');
console.log('PASS: all 26 Fortitude files, manual-name preservation and reset, TorrServer, and first-air year');

const legacy = api.createEditableLinks([
  { name: 'Show/Disc 1/Track 03.mkv', url: original },
  { name: 'Show/Disc 2/Track 07.mp4', url: original + '&second=1' },
  { name: 'Show/Disc 2/Track 10.mkv', url: original + '&third=1' },
  { name: 'Show/Disc 2/featurette.mkv', url: original + '&extra=1' }
], { id: 55, title: 'Incorrect movie' });
legacy[0].editedName = 'Correct Show (2015) S01E03';
legacy[2].enabled = false;
const template = api.episodeFromFilename({ name: legacy[0].editedName });
const matcher = api.sourceEpisodePattern('Show/Disc {season}/Track {episode}.*', template);
assert.equal(matcher(legacy[0].source).episode, 3);
assert.equal(matcher(legacy[1].source).season, 2);
assert.equal(api.sourceEpisodePattern('Track {episode}-{episode}.mkv', template), null);
assert.equal(api.sourceEpisodePattern('Track {episode}.mkv', template)(legacy[1].source), null);
const applied = api.applyEpisodeTemplate(legacy, legacy[0], template, matcher);
api.normalizeEditableLinks(legacy, null);
assert.equal(applied.renamed, 2);
assert.equal(applied.skipped, 1);
assert.equal(legacy[2].enabled, false);
assert.equal(legacy[2].editedName, 'Correct Show (2015) S02E10');
assert.deepEqual(Array.from(api.getPreparedLinks(legacy, null).slice(0, 2), link => [link.filename, link.url]), [
  ['Correct Show (2015) S01E03.mkv', original],
  ['Correct Show (2015) S02E07.mp4', original + '&second=1']
]);
api.normalizeEditableLinks(legacy, null, true);
assert.equal(legacy[0].templateApplied, false);
assert.equal(legacy[1].templateApplied, false);
assert.notEqual(legacy[1].editedName, 'Correct Show (2015) S02E07');
assert.equal(legacy[2].enabled, false);
const sorted = api.createEditableLinks([fortitude[14], fortitude[0], fortitude[25]], null);
sorted[0].editedName = 'Fortitude Custom S02E03 - Episode title';
api.applyEpisodeTemplate(sorted, sorted[0], api.episodeFromFilename({ name: sorted[0].editedName }));
assert.equal(sorted[1].editedName, 'Fortitude Custom S01E01');
assert.equal(sorted[2].editedName, 'Fortitude Custom S03E04');
console.log('PASS: edited episode template, source masks, multiple seasons, gaps, extensions and ignored extras');
