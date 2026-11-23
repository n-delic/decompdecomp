"""libultra's remote-debug monitor (__rmon*, rmon*) is SDK code, set aside before agents or the Deep Permuter see it.

A retail N64 game had 45 __rmon functions named by library detection that stayed open: the port kit called the names
"the game's own", so the library setting never set them aside, and the Deep Permuter only asked the digest tables
(this build is shorter than the debug archive's). An agent matched __rmonSendHeader by hand. Homemade bodies only.
"""
import json
import threading

import pytest

from decompnet.app import sdk_assembly, source_assembly
from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64 import N64
from decompnet.consoles.n64.port import _sdk_group
from decompnet.rom import Picked, split


def body(n):
    return bytes.fromhex('03e00008') + (0x24020000 + n).to_bytes(4, 'big')  # jr ra; li v0,n: homemade


RECORDED = {'naming': {'method': 'sdk_signature', 'source_corpora': ['Toy SDK']}}
LAYOUT = [  # (name, meta): in address order, 8 bytes each
    ('__rmonSendFault', RECORDED),                # found by library detection (a recorded signature)
    ('__rmonSendHeader', {}),                     # the game's own symbol, beside it: SDK by its family name
    ('__rmonHitBreak', {}),                       # beside a name-only one only: never chained
    ('gameUpdate', {}),                           # the game's own code
    ('__rmonGuess', {'naming': {'method': 'rename', 'name': '__rmonGuess'}}),  # an agent's rename: not evidence
    ('__rmonSetComm', RECORDED),
]


