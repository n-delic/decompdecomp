'use strict';
// The game picker's compiler list (fillCompilers in home.js), run against a stand-in for the page state: a user's
// own kit compiler is offered beside the console's pinned packs and pack versions, never in place of them.
// tests/test_gui.py runs it; a path given as the first argument reads that home.js instead (to try an older one).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const home = process.argv[2] || path.join(__dirname, '../src/decompnet/app/static/js/home.js');
const source = fs.readFileSync(home, 'utf8');
const start = source.indexOf('function fillCompilers(');
const end = source.indexOf('\nasync function runImport(', start);

function fill(entry, state, current, platform) {
  const context = vm.createContext({consoleOf: () => entry, S: {st: state}, esc: String});
  vm.runInContext(source.slice(start, end), context);
  const select = {innerHTML: ''};
  context.fillCompilers(select, current, platform);
  return select.innerHTML;
}

// A PC kit (Visual C++ 9.0): the pinned versions stay, the kit's copy is added. The state's toolchains hold the
// packs as Compilers & tools lists them (msvc6, ...), which aren't the console's version ids.
const kitRow = {id: 'MSVC/own-9.0.30729', name: 'Your Visual C++', kind: 'compiler',
                use: '9.0.30729', supported: true, installed: true};
const pc = fill({compilers: [
  {id: 'MSVC/6.0', name: 'Visual C++ 6.0', built: ''},
  {id: 'MSVC/7.1', name: 'Visual C++ 7.1', built: ''},
  {id: 'MSVC/own-9.0.30729', name: 'Your Visual C++', built: '9.0.30729'},
]}, {toolchains: [{id: 'msvc6', name: 'Visual C++ 6.0 pack', kind: 'compiler', use: 'packs', supported: true, installed: true}],
     compiler_choices: [kitRow]}, 'MSVC/6.0', 'pc');
assert.match(pc, /value="MSVC\/6\.0" selected/);
assert.match(pc, /value="MSVC\/7\.1"/);
assert.match(pc, /value="MSVC\/own-9\.0\.30729"/);
assert.doesNotMatch(pc, /value="msvc6"/);  // (a pack, not one of this console's compilers)
// a version that says nothing about its games has no dangling separator
assert.match(pc, />Visual C\+\+ 6\.0<\/option>/);
assert.match(pc, />Your Visual C\+\+ · 9\.0\.30729<\/option>/);

// A console whose list mixes compilers that are toolchains of their own (a row in the state's toolchains) with
// versions of a pack, and a kit's copy: each kind is offered, none hides the others.
const mixed = fill({compilers: [
  {id: 'gcc-2.7.2-kmc', name: 'GCC 2.7.2 (KMC)', built: 'third-party games'},
  {id: 'pack/1.0', name: 'Pack 1.0', built: ''},
  {id: 'gcc-2.7.2-kmc-own-1234', name: 'Your GCC', built: 'your copy'},
]}, {toolchains: [{id: 'gcc-2.7.2-kmc', name: 'GCC 2.7.2 (KMC)', kind: 'compiler', use: 'third-party games',
                   supported: true, installed: true}],
     compiler_choices: [{id: 'gcc-2.7.2-kmc-own-1234', name: 'Your GCC', kind: 'compiler', use: 'your copy',
                         supported: true, installed: true}]}, 'gcc-2.7.2-kmc', 'n64');
for (const id of ['gcc-2.7.2-kmc', 'pack/1.0', 'gcc-2.7.2-kmc-own-1234']) assert.match(mixed, new RegExp(`value="${id}"`));
assert.equal((mixed.match(/value="gcc-2\.7\.2-kmc"/g) || []).length, 1);
assert.match(mixed, />Pack 1\.0<\/option>/);
