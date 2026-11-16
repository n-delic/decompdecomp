// Follow the actual project route and tab switch in both ROM and project mode.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const root = path.join(__dirname, "../src/decompnet/app/static");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const base = fs.readFileSync(path.join(root, "js/base.js"), "utf8");
const project = fs.readFileSync(path.join(root, "js/project.js"), "utf8");
const element = dataset => ({dataset, hidden:false, open:false, attributes:{}, classList:{toggle(){}},
  setAttribute(name, value) {this.attributes[name] = value;}, removeAttribute(name) {delete this.attributes[name];}});
const tabs = [...html.matchAll(/data-ptab="([^"]+)"/g)].map(m => element({ptab:m[1]}));
const panes = [...html.matchAll(/data-pane="([^"]+)"/g)].map(m => element({pane:m[1]}));
let deepLoads = 0, settingsLoads = 0;
const ctx = {S:{slug:"demo",ov:{kind:"rom",ready:true},map:[]}, location:{hash:"#/p/demo/matching"},
  $:()=>({hidden:false}), document:{querySelectorAll:selector=>selector.includes("data-ptab") ? tabs : panes},
  closeAll(){}, closeNav(){}, hideLaunch(){}, markNav(){}, renderOverview(){},
  renderDeepSweep(){deepLoads++;}, renderProjectSettings(){settingsLoads++;}};
vm.createContext(ctx);
vm.runInContext(base.match(/const TABS = \[[^;]+;/)[0], ctx);
vm.runInContext(project.slice(project.indexOf("function setTab("), project.indexOf('document.querySelectorAll("[data-goto]")')), ctx);
vm.runInContext(base.slice(base.indexOf("function route()"), base.indexOf("\n}", base.indexOf("function route()")) + 2), ctx);
for (const kind of ["rom", "fork"]) {
  ctx.S.ov.kind = kind;
  ctx.location.hash = "#/p/demo/matching";
  ctx.route();
  assert.equal(ctx.S.tab, "matching");
  assert.deepEqual(panes.filter(p=>!p.hidden).map(p=>p.dataset.pane), ["matching"]);
  assert.equal(tabs.find(t=>t.dataset.ptab === "matching").attributes["aria-current"], "page");
  const before = deepLoads;
  ctx.setTab("settings");
  assert.equal(deepLoads, before, "Settings must not load local search status");
  assert.deepEqual(panes.filter(p=>!p.hidden).map(p=>p.dataset.pane), ["settings"]);
  ctx.setTab("matching");
  assert.equal(deepLoads, before + 1, "returning reloads status to reattach existing work");
}
assert.equal(settingsLoads, 2);
assert.equal(deepLoads, 4);
console.log("Local Matching routes and tab navigation passed.");
