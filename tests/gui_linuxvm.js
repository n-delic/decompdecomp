"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const elements = new Map(), requests = [], progress = [];
const get = id => {
  if (!elements.has(id)) elements.set(id, { innerHTML: "", querySelectorAll: () => [], querySelector: () => get("linuxVmLog") });
  return elements.get(id);
};
const ctx = { $: get, S: { st: { toolchains: [{id:"example", name:"Example", use:"comparison", platform:"N64", supported:true, linux_vm:true}] } },
  esc: String, show: () => {}, loadState: async () => {}, dialog: () => get("dialog"),
  beginWork: (log, title) => { progress.push(title); return () => progress.push("finished"); },
  followJob: async job => assert.equal(job, 7), showJobError: () => {},
  installToolchain: () => {}, api: async (url, opts) => { requests.push([url, opts]); return {job:7}; } };
vm.createContext(ctx);
const text = fs.readFileSync(path.join(__dirname,"../src/decompnet/app/static/js/pages.js"), "utf8");
vm.runInContext(text.slice(text.indexOf("let toolFilter"), text.indexOf("/* ------------------------------------------------------------------ settings page */")), ctx);
ctx.renderTools();
assert(!get("toolGrid").innerHTML.includes("Local Linux compiler runner"));
ctx.S.st.linux_vm = {ready:false, why:"Install Colima first"};
ctx.renderTools();
assert.match(get("toolGrid").innerHTML, /Prepare Linux runner/);
assert.match(get("toolGrid").innerHTML, /Install Colima first/);
assert.match(get("toolGrid").innerHTML, /Linux VM/);
// Runtime tools remain independently installable; an existing compiler can repair
// dependencies without forcing another download of the compiler pack.
ctx.S.st.linux_vm = null;
ctx.S.st.toolchains = [{id:'wibo',name:'wibo',use:'Runs Windows compilers on macOS',supported:true,installed:false}];
ctx.renderTools();
assert.match(get('toolGrid').innerHTML,/data-tc="wibo"[^>]*>Install</);
ctx.S.st.toolchains = [{id:'compiler',name:'Example compiler',use:'Compiler',supported:true,installed:true,
  missing_dependencies:[{id:'wibo',name:'wibo'}]}];
ctx.renderTools();
assert.match(get('toolGrid').innerHTML,/Required tools missing: wibo/);
assert.match(get('toolGrid').innerHTML,/data-tc="compiler"[^>]*>Install missing tools</);
assert.doesNotMatch(get('toolGrid').innerHTML,/✓ installed|data-force="1"/);

ctx.S.st.toolchains = [{id:'example', name:'Example', use:'comparison', platform:'N64', supported:true, linux_vm:true}];
(async () => {
  await get("prepareLinuxVm").onclick();
  assert.equal(requests[0][0], "/api/linux-vm");
  assert.equal(requests[0][1].method, "POST");
  assert.deepEqual(progress, ["Preparing local Linux runner", "finished"]);
  ctx.S.st.linux_vm = {ready:true};
  ctx.renderTools();
  assert.match(get("toolGrid").innerHTML, /Check Linux runner/);
  ctx.S.st.toolchains[0].installed = true;
  ctx.renderTools();
  assert.match(get("toolGrid").innerHTML, /data-force="1"[^>]*>Reinstall/);
  ctx.S.st.toolchains[0].linux_vm = false;
  ctx.S.st.toolchains[0].wsl = true;
  ctx.renderTools();
  assert.match(get("toolGrid").innerHTML, /Reinstall/);
})().catch(e => { console.error(e); process.exitCode=1; });
