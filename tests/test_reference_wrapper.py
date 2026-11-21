from types import SimpleNamespace

import pytest

from decompnet.app.reference_wrapper import analyze
from decompnet.app.tools import _redraft
from decompnet.network import NetworkError

URL = 'https://github.com/demo/project/blob/' + '1'*40 + '/wrapper.cpp'
SHA = 'a'*64
SOURCE = '''
uint32_t out = call_sp + 0x80U;
ctx->r29 = static_cast<gpr>(static_cast<int32_t>(call_sp));
ctx->r4 = static_cast<gpr>(static_cast<int32_t>(out));
ctx->r5 = count;
ctx->r6 = flags;
ctx->r7 = other;
MEM_W(0x10, call_sp) = out;
func_80001234(rdram, ctx);
if (ctx->r2 == 0) {}
'''
ASM = 'addiu $sp, $sp, -0xc0\nlw $t1, 0xd0($sp)'


def test_wrapper_checks_revision_and_cross_checks_stack_argument():
    prototype, evidence = analyze(SOURCE, URL, SHA, SHA, 'sample', 0x80001234, ASM)
    assert prototype.startswith('int sample(void * arg0, unsigned int arg1,')
    assert 'sp+0x10=out (stack load confirmed)' in evidence
    assert 'caller tests v0 as a status' in evidence
    with pytest.raises(NetworkError, match='differs'):
        analyze(SOURCE, URL, SHA, 'b'*64, 'sample', 0x80001234, ASM)
    with pytest.raises(NetworkError, match='missing'):
        analyze(SOURCE, URL, SHA, SHA, 'sample', 0x80001234, ASM.replace('0xd0', '0xd4'))


def test_redraft_passes_context_then_saves_function_evidence(monkeypatch):
    calls = []
    net = SimpleNamespace(
        reference_wrapper=lambda *args: ('int sample(int);', 'evidence'),
        redraft=lambda *args: calls.append(args) or 'draft',
        add_note=lambda *args: calls.append(args),
    )
    monkeypatch.setattr('decompnet.app.tools.agent_tokens.brief', lambda net, text, slug, agent: text)
    assert _redraft(net, 'game', 'sample', '', 'agent', SOURCE, URL, SHA) == 'evidence\n\ndraft'
    assert calls == [('game', 'sample', 'int sample(int);\n'), ('agent', 'game', 'evidence', 'sample')]
    calls.clear()
    _redraft(net, 'game', 'sample', 'int sample(short);', 'agent', SOURCE, URL, SHA)
    assert calls[0][-1] == 'int sample(short);'


def test_saved_register_swap_with_reordered_prologue_saves():
    from decompnet.app.allocation_search import saved_register_swap

    rows = [
        (' ', 'addiu $sp, $sp, -0x30', 'addiu $sp, $sp, -0x30'),
        ('i', 'sw $s1, 0x24($sp)', 'sw $s0, 0x20($sp)'),
        ('r', 'move $s1, $a2', 'move $s0, $a2'),
        ('i', 'sw $s0, 0x20($sp)', 'sw $s1, 0x24($sp)'),
        ('r', 'move $s0, $zero', 'move $s1, $zero'),
    ]
    assert saved_register_swap(rows, 'mips')
    assert not saved_register_swap(rows, 'ppc')
    assert not saved_register_swap(rows + [('i', 'li $a0, 1', 'li $a0, 2')], 'mips')


def test_wrapper_fetches_only_the_pinned_raw_file_after_revision_check(monkeypatch):
    requests = []
    monkeypatch.setattr('decompnet.app.reference_wrapper.fetch_source', lambda url: requests.append(url) or SOURCE)
    with pytest.raises(NetworkError, match='differs'):
        analyze('', URL, SHA, 'b'*64, 'sample', 0x80001234, ASM)
    assert not requests
    _, evidence = analyze('', URL, SHA, SHA, 'sample', 0x80001234, ASM)
    assert requests == ['https://raw.githubusercontent.com/demo/project/' + '1'*40 + '/wrapper.cpp']
    assert 'Source fetched from commit' in evidence


def test_wrapper_findings_appear_in_the_function_brief(tmp_path, monkeypatch):
    from decompnet.network import Network

    net = Network(tmp_path / 'test.db')
    agent = net.authenticate(net.register_agent('reference-reader', 'local'))
    with net.project_registration(slug='game', name='Demo', description='', arch='mips',
                                  big_endian=True, root=tmp_path, config={}) as (db, pid):
        db.execute("INSERT INTO functions(project_id,name,size,n_insns) VALUES(?, 'sample', 4, 1)", (pid,))
    monkeypatch.setattr(net, 'reference_wrapper', lambda *args: ('int sample(int);', 'Verified reference argument locations.'),
                        raising=False)
    monkeypatch.setattr(net, 'redraft', lambda *args: 'new draft', raising=False)
    monkeypatch.setattr('decompnet.app.tools.agent_tokens.brief', lambda net, text, slug, agent: text)
    _redraft(net, 'game', 'sample', '', agent, SOURCE, URL, SHA)
    note = net.notes_for_brief(net._function('sample', 'game'))
    assert any('Verified reference argument locations.' in line for line in note)
