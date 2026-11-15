'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {hidden: false, textContent: '', style: {}, firstElementChild: {style: {}}});
  return elements.get(id);
};
let calls = [], finish;
const ctx = {$, Blob, ArrayBuffer, Date, encodeURIComponent, setInterval, clearInterval,
  uploadFile: async (url, body) => {calls.push({url, body}); return {job: 1};},
  api: async (url, options) => {calls.push({url, options}); return {job: 1};},
  followJob: async () => new Promise(resolve => {finish = resolve;}),
  loadNav() {}, location: {}, esc: String, toast() {}};
vm.createContext(ctx);
const source = fs.readFileSync('src/decompnet/app/static/js/home.js', 'utf8');
vm.runInContext(source.slice(source.indexOf('let importRunning ='), source.indexOf('async function ensureCodeWarrior')), ctx);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const blob = new Blob(['game', 'debug symbols']);
  const query = 'confirmed=1&toolchain=chosen&scope=program&extras=debug&options=custom';
  const first = ctx.runImport(query, blob, 'Analysing Game…', 'game.z64');
  await tick();
  // A second start cannot upload or launch another import while the first is active.
  await ctx.runImport(query, blob, 'Analysing Game…', 'game.z64');
  assert.equal(calls.length, 1);
  finish(Promise.reject(new Error('Install Colima')));
  await first;
  assert.equal($('dropError').hidden, false);
  assert.equal($('errRetry').hidden, false);
  const retry = $('errRetry').onclick({stopPropagation() {}});
  assert.equal($('dropError').hidden, true);
  assert.equal($('errRetry').onclick, null);
  await tick();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, calls[1].url);
  assert.equal(calls[1].body, blob);
  finish({slug: 'imported'}); await retry;
  assert.equal(ctx.location.hash, '#/p/imported');
  calls = [];
  const local = ctx.runImport('confirmed=1&path=%2Fgames%2Fgame.iso', undefined, 'Analysing your game…');
  await tick(); finish(Promise.reject(new Error('Missing tool'))); await local;
  const again = $('errRetry').onclick({stopPropagation() {}});
  await tick(); assert.equal(calls[0].url, calls[1].url);
  finish({slug: 'local'}); await again;
  ctx.importFailed('game', new Error('Invalid file'), '', undefined);
  assert.equal($('errRetry').hidden, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
