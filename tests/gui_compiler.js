// A compiler range's Remove and the Revert it leaves behind (steering.js), against a stand-in for the page.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const read = (file) => fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js", file + ".js"), "utf8").replace(/\r\n/g, "\n");
function element() {
  return {
    disabled: false, hidden: false, value: "", textContent: "", innerHTML: "", _html: null, dataset: {}, attrs: {},
    setAttribute(key, value) { this.attrs[key] = value; },
  };
}
async function main() {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const calls = [], notes = [];
  const original = { start: 0x1000, end: 0x2000, toolchain: "ee-gcc-2.96", flags: ["-O2", "-G8"], name: "EE-GCC 2.96" };
  const ctx = {
    $: get, document: { createElement: element }, window: { addEventListener() {} }, setTimeout: () => 0, clearTimeout: () => {},
    confirm: () => true,
    S: { slug: "revert", tab: "settings", removedParts: [], ov: {
      kind: "rom", compiler: { name: "EE-GCC", flags: ["-O1"] }, compiler_ranges: [original],
    } },
  };
  vm.createContext(ctx);
  const base = read("base");
  vm.runInContext(base.slice(base.indexOf("const setHtml"), base.indexOf("/* The map's still")), ctx);
  const source = read("steering");
  vm.runInContext(source.slice(source.indexOf("const PART_STATE"), source.indexOf("async function loadCompilerAnswer")), ctx);
  vm.runInContext(source.slice(source.indexOf("function renderCompilerAnswer(a) {"), source.indexOf("function renderCompilerWaiting")), ctx);
  const marker = '$("compilerParts").onclick = async (e) => {';
  const start = source.indexOf(marker) + marker.length;
  vm.runInContext(source.slice(source.indexOf(marker), source.indexOf("\n};", start) + 3), ctx);
  const parts = get("compilerParts");
  const click = (button, selector) => parts.onclick({ target: { closest: (s) => (s === selector ? button : null) } });
  const answer = () => ({
    compiler: "EE-GCC 2.96", flags: "-O2 -G8", elsewhere: [], elsewhere_count: 0, checks: [], checking: false,
    mips: true, survey: {},
    parts: (ctx.S.ov.compiler_ranges || []).map((r) => ({
      start: r.start, end: r.end, compiler: r.name, flags: (r.flags || []).join(" "), file: r.file,
    })),
  });
  ctx.renderStats = () => {};
  ctx.renderProjectSettings = (keepRemoved) => {
    if (!keepRemoved) ctx.S.removedParts = [];
    ctx.renderCompilerAnswer(answer());
  };
  ctx.toast = (html) => notes.push(String(html));
  ctx.api = async (url, opts) => {
    const body = opts.body;
    calls.push([url, body]);
    if (body.remove && !body.reopen) {
      throw new Error("1 matched function(s) in those ranges wouldn't match with it any more (func_a): narrow the "
        + "ranges, or remove them and reopen those functions");
    }
    if (body.remove) {
      ctx.S.ov = { ...ctx.S.ov, compiler_ranges: [], reopened: ["func_a"] };
    } else {
      ctx.S.ov = { ...ctx.S.ov, compiler_ranges: [original], reopened: [] };
    }
    return ctx.S.ov;
  };
  ctx.renderProjectSettings(true);

  const remove = { dataset: { unrange: "0x1000-0x00002000", file: "" }, disabled: false };
  await click(remove, "button[data-unrange]");
  const posted = JSON.parse(JSON.stringify(calls));
  assert.deepEqual(posted[0], ["/api/compiler", { slug: "revert", ranges: "0x1000-0x00002000", remove: true, reopen: false }]);
  assert.deepEqual(posted[1], ["/api/compiler", { slug: "revert", ranges: "0x1000-0x00002000", remove: true, reopen: true }]);
  assert.equal(ctx.S.removedParts.length, 1);
  assert.equal(ctx.S.removedParts[0].toolchain, original.toolchain);
  assert.deepEqual(ctx.S.removedParts[0].flags, original.flags);
  assert.equal(ctx.S.removedParts[0].reopened, 1);
  assert.match(parts.innerHTML, /data-revert="0"/);
  assert.match(parts.innerHTML, /Revert/);
  assert.match(parts.innerHTML, /removed/);
  assert.match(notes[0], /Revert below puts it back/);
  assert.match(notes[0], /function it reopened stays open/);

  const revert = { dataset: { revert: "0" }, disabled: false };
  await click(revert, "button[data-revert]");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[2])), ["/api/compiler", {
    slug: "revert", ranges: "0x1000-0x00002000", toolchain: original.toolchain, flags: original.flags,
  }]);
  assert.equal(ctx.S.removedParts.length, 0);
  assert.match(parts.innerHTML, /data-unrange="0x1000-0x00002000"/);
  assert.doesNotMatch(parts.innerHTML, /data-revert/);
  assert.match(notes[1], /built with EE-GCC 2.96 again/);
  assert.match(notes[1], /function it had reopened stays open/);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
