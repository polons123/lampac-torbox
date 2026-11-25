const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../Modules/LampaWeb/Controllers/ApiController.cs'), 'utf8');
const snippet = source.match(/bulder\.Insert\(0, """\s*(if \(typeof AndroidJS[\s\S]*?)\s*"""/);
assert(snippet, 'Android default must run before client initialization');
for (const [android, initial, expected] of [[true, null, 'integrate'], [true, 'native', 'native'], [false, null, null]]) {
    let preference = initial;
    const context = { localStorage: { getItem: () => preference, setItem: (key, value) => {
        assert.equal(key, 'keyboard_type');
        preference = value;
    } } };
    if (android) context.AndroidJS = {};
    vm.runInNewContext(snippet[1], context);
    assert.equal(preference, expected);
}
console.log('PASS Android keyboard default and existing preferences');
