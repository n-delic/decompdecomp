"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const text = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
const code = text.slice(text.indexOf("function renderFinishNear("), text.indexOf("function renderAutopilot("));
(async () => {
  const nodes = {};
  const $ = id => nodes[id] ||= {value: id.endsWith("Seconds") ? "30" : "12", hidden: false};
  const calls = []; let release;
  const gate = new Promise(resolve => release = resolve);
  const context = { $, S: {slug: "game"},
    beginWork: (log, title, buttons) => { buttons[0].disabled = true; return () => buttons[0].disabled = false; },
    showJobError: (log, message) => log.error = message,
    api: async (url, options) => { calls.push([url, options.body]); return {job: "job1"}; },
    followJob: async (id, log, update) => {update([], {progress: {matched: 2, attempts: 8, matches_per_minute: 4}}); await gate;},
  };
  vm.createContext(context); vm.runInContext(code, context);
  context.renderFinishNear({kind: "rom"});
  const running = $("finishNearStart").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($("finishNearStart").disabled, true);
  assert.match($("finishNearSummary").textContent, /2 matched.*8 compiles.*4.00 matches\/min/);
  await $("finishNearStop").onclick();
  assert.equal(calls[1][0], "/api/job/cancel");
  assert.equal(calls[1][1].id, "job1");
  release(); await running;
  assert.equal($("finishNearStart").disabled, false);
  assert.equal($("finishNearStop").hidden, true);
  context.renderFinishNear({kind: "fork"});
  assert.equal($("finishNearPanel").hidden, true);
  console.log("finish near UI passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
