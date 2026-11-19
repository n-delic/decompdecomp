// The function map's own palette: map-only CSS overrides, settings pickers and reset, in node against a stand-in DOM.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const defaults = Object.fromEntries([
  "open", "tried", "close", "work", "match", "polished", "aside", "not-c", "ink",
  ...Array.from({ length: 8 }, (_, i) => `cc-${i}`),
].map((key, i) => [`--map-${key}`, `rgb(${i + 1}, ${i + 2}, ${i + 3})`]));
const hex = (value) => "#" + value.slice(4, -1).split(",").map((n) => Number(n.trim()).toString(16).padStart(2, "0")).join("");
const rgb = (value) => `rgb(${parseInt(value.slice(1, 3), 16)}, ${parseInt(value.slice(3, 5), 16)}, ${parseInt(value.slice(5, 7), 16)})`;

class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.dataset = {}; this.value = ""; this.hidden = false;
    this.disabled = false; this.isConnected = true; this.innerHTML = ""; this.textContent = "";
    this.style = { props: {}, setProperty(name, value) { this.props[name] = value; }, removeProperty(name) { delete this.props[name]; } };
  }
  append(...nodes) { this.children.push(...nodes); }
  remove() { this.isConnected = false; }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelectorAll(selector) { return selector === "[data-map-color]" ? (this._inputs || []) : []; }
  addEventListener() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 600 }; }
}
const swatch = {
  fillStyle: "",
  clearRect() {}, fillRect() {},
  getImageData() { return { data: this.fillStyle.match(/\d+/g).slice(0, 3).map(Number) }; },
};
const document = {
  body: new Element("body"),
  documentElement: new Element("html"),
  createElement(tag) {
    if (tag === "canvas") return { width: 0, height: 0, style: {}, getContext: () => swatch };
    return new Element(tag);
  },
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
};
const elements = new Map();
const S = { map: [], byName: new Map(), flash: new Map(), tab: "overview", st: { settings: {} } };
const ctx = {
  console, document, S, window: {}, Map, Set, Promise, JSON, Math, Object, Array, String, Number,
  baseDirty: true, base: null, moving: [], dirtyCells: new Set(),
  getComputedStyle: (probe) => {
    const name = probe.style.color.match(/var\((--[^)]+)\)/)[1];
    const override = document.documentElement.style.props[name];
    return { color: override ? rgb(override) : defaults[name] || "rgb(1, 2, 3)" };
  },
  $: (id) => document.getElementById(id),
  esc: (value) => String(value), setHtml: (el, html) => { el.innerHTML = html; el._html = html; },
  api: async () => ({}), toast() {}, matchMedia: () => ({ addEventListener() {} }),
  loadNav() {}, route() {}, pollEvents() {}, checkUpdate() {},
  addEventListener() {}, setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval() {},
  location: { hash: "" }, localStorage: { getItem: () => null },
};
ctx.window = ctx;
vm.createContext(ctx);
const js = path.join(__dirname, "..", "src", "decompnet", "app", "static", "js");
for (const name of ["map", "pages"]) vm.runInContext(fs.readFileSync(path.join(js, `${name}.js`), "utf8"), ctx, { filename: `${name}.js` });

(async () => {
  const choices = ctx.window.mapColorChoices();
  assert.equal(choices.length, 17);
  assert.equal(choices.slice(0, 3).map((c) => c.key).join(","), "open,tried,close");
  assert.equal(choices[0].value, hex(defaults["--map-open"]));
  assert.equal(choices.at(-1).key, "cc7");

  document.getElementById("mapColorsBtn").onclick();
  assert.equal(ctx.location.hash, "#/settings");

  const markup = ctx.renderMapColorSettings();
  for (const choice of choices) {
    assert.ok(markup.includes(`data-map-color="${choice.key}"`), choice.key);
    assert.ok(markup.includes(`value="${choice.value}"`), choice.key);
    assert.ok(markup.includes(choice.label), choice.label);
  }
  assert.ok(markup.includes('id="mapColorsReset"'));
  assert.ok(!markup.includes("style="), "the CSP forbids inline styles");

  const root = document.getElementById("setMapColors"), reset = document.getElementById("mapColorsReset");
  ctx.setHtml(root, markup);
  root._inputs = choices.map((choice) => {
    const input = new Element("input");
    input.dataset.mapColor = choice.key; input.value = choice.value;
    return input;
  });
  const saved = [];
  ctx.wireMapColorSettings((body, message) => { saved.push([body, message]); ctx.S.st.settings = body; return Promise.resolve(); });

  root._inputs[0].value = "#ff0000";
  root._inputs[0].onchange();
  assert.equal(saved[0][0].map_colors.open, "#ff0000");
  assert.equal(document.documentElement.style.props["--map-open"], "#ff0000");
  assert.equal(ctx.window.mapColorChoices()[0].value, "#ff0000");
  assert.equal(document.documentElement.style.props["--map-match"], choices.find((c) => c.key === "match").value, "saved palette keeps every state explicit");
  vm.runInContext("readColors()", ctx);
  assert.equal(vm.runInContext("cellColor({status: 'open'})[0].join(',')", ctx), "255,0,0");

  reset.onclick();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(Object.keys(document.documentElement.style.props).length, 0);
  assert.equal(JSON.stringify(saved.at(-1)[0]), JSON.stringify({ map_colors: {} }));
  assert.equal(ctx.window.mapColorChoices()[0].value, hex(defaults["--map-open"]));
  vm.runInContext("readColors()", ctx);
  assert.equal(vm.runInContext("cellColor({status: 'open'})[0].join(',')", ctx), "1,2,3");
  console.log("function map colour checks passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
