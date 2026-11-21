"""Proposal generation that reads the project runs in the proposal subprocess and asks the app for what it needs."""
import os
from types import SimpleNamespace

from decompnet.app import generation_calls
from decompnet.app.diff_gated_repairs import proposals
from decomprepair.symbols import Symbol, SymbolTable

KMC = 'gcc-2.7.2-kmc'
SOURCE = 'int f(int k) { switch (k) { case 2: return 11; case 7: return 22; default: return 0; } }'
TABLES = [{'case': 2, 'table': 100, 'target': 202, 'candidate': 201},
          {'case': 7, 'table': 100, 'target': 201, 'candidate': 202}]


def test_a_call_in_the_subprocess_gives_what_it_gives_in_the_server():
    args = (SOURCE, 'f', '', 'mips', KMC, [], '', TABLES)
    expected = proposals(*args)
    assert expected and generation_calls.generate('diff_gated_repairs.proposals', args, {'flags': []}) == expected


def test_the_child_asks_the_app_for_symbols_and_original_bytes():
    table = SymbolTable([Symbol('gTable', 0x800E41DC, 8, 'data')])
    reads = []

    def read(symbol, width):
        reads.append((symbol, width))
        return b'\x80\x0e\x41\xdc'
    out = dict(generation_calls.generate('probe.callbacks', ('gTable',), {}, resolve=table.resolve, symbols=table,
                                         read_data=read))
    assert out['resolve'] == str(0x800E41DC) and out['by_name'] == 'gTable' and out['missing'] == 'None'
    assert out['read'] == '800e41dc' and reads == [('gTable', 4)]
    assert int(out['pid']) != os.getpid()  # it ran in the child


def test_a_question_the_app_cannot_answer_gives_none_instead_of_failing_the_generation():
    def broken(symbol, width):
        raise OSError('no such overlay')
    out = dict(generation_calls.generate('probe.callbacks', ('gTable',), {}, read_data=broken))
    assert out['read'] == ''


def test_an_input_that_cannot_be_sent_runs_in_the_server():
    out = dict(generation_calls.generate('probe.callbacks', ('gTable',), {'unused': SimpleNamespace()}))
    assert int(out['pid']) == os.getpid()


def test_the_handlers_answer_from_the_projects_tables():
    table = SymbolTable([Symbol('gTable', 0x800E41DC, 8, 'data')])
    answers = generation_calls.handlers(table.resolve, table, lambda symbol, width: b'\x01\x02')
    assert answers['resolve']('gTable') == 0x800E41DC and answers['by_name']('gTable')['address'] == 0x800E41DC
    assert answers['by_name']('x') is None and answers['read_data']('gTable', 2) == '0102'


def _conversion_cases():
    from tests.test_target_conversion_search import CASES
    return CASES


def test_every_measured_conversion_case_gives_the_same_candidates_in_the_subprocess():
    """The real families, with real rows and a read_data the child has to ask the app for."""
    import struct

    from tests.test_target_conversion_search import run

    def read(name, size):
        return struct.pack('>d', 2.0 ** 32) if (name, size) == ('bias', 8) else None
    checked = 0
    for case in _conversion_cases():
        _, compiler, source, rows, assembly, _ = case
        expected = run(case)
        got = generation_calls.generate(
            'diff_gated_repairs.proposals', (source, 'f', '', 'mips', compiler, rows, assembly), {'big_endian': True},
            read_data=read)
        assert [tuple(item) for item in got] == [tuple(item) for item in expected], case[0]
        checked += 1
    assert checked >= 10
