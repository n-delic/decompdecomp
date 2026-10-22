// Exercise the real restore handlers with delayed requests and a drawer that is rebuilt on every visit.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
let elements = {}, requests = [], opening, polling, rejectCancel = false;
const ctx = {
  $: (id) => elements[id] || null,
  S: {}, location: {}, JOB_POLL_MS: 1,
  dialog() {
    elements = Object.fromEntries(["rsSource", "rsGame", "rsOwn", "rsGo", "rsCancel", "rsWork", "rsStatus", "rsLog", "dialogBody"]
      .map((id) => [id, { value: "", checked: false, textContent: "" }]));
    elements.dialogBody.querySelector = (s) => elements[s.slice(1)];
    elements.dialogBody.querySelectorAll = () => [];
    ctx.S.dialogKind = "restore";
    return elements.dialogBody;
  },
  closeDialog() { elements = {}; ctx.S.dialogKind = ""; },
  showJobError(el, message) { el.error = message; },
  toast() {}, loadNav() {}, plural: () => "",
  sleep: () => new Promise(() => {}),
  api(url, options) {
    requests.push([url, options]);
    if (url === "/api/restore") return new Promise((resolve) => { opening = resolve; });
    if (url === "/api/job/cancel") return rejectCancel ? Promise.reject(new Error("Try again")) : Promise.resolve({});
    return new Promise((resolve) => { polling = resolve; });
  },
};
vm.createContext(ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/home.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("let restoreJob =")), ctx);
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const reopen = () => { ctx.closeDialog(); ctx.openRestore(); };
async function main() {
  ctx.openRestore();
  assert.equal(elements.rsWork.hidden, true);
  elements.rsSource.value = "save";
  elements.rsSource.oninput();
  elements.rsGame.value = "game";
  elements.rsGame.oninput();
  reopen();
  assert.equal(elements.rsSource.value, "save");
  assert.equal(elements.rsGame.value, "game");
  assert.equal(elements.rsOwn.checked, false);
  assert.equal(elements.rsGo.disabled, true);
  elements.rsOwn.checked = true;
  elements.rsOwn.onchange();
  reopen();
  assert.equal(elements.rsGo.disabled, false);
  const work = elements.rsGo.onclick();
  assert.equal(elements.rsWork.hidden, false);
  assert.match(elements.rsStatus.textContent, /Opening saved project/);
  await elements.rsCancel.onclick();
  reopen();
  assert.equal(elements.rsCancel.disabled, true);
  assert.equal(elements.rsGo.disabled, true);
  assert.equal(requests.filter(([url]) => url === "/api/restore").length, 1);
  opening({ job: "first" });
  await tick();
  assert.equal(requests.find(([url]) => url === "/api/job/cancel")[1].body.id, "first");
  polling({ id: "first", state: "cancelled", log: ["checking"], error: "Cancelled" });
  await work;
  reopen();
  assert.equal(elements.rsWork.hidden, true);
  assert.equal(elements.rsLog.error, "Cancelled");
  assert.match(elements.rsStatus.textContent, /Cancelled/);
  assert.equal(elements.rsLog.textContent, "checking");
  assert.equal(elements.rsGo.disabled, false);
  // A reopened terminal dialog still has working handlers, including retry.
  const retry = elements.rsGo.onclick();
  opening({ job: "second" });
  await tick();
  rejectCancel = true;
  await elements.rsCancel.onclick();
  assert.equal(elements.rsCancel.disabled, false);
  assert.equal(elements.rsLog.error, "Try again");
  rejectCancel = false;
  await elements.rsCancel.onclick();
  assert.equal(elements.rsCancel.disabled, true);
  polling({ id: "second", state: "error", log: ["failed"], error: "Missing save" });
  await retry;
  reopen();
  assert.equal(elements.rsWork.hidden, true);
  assert.equal(elements.rsGo.disabled, false);
  assert.match(elements.rsStatus.textContent, /Missing save/);
  await elements.rsCancel.onclick();
  ctx.openRestore();
  assert.equal(elements.rsSource.value, "");
  assert.equal(elements.rsOwn.checked, false);
}
main().catch((e) => { console.error(e); process.exitCode = 1; });
