// Exercise the actual Settings handler with a small DOM and a fake settings API.
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const nodes = new Map();
class Element {
  constructor() { this.checked = false; }
  querySelectorAll() { return []; }
  set innerHTML(text) {
    for (const [, id, tail] of text.matchAll(/<input[^>]*id="([^"]+)"([^>]*)>/g)) {
      const input = new Element();
      input.checked = /\bchecked\b/.test(tail);
      nodes.set(id, input);
    }
  }
}
const $ = id => {
  if (!nodes.has(id)) nodes.set(id, new Element());
  return nodes.get(id);
};
const st = {settings: {match_diagnostics: false}, dev: true};
const requests = [];
const context = {
  $, S: {st}, loadState: async () => st, show() {}, savedTheme: () => "system", THEMES: [],
  cmdBox: String, esc: String, wireCopies() {}, toast() {},
  api: async (url, request) => {
    assert.equal(url, "/api/settings");
    assert.equal(request.method, "POST");
    assert.equal(typeof request.body.match_diagnostics, "boolean");
    requests.push(request.body.match_diagnostics);
    return Object.assign(st.settings, request.body);
  }
};
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/pages.js"), "utf8");
const start = source.indexOf("async function showSettingsPage()");
const end = source.indexOf("/* ------------------------------------------------------------------ updates", start);
const showSettings = vm.runInNewContext(source.slice(start, end) + "\nshowSettingsPage", context);
(async () => {
  await showSettings();
  assert.equal($("matchDiagnosticsSet").checked, false);
  $("matchDiagnosticsSet").checked = true;
  await $("matchDiagnosticsSet").onchange();
  await showSettings();
  assert.equal($("matchDiagnosticsSet").checked, true);
  $("matchDiagnosticsSet").checked = false;
  await $("matchDiagnosticsSet").onchange();
  await showSettings();
  assert.equal($("matchDiagnosticsSet").checked, false);
  assert.deepEqual(requests, [true, false]);
})().catch(error => { console.error(error); process.exit(1); });
