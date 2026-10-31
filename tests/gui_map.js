// A big game's project page while agents work, in node against a stand-in page (tests/gui_polls.js's way): what each
// poll redraws on the map, and that the polls don't pile up or run for a minimised window. No browser, no game data.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const FUNCTIONS = 2000;
function stub() {
  const props = {};
  return new Proxy(function () { return stub(); }, {
    get(_, key) {
      if (key in props) return props[key];
      if (key === Symbol.toPrimitive) return () => "";
      if (key === Symbol.iterator) return function* () {};
      if (key === "then") return undefined;
      if (key === "clientWidth") return 600;  // on screen: the map is laid out
      if (["width", "height", "length", "scrollHeight"].includes(key)) return 0;
      if (key === "hidden") return false;
      return (props[key] = stub());
    },
    set(_, key, value) { props[key] = value; return true; },
    apply() { return stub(); },
  });
}
// a canvas whose drawing calls are counted: `base` (the still cells) and the map shown on the page
const drawn = { base: [], map: [] };
function canvas(name) {
  const ctx = new Proxy({}, {
    get(_, key) {
      if (key === "getImageData") return () => ({ data: [1, 2, 3, 255] });
      return (...args) => { drawn[name].push([key, args.length]); };
    },
    set() { return true; },
  });
  return { width: 0, height: 0, style: {}, getContext: () => ctx, getBoundingClientRect: () => ({ left: 0, top: 0 }), addEventListener() {} };
}
const page = canvas("map");
const document = stub();
document.createElement = (tag) => (tag === "canvas" ? canvas("base") : stub());
const elements = new Map([["map", page]]);
document.getElementById = (id) => { if (!elements.has(id)) elements.set(id, stub()); return elements.get(id); };

const keys = ["name", "vram", "size", "insns", "unit", "status", "best", "attempts", "agent", "matched_at", "matched_by", "not_c", "polished"];
const rows = Array.from({ length: FUNCTIONS }, (_, i) => [`f${i}`, 0x1000 + 16 * i, 16, 4, "", "open", null, 0, null, null, null, false, false]);
const ov = { slug: "a", title: "a", kind: "rom", platform: "ps2", platform_name: "PlayStation 2", ready: true, rom: { game_code: "X" },
  compiler: { name: "gcc", flags: [], installed: true, supported: true, missing: [] }, functions: FUNCTIONS, matched: 0, bytes: 1,
  matched_bytes: 0, attempts: 0, priority: [], suggestions: [], boundaries: [], compiler_ranges: [], compilers: [] };
let live = { overview: ov, events: [], attempts: [], claims: [], sessions: [], cut: false };
const asked = [], held = [];
let hold = null;
const answers = {
  "/api/state": () => ({ version: "t", settings: {}, agents: [], toolchains: [], consoles: [] }),
  "/api/projects": () => [], "/api/mapcolumns": () => ({ keys: ["vram", "cc", "ccx"], rows: [] }),
  "/api/stats": () => ({ agents: [], matches_per_hour: new Array(24).fill(0), compiles_last_hour: 0 }),
  "/api/knowledge": () => ({ auto: false, items: [] }), "/api/events": () => ({ last: 0, events: [] }),
  "/api/project?slug=": () => ov, "/api/map?slug=": () => ({ sig: "s", keys, rows }), "/api/live?slug=": () => live,
};
function fetch(url) {
  asked.push(url);
  const prefix = Object.keys(answers).sort((a, b) => b.length - a.length).find((p) => url.startsWith(p));
  const res = { ok: true, json: async () => (prefix ? answers[prefix](url) : {}) };
  if (hold && url.startsWith(hold)) { hold = null; return new Promise((resolve) => held.push(() => resolve(res))); }
  return Promise.resolve(res);
}
const ctx = {
  console, fetch, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number, Symbol, Error, RegExp, Uint8Array, URLSearchParams,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {}, requestAnimationFrame: () => 0,
  performance: { now: () => 0 }, navigator: stub(), localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => stub(), getComputedStyle: () => stub(), location: { hash: "", href: "" }, confirm: () => false,
  addEventListener: () => {}, scrollTo: () => {}, devicePixelRatio: 1, document,
};
ctx.window = ctx;
vm.createContext(ctx);
const js = process.env.GUI_JS || path.join(__dirname, "..", "src", "decompnet", "app", "static", "js");
for (const name of ["base", "sidebar", "home", "project", "map", "agents", "function", "steering", "pages"]) {
  vm.runInContext(fs.readFileSync(path.join(js, `${name}.js`), "utf8"), ctx, { filename: `${name}.js` });
}
const settle = async () => { for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r)); };
const fills = (name) => drawn[name].filter(([k]) => k === "fill").length;
const reset = () => { drawn.base.length = 0; drawn.map.length = 0; };

(async () => {
  await ctx.openProject("a");
  await settle();
  // the first drawing: every cell once
  assert.equal(fills("base"), FUNCTIONS);

  // a poll that brings a better score for one function and a claim on another: those two cells, not all 2,000
  reset();
  live = { ...live, attempts: [{ id: 1, function: "f7", score: 3, created_at: 0, n_insns: 4 }],
    claims: [{ name: "f9", agent: "claude", n_insns: 4 }] };
  await ctx.poll();
  await settle();
  assert.equal(fills("base"), 1);  // f7, in its new colour (f9 is working: it pulses on the page, not in `base`)
  assert.equal(drawn.base.filter(([k]) => k === "clearRect").length, 2);
  assert.ok(drawn.map.some(([k, n]) => k === "drawImage" && n === 3), "the page shows the changed base whole once");

  // a frame where only f9 pulses: f9's square comes back from `base`, the whole map isn't copied again
  reset();
  vm.runInContext("drawMap()", ctx);
  assert.deepEqual(drawn.map.filter(([k]) => k === "drawImage").map(([, n]) => n), [9]);
  assert.equal(fills("map"), 1);
  assert.equal(fills("base"), 0);

  // a match's flash rings reach over the neighbours: those frames draw the whole map
  reset();
  vm.runInContext("S.flash.set('f3', performance.now()); drawMap()", ctx);
  assert.ok(drawn.map.some(([k, n]) => k === "drawImage" && n === 3));

  // a poll that couldn't bring every attempt (`cut`): the whole map comes again
  live = { ...live, attempts: [], cut: true };
  asked.length = 0;
  await ctx.poll();
  await settle();
  assert.ok(asked.some((u) => u.startsWith("/api/map?slug=a") && !u.includes("since=")), asked.join(" "));
  live = { ...live, cut: false };

  // a minimised window: no live poll, no stats
  document.hidden = true;
  asked.length = 0;
  await ctx.poll();
  await vm.runInContext("loadStats()", ctx);
  await settle();
  assert.deepEqual(asked, []);
  document.hidden = false;

  // the project list: the timer's asks wait for the one under way; one asked for meanwhile (a rename) goes after it
  asked.length = 0;
  hold = "/api/projects";
  ctx.loadNav(true);
  ctx.loadNav(true);
  ctx.loadNav(true);
  await settle();
  assert.equal(asked.filter((u) => u === "/api/projects").length, 1);
  ctx.loadNav();
  held.shift()();
  await settle();
  assert.equal(asked.filter((u) => u === "/api/projects").length, 2);

  // the stats: one ask at a time
  asked.length = 0;
  hold = "/api/stats";
  vm.runInContext("loadStats(); loadStats()", ctx);
  await settle();
  assert.equal(asked.filter((u) => u.startsWith("/api/stats")).length, 1);
  held.shift()();
  await settle();
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
