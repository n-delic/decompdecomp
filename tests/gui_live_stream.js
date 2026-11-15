"use strict";
/* The project page's live schedule (project.js): /api/stream's news asks /api/live for only the sections that moved,
   a page without a stream polls and slows down when nothing changes or the server fails, and a hidden window asks
   nothing. Also the function list's age refresh (function.js), which looked for cells renderList never made. */
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync("src/decompnet/app/static/js/project.js", "utf8");
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));

const asked = [], timers = [], listeners = {};
let answer = () => ({ events: [], attempts: [], versions: {} }), streams = [];
class FakeEventSource {
  constructor(url) { this.url = url; this.readyState = 0; this.handlers = {}; streams.push(this); }
  addEventListener(kind, fn) { this.handlers[kind] = fn; }
  emit(kind, data) { (this.handlers[kind] || (() => {}))({ data: JSON.stringify(data) }); }
  close() { this.readyState = 2; this.closed = true; }
}
const ctx = {
  S: { gen: 1, slug: "game", primed: true, tab: "settings", lastEvent: 0, lastAttempt: 0, seq: {}, scores: new Map(),
    flash: new Map(), sessions: [], claims: [], lastMapFetch: Date.now(), fn: null, mapSig: "s" },
  document: { hidden: false, addEventListener: (kind, fn) => { listeners[kind] = fn; } },
  window: {}, Date, JSON, Map, Set, Math, Array, performance, encodeURIComponent, EventSource: FakeEventSource,
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {},
  api: async (url) => { asked.push(url); const a = answer(url); if (a instanceof Error) throw a; return structuredClone(a); },
  addActivity() {}, addThoughts() {}, applyAttempts() {}, applyClaims() {}, renderList() {}, renderListSoon() {},
  renderSessions() {}, renderNow() {}, renderStats() {}, refreshMap: async () => {}, MAP_EVERY_MS: 1e9,
  $: () => ({ hidden: true }),
};
vm.createContext(ctx);
vm.runInContext(slice("const POLL_MS", "let projectLoad"), ctx);
vm.runInContext(slice("function stopPolling()", "function setTab("), ctx);
vm.runInContext(slice("let pollingGen =", "let statsLoading ="), ctx);
const lastTimer = () => timers[timers.length - 1];
const settle = () => new Promise((r) => setImmediate(r));

(async () => {
  // a stream: hello asks once for everything, then each "changed" asks for the sections it names
  vm.runInContext("LIVEFEED.gen = S.gen; startLive()", ctx);
  assert.equal(streams.length, 1);
  assert.ok(streams[0].url.startsWith("/api/stream?slug=game"));
  streams[0].emit("hello", { sections: ["feed"], event: 0 });
  await settle(); await settle();
  assert.ok(asked.at(-1).startsWith("/api/live?slug=game") && !asked.at(-1).includes("only="));
  assert.equal(lastTimer().ms, 15000, "a healthy stream leaves only the safety poll");
  streams[0].emit("changed", { sections: ["feed", "map"], event: 3 });
  await settle(); await settle();
  assert.ok(asked.at(-1).includes("&only=feed,claims"), asked.at(-1));
  streams[0].emit("changed", { sections: ["overview"], event: 3 });
  await settle(); await settle();
  assert.ok(asked.at(-1).endsWith("&only=overview"), asked.at(-1));

  // the stream breaks for good: polls again, at once
  streams[0].readyState = 2;
  streams[0].onerror();
  assert.ok(streams[0].closed);
  assert.equal(lastTimer().ms, 1000);

  // no stream: quiet polls slow down to 5 s; errors back off further; news brings it back to 1 s
  vm.runInContext("stopPolling(); LIVEFEED.noStream = true; startLive()", ctx);
  const delays = [];
  for (let i = 0; i < 6; i++) { await vm.runInContext("liveTick()", ctx); delays.push(lastTimer().ms); }
  assert.deepEqual(delays, [1500, 2250, 3375, 5000, 5000, 5000]);
  answer = () => new Error("busy");
  await vm.runInContext("liveTick()", ctx); await vm.runInContext("liveTick()", ctx);
  assert.equal(lastTimer().ms, 15000);
  answer = () => ({ events: [{ id: 9, kind: "claim", detail: "f" }], attempts: [], versions: {} });
  await vm.runInContext("liveTick()", ctx);
  assert.equal(lastTimer().ms, 1000);

  // a hidden window asks nothing and closes its stream
  vm.runInContext("LIVEFEED.noStream = false; openStream()", ctx);
  const open = streams.at(-1);
  ctx.document.hidden = true;
  listeners.visibilitychange();
  assert.ok(open.closed);
  const before = asked.length;
  await vm.runInContext("liveTick()", ctx);
  assert.equal(asked.length, before);
  assert.equal(lastTimer().ms, 5000);
  ctx.document.hidden = false;

  // the age refresh finds the cells renderList draws (it threw a TypeError every 15 s)
  const fn = fs.readFileSync("src/decompnet/app/static/js/function.js", "utf8");
  assert.ok(fn.includes('class="fn-age-improved"') && fn.includes('class="fn-age-checked"'));
  console.log("the live feed follows the stream, and polls with backoff without one");
})().catch((e) => { console.error(e); process.exitCode = 1; });