def import_game(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db', tmp_path)
    code = b''.join(body(i) for i in range(len(LAYOUT)))

    def fake_split(workdir, *args, **kwargs):
        functions = []
        for index in range(len(LAYOUT)):
            addr = 0x80001000 + index * 8
            lines = [split.AsmLine(index * 8 + i, addr + i, int.from_bytes(code[index * 8 + i:index * 8 + i + 4], 'big'), 'nop', '')
                     for i in range(0, 8, 4)]
            functions.append(split.RomFunction(f'func_{addr:08X}', addr, index * 8, 'code', lines, workdir / 'toy.s'))
        return split.SplitResult(functions, [], [split.Segment('code', 0, len(code), 0x80001000)], {}, 0, '', '', '')
    monkeypatch.setattr(N64, 'split_game', fake_split)
    monkeypatch.setattr(N64, 'debug_info', lambda *a: None)
    picked = Picked('n64', code, 'toy.z64', {'title': 'Toy', 'size': len(code), 'game_code': 'TEST', 'region': 'US',
                                             'compiler_guess': 'KMC', 'sha1': 'toy'}, True)
    slug = net.import_split(N64, picked, toolchain='gcc-2.7.2-kmc', flags=['-O2'], log=lambda s: None)
    proj = net.project_row(slug)
    with net.project_store(proj['id']).tx() as db:
        for index, (name, meta) in enumerate(LAYOUT):
            if meta.get('naming', {}).get('method') == 'sdk_signature':
                meta = {'naming': {**meta['naming'], 'name': name, 'source_name': name}}
            db.execute("UPDATE functions SET name=?,status='open',meta=? WHERE vram=?",
                       (name, json.dumps(meta), 0x80001000 + index * 8))
    return net, slug


def statuses(net, slug):
    return {r['name']: r['status'] for r in net.project_store(net.project_row(slug)['id']).all('SELECT name,status FROM functions')}


def test_the_port_kit_files_the_remote_debugger_as_glue():
    for name in ('__rmonSendHeader', '__rmonHitBreak', 'rmonMain', 'rmonPrintf', 'rmonFindFaultedThreads', 'kdebugserver'):
        assert _sdk_group(name) == 'glue', name
    for name in ('read32', '_n_loadBuffer', 'send', 'rmonitor', 'gameRmonThing'):
        assert _sdk_group(name) is None, name  # the game's own names, even ones that look alike


def test_library_detection_and_family_names_set_the_remote_debugger_aside(tmp_path, monkeypatch):
    net, slug = import_game(tmp_path, monkeypatch)
    proj = net.project_row(slug)
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 3
    assert statuses(net, slug) == {'__rmonSendFault': 'asm', '__rmonSendHeader': 'asm', '__rmonHitBreak': 'open',
                                   'gameUpdate': 'open', '__rmonGuess': 'open', '__rmonSetComm': 'asm'}
    found = net._function('__rmonSendFault', slug)
    assert json.loads(found['meta'])['verified_assembly']['verification_method'] == 'sdk-signature'
    named = net._function('__rmonSendHeader', slug)
    proof = json.loads(named['meta'])['verified_assembly']
    # said as what it is: a name beside library code, never a digest or linked bytes
    assert proof['verification_method'] == 'sdk-name' and proof['kind'] == 'sdk' and proof['from_c']
    assert proof['name_evidence']['neighbour'] == '__rmonSendFault' and 'no signature or digest' in proof['evidence']
    assert 'object_sha256' not in proof and 'not linked' in proof['evidence']
    assert source_assembly.verified(proj, named)
    assert source_assembly.breakdown(net, proj)['exact_c'] == 0
    changed = dict(named); changed['target'] = b'bad!' + bytes(named['target'])[4:]
    assert not source_assembly.verified(proj, changed)  # a changed target undoes it


def test_the_set_aside_pass_files_library_code_before_asm_reason(tmp_path, monkeypatch):
    net, slug = import_game(tmp_path, monkeypatch)
    net.twins.set_aside_asm(net.project_row(slug))
    assert statuses(net, slug)['__rmonSendHeader'] == 'asm' and statuses(net, slug)['__rmonSetComm'] == 'asm'
    assert statuses(net, slug)['gameUpdate'] == 'open'


def test_the_set_aside_pass_waits_for_the_library_name(tmp_path, monkeypatch):
    """Before SDK naming, a detected function still has its func_ name: the port goes by names, so it waits."""
    net, slug = import_game(tmp_path, monkeypatch)
    proj = net.project_row(slug)
    with net.project_store(proj['id']).tx() as db:
        db.execute("UPDATE functions SET name='func_80001028' WHERE name='__rmonSetComm'")
    sdk_assembly.set_aside_known(net, proj)
    assert statuses(net, slug)['func_80001028'] == 'open'


def test_a_project_that_turned_the_library_setting_off_keeps_them_open(tmp_path, monkeypatch):
    net, slug = import_game(tmp_path, monkeypatch)
    sdk_assembly.choose_c(net, slug, False)
    assert sdk_assembly.set_aside_known(net, net.project_row(slug)) == 0
    assert set(statuses(net, slug).values()) == {'open'}


@pytest.mark.parametrize('single', [False, True])
def test_the_deep_permuter_sets_them_aside_instead_of_searching(tmp_path, monkeypatch, single):
    from decompnet.app import deep_permuter
    from decompnet.app.deep_sweep import Sweeps
    from decompnet.app.support import Jobs
    from tests.test_deep_permuter import Log

    net, slug = import_game(tmp_path, monkeypatch)
    proj = net.project_row(slug)
    with net.project_store(proj['id']).tx() as db:
        db.execute("UPDATE functions SET best_score=10,best_source='void f(void) {}'")

    def unexpected_search(*args, **kwargs):
        pytest.fail('The remote debugger reached C candidate search.')
    monkeypatch.setattr(deep_permuter, '_run', unexpected_search)
    if single:
        result = deep_permuter.run(net, slug, '__rmonSendHeader', 1, 10, Log())
        assert result['reason'] == 'Identified and set aside as SDK library code' and result['tested'] == 0
    else:
        class Prepared(Exception):
            pass

        class FirstStep(Log):  # the sweep's SDK step only: what follows searches the game's own code
            def __call__(self, line):
                super().__call__(line)
                if line.startswith('Identified '):
                    raise Prepared(line)
        sweeps = Sweeps(net, Jobs())
        monkeypatch.setattr(sweeps, 'search_one', unexpected_search)
        with pytest.raises(Prepared, match='Identified 3 SDK library functions'):
            sweeps.run(slug, 1, 10, FirstStep())
    status = statuses(net, slug)
    assert status['__rmonSendHeader'] == 'asm'
    if not single:
        assert status['__rmonSendFault'] == 'asm' and status['__rmonSetComm'] == 'asm'
