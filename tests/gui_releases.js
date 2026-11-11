/* The "Other releases of this game" panel (steering.js) with the shared progress helpers from base.js. */
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js', name), 'utf8');
const base = read('base.js'), steering = read('steering.js');
const tick = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };

function element() {
  const node = {hidden: false, disabled: false, textContent: '', dataset: {}, children: [], buttons: [], attrs: {}, style: {setProperty() {}},
    className: '', classList: {add() {}, remove() {}}, append(child) { this.children.push(child); }, remove() { this.removed = true; },
    before(child) { this.children.push(child); }, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    querySelectorAll(selector) { const key = selector.match(/\[data-([a-z-]+)\]/)?.[1]; return key ? this.buttons.filter(b => b.key === key) : this.buttons; },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }};
  let html = '';
  Object.defineProperty(node, 'innerHTML', {get: () => html, set(value) {
    html = value;  // each button of the markup, by its data- attribute, as the page would have it
    node.buttons = [...value.matchAll(/<button[^>]*data-([a-z-]+)(?:="([^"]*)")?/g)].map(([, key, arg]) => {
      const button = element(); button.key = key;
      button.dataset[key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = arg; return button;
    });
  }});
  return node;
}

function setup(replies) {
  const nodes = new Map(), calls = [], toasts = [];
  const context = vm.createContext({console, Map, Set, Math, Number, Promise, Error, Object, String, encodeURIComponent,
    S: {slug: 'ntsc'}, $: id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    document: {createElement: element}, esc: s => String(s), toast: (text, kind) => toasts.push(text),
    api: async (url, options) => { calls.push({url, body: options?.body}); const reply = replies(url, options); if (reply instanceof Error) throw reply; return reply; },
    followJob: async () => context.jobResult()});
  vm.runInContext(base.slice(base.indexOf('function beginWork('), base.indexOf('async function followJob(')), context);
  vm.runInContext(base.slice(base.indexOf('function showJobError('), base.indexOf('/*', base.indexOf('function showJobError('))), context);
  vm.runInContext(steering.slice(steering.indexOf('/* Other releases of this game'), steering.indexOf('function renderAutopilot(ov) {')), context);
  return {context, nodes, calls, toasts, $: context.$};
}

const pal = {slug: 'pal', name: 'Rally (Europe)', region: 'Europe', game_code: 'NVLP', matched: 2093, functions: 2121};

(async () => {
  {
    // A release found by its game code can be linked; linking redraws the panel with the names button.
    let linked = null;
    const t = setup((url, options) => {
      if (url.startsWith('/api/regional?')) return {supported: true, linked, candidates: linked ? [] : [{...pal, evidence: 'game code', shared: null}]};
      if (url === '/api/regional') { linked = options.body.other ? {...pal, last: null} : null; return {supported: true, linked, candidates: []}; }
      return {};
    });
    t.context.renderReleases({slug: 'ntsc', kind: 'rom'});
    await tick();
    assert.equal(t.$('releasePanel').hidden, false);
    assert.match(t.$('releaseBody').innerHTML, /same game code/);
    // The overview polls every second: with nothing changed, the button the user is clicking stays the same one.
    const button = t.$('releaseBody').querySelector('[data-release-link]');
    t.context.renderReleases({slug: 'ntsc', kind: 'rom'});
    assert.equal(t.$('releaseBody').querySelector('[data-release-link]'), button);
    button.onclick();
    assert.equal(t.$('releaseLog').children.filter(c => c.className.startsWith('workbar') && !c.removed).length, 1);
    await tick();
    assert.ok(t.$('releaseLog').children.filter(c => c.className.startsWith('workbar')).every(c => c.removed));
    assert.equal(JSON.stringify(t.calls.at(-1)), JSON.stringify({url: '/api/regional', body: {slug: 'ntsc', other: 'pal'}}));
    assert.match(t.$('releaseBody').innerHTML, /Linked:/);
    assert.ok(t.$('releaseBody').querySelector('[data-release-names]'));
    assert.ok(t.$('releaseBody').querySelector('[data-release-code]'));
    t.context.renderReleases({slug: 'ntsc', kind: 'rom'});  // the next poll keeps showing the link
    assert.match(t.$('releaseBody').innerHTML, /Linked:/);
  }
  {
    // Bringing names over shows the shared bar while it works, and clears it on success and on failure.
    let fail = false;
    const t = setup(url => url === '/api/regional/names' ? (fail ? new Error('Link this project with another release of the game first.') : {job: 'j1'})
      : {supported: true, linked: {...pal, last: {renamed: 5, paired: 9, identical: 8, header: {added: 1}, rejected: 0}}, candidates: []});
    t.context.jobResult = async () => { assert.equal(t.$('releaseLog').children.filter(c => c.className.startsWith('workbar') && !c.removed).length, 1); return {renamed: 5}; };
    t.context.renderReleases({slug: 'ntsc', kind: 'rom'});
    await tick();
    assert.match(t.$('releaseBody').innerHTML, /5 names brought over/);
    t.$('releaseBody').querySelector('[data-release-names]').onclick();
    await tick();
    assert.equal(t.toasts.length, 1);
    assert.ok(t.$('releaseLog').children.filter(c => c.className.startsWith('workbar')).every(c => c.removed));
    fail = true;
    t.$('releaseBody').querySelector('[data-release-names]').onclick();
    await tick();
    assert.ok(t.$('releaseLog').children.filter(c => c.className.startsWith('workbar')).every(c => c.removed));
    assert.match(t.$('releaseLog')._errorBox.textContent, /Link this project/);
  }
  {
    // Project-mode games have no panel.
    const t = setup(() => ({supported: true, linked: null, candidates: []}));
    t.context.renderReleases({slug: 'ntsc', kind: 'fork'});
    assert.equal(t.$('releasePanel').hidden, true);
    assert.equal(t.calls.length, 0);
  }
  console.log('Other releases panel passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
