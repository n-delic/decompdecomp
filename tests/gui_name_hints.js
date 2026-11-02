"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/function.js"), "utf8");
const elements = new Map();
const el = (id) => { if (!elements.has(id)) elements.set(id, { hidden: true, textContent: "", querySelectorAll: () => [button] }); return elements.get(id); };
const button = { dataset: { name: "sdkName" }, disabled: false };
let resolve, fail = false, busy = 0;
const S = { slug: "example", fn: "func_80001000", ov: {} };
const ctx = vm.createContext({ S, $: el, esc: (s) => String(s).replaceAll("<", "&lt;"),
  showJobError: (log, msg) => { log.error = msg; },
  beginWork: () => { busy++; button.disabled = true; return () => { busy--; button.disabled = false; }; },
  api: () => new Promise((yes, no) => { resolve = fail ? () => no(new Error("Name already taken")) : yes; }),
  refreshMap: async () => {}, refreshFn: async () => {},
});
vm.runInContext(source.slice(source.indexOf("function relatedFunctionLink"), source.indexOf("async function openFn")), ctx);
(async () => {
  const hints = [
    { name: "sdkName", game: "Game", kind: "verified C in another game", score: 1 },
    { name: "sdkName", game: "SDK / lib.a / sha256:123", kind: "SDK object signature; name only", score: 1 },
    { name: "otherName", game: "Other", kind: "similar code; unverified", score: .9 },
  ];
  ctx.hints = hints.concat(hints[0]);
  const html = vm.runInContext('nameHintsHTML(hints, "func_80001000")', ctx);
  assert.equal((html.match(/Use name/g) || []).length, 1);
  assert.ok(html.includes("2 sources agree"));
  assert.ok(!html.includes("sha256:"));
  assert.ok(html.indexOf("sdkName") < html.indexOf("otherName"));
  const pending = vm.runInContext('useSuggestedName(button, {name: S.fn})', Object.assign(ctx, { button }));
  assert.equal(button.disabled, true); assert.equal(busy, 1);
  resolve({ message: "Renamed" }); await pending;
  assert.equal(S.fn, "sdkName"); assert.equal(el("fnName").textContent, "sdkName");
  assert.equal(button.disabled, false); assert.equal(busy, 0);
  S.fn = "old"; fail = true;
  const rejected = vm.runInContext('useSuggestedName(button, {name: S.fn})', ctx);
  resolve(); await rejected;
  assert.equal(el("fnNameLog").error, "Name already taken"); assert.equal(S.fn, "old"); assert.equal(busy, 0);
  // The actual refresh accepts a server-resolved alias, while ignoring a response for a drawer we left.
  vm.runInContext(source.slice(source.indexOf("let fnBusy"), source.indexOf("function setEditor")), ctx);
  ctx.api = async () => ({ name: "canonical", calls: [], called_by: [] });
  await vm.runInContext('refreshFn(false)', ctx);
  assert.equal(S.fn, "canonical");
  assert.equal(S.fnDetail.name, "canonical");
  ctx.api = async () => { S.fn = "anotherFunction"; return { name: "stale" }; };
  await vm.runInContext('refreshFn(false)', ctx);
  assert.equal(S.fn, "anotherFunction");
  console.log("Grouped suggestions, rename feedback, failure recovery and canonical refresh passed.");
})().catch((e) => { console.error(e); process.exitCode = 1; });
