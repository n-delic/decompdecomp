"use strict";
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('src/decompnet/app/static/js/project.js', 'utf8');
const pollSource = source.slice(source.indexOf('let pollingGen ='), source.indexOf('let statsLoading ='));
const counts = {sessions: 0, claims: 0, stats: 0, map: 0};
let response;
const ctx = {S: {gen: 1, slug: 'game', primed: true, tab: 'settings', lastEvent: 0, lastAttempt: 0,
  seq: {}, scores: new Map(), flash: new Map(), sessions: [], claims: [], lastMapFetch: 0, fn: null},
  document: {hidden: false}, window: {}, Date, JSON, Map, performance,
  api: async () => structuredClone(response),
  addActivity() {}, addThoughts() {}, applyAttempts() {}, applyClaims() {}, renderList() {},
  renderSessions() { counts.sessions++; }, renderNow() { counts.claims++; }, renderStats() { counts.stats++; },
  refreshMap: async () => { counts.map++; }, MAP_EVERY_MS: 20000};
vm.createContext(ctx); vm.runInContext(pollSource, ctx);
(async () => {
  response = {overview: {matched: 2}, claims: [], sessions: [], events: [], attempts: [], versions: {overview: 'one'}};
  await ctx.poll();
  response = {events: [{id: 1, kind: 'rename'}], attempts: [], versions: {overview: 'one'}};
  await ctx.poll();
  assert.equal(counts.stats, 1); assert.equal(counts.claims, 1); assert.equal(counts.map, 0);
  assert.equal(ctx.S.ov.matched, 2); assert.equal(ctx.S.mapPending, true);
  ctx.S.tab = 'overview'; response.events = [];
  await ctx.poll(); assert.equal(counts.map, 1); assert.equal(ctx.S.mapPending, false);
  console.log('compact polls preserve state and defer hidden map work');
})().catch(e => { console.error(e); process.exitCode = 1; });
