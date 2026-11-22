const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function testScreenRepairs() {
  const shortcuts = {dataset: {}, hidden: true, querySelectorAll: () => [button]};
  const button = {dataset: {screenRepair: '0'}};
  const requests = [];
  const context = vm.createContext({
    $: () => shortcuts,
    S: {st: {app_repair: true}},
    location: {hash: '#/p/game'},
    esc: value => value.replaceAll('<', '&lt;'),
    setHtml: (element, html) => { element.innerHTML = html; },
    api: url => new Promise(resolve => { requests.push({url, resolve}); }),
  });
  vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/app-repair.js', 'utf8'), context);
  const opened = [];
  context.openAppRepair = (...args) => opened.push(args);
  const home = context.refreshScreenRepairs();
  assert.equal(requests[0].url, '/api/app-repair?screen=%23%2Fp%2Fgame%2Foverview');

  context.location.hash = '#/p/game/settings';
  const settings = context.refreshScreenRepairs();
  requests[1].resolve({repairs: [{source: 'failed-settings', task: '<settings>'}]});
  await settings;
  assert.match(shortcuts.innerHTML, /Open repair conversation · &lt;settings>/);
  button.onclick();
  assert.deepEqual(opened, [['failed-settings', '#/p/game/settings']]);
  requests[0].resolve({repairs: []});
  await home;
  assert.equal(shortcuts.hidden, false);

  context.location.hash = '#/p/other/settings';
  const other = context.refreshScreenRepairs();
  assert.equal(shortcuts.hidden, true);
  requests[2].resolve({repairs: []});
  await other;
  assert.equal(shortcuts.hidden, true);

  context.location.hash = '#/p/game/settings';
  const returned = context.refreshScreenRepairs();
  requests[3].resolve({repairs: [{source: 'failed-settings', task: 'Settings', state: 'done'}]});
  await returned;
  assert.equal(shortcuts.hidden, false);
  button.onclick();
  assert.deepEqual(opened.at(-1), ['failed-settings', '#/p/game/settings']);
}

testScreenRepairs().catch(error => { console.error(error); process.exitCode = 1; });
