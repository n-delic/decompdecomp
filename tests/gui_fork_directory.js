const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const file = path.join(__dirname, "../src/decompnet/app/static/js/project.js");
const source = fs.readFileSync(file, "utf8").split("/* RetroReversing's directory")[1]
  .split("/* What the project's own files")[0];
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) {
    elements.set(id, {
      value: "", open: true, options: [], hidden: false,
      add(option) { this.options.push(option); },
      addEventListener(event, fn) { this[event] = fn; },
      querySelectorAll(selector) {
        if (selector === ".directory-game img") return [];
        const attribute = selector.slice(1, -1);
        const key = attribute === "data-dir-import" ? "dirImport" : "dirSort";
        return [...this.html.matchAll(new RegExp(attribute + '="([^"]+)"', "g"))]
          .map(match => ({ dataset: { [key]: match[1] } }));
      },
    });
  }
  return elements.get(id);
};
const projects = [
  { name: "Disc game", platform: "ps", platform_name: "PlayStation", repo_url: "https://github.com/a/disc",
    last_commit_at: "2026-10-24" },
  { name: "Cartridge game", platform: "n64", platform_name: "Nintendo 64", repo_url: "https://github.com/a/cart",
    artwork_url: "https://upload.wikimedia.org/example.png" },
];
const calls = [];
let complete, imported;
vm.runInNewContext(source.slice(source.indexOf("let directoryLoading")), {
  $, S: { st: { settings: { rom_library: "/local/library" } } }, Option: class { constructor(name, id) { this.id = id; } },
  setHtml: (el, html) => { el.html = html; el.importButtons = el.querySelectorAll("[data-dir-import]"); },
  esc: value => value, openForkDialog: project => { imported = project; },
  beginWork(log, label, [button]) {
    button.disabled = true;
    log._workStatus = { textContent: label, dataset: {} };
    return () => { button.disabled = false; };
  },
  showJobError() {},
  api: async url => {
    calls.push(url);
    return url.endsWith("/library") ? { job: "scan" } : { projects };
  },
  followJob: () => new Promise(resolve => { complete = resolve; }),
});

// Reuse the same mock buttons so render can attach their real click handlers.
const list = $("decompDirectoryList");
const query = list.querySelectorAll;
list.querySelectorAll = function (selector) {
  if (selector === "[data-dir-import]" && this.previousHtml === this.html) return this.importButtons;
  this.previousHtml = this.html;
  return query.call(this, selector);
};

(async () => {
  await $("decompDirectory").toggle();
  assert.deepEqual($("decompDirectoryPlatform").options.map(option => option.id), ["n64", "ps"]);
  assert.match(list.html, /Disc game.*Cartridge game/s);
  assert.deepEqual(calls, ["/api/fork/directory", "/api/fork/directory/library"]);
  $("decompDirectoryPlatform").value = "n64";
  $("decompDirectoryPlatform").onchange();
  assert.doesNotMatch(list.html, /Disc game|ROM available/);
  assert.match(list.html, /<img src="https:\/\/upload.wikimedia.org\/example.png"/);
  assert.equal($("decompDirectoryCount").textContent, "1 of 2 decompilations");
  list.importButtons[0].onclick();
  assert.equal(imported.repo_url, projects[1].repo_url);
  await new Promise(resolve => setImmediate(resolve));
  complete({ matches: [["n64", projects[1].repo_url]], message: "Checked" });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(list.html, /Cartridge game.*ROM available/);
  await $("decompDirectory").toggle();
  assert.equal(calls.length, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
