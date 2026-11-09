"""The settings save handler preserves argument boundaries, including when reopening and saving."""
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node for UI handler test")
def test_compiler_settings_quoted_flags():
    source = (Path(__file__).parents[1] / "src/decompnet/app/static/js/steering.js").read_text()
    helpers = source[source.index("function parseCompilerFlags("):source.index("/* ------------------------------------------------------------------ drawers")]
    start = source.index('$("setTcSave").onclick = async () => {')
    handler = source[start:source.index("\n};", start) + 3]
    flags = ["-O3,p", "-sdatathreshold", "8", "-pragma", "divbyzerocheck on"]
    cases = [flags, ["", "a'b", 'a"b', r'C:\SDK files\include', '$(id)', '`id`'], ["-DNAME=a b", "-I", "some path"]]
    script = helpers + "\nconst cases = " + json.dumps([[shlex.join(v), v] for v in cases]) + r''';
const assert = require("node:assert/strict");
for (const [text, args] of cases) {
  assert.deepEqual(parseCompilerFlags(text), args);
  assert.deepEqual(parseCompilerFlags(quoteCompilerFlags(args)), args);
}
assert.throws(() => parseCompilerFlags('-pragma "unfinished'));
assert.throws(() => parseCompilerFlags('trailing\\'));
const elements = {};
const $ = (id) => elements[id] ||= {value: "", disabled: false};
const S = {slug: "test", ov: {kind: "rom"}};
let saved;
const api = async (url, options) => { saved = options.body; return {}; };
const renderStats = () => {}, renderProjectSettings = () => {}, toast = () => {}, esc = String;
'''
    script += handler + r'''
(async () => {
  $("setFlags").value = cases[0][0];
  $("setTc").value = "mwcps2-2.3.3";
  await $("setTcSave").onclick();
  assert.deepEqual(saved.flags, cases[0][1]);
  $("setFlags").value = quoteCompilerFlags(saved.flags);
  await $("setTcSave").onclick();
  assert.deepEqual(saved.flags, cases[0][1]);
  saved = null;
  $("setFlags").value = '-pragma "unfinished';
  await $("setTcSave").onclick();
  assert.equal(saved, null);
  assert.equal($("setTcSave").disabled, false);
})().catch((e) => { console.error(e); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
