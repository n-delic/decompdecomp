'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('src/decompnet/app/static/js/project.js', 'utf8');
let stored = null, html = '';
const controls = {'[name="mode"]:checked': {value:'address'}, '#addrIn': {value:'https://rooms.example.org'}, '#addrPort': {value:'6482'}};
const box = {querySelector: key => controls[key]};
const done = new Error('rendered');
const ctx = {localStorage: {getItem: () => stored, setItem: (_, value) => {stored = value;}},
  S: {ov:{}}, ROOM_PORT:6481, esc: String, teamJoinHtml:()=>'', setHtml: (_, value) => {html = value; throw done;}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function roomAddressPreferences('), source.indexOf('function renderHost(')), ctx);
const start = source.indexOf('function renderHost(');
// Stop at the next top-level function; the renderer itself is exercised through its initial markup.
const end = source.indexOf('\nfunction ', start + 10);
vm.runInContext(source.slice(start, end), ctx);
const render = info => {try {ctx.renderHost({}, {settings:{}, ...info});} catch(e) {if(e !== done) throw e;}};
ctx.rememberRoomAddress(box);
render({});
assert.match(html, /value="https:\/\/rooms.example.org"/);
assert.match(html, /value="6482"/);
assert.match(html, /value="address" checked/);
render({address:'https://project.example.org', port:6483, mode:'address'});
assert.match(html, /value="https:\/\/project.example.org"/);
assert.match(html, /value="6483"/);
stored = '{invalid'; render({});
assert.match(html, /value="6481"/);
ctx.localStorage.getItem = () => {throw Error('disabled');}; render({});
ctx.localStorage.setItem = () => {throw Error('disabled');}; ctx.rememberRoomAddress(box);
console.log('Room address preferences passed');
