// A game too big to copy through the window is read where it is (#154): dropping a 792 MB disc image used to send it
// through WebView2, whose browser process died and took the app with it. A PC game's debug files ride the same copy,
// so they count towards the limit too.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const asked = [], toasts = [], resets = [];
let loaded = 0, head = null;
const ctx = {
  $: (id) => ({ hidden: false, value: "", textContent: "", checked: false, addEventListener() {} }),
  S: {},
  askPath(why) { asked.push(why); },
  resetDrop() { resets.push(1); },
  toast(text, kind) { toasts.push([text, kind]); },
  esc: (text) => text,
  MB: (bytes) => `${Math.round(bytes / 1e6)} MB`,
  loadState: async () => { loaded++; },
  identifyXboxDisc: async () => null,
  identifyHead: () => head,
  notSupported: () => "not a game decompdecomp reads",
  parseRom: () => { throw new Error("not a ROM"); },
  NOT_A_GAME: "not a game",
};
vm.createContext(ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/home.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("const COPY_LIMIT"), source.indexOf("function setupCompilers")), ctx);

const file = (size, name) => ({ size, name, slice: () => ({ arrayBuffer: async () => new ArrayBuffer(16) }) });
const MB = 1024 * 1024;

async function main() {
  // a disc image bigger than the copy limit: the path dialog, and nothing read from the file
  await ctx.pickFile(file(792788992, "BMX XXX (USA).xiso.iso"));
  assert.equal(asked.length, 1, "the user is asked for a path instead of the file being copied in");
  assert.match(asked[0], /BMX XXX \(USA\)\.xiso\.iso comes to 793 MB/);
  assert.match(asked[0], /too big to copy through this window/);
  assert.match(asked[0], /copying nothing/);
  assert.equal(resets.length, 1, "the picked file is cleared");
  assert.equal(loaded, 0, "nothing of the game is read: it isn't the window's to copy");

  // a small program whose debug files make the copy big: the same dialog, before anything is picked
  head = { platform: "win32", title: "BMX XXX", order: "Windows program (.exe)" };
  await ctx.pickFile(file(2 * MB, "bmx.exe"), [file(200 * MB, "bmx.pdb")]);
  assert.equal(asked.length, 2, "the debug files are part of the copy, and it is too big");
  assert.match(asked[1], /with its debug files/);
  assert.equal(ctx.picked, undefined, "nothing was picked: the path dialog is the way in");

  // a ROM small enough to copy in still is: it goes on to be identified
  head = null;
  const identified = loaded;
  await ctx.pickFile(file(4 * MB, "small.n64"));
  assert.equal(asked.length, 2, "a small game isn't sent to the path dialog");
  assert.equal(loaded, identified + 1, "it is identified as before");
  assert.deepEqual(toasts, [["not a game decompdecomp reads", "err"]]);
  console.log("gui_drop: ok");
}
main().catch((e) => { console.error(e); process.exit(1); });
