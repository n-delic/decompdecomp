'use strict';
// The function drawer's Rename… button: validates the typed name, then uses the checked rename job.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('src/decompnet/app/static/js/function.js', 'utf8');
const page = fs.readFileSync('src/decompnet/app/static/index.html', 'utf8');
assert.ok(page.includes('id="fnRename"'), 'the drawer has a Rename button');
assert.ok(source.includes('$("fnRename").onclick = () => renameFunctionDialog(d);'), 'the button opens the dialog');

function element(extra = {}) {
  return {dataset: {}, value: '', hidden: true, textContent: '', focus() {}, select() {}, ...extra};
}
const input = element({value: 'local'}), go = element(), log = element(), close = element();
const calls = [], errors = [];
const panel = {querySelector: selector => ({'[data-fn-new-name]': input, '[data-rename-go]': go,
                                             '#fnRenameLog': log, '[data-rename-close]': close})[selector]};
const ctx = vm.createContext({
  S: {fn: 'local'}, esc: s => String(s), toast: () => {}, closeDialog: () => {},
  dialog: (title, html) => { ctx.opened = {title, html}; return panel; },
  showJobError: (_log, message) => errors.push(message),
  useSuggestedName: (button, detail, logId, rootId, endpoint) => {
    calls.push({name: button.dataset.name, old: detail.name, logId, rootId, endpoint});
    return Promise.resolve();
  },
});
vm.runInContext('let nameChangeBusy = false;\n' +
  source.slice(source.indexOf('function renameFunctionDialog'), source.indexOf('function projectPeersHTML')), ctx);
vm.runInContext('renameFunctionDialog({name: "local"})', ctx);
assert.equal(ctx.opened.title, 'Rename function');
assert.ok(ctx.opened.html.includes('only in capitals'), 'the dialog says why a capitals-only name is refused');
assert.equal(close.onclick, ctx.closeDialog);

go.onclick();
assert.match(errors.pop(), /already has that name/);
input.value = '9bad';
go.onclick();
assert.match(errors.pop(), /C identifier/);
assert.equal(calls.length, 0, 'invalid names never reach the server');

input.value = '  decode_packet ';
input.onkeydown({key: 'Enter', preventDefault() {}});
assert.deepEqual(calls, [{name: 'decode_packet', old: 'local', logId: 'fnRenameLog', rootId: 'fnRenameActions',
                          endpoint: '/api/rename-function'}]);
