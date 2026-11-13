const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../../Modules/TorBox/plugin.js'), 'utf8');
const block = source.slice(source.indexOf('  function infuseMovie(torrent) {'), source.indexOf('  function item(id, back, g) {'));
assert(block.startsWith('  function infuseMovie'));
let calls = [], menus = [], options, done = false, stopped = 0, back = 0;
const activity = {};
const context = {
  generation: 1, infuseJob: 0, infuseBusy: false, originController: 'content',
  escaped: String, video: f => /\.mkv$/.test(f.name),
  $: { extend: Object.assign },
  window: { lampac_infuse_saver: { menu(value) {
    options = value;
    return [{ title: 'All', infuse_scope: 'all' }];
  } } },
  Lampa: {
    Activity: { active: () => activity },
    Noty: { show() {} }, Loading: { start() {}, stop() { stopped++; } },
    Select: { show() {}, hide() {} }, Controller: { toggle() {} }
  },
  choose: (title, items) => menus.push(items),
  request: (path, body, ok, fail) => calls.push({ path, body, ok, fail })
};
vm.createContext(context);
vm.runInContext(block, context);
context.infuseItem(2, () => back++, 1);
assert.equal(calls[0].path, 'item?id=2');
calls[0].ok({ data: { id: 2, name: 'Fixture', ready: true,
  files: Array.from({ length: 9 }, (_, i) => ({ id: i + 1, name: `S01E${String(i + 1).padStart(2, '0')}.mkv` })) } });
assert.equal(menus[0].length, 10);
menus[0][0].run();
options.resolve(options.items, () => done = true);
assert.equal(calls[1].path, 'infuse/links');
assert.equal(calls[1].body.files.length, 8);
calls[1].ok({ data: Array.from(calls[1].body.files).reverse().map(id => ({ id, url: `https://media.example/${id}` })) });
assert.deepEqual(Array.from(calls[2].body.files), [9]);
calls[2].ok({ data: [{ id: 9, url: 'https://media.example/9' }] });
assert(done && stopped === 1 && back === 0);
assert(options.items.every(item => item.url === `https://media.example/${item.id}`));
console.log('PASS Infuse lazy details, eight-plus-one batching and ID-based response order');

done = false;
options.resolve(options.items, () => done = true);
calls[calls.length - 1].ok({ data: [] });
assert(!done && back === 1 && !context.infuseBusy);
console.log('PASS incomplete batch does not hand unresolved links to Infuse');
