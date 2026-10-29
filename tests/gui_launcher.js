// Exercise the launcher's model controls and events without a browser or an installed agent.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class Element {
  constructor(tag = "div", attrs = {}) {
    this.tag = tag;
    this.dataset = {};
    this.attributes = attrs;
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      else this[key] = value;
    }
    this.checked = Object.hasOwn(attrs, "checked");
    this.hidden = Object.hasOwn(attrs, "hidden");
    this.disabled = Object.hasOwn(attrs, "disabled");
    this.value = attrs.value || "";
    this.textContent = "";
    this.isConnected = true;
  }
  focus() {}
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelectorAll(selector) { return []; }
  querySelector() { return null; }
}

class Box extends Element {
  set innerHTML(html) {
    this.html = html;
    this.elements = [];
    const tagPattern = /<(button|input|select|option|output|a|span)\b([^>]*)>/g;
    for (const match of html.matchAll(tagPattern)) {
      const attrs = {};
      for (const item of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[item[1]] = item[2] ?? "";
      const element = new Element(match[1], attrs);
      if (match[1] === "span") element.textContent = html.slice(match.index + match[0].length, html.indexOf("</span>", match.index));
      this.elements.push(element);
    }
    for (const select of this.elements.filter((element) => element.tag === "select")) {
      const start = html.indexOf(`<select${select.attributes["data-f"] ? ` data-f="${select.attributes["data-f"]}"` : ""}`);
      const tail = start < 0 ? html : html.slice(start, html.indexOf("</select>", start));
      select.options = [...tail.matchAll(/<option\b[^>]*value="([^"]*)"[^>]*>/g)].map(([, value]) => value);
      const chosen = tail.match(/<option\b(?=[^>]*selected)[^>]*value="([^"]*)"/);
      select.value = chosen ? chosen[1] : (select.options[0] || "");
    }
  }
  get innerHTML() { return this.html || ""; }
  querySelectorAll(selector) {
    const checked = selector.endsWith(":checked");
    const base = checked ? selector.slice(0, -8) : selector;
    return this.elements.filter((element) => {
      let found = false;
      const attr = base.match(/^(\w+)?\[data-([\w-]+)(?:="([^"]*)")?\]$/);
      if (attr) {
        const key = attr[2].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        found = (!attr[1] || element.tag === attr[1]) && Object.hasOwn(element.dataset, key) &&
          (attr[3] === undefined || element.dataset[key] === attr[3]);
      } else if (base.startsWith(".")) found = (element.attributes.class || "").split(" ").includes(base.slice(1));
      else if (base.includes(",")) found = base.split(",").map((part) => part.trim()).includes(element.tag);
      return found && (!checked || element.checked);
    });
  }
  querySelector(selector) {
    if (selector === ".stepper output") return this.elements.find((e) => e.tag === "output") || null;
    if (selector === "button, input, select") return this.elements.find((e) => ["button", "input", "select"].includes(e.tag)) || null;
    return this.querySelectorAll(selector)[0] || null;
  }
}

const nodes = new Map();
const get = (id) => {
  if (!nodes.has(id)) nodes.set(id, new Element());
  return nodes.get(id);
};
const box = new Box();
nodes.set("launchPopBody", box);
const calls = [];
const endpoint = { id: "e1", name: "Local", model: "base", base_url: "http://127.0.0.1:8080/v1", max_sessions: 4 };
const ctx = {
  console, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number, Symbol, Error, RegExp, encodeURIComponent,
  document: { addEventListener() {}, getElementById: get },
  $: get, S: { slug: "game", settings: {}, st: { settings: { endpoints: [endpoint], per_session: 4 }, agents: [] } },
  AUTO: "autopilot", esc: (value) => String(value ?? ""), setHtml: (el, html) => { el.innerHTML = html; },
  wireCopies() {}, wireEndpointFields() {}, endpointFields: () => "", hostOf: () => "127.0.0.1",
  polishOffered: () => false, cFunctions: () => 0, cmdBox: () => "", effortOf: () => "", EFFORT_NAMES: {},
  MAX_PER_SESSION: 20, MAX_AT_ONCE: 8, poll() {}, loadNav() {}, hideLaunch() {}, toast() {},
  beginWork() { return () => {}; }, showJobError() {}, followJob: async () => {},
  api: async (url, options = {}) => {
    if (url.startsWith("/api/endpoints/models")) return { models: [{ id: "m-one", name: "Model one" }, { id: "m-two", name: "Model two" }] };
    calls.push({ url, body: options.body });
    return { started: ["helper"] };
  },
  TASKS: [["", "Decompile", "decompile"]], location: { hash: "" },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "src", "decompnet", "app", "static", "js", "agents.js"), "utf8"), ctx);

