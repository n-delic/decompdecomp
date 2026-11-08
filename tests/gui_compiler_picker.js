'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/decompnet/app/static/js/home.js', 'utf8');
const start = source.indexOf('function fillCompilers(');
const end = source.indexOf('\nasync function runImport(', start);
const consoleEntry = {compilers: [
  {id: 'MSVC/6.0', name: 'Visual C++ 6.0', built: '6.0'},
  {id: 'MSVC/own-9.0.30729', name: 'Your Visual C++', built: '9.0.30729'},
]};
const context = vm.createContext({
  consoleOf: () => consoleEntry,
  S: {st: {toolchains: [], compiler_choices: [
    {id: 'MSVC/own-9.0.30729', name: 'Your Visual C++', kind: 'compiler',
     use: '9.0.30729', supported: true, installed: true},
  ]}},
  esc: String,
});
vm.runInContext(source.slice(start, end), context);
const select = {innerHTML: ''};
context.fillCompilers(select, 'MSVC/6.0', 'pc');
assert.match(select.innerHTML, /MSVC\/6\.0/);
assert.match(select.innerHTML, /MSVC\/own-9\.0\.30729/);
assert.match(select.innerHTML, /value="MSVC\/6\.0" selected/);
