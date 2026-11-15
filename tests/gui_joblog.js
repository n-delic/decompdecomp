// Log boxes colour their lines like a terminal without changing their text (base.js, joblogHtml).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const base = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/base.js"), "utf8");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(base.slice(base.indexOf("const JOBLOG_COLOUR_LINES"), base.indexOf("function watchJoblogColour")), ctx);
const html = (text) => ctx.joblogHtml(text);
// What the page's textContent gives back: the tags gone, the entities read.
const text = (markup) => markup.replace(/<[^>]*>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const spans = (markup, kind) => [...markup.matchAll(new RegExp(`<span class="${kind}">([^<]*)</span>`, "g"))].map((m) => m[1]);

const build = [
  "decompnet.network.NetworkError: 3 of the game's files don't compile: __osPfsRequestData.c: "
    + "/synthetic/build/src/__osPfsRequestData.c:2:8: error: unknown type name 'OSPifRam'; func_80029EAC.c: reference.c:3:3: error: use",
  '  File "/synthetic/src/decompnet/port/wasm/build.py", line 323, in build',
  "    ^^^^^^^^^^",
  "src/game/a.c:41:9: warning: unused variable 'x' [-Wunused-variable]",
  "src/game/a.c:12: note: previous declaration is here",
  "make: *** [Makefile:12: build/a.o] Error 1",
  "Matched 4 functions",
  "Compiling <tag> & friends",
  "",
].join("\n");
const markup = html(build);
// The text is exactly what was written, so Copy and every reader of textContent see no difference.
assert.equal(text(markup), build);
assert.equal(html(""), "");
assert.equal(text(html("a\n\nb\n")), "a\n\nb\n");
assert.ok(!markup.includes("<tag>"), "log text is escaped");
const lines = markup.split("\n");
assert.match(lines[0], /^<span class="jl-err">/);
assert.deepEqual(spans(lines[0], "jl-loc"), ["/synthetic/build/src/__osPfsRequestData.c:2:8", "reference.c:3:3"]);
assert.deepEqual(spans(lines[0], "jl-err jl-key"), ["error", "error"]);
assert.match(lines[1], /^<span class="jl-dim">/);
assert.match(lines[2], /^<span class="jl-dim">/);
assert.deepEqual(spans(lines[3], "jl-warn jl-key"), ["warning"]);
assert.deepEqual(spans(lines[3], "jl-loc"), ["src/game/a.c:41:9"]);
assert.deepEqual(spans(lines[4], "jl-note jl-key"), ["note"]);
assert.match(lines[5], /^<span class="jl-err">/);
assert.match(lines[6], /^<span class="jl-ok">/);
assert.equal(lines[7], "Compiling &lt;tag&gt; &amp; friends");
// A class name ending in Error is not an "error:" keyword, and an ordinary line stays plain.
assert.deepEqual(spans(html("x: NetworkError: y"), "jl-err jl-key"), []);
assert.equal(html("Linking 12 objects"), "Linking 12 objects");
// A long log colours its tail only, and still gives back its whole text.
const long = Array.from({ length: 5000 }, (_, i) => `x.c:${i + 1}:1: error: e`).join("\n");
const longMarkup = html(long);
assert.equal(text(longMarkup), long);
assert.equal(spans(longMarkup, "jl-err jl-key").length, 3000);
console.log("ok");
