"use strict";
const assert = require("assert"), fs = require("fs"), path = require("path"), vm = require("vm");
const elements = new Map();
const element = () => ({ value: "", checked: false, hidden: false, disabled: false, textContent: "", innerHTML: "", dataset: {} });
const get = selector => { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); };
let inputs = [];
const host = { _html: "", set innerHTML(html) {
  this._html = html;
  inputs = [...html.matchAll(/<input type="checkbox" data-local="(\d+)"([^>]*)>/g)].map(m => ({ ...element(), dataset: { local: m[1] }, checked: m[2].includes("checked"), disabled: m[2].includes("disabled") }));
}, get innerHTML() { return this._html; }, querySelectorAll: () => inputs };
elements.set("[data-project-groups]", host);
const box = { innerHTML: "", querySelector: get, querySelectorAll: () => [] };
let importedPaths;
const ctx = { window: {addEventListener() {}}, console, Set, Map, Array, Number, String, encodeURIComponent,
  $: get, esc: value => String(value).replaceAll("<", "&lt;"), plural: (n, word) => `${n} ${word}s`, policyHtml: () => "Rules",
  beginWork: () => () => {}, showJobError: () => {}, loadState: async () => {}, loadNav: () => {},
  api: async (url, data) => { importedPaths = data.body.paths; return { job: "job" }; },
  followJob: async () => ({ imported: [{ path: "/games/alpha", slug: "alpha" }], errors: [] }) };
vm.createContext(ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/project.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("function localProjectGroups(")), ctx);
const scan = { folder: "/games", projects: [
  { name: "Imported", path: "/games/imported", kind: "splat", existing: "old" },
  { name: "Zulu", path: "/games/zulu", kind: "splat" },
  { name: "Alpha", path: "/games/alpha", kind: "configure" },
  { name: "Beta", path: "/games/beta", kind: "splat" }
] };
async function main() {
  ctx.renderLocalProjects(box, scan);
  assert.doesNotMatch(host.innerHTML, /<b>Imported<\/b>/);
  assert.ok(host.innerHTML.indexOf("<h3>configure</h3>") < host.innerHTML.indexOf("<h3>splat</h3>"));
  assert.ok(host.innerHTML.indexOf("<b>Beta</b>") < host.innerHTML.indexOf("<b>Zulu</b>"));
  get("[data-search]").value = "ALPHA configure"; get("[data-search]").oninput();
  assert.equal(inputs.length, 1); assert.equal(inputs[0].dataset.local, "2");
  get("[data-select]").onclick();
  assert.match(get("[data-selection]").textContent, /1 selected/);
  get("[data-search]").value = "no-match"; get("[data-search]").oninput();
  assert.equal(get("[data-empty]").hidden, false);
  assert.match(get("[data-selection]").textContent, /1 hidden by filters/);
  get("[data-search]").value = "/games/beta"; get("[data-search]").oninput();
  get("[data-select]").onclick();
  assert.match(get("[data-selection]").textContent, /2 selected/);
  get("[data-clear]").onclick();
  assert.match(get("[data-selection]").textContent, /0 selected/);
  get("[data-search]").value = ""; get("[data-show-imported]").checked = true; get("[data-show-imported]").onchange();
  assert.equal(inputs.find(i => i.dataset.local === "0").disabled, true);
  get("[data-search]").value = "Alpha"; get("[data-search]").oninput(); get("[data-select]").onclick();
  assert.equal(get("[data-import]").disabled, true);
  get("[data-rules]").checked = true; get("[data-rules]").onchange();
  assert.equal(get("[data-import]").disabled, false);
  await get("[data-import]").onclick();
  assert.deepEqual(Array.from(importedPaths), ["/games/alpha"]);
  assert.equal(scan.projects[2].existing, "alpha");
  assert.equal(get("[data-import]").disabled, true);
  console.log("Grouped importer filtering, selection and import passed");
}
main().catch(e => { console.error(e); process.exitCode = 1; });
