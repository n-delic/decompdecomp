"""Ghidra-backed Android briefs, header-aware redraft and autopilot, with a homemade native demo (#133)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.compiler import compile_source
from decompnet.consoles.android import ANDROID, drafts
from decompnet.forks import ghidra, ghidra_worker
from decompnet.project import compiler_config
from decompnet.rom.symbols import SymbolTable

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    if toolchains.installed('android-clang-3.5') is None or not ghidra.available():
        pytest.skip('needs pinned Android Clang 3.5, Ghidra and Java 21+')
    import sys
    subprocess.run([sys.executable, str(ROOT / 'projects/demo-android/build.py')], check=True, timeout=120)
    root = tmp_path_factory.mktemp('android-drafts')
    net = RomNetwork(root / 'app.db', root)
    slug = net.import_rom(ROOT / 'projects/demo-android/build/demo.apk', confirmed=True, log=lambda *_: None)
    yield net, slug
    net.drafter.stop(slug)
    ghidra_worker.close_all()


def test_ghidra_makes_a_real_draft_and_autopilot_judges_it(demo):
    net, slug = demo
    proj = net.project_row(slug)
    fn = net._function('abs_s32', slug)
    draft = net._seed_for(fn, proj)
    assert 'Ghidra' in draft and 'abs_s32' in draft
    agent, _ = net.local_agent('autopilot')
    assert net._autopilot_one(agent, proj, fn['id']) == 'match'
    fn = net._function('abs_s32', slug)
    assert fn['status'] == 'matched' and 'Ghidra' in fn['matched_source']
    wrong = 'int abs_s32(int x) { return x < 0 ? -x + 1 : x; }'
    assert not net._judge_with(proj, fn, 'android-clang-3.5', net.cfg(proj)['compiler']['flags'], wrong)[0]


def test_redraft_applies_the_supplied_prototype_and_does_not_leak_into_other_requests(demo):
    net, slug = demo
    first = net.redraft(slug, 'demo_twice', 'int demo_twice(int amount);')
    second = net.redraft(slug, 'demo_twice', 'int demo_twice(int value);')
    assert 'amount' in first and 'int demo_twice' in first
    assert 'value' in second and 'amount' not in second
    assert net._function('demo_twice', slug)['seed'] is None  # redraft is recorded nowhere


def test_header_changes_make_background_drafts_stale(demo):
    net, slug = demo
    proj = net.project_row(slug)
    fn = net._function('demo_identity', slug)
    net._seed_for(fn, proj)
    before = net._draft_plan(fn, proj)[0]
    agent, _ = net.local_agent('header-test')
    header = net._context(proj['id'])['header']
    net.edit_context(agent, slug, header, header + '\nint demo_identity(int position);\n', 'Name the argument.')
    proj = net.project_row(slug)
    assert net._draft_plan(fn, proj)[0] != before
    assert fn['id'] in net.stale_drafts(proj)
    assert 'position' in net._seed_for(net._function('demo_identity', slug), proj)


def test_a_missing_provider_is_explained_and_the_reason_survives_reopening(demo, monkeypatch):
    net, slug = demo
    monkeypatch.setattr(ANDROID, 'drafts_ready', lambda: False)
    # Synchronous runner makes persistence assertions deterministic without starting another background pass.
    import threading
    from types import SimpleNamespace
    def inline_thread(**kwargs):
        return type('Run', (), {'start': lambda self: kwargs['target'](*kwargs['args'])})()
    monkeypatch.setattr('decompnet.app.engine.autopilot.threading', SimpleNamespace(Thread=inline_thread, Event=threading.Event))
    state = net.start_autopilot(slug)
    assert 'Ghidra' in state['no_drafts']
    net.autopilots.pop(slug)
    assert 'Ghidra' in net.autopilot_status(slug)['no_drafts']


def test_autopilot_exposes_real_compile_counts_and_keeps_near_misses(demo):
    net, slug = demo
    state = net.start_autopilot(slug, workers=2)
    until = time.monotonic() + 90
    while state['state'] == 'running' and time.monotonic() < until:
        time.sleep(0.05)
    if state['state'] == 'running':
        net.stop_autopilot(slug)
    assert state['state'] == 'done'
    assert state['drafted'] > 0 and state['compiled'] > 0 and not state['no_drafts']
    assert net._function('demo_answer', slug)['status'] == 'matched'
    saved = json.loads(net.project_row(slug)['config'])['autopilot']
    assert saved['compiled'] == state['compiled']


def test_the_compilable_form_covers_types_subpieces_and_carry_operations(tmp_path):
    if toolchains.installed('android-clang-3.5') is None:
        pytest.skip('needs pinned Android Clang 3.5')
    raw = '''uint arithmetic(uint a, uint b) {
      uint value = a;
      value._0_1_ = SUB41(b,0);
      return CONCAT22(value,b) + ZEXT14(a) + SEXT14(b) + CARRY4(a,b) + SCARRY4(a,b) + SBORROW4(a,b);
    }'''
    source = drafts.compilable(raw, 'arithmetic', '', SymbolTable())
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'android-clang-3.5'}})
    result = compile_source(cfg, '', source)
    assert result.ok, result.log


def test_known_header_declarations_win_over_guessed_externs():
    raw = 'undefined4 get_value(void) { return shared; }'
    source = drafts.compilable(raw, 'get_value', 'extern unsigned char shared;', SymbolTable())
    assert 'extern unsigned int shared' not in source


def test_ghidra_global_getters_compile_back_to_pic_code(tmp_path):
    if toolchains.installed('android-clang-3.5') is None or not ghidra.available():
        pytest.skip('needs Android Clang 3.5 and Ghidra')
    from decompnet.limits import capped
    source = 'int value = 7; int get_value(void) { return value; }'
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'android-clang-3.5'}})
    compiled = compile_source(cfg, '', source)
    assert compiled.ok, compiled.log
    (tmp_path / 'input.o').write_bytes(compiled.obj)
    linker = toolchains.path('android-clang-3.5') / 'gcc/bin/arm-linux-androideabi-ld'
    subprocess.run(capped([str(linker), '-shared', str(tmp_path / 'input.o'), '-o', str(tmp_path / 'input.so')]),
                   check=True, capture_output=True, timeout=60)
    work = tmp_path / 'work'
    work.mkdir()
    net = RomNetwork(work / 'app.db', work)
    slug = net.import_rom(tmp_path / 'input.so', confirmed=True, log=lambda *_: None)
    proj = net.project_row(slug)
    fn = net._function('get_value', slug)
    agent, _ = net.local_agent('autopilot')
    outcome = net._autopilot_one(agent, proj, fn['id'])
    assert outcome == 'match', net._function('get_value', slug)['seed']
    ghidra_worker.close_all()


def test_redraft_uses_real_struct_fields_and_its_compilable_form_still_matches(demo):
    net, slug = demo
    context = 'typedef struct { int unused; int score; } Sample; int demo_field(Sample *sample);'
    text = net.redraft(slug, 'demo_field', context)
    assert 'sample->score' in text
    other = net.redraft(slug, 'demo_field', 'typedef struct { int unused; int height; } Other; int demo_field(Other *p);')
    assert 'p->height' in other and 'score' not in other
    proj = net.project_row(slug)
    fn = net._function('demo_field', slug)
    header = net._context(proj['id'])['header'] + '\n' + context
    source = ANDROID.machine_draft(net, proj, fn, header, valid=True)
    cfg = compiler_config(Path(proj['root']), {'compiler': net.cfg(proj)['compiler']})
    result = compile_source(cfg, header, source)
    assert result.ok, result.log
    assert net._evaluate(fn, proj, result.obj)[1]
