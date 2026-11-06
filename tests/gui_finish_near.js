"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const text = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
const code = text.slice(text.indexOf("function renderSavedMatchRecovery("), text.indexOf("function renderAutopilot("));
(async () => {
  const nodes = {};
  const $ = id => nodes[id] ||= {value: "", hidden: false};
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
  assert.equal($("recoverSavedMatches").textContent, "Recover all saved matches");
  const running = $("recoverSavedMatches").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($("recoverSavedMatches").disabled, true);
  assert.equal($("savedMatchSeconds").disabled, true);
  assert.equal($("savedMatchAttempts").disabled, true);
  assert.match($("savedMatchSummary").textContent, /2 matched.*8 compiles.*4.00 matches\/min/);
  await $("savedMatchStop").onclick();
  assert.equal(calls[1][0], "/api/job/cancel");
  assert.equal(calls[1][1].id, "job1");
  release(); await running;
  assert.equal($("recoverSavedMatches").disabled, false);
  assert.equal($("savedMatchStop").hidden, true);
  $("savedMatchSeconds").value = "30";
  $("savedMatchAttempts").value = "12";
  $("savedMatchAttempts").oninput();
  assert.equal($("recoverSavedMatches").textContent, "Recover saved matches");
  await $("recoverSavedMatches").onclick();
  assert.equal(calls[0][0], "/api/reconcile-matches");
  assert.equal(calls[0][1].seconds, null);
  assert.equal(calls[0][1].attempts, null);
  assert.equal(calls[2][0], "/api/reconcile-matches");
  assert.equal(calls[2][1].seconds, 30);
  assert.equal(calls[2][1].attempts, 12);
  $("savedMatchSeconds").value = "";
  await $("recoverSavedMatches").onclick();
  assert.equal(calls[3][1].seconds, null);
  assert.equal(calls[3][1].attempts, 12);
  $("savedMatchLimits").open = true;
  context.renderSavedMatchRecovery({kind: "rom"});
  assert.equal($("savedMatchLimits").open, true);
  context.api = async () => { throw new Error("Cannot start recovery"); };
  await $("recoverSavedMatches").onclick();
  assert.equal($("savedMatchLog").error, "Cannot start recovery");
  assert.equal($("savedMatchStop").hidden, true);
  assert.ok(!fs.readFileSync("src/decompnet/app/static/index.html", "utf8").includes("finishNearStart"));
  assert.equal($("recoverSavedMatches").disabled, false);
  context.renderSavedMatchRecovery({kind: "fork"});
  assert.equal($("savedMatchRecoveryPanel").hidden, true);
  console.log("saved match recovery UI passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
