"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const text = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
const code = text.slice(text.indexOf("function renderSavedMatchRecovery("), text.indexOf("function renderAutopilot("));
(async () => {
  const nodes = {};
  const $ = id => nodes[id] ||= {value: id.endsWith("Seconds") ? "30" : "12", hidden: false};
  const calls = []; let release;
  const gate = new Promise(resolve => release = resolve);
  const context = { $, S: {slug: "game"},
    beginWork: (log, title, buttons) => { buttons.forEach(b => b.disabled = true); return () => buttons.forEach(b => b.disabled = false); },
    showJobError: (log, message) => log.error = message,
    api: async (url, options) => { calls.push([url, options.body]); return {job: "job1"}; },
    followJob: async (id, log, update) => {update([], {progress: {matched: 2, attempts: 8, matches_per_minute: 4}}); await gate;},
  };
  vm.createContext(context); vm.runInContext(code, context);
  context.renderSavedMatchRecovery({kind: "rom"});
  const running = $("recoverSavedMatches").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($("recoverSavedMatches").disabled, true);
  assert.match($("savedMatchSummary").textContent, /2 matched.*8 compiles.*4.00 matches\/min/);
  await $("savedMatchStop").onclick();
  assert.equal(calls[1][0], "/api/job/cancel");
  assert.equal(calls[1][1].id, "job1");
  release(); await running;
  assert.equal($("recoverSavedMatches").disabled, false);
  assert.equal($("savedMatchStop").hidden, true);
  await $("recoverSavedMatches").onclick();
  assert.equal(calls[0][0], "/api/reconcile-matches");
  assert.equal(calls[2][0], "/api/reconcile-matches");
  assert.ok(!fs.readFileSync("src/decompnet/app/static/index.html", "utf8").includes("finishNearStart"));
  assert.equal($("recoverSavedMatches").disabled, false);
  context.renderSavedMatchRecovery({kind: "fork"});
  assert.equal($("savedMatchRecoveryPanel").hidden, true);
  console.log("saved match recovery UI passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
