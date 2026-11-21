const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("src/decompnet/app/static/index.html", "utf8");
const agentsTab = html.indexOf('data-pane="agents"');
const checklist = html.indexOf('id="polishChecklist"');
assert(agentsTab >= 0 && checklist > agentsTab && checklist < html.indexOf("data-pane=", agentsTab + 1));

const source = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
const panel = {dataset: {}, open: true};
const pc = {};
const requests = [];
function makeForm() {
  const inputs = ["require_name", "compile_64bit", "pointer_casts"].map(name => ({name, checked: false, disabled: true}));
  const button = {disabled: true}, log = {}, format = {disabled: true, value: "camelCase"};
  return {
    inputs, button, log, format, innerHTML: "clean template",
    cloneNode: makeForm, replaceWith(next) { form = next; },
    querySelectorAll() { return inputs; },
    querySelector(selector) { return ({button, select: format})[selector] || log; },
  };
}
let form = makeForm();
const S = {slug: "first"};
const context = vm.createContext({
  S, $: id => ({polishChecklist: panel, polishChecklistPc: pc, polishChecklistForm: form})[id],
  api(path, options) {
    return new Promise((resolve, reject) => requests.push({path, options, resolve, reject}));
  },
  beginWork(log, label, controls) {
    log.label = label;
    controls.forEach(control => { control.disabled = true; });
    return () => { log.label = ""; };
  },
  showJobError(log, message) { log.error = message; },
});
vm.runInContext(source.slice(source.indexOf("function renderPolishChecklist("),
  source.indexOf("async function renderProjectSettings(")), context);
const render = (slug, fork = false) => { S.slug = slug; context.renderPolishChecklist(slug, fork); };
const settle = () => new Promise(resolve => setImmediate(resolve));
const submit = () => form.onsubmit({preventDefault() {}});

(async () => {
  render("first");
  const first = form;
  assert.equal(first.log.label, "Loading checklist");
  assert(first.button.disabled);
  requests[0].resolve({selected: ["require_name", "pointer_casts"], name_format: "camelCase"});
  await settle();
  assert(first.inputs[0].checked && first.inputs[2].checked);
  assert.equal(first.format.value, "camelCase");
  assert(!first.format.disabled);
  assert(!first.button.disabled);

  first.format.value = "PascalCase";
  first.inputs[0].checked = false;
  first.inputs[0].onchange();
  assert(first.format.disabled);
  first.inputs[1].checked = true;
  submit();
  submit();
  assert.equal(requests.length, 2);
  assert.equal(first.log.label, "Saving checklist");
  assert(first.button.disabled);

  render("second", true);
  assert(!pc.hidden);
  render("first");
  assert.equal(form, first);
  assert(first.button.disabled);
  assert.equal(requests.length, 3);
  requests[2].resolve({selected: ["require_name"], name_format: "camelCase"});
  requests[1].reject(new Error("Try again."));
  await settle();
  assert.equal(first.log.error, "Try again.");
  assert(!first.inputs[0].checked && first.inputs[1].checked && first.inputs[2].checked);
  assert(!first.button.disabled);

  submit();
  assert.deepEqual(Array.from(requests[3].options.body.selected), ["compile_64bit", "pointer_casts"]);
  assert.equal(requests[3].options.body.name_format, "PascalCase");
  requests[3].resolve({selected: ["compile_64bit", "pointer_casts"], name_format: "PascalCase"});
  await settle();
  assert.equal(first.log.error, "");
  render("second", true);
  assert(form.inputs[0].checked && form.inputs.slice(1).every(input => !input.checked));
  assert.equal(form.format.value, "camelCase");
})().catch(error => { console.error(error); process.exitCode = 1; });
