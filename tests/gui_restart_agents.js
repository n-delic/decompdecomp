const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  return {
    children: [], textContent: '', hidden: false, disabled: false,
    get firstChild() { return this.children[0] || null; },
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren(...children) { this.children = children; },
    setAttribute() {},
  };
}

const controls = { restartAll: element(), restartAllFeedback: element() };
const stopped = [{ state: 'stopped' }];
const unavailable = [{ state: 'paused' }, { state: 'working' }, { state: 'stopped', configuration_error: true }];
const calls = [];
let resolve, reject, polled = 0, finished = 0;
const env = {
  S: { slug: 'game', sessions: stopped },
  $: id => controls[id],
  document: { createElement: element },
  beginWork(log, label) {
    log._workStatus = { textContent: label };
    return () => { finished++; };
  },
  showJobError(log, message) { log.textContent = message; },
  plural: (n, word) => n + ' ' + word + (n === 1 ? '' : 's'),
  api(path, { method, body }) {
    calls.push([path, method, body.slug]);
    return new Promise((success, failure) => { resolve = success; reject = failure; });
  },
  renderSessions(sessions) { env.renderAgentRestart(sessions); },
  poll() { polled++; },
};
const source = fs.readFileSync('src/decompnet/app/static/js/agents.js', 'utf8');
vm.createContext(env);
vm.runInContext(source.slice(source.indexOf('const agentRestarts'), source.indexOf('function renderSessions(')), env);

async function test() {
  const { restartAll: button, restartAllFeedback: feedback } = controls;
  env.renderAgentRestart(unavailable);
  assert(button.hidden);
  env.renderAgentRestart(stopped);
  assert(!button.hidden);
  const request = button.onclick();
  assert(button.disabled && !feedback.hidden);
  assert.equal(button.textContent, 'Restarting agents…');
  env.renderAgentRestart(stopped); // A poll still sees the old cards.
  await button.onclick();
  assert.deepEqual(calls, [['/api/agents/restart', 'POST', 'game']]);
  const work = feedback.firstChild.firstChild;
  assert.equal(work._workStatus.textContent, 'Restarting stopped agents');

  env.S.slug = 'other';
  env.renderAgentRestart(unavailable);
  assert(button.hidden && feedback.hidden);
  resolve({ restarted: ['one'], errors: [{ label: 'Two', message: 'Still stopping' }] });
  await request;
  assert.equal(polled, 0);
  assert(feedback.hidden);
  env.S.slug = 'game';
  env.renderAgentRestart(stopped);
  assert.equal(work._workStatus.textContent, 'Started 1 agent.');
  assert.equal(work.firstChild.firstChild.textContent, 'Two — Still stopping');
  assert(!button.disabled);
  const retry = button.onclick();
  reject(Error('Connection lost'));
  await retry;
  assert.equal(feedback.firstChild.firstChild.textContent, 'Connection lost');
  assert(!button.disabled);
  assert.equal(polled, 1);
  assert.equal(finished, 2);
}

test().catch(error => { console.error(error); process.exitCode = 1; });
