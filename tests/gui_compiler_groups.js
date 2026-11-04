"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const ctx = {}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/compiler-groups.js', 'utf8'), ctx);
const parts = [
 {start: 30, end: 40, compiler: 'GCC A', toolchain: 'a', family: 'gcc', flags: '-O2 -g', functions: 2},
 {start: 10, end: 20, compiler: 'GCC A', toolchain: 'a', family: 'gcc', flags: '-O2 -g', functions: 1},
 {start: 50, end: 60, compiler: 'GCC A', toolchain: 'a', family: 'gcc', flags: '-g -O2', functions: 3},
 {start: 70, end: 80, compiler: 'GCC B', toolchain: 'b', family: 'gcc', flags: '-O2 -g', functions: 4},
];
let groups = ctx.compilerPartGroups(parts);
assert.equal(groups.length, 3); assert.equal(groups[0].parts.length, 2); assert.equal(groups[0].functions, 3);
assert.equal(groups[0].parts[0].start, 10);
groups = ctx.compilerPartGroups(parts, 'family');
assert.equal(groups.length, 1); assert.equal(groups[0].profiles, 3); assert.equal(groups[0].functions, 10);
assert.equal(ctx.compilerPartGroups(parts, 'range', 'address-desc')[0].start, 70);
assert.equal(parts[0].start, 30); // grouping never mutates the source configuration
assert.equal(ctx.compilerPartGroups([], 'settings').length, 0);
assert.equal(ctx.compilerPartGroups([{start:0,end:1,compiler:'Custom',flags:''}])[0].functions, null);
console.log('compiler groups passed');

// ROM overview responses omit kind; the profile migration action must still appear.
const elements = new Map();
ctx.$ = id => { if (!elements.has(id)) elements.set(id, {value:id === 'compilerGroup' ? 'settings' : 'most-ranges', querySelectorAll:()=>[]}); return elements.get(id); };
ctx.S = {slug:'demo', ov:{}};
ctx.esc = String; ctx.hexShort = String; ctx.hex8 = String;
ctx.plural = (n, word) => `${n} ${word}`;
ctx.PART_STATE = {'':['waiting','']};
ctx.setHtml = (el, html) => { el.innerHTML = html; };
const steering = fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
vm.runInContext(steering.slice(steering.indexOf('function compilerFunctionLinks'), steering.indexOf('for (const id of ["compilerGroup"')), ctx);
ctx.renderCompilerParts({parts});
assert.match(elements.get('compilerParts').innerHTML, /Compare all matches with default/);
assert.match(elements.get('compilerParts').innerHTML, /Test this profile as default/);
ctx.S.ov.kind = 'fork';
ctx.renderCompilerParts({parts});
assert.doesNotMatch(elements.get('compilerParts').innerHTML, /Compare all matches with default/);

assert.doesNotMatch(elements.get('compilerParts').innerHTML, /Test this profile as default/);
