// Exercise page-to-native theme synchronization without opening a desktop window.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const listeners = {}, calls = [], classes = new Set();
let mutation, systemChange;
let background = "oklch(light)";
const palette = {
  "oklch(light)": [250, 249, 245, 255], "oklch(dark)": [34, 33, 30, 255],
  "oklch(ink)": [29, 27, 20, 255], "oklch(hover)": [240, 239, 230, 255], "oklch(danger)": [196, 43, 28, 255],
};
const pixel = {
  fillRect() {},
  getImageData() { return { data: palette[this.fillStyle] }; },
};
const context = {
  URLSearchParams, location: { search: "?native=1" },
  document: {
    body: {}, documentElement: { classList: { add: (name) => classes.add(name) } },
    createElement: () => ({ getContext: () => pixel }),
  },
  getComputedStyle: () => ({ backgroundColor: background, color: "oklch(ink)",
    getPropertyValue: (name) => name.includes("sunken") ? "oklch(hover)" : "oklch(danger)" }),
  MutationObserver: class { constructor(callback) { mutation = callback; } observe() {} },
  matchMedia: () => ({ addEventListener: (_, callback) => { systemChange = callback; } }),
  addEventListener: (name, callback) => { listeners[name] = callback; },
  pywebview: { api: { set_theme: (...colors) => calls.push(colors) } },
};
context.window = context;
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/base.js"), "utf8");
vm.runInContext(source.split("/* A list the live feed")[0], context);
assert(classes.has("windows-window"));
listeners.pywebviewready();
assert.deepEqual(calls[0], ["#faf9f5", "#1d1b14", "#f0efe6", "#c42b1c"]);
background = "oklch(dark)";
mutation();
assert.equal(calls[1][0], "#22211e");
background = "oklch(light)";
systemChange();
assert.equal(calls[2][0], "#faf9f5");
