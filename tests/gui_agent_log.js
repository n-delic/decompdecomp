"use strict";
/* A helper's log (agents.js addThoughts): two polls in flight at once both answered with the lines after the same
   seq, and each was added, so a local model's every step showed two or three times with the same time. */
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync("src/decompnet/app/static/js/agents.js", "utf8");
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));

const shown = [];
const list = { children: [], querySelector: () => null, prepend: (li) => { shown.push(li.text); list.children.push(li); },
  get lastChild() { return { remove() {} }; } };
const ctx = { S: { feeds: {}, logFor: "1" }, FEED_KEEP: 500, Math, $: () => list,
  logLine: (f) => ({ text: f.text }), renderLog() {} };
vm.createContext(ctx);
vm.runInContext(slice("function addThoughts(", "function showRetryPaused("), ctx);

const line = (seq, text) => ({ seq, t: 1, type: "tool", text });
const answer = [{ id: "1", feed: [line(1, "get_assembly(a)"), line(2, "get_assembly(b)")] }];
ctx.addThoughts(structuredClone(answer), false);
ctx.addThoughts(structuredClone(answer), false);  // the second poll's answer: the same lines
ctx.addThoughts([{ id: "1", feed: [line(2, "get_assembly(b)"), line(3, "compile(c)")] }], false);
assert.deepEqual(ctx.S.feeds["1"].map((f) => f.seq), [1, 2, 3]);
assert.deepEqual(shown, ["get_assembly(a)", "get_assembly(b)", "compile(c)"]);
