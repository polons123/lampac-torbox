const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const start = source.indexOf('  function Library(object) {');
const end = source.indexOf('  function openLibrary()');
assert(start > 0 && end > start);
const block = source.slice(start, end);

const requests = [];
const menus = [];
const replaced = [];
let generation = 1;
let current = null;
function node() {
  const n = { addClass() { return n; }, removeClass() { return n; }, find() { return n; }, append() { return n; }, appendTo() { return n; }, text() { return n; }, css() { return n; }, on() { return n; }, remove() { return n; } };
  return n;
}
function $(html) { return node(); }
function request(path, body, done, failed) { requests.push({ path, done, failed }); }
function choose(title, items) { menus.push({ title, items }); }
function begin() { return ++generation; }
function keyIdentity() { return 'acct'; }
const deletionQueue = [];
let deletionView = null;
function resumeDeletions() {}
function state(t) { return t && t.ready ? 'Готово' : 'Загрузка'; }
function size() { return '1 МБ'; }
function item() { throw new Error('list must not open files inline'); }
function infuseItem() { throw new Error('list must not request infuse inline'); }
function openSeasonUpdates() {}
function clarifyFilm() {}
function rememberDeletion() {}
function saveDeletions() {}
function showDeletionFailures() {}
function posterLookup() {}
function escaped(s) { return String(s || ''); }
const Lampa = {
  InteractionCategory: function () {
    this.activity = { loader() {} };
    this.render = () => ({ find: () => ({ first: () => ({ prepend() {} }) }) });
    this.build = function (data) {
      this.built = data;
      this.cards = [];
      (data.results || []).forEach((row) => {
        const card = { render: () => node() };
        this.cardRender({}, row, card);
        this.cards.push(card);
      });
    };
    this.append = function (data) { this.appended = data; };
    this.limit = function () {};
    this.destroy = function () {};
  },
  Activity: {
    replace(params) {
      replaced.push(params);
      if (current && current.destroy) current.destroy();
      current = new ctx.Library(params);
      current.create();
    },
    active() { return { component: 'my_torbox' }; }
  },
  Player: { opened() { return false; } },
  Controller: { toggle() {} },
  Noty: { show() {} },
  Select: { show(opts) { menus.push({ title: opts.title, items: opts.items }); }, hide() {}, opened() { return false; } },
  Storage: { get() { return {}; }, set() {} },
  Api: { img(v) { return v; }, sources: { tmdb: { get() {} } } },
  Listener: { follow() {} }
};
const ctx = {
  request, choose, begin, keyIdentity, deletionQueue, deletionView, generation, resumeDeletions,
  state, size, item, infuseItem, openSeasonUpdates, clarifyFilm, rememberDeletion, saveDeletions,
  showDeletionFailures, posterLookup, escaped, Lampa, $, setTimeout(fn) { fn(); }, console
};
vm.createContext(ctx);
vm.runInContext(block + '\nthis.Library = Library;', ctx);

function listCalls() { return requests.filter((r) => r.path.startsWith('list?')); }
function complete(rec, data, more) { rec.done({ data, more, deletion_scope: 'scope' }); }
const row = (id, name) => ({ id, name, ready: true, size: 10, state: 'cached', progress: 1, airlocked: false });

Lampa.Activity.replace({ component: 'my_torbox', title: 'Мой TorBox', page: 1 });
assert.equal(listCalls()[0].path, 'list?offset=0');
complete(listCalls()[0], [row(1, 'One'), row(2, 'Two')], true);
assert.deepEqual(current.built.results.map((r) => r.title), ['One', 'Two']);
assert.equal(current.built.results[0].torboxItem.files, undefined);
current.next();
const page2 = listCalls()[listCalls().length - 1];
assert.equal(page2.path, 'list?offset=100');
current.cards[0].onMenu();
const filter = menus[menus.length - 1].items.find((i) => i.title === 'Активные загрузки');
assert(filter, 'filter action missing');
const before = listCalls().length;
filter.run();
assert.equal(replaced[replaced.length - 1].page, 1);
assert.equal(replaced[replaced.length - 1].active, true);
const active = listCalls()[listCalls().length - 1];
assert.equal(active.path, 'list?offset=0&active=true');
assert.notEqual(active, page2);
page2.done({ data: [row(9, 'LATE')], more: false, deletion_scope: 'scope' });
assert.equal(current.appended, undefined);
assert.equal(current.built, undefined);
complete(active, [], false);
assert.equal(current.built.results[0].title, 'Нет активных загрузок');
current.cards[0].onMenu();
const back = menus[menus.length - 1].items.find((i) => i.title === 'Показать всю библиотеку');
assert(back, 'return-to-library action missing on the empty active list');
back.run();
const again = listCalls()[listCalls().length - 1];
assert.equal(again.path, 'list?offset=0');
assert.equal(replaced[replaced.length - 1].page, 1);
complete(again, [row(3, 'Three')], false);
assert.deepEqual(current.built.results.map((r) => r.title), ['Three']);
assert(source.includes('cursor + 8'));
console.log('PASS library filter keeps its own request and resets the page');
