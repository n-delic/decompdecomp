"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/project.js"), "utf8");
const overview = source.slice(source.indexOf("function fnCounts("), source.indexOf("function renderAgentsKpi("));
const elements = new Map();
const row = (agent, matched, compiles) => ({agent, matched, compiles});
const agents = [
  row("autopilot", 9, 12), row("another-agent", 2, 5), row("permuter", 2, 4),
  ...Array.from({length: 6}, (_, i) => row(`permuter-${i}`, 1, 2)),
  row("permuter-1-lane-2", 1, 1), row("deep-permuter", 0, 3), row("permuter-helper", 0, 1),
];
const ctx = {
  S: {tab: "overview", map: [], ov: {matched: 0}, stats: {
    agents, matches_per_hour: Array(24).fill(0), compiles_last_hour: 0,
  }},
  $(id) {
    if (!elements.has(id)) elements.set(id, {querySelectorAll: () => []});
    return elements.get(id);
  },
  setWidths() {}, cFunctions: () => 0, esc: String,
  plural: (n, label) => `${n} ${label}${n === 1 ? "" : "s"}`,
};
vm.runInNewContext(overview + "\nrenderOverview();", ctx);

const board = elements.get("board").innerHTML;
assert.deepEqual([...board.matchAll(/class="who">([^<]+)/g)].map(match => match[1]),
  ["autopilot", "deep-permuter", "another-agent", "permuter", "permuter-helper"]);
assert.match(board, /deep-permuter<.*\s+.*>7<\/b> matched · 16 compiles/);
assert.equal(agents[0].agent, "autopilot");
assert.equal(agents[3].agent, "permuter-0");