const apiAgent = { id: "api", name: "API", uses_endpoints: true, installed: true, recommended: true, takes_effort: false };
const cliAgent = { id: "claude", name: "Claude Code", installed: true, recommended: false, takes_effort: false,
  models: [{ id: "opus", name: "Opus" }, { id: "sonnet", name: "Sonnet" }, { id: "haiku", name: "Haiku" }] };
const manualAgent = { id: "manual", name: "Manual provider", installed: true, recommended: false, takes_effort: false, models: [] };
ctx.S.st.agents = [apiAgent, cliAgent, manualAgent];
ctx.renderLauncher(box);

async function settle() { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); }
function requireOk(test, message) { if (!test) throw new Error(message); }

(async () => {
  await settle();
  requireOk(box.html.includes("Multiple models") && box.html.includes("Default · base"), "API provider must show a model mode dropdown");
  const mode = box.querySelector('[data-f="api-model"]');
  requireOk(mode.options.includes("__multiple"), "API dropdown must expose multiple models");
  mode.value = "__multiple";
  mode.onchange();
  const choices = box.querySelectorAll("[data-model-choice]");
  requireOk(choices.length === 2, "discovered API models should be selectable");
  choices[0].checked = true;
  choices[0].onchange();
  const apiBefore = box.html;
  const cliTile = box.querySelectorAll(".lz-kind").find((tile) => tile.dataset.k === "claude");
  cliTile.onclick();
  requireOk(box.html.includes("Opus") && box.html.includes("Multiple models"), "CLI provider must show known models and the shared multiple mode");
  const cliSelect = box.querySelector('[data-f="model"]');
  requireOk(cliSelect.options.includes("opus") && cliSelect.options.includes("sonnet"), "single dropdown must contain known CLI models");
  cliSelect.value = "__multiple";
  cliSelect.onchange();
  const cliChoices = box.querySelectorAll("[data-model-choice]");
  cliChoices[0].checked = true;
  cliChoices[0].onchange();
  const apiTile = box.querySelectorAll(".lz-kind").find((tile) => tile.dataset.k === "api");
  apiTile.onclick();
  requireOk(box.html === apiBefore || box.html.includes("checked"), "switching providers should retain API model selection");
  const apiStart = box.querySelector('[data-act="start"]');
  await apiStart.onclick();
  const apiCall = calls.find((call) => call.url === "/api/agents/start" && call.body.kind === "api");
  requireOk(apiCall && apiCall.body.options.multiple_models && apiCall.body.options.models[0].endpoint === "e1" &&
    apiCall.body.options.models[0].model === "m-one", "API launch must send only selected endpoint/model pairs");

  const apiModeAgain = box.querySelector('[data-f="api-model"]');
  apiModeAgain.value = "__other";
  apiModeAgain.onchange();
  const apiCustomSelect = box.querySelector('[data-f="api-model"]');
  requireOk(apiCustomSelect.value === "__other", "API custom mode must remain visibly selected");
  const apiCustom = box.querySelector('[data-f="api-custom"]');
  apiCustom.value = "manual-api-model";
  apiCustom.oninput();
  await box.querySelector('[data-act="start"]').onclick();
  const apiSingleCall = calls.filter((call) => call.url === "/api/agents/start" && call.body.kind === "api").at(-1);
  requireOk(apiSingleCall.body.options.model === "manual-api-model" && apiSingleCall.body.options.endpoints[0] === "e1",
    "API single-model fallback must send the typed model on its saved endpoint");

  box.querySelectorAll("[data-ep]")[0].checked = false;
  box.querySelectorAll("[data-ep]")[0].onchange();
  await box.querySelector('[data-act="start"]').onclick();
  requireOk(calls.filter((call) => call.url === "/api/agents/start" && call.body.kind === "api").length === 2,
    "deselecting the selected API endpoint must prevent starting against it");

  const manualTile = box.querySelectorAll(".lz-kind").find((tile) => tile.dataset.k === "manual");
  manualTile.onclick();
  requireOk(box.querySelector('[data-f="model"]') && box.html.includes("Another model"), "provider without a model list keeps single-model fallback");
  const manualMode = box.querySelector('[data-f="model"]');
  manualMode.value = "__other";
  manualMode.onchange();
  requireOk(box.querySelector('[data-f="model"]').value === "__other", "CLI custom mode must remain visibly selected");
  const cliCustom = box.querySelector('[data-f="custom"]');
  cliCustom.value = "manual-cli-model";
  cliCustom.oninput();
  await box.querySelector('[data-act="start"]').onclick();
  const cliSingleCall = calls.find((call) => call.url === "/api/agents/start" && call.body.kind === "manual");
  requireOk(cliSingleCall.body.options.model === "manual-cli-model", "CLI single-model fallback must start the typed name");

  const manualTileAgain = box.querySelectorAll(".lz-kind").find((tile) => tile.dataset.k === "manual");
  manualTileAgain.onclick();
  const manualMultiSelect = box.querySelector('[data-f="model"]');
  manualMultiSelect.value = "__multiple";
  manualMultiSelect.onchange();
  requireOk(box.querySelector('[data-act="start"]').disabled, "empty Multiple models selection must disable start");
  for (const name of ["first-custom", "second-custom"]) {
    const input = box.querySelector('[data-f="manual-model"]');
    input.value = name;
    box.querySelector('[data-act="add-cli-model"]').onclick();
  }
  requireOk(box.querySelectorAll("[data-model-choice]:checked").length === 2, "manual model names must remain visible and removable");
  await box.querySelector('[data-act="start"]').onclick();
  const manualCall = calls.filter((call) => call.url === "/api/agents/start" && call.body.kind === "manual").at(-1);
  requireOk(JSON.stringify(manualCall.body.options.models) === JSON.stringify(["first-custom", "second-custom"]),
    `no-list Multiple mode must send only typed model names: ${JSON.stringify(manualCall.body.options.models)}`);

  const selections = {
    api: [{ endpoint: "e1", model: "m-one" }, { endpoint: "e1", model: "m-two" }, { endpoint: "e1", model: "m-three" }],
    claude: ["opus", "sonnet", "haiku"],
    manual: ["first-custom", "second-custom", "third-custom"],
  };
  for (const kind of Object.keys(selections)) for (const count of [1, 2, 4, 8]) {
    vm.runInContext(`LZ.kind=${JSON.stringify(kind)}; LZ.eps=["e1"]; LZ.count=${count}; LZ.model=""; LZ.multiple=true; LZ.models=${JSON.stringify(selections[kind])};`, ctx);
    ctx.renderLauncher(box);
    requireOk(!box.querySelector('[data-act="start"]').disabled,
      `${kind} should allow ${selections[kind].length} selected models with ${count} sessions each`);
    requireOk(box.html.includes(`Each selected model gets ${count} session`), "multiple mode should explain per-model session count");
    requireOk(box.querySelector('[data-act="start"]').textContent.includes(`= ${selections[kind].length * count} agents`),
      "the start label must show the total number of helpers");
  }
  const overCapModels = Array.from({ length: 7 }, (_, index) => ({ endpoint: "e1", model: `model-${index}` }));
  vm.runInContext(`LZ.kind="api"; LZ.eps=["e1"]; LZ.count=4; LZ.multiple=true; LZ.models=${JSON.stringify(overCapModels)};`, ctx);
  ctx.renderLauncher(box);
  const overCap = box.querySelector('[data-act="start"]');
  requireOk(overCap.disabled, "more than 24 helpers in one start must disable Start");
  requireOk(overCap.textContent.includes("= 28 agents") && overCap.textContent.includes("at most 24") &&
    overCap.textContent.includes("pick fewer models"), `the label must say why Start is disabled: ${overCap.textContent}`);
  calls.length = 0;
  ctx.S.st.settings.launcher = {
    kind: "api", multiple: true, count: 2, endpoints: ["e1", "gone"],
    models: [{ endpoint: "gone", model: "removed-model" }, { endpoint: "e1", model: "m-two" }],
  };
  vm.runInContext("LZ.kind=null; LZ.models=[]; LZ.apiSingleModel=null; LZ.multiple=false; LZ.eps=[];", ctx);
  ctx.renderLauncher(box);
  await settle();
  const restored = box.querySelectorAll("[data-model-choice]:checked");
  requireOk(restored.length === 1 && restored[0].dataset.modelChoice === "e1::m-two",
    "a saved launcher must restore only models of endpoints that still exist");
  requireOk(box.html.includes("Start 1 model") && box.html.includes("= 2 agents"),
    "the restored launcher's label must count only the models it will start");
  requireOk(!box.querySelector('[data-act="start"]').disabled, "a restored selection under the cap must start");
  await box.querySelector('[data-act="start"]').onclick();
  const restoredCall = calls.filter((call) => call.url === "/api/agents/start" && call.body.kind === "api").at(-1);
  requireOk(restoredCall && restoredCall.body.count === 2 &&
    JSON.stringify(restoredCall.body.options.models) === JSON.stringify([{ endpoint: "e1", model: "m-two" }]),
    `a restored launcher must send only current endpoint/model pairs: ${JSON.stringify(restoredCall && restoredCall.body)}`);
  vm.runInContext(`LZ.kind="api"; LZ.eps=["e1"]; LZ.count=1; LZ.multiple=true; LZ.models=${JSON.stringify(selections.api)};`, ctx);
  ctx.renderLauncher(box);
  box.querySelectorAll("[data-step]").find((button) => button.dataset.step === "1").onclick();
  requireOk(box.querySelector("[data-model-limit]").textContent.includes("2 sessions"),
    "API per-model count guidance must update with the How many control");
  console.log("launcher model selections passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
