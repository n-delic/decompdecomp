// Search options use the shared drawer without cloning or losing the controls the start request reads.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/steering.js"), "utf8");
const elements = new Map(), keys = {};
let active;
function get(id) {
  if (!elements.has(id)) {
    let html = "";
    elements.set(id, {id, dataset:{}, children:[], hidden:false, disabled:false, isConnected:true,
      textContent:"", value:"", checked:false, valid:true, offsetParent:{}, classList:{add(){},remove(){}},
      get innerHTML() {return html;}, set innerHTML(value) {
        html = value; this.children = [];
        for (const match of value.matchAll(/id="([^"]+)"/g)) this.append(get(match[1]));
      },
      append(child) {
        if (child.parentNode) child.parentNode.children = child.parentNode.children.filter(c=>c !== child);
        this.children.push(child); child.parentNode = this;
      },
      querySelectorAll(selector) {
        if (selector === "input") return this.children.filter(child=>child.isInput);
        if (selector.includes(".drawer-body")) return get("deepSweepSettings").parentNode === get("deepSweepOptionsHost") ? get("deepSweepSettings").querySelectorAll("input") : [];
        return [];
      },
      contains(node) {return node === this || this.children.some(child=>child.contains(node));},
      querySelector:()=>get("close"),
      addEventListener(name, handler) {this[name] = handler;},
      closest(selector) {return selector === "[hidden]" && (this.hidden || this.parentNode?.closest(selector)) ? this : null;},
      focus() {active = this;}, checkValidity() {return this.valid;}, reportValidity() {return this.valid;},
    });
  }
  return elements.get(id);
}
const ctx = {$:get, S:{slug:"game",map:[]}, document:{
  get activeElement() {return active;}, body:get("body"),
  querySelectorAll:()=>[get("dialog"),get("drawer")], addEventListener:(key,handler)=>{keys[key]=handler;},
}, navigator:{}, esc:String};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf("function releaseDialogContent"), source.indexOf('$("joinBtn").onclick')), ctx);
vm.runInContext(source.slice(source.indexOf("function openDeepOptions"), source.indexOf("function retryDeepUpdates")), ctx);
vm.runInContext(source.slice(source.indexOf("function renderDeepOptionsSummary"), source.indexOf("function renderDeepQueueState")), ctx);
const settings = get("deepSweepSettings"), home = get("deepSweepSettingsHome");
home.hidden = true; home.append(settings);
const minutes = get("deepSweepMinutes"), maximum = get("deepSweepMaximum"), loop = get("deepSweepLoop");
for (const input of [minutes,maximum,loop]) {input.isInput = true; settings.append(input);}
minutes.value = "5";
get("dialog").append(get("dialogBody"));
get("dialog").hidden = get("drawer").hidden = true;
get("launchPop").hidden = get("notifPanel").hidden = true;
const trigger = get("deepSweepOptions");
const open = () => {trigger.focus(); ctx.openDeepOptions(); assert.equal(settings.parentNode, get("deepSweepOptionsHost"));};
const restored = () => {assert.equal(settings.parentNode, home); assert.equal(get("dialog").hidden,true); assert.equal(minutes.value,"12"); assert.equal(active,trigger);};
open();
assert.equal(active,minutes);
minutes.value = "12"; maximum.checked = loop.checked = true; settings.oninput();
assert.equal(settings.dataset.editedSlug,"game");
assert.equal(get("deepSweepOptionsSummary").textContent,"12 min per function · maximum CPU · looping");
get("deepSweepOptionsDone").onclick(); restored();
open(); minutes.valid = false; get("deepSweepOptionsDone").onclick();
assert.equal(get("dialog").hidden,false,"invalid settings keep the drawer open");
minutes.valid = true; keys.keydown({key:"Escape"}); restored();
open(); get("dialog").click({target:get("dialog")}); restored();
open(); get("dialog").click({target:{closest:()=>get("close")}}); restored();
open(); ctx.dialog("Matched functions","<p>Results</p>","deep-metric:matched");
assert.equal(settings.parentNode,home,"replacing the drawer returns the actual inputs before clearing it");
assert.equal(minutes.value,"12");
ctx.closeAll();
open(); ctx.closeAll(); restored();
open(); ctx.setDeepRunVisibility(true);
assert.equal(settings.disabled,true);
assert.match(get("deepSweepOptionsHelp").textContent,/Stop the search/);
ctx.closeAll(); open(); assert.equal(active,get("deepSweepOptionsDone"));
ctx.setDeepRunVisibility(false); assert.equal(settings.disabled,false);
assert.match(get("deepSweepOptionsHelp").textContent,/next search/);
ctx.closeAll();
let starts = 0;
ctx.startDeepSweep = () => ++starts;
vm.runInContext(source.slice(source.indexOf('$("deepSweepOptions").onclick'), source.indexOf("async function startDeepSweep")), ctx);
const start = get("deepSweepStart");
for (const label of ["Start search", "Continue search", "Run again"]) {
  start.textContent = label;
  start.onclick();
  assert.equal(get("deepSweepOptionsDone").dataset.start, label);
  assert.equal(active, minutes);
  assert.equal(starts, 0, "opening options does not start work");
  minutes.valid = false;
  get("deepSweepOptionsDone").onclick();
  assert.equal(starts, 0);
  minutes.valid = true;
  keys.keydown({key:"Escape"});
}
start.onclick();
get("deepSweepOptionsDone").onclick();
assert.equal(starts, 1, "confirming options starts the search");
assert.equal(get("dialog").hidden, true);
console.log("Search options preserve values, validation and focus across every drawer dismissal.");
