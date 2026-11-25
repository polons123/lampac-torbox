const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const file = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const start = file.indexOf('  function posterName(');
const end = file.indexOf('  // Keep delayed verification', start);
assert(start > 0 && end > start);
const requests = [], saves = [];
let actions, entered, active = true, done = false;
const context = {
  window: {},
  Lampa: {
    Input: { edit: (input, send) => { entered = input; entered.send = send; } },
    Noty: { show() {} },
    Api: { sources: { tmdb: { get: (type, params, success, error) => requests.push({type, params, success, error}) } } }
  },
  choose: (_, items) => { actions = items; },
  escaped: String,
  request: (route, data, success) => { saves.push({ route, data }); success(); },
  setTimeout: () => 1, clearTimeout() {}
};
vm.runInNewContext(file.slice(start, end), context);
assert.equal(context.window.lampac_choose_film, context.clarifyFilm);
const torrent = { id: 23, name: 'Фортитьюд  Fortitude [S01-03] (2015-2018) WEB-DL 720p от ExKinoRay | Русский репортаж' };
assert.equal(context.posterName(torrent.name).type, 'tv');
context.clarifyFilm(torrent, () => { done = true; }, () => {}, () => active);
assert.equal(entered.value, 'Фортитьюд');
entered.send(entered.value);
assert.deepEqual(requests.map(x => [x.type, decodeURIComponent(x.params.query)]), [
  ['search/tv', 'Фортитьюд'], ['search/tv', 'Fortitude'],
  ['search/movie', 'Фортитьюд'], ['search/movie', 'Fortitude']
]);
const series = { id: 61335, name: 'Фортитьюд', original_name: 'Fortitude', first_air_date: '2015-01-29', poster_path: '/example.jpg' };
requests[0].error();
requests[1].success({ results: [series, series] });
requests[2].success({ results: [] });
requests[3].success({ results: [{ id: 11, title: 'Fortitude', release_date: '2015-01-01' }] });
assert.equal(actions.length, 3);
assert.match(actions[0].subtitle, /Сериал$/);
actions[0].run();
assert.equal(saves[0].route, 'metadata');
assert.equal(saves[0].data.film.media_type, 'tv');
assert.equal(saves[0].data.film.poster_path, '/example.jpg');
assert.equal(done, true);
active = false; actions[0].run();
assert.equal(saves.length, 1);
console.log('PASS Fortitude title aliases, series priority, partial search failure, poster selection and inactive profile guard');
