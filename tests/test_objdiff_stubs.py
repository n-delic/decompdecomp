"""A byte-identical assembly placeholder is still not recovered C."""

from decompnet.forks.objdiff import _exclude_stubs


def test_reports_remove_stub_matches_but_keep_real_c_matches(tmp_path):
    (tmp_path / 'unit.c').write_text('#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n')
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}
    measures = {'total_functions': 2, 'matched_functions': 2, 'total_code': '16', 'matched_code': '16'}
    rep = {'measures': dict(measures), 'units': [{'name': 'unit', 'measures': dict(measures), 'functions': [
        {'name': 'stub', 'size': '8', 'fuzzy_match_percent': 100},
        {'name': 'actual', 'size': '8', 'fuzzy_match_percent': 100}]}]}
    out = _exclude_stubs(tmp_path, cfg, rep)
    assert out['measures']['matched_functions'] == 1
    assert out['measures']['matched_code'] == '8'
    assert out['measures']['matched_code_percent'] == 50
    assert out['measures']['total_functions'] == 2
    assert 'fuzzy_match_percent' not in out['units'][0]['functions'][0]
    assert out['units'][0]['functions'][1]['fuzzy_match_percent'] == 100
    assert _exclude_stubs(tmp_path, cfg, rep)['measures']['matched_code'] == '8'  # idempotent


def test_only_possible_stub_names_need_source_parsing(tmp_path, monkeypatch):
    from decompnet.forks import csource

    (tmp_path / 'unit.c').write_text('#pragma GLOBAL_ASM("asm/stub.s")\n'
                                     'int actual(void) { return 1; }\n'
                                     'int actual2(void) { return 2; }\n')
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}
    measures = {'total_functions': 3, 'matched_functions': 3, 'total_code': '24', 'matched_code': '24'}
    rep = {'measures': dict(measures), 'units': [{'name': 'unit', 'measures': dict(measures), 'functions': [
        {'name': name, 'size': '8', 'fuzzy_match_percent': 100} for name in ('stub', 'actual', 'actual2')]}]}
    seen = []
    real_find = csource.find
    def find(text, name):
        seen.append(name)
        return real_find(text, name)
    monkeypatch.setattr(csource, 'find', find)
    out = _exclude_stubs(tmp_path, cfg, rep)
    assert seen == ['stub']
    assert out['measures']['matched_functions'] == 2
    assert [f.get('fuzzy_match_percent') for f in out['units'][0]['functions']] == [None, 100, 100]
