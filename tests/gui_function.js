// The function drawer's latest/best switch, in node against a stand-in page (tests/gui_polls.js's way): a fork's
// function opens on its best attempt, keeps that choice while it stays open, and switches to the source now after a
// manual compile; a matched function or one with no best has nothing to switch.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function classList() {
  const names = new Set();
  return {
    toggle(name, on) { if (on) names.add(name); else names.delete(name); },
    add(name) { names.add(name); },
    remove(name) { names.delete(name); },
    contains(name) { return names.has(name); },
  };
}
function element(id) {
  const ctx = {
    clearRect() {}, fillRect() {}, getImageData: () => ({ data: [0, 0, 0, 255] }),
  };
  return {
    id, hidden: false, textContent: "", innerHTML: "", className: "", value: "", disabled: false,
    dataset: {}, style: {}, classList: classList(), scrollTop: 0, scrollLeft: 0,
    setAttribute() {}, removeAttribute() {}, append() {}, prepend() {}, addEventListener() {}, remove() {},
    focus() {}, contains: () => true, querySelector: () => element("x"), querySelectorAll: () => [],
    getContext: () => ctx, scrollTo() {}, appendChild() {}, insertBefore() {}, removeChild() {},
    getBoundingClientRect: () => ({ left: 0, top: 0 }), children: [], lastChild: null,
  };
}
const elements = new Map();
const get = (id) => { if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); };
const buttons = ["latest", "best"].map((which) => { const b = element(which); b.dataset.which = which; return b; });
const document = {
  getElementById: get,
  querySelectorAll: (selector) => (selector === "#diffWhich [data-which]" ? buttons : []),
  querySelector: (selector) => (selector === '#diffWhich [data-which="latest"]' ? buttons[0] : null),
  createElement: () => element("created"),
  addEventListener() {}, body: element("body"), documentElement: element("root"), hidden: false,
  activeElement: element("active"),
};

const asked = [];
let detail = {
  name: "f1", status: "open", best: 20, best_differs: true, insns: 10, unit: "src/a.c",
  source: "void f1(void) {}", source_kind: "latest", asm: "", not_c: false,
  scores: [{ score: 20, t: 1, agent: "a" }, { score: 40, t: 2, agent: "b" }],
  notes: [], calls: [], called_by: [],
};
const answers = {
  "/api/state": () => ({ version: "t", settings: {}, agents: [], toolchains: [], consoles: [] }),
  "/api/projects": () => [], "/api/events": () => ({ last: 0, events: [] }),
  "/api/function": () => detail,
  "/api/diff": (url) => ({ kind: new URL("http://x" + url).searchParams.get("which"), rows: [[" ", "a", "a"]],
    percent: 50, score: null, problems: [] }),
  "/api/try": () => ({ matched: false, score: 30, text: "score 30" }),
  "/api/release": () => ({}),
};
function fetch(url) {
  asked.push(url);
  const prefix = Object.keys(answers).sort((a, b) => b.length - a.length).find((p) => url.startsWith(p));
  const body = prefix ? answers[prefix](url) : {};
  return Promise.resolve({ ok: true, json: async () => body });
}
const ctx = {
  console, fetch, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number, Symbol, Error, RegExp,
  Uint8Array, URLSearchParams,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: () => 0, performance: { now: () => 0 },
  navigator: { clipboard: { writeText: async () => {} }, platform: "test" },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ color: "", getPropertyValue: () => "" }),
  location: { hash: "", search: "", href: "" }, confirm: () => false,
  addEventListener: () => {}, scrollTo: () => {}, devicePixelRatio: 1, document,
};
ctx.window = ctx;
vm.createContext(ctx);
const js = process.env.GUI_JS || path.join(__dirname, "..", "src", "decompnet", "app", "static", "js");
for (const name of ["base", "sidebar", "home", "project", "map", "agents", "function", "steering", "pages"]) {
  vm.runInContext(fs.readFileSync(path.join(js, `${name}.js`), "utf8"), ctx, { filename: `${name}.js` });
}
ctx.drawMap = () => {};
const settle = async () => { for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r)); };
const which = () => vm.runInContext("S.which", ctx);

(async () => {
  vm.runInContext(`S.slug = "demo"; S.ov = { kind: "fork", priority: [] }; S.claims = []; S.map = [];
    S.byName = new Map(); S.fn = null; S.ftab = null; S.editDirty = false; S.myClaim = null;`, ctx);

  await ctx.openFn("f1");
  await settle();
  assert.equal(which(), "best");
  assert.equal(get("diffWhich").hidden, false);
  assert.equal(buttons[0].classList.contains("on"), false);
  assert.equal(buttons[1].classList.contains("on"), true);
  assert.ok(asked.some((url) => url.includes("/api/diff") && url.includes("which=best")), asked.join(" "));

  // The same function's refresh keeps the choice (a live poll calls refreshFn(false)).
  await ctx.refreshFn(false);
  await settle();
  assert.equal(which(), "best");

  // A manual compile shows what was just compiled: the source now.
  get("fnEdit").value = "void f1(void) { return; }";
  await ctx.tryIt();
  await settle();
  assert.equal(which(), "latest");
  assert.equal(buttons[0].classList.contains("on"), true);
  assert.ok(asked.some((url) => url.includes("/api/diff") && url.includes("which=latest")), asked.join(" "));

  // A matched function has nothing to switch between.
  detail = { ...detail, name: "f2", status: "matched", best: 0, best_differs: false, source_kind: "matched" };
  await ctx.openFn("f2");
  await settle();
  assert.equal(get("diffWhich").hidden, true);
  assert.equal(which(), "latest");

  // Nor does one with no best attempt yet.
  detail = { ...detail, name: "f3", status: "open", best: null, best_differs: false, source_kind: "latest",
    scores: [{ score: null, t: 1, agent: "a" }] };
  await ctx.openFn("f3");
  await settle();
  assert.equal(get("diffWhich").hidden, true);
  assert.equal(which(), "latest");
  // its badge says it was tried, not "not started" (a report: 12 compiles, none built, said "not started")
  assert.equal(get("fnStatus").textContent, "1 compile, none built");

  // A function nobody compiled is not started.
  detail = { ...detail, name: "f4", scores: [] };
  await ctx.openFn("f4");
  await settle();
  assert.equal(get("fnStatus").textContent, "not started");

  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
