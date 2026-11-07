"""Export source isolation and verified header recovery, with homemade code only."""
from types import SimpleNamespace

from decompnet.app import repository_candidates as candidates


def test_isolation_keeps_line_numbers_target_literals_and_real_prototypes(monkeypatch):
    source='int helper(int a) { return a+1; }\nint entry(void) { return helper(2); }\n'
    monkeypatch.setattr(candidates,'ELFFile',lambda _:None)
    monkeypatch.setattr(candidates,'_function_symbols',lambda _:{'helper':None,'entry':None})
    got=candidates.single_source(source,b'obj','entry')
    assert got.count('\n')==source.count('\n')
    assert 'int helper(int a) ;' in got and 'return a+1' not in got
    assert source.splitlines()[1]==got.splitlines()[1]
    assert candidates.single_source(source.replace('int helper','static int helper'),b'obj','entry') is None
    assert candidates.single_source(source.replace('int helper','inline int helper'),b'obj','entry') is None


def test_recorded_context_is_only_a_hypothesis_and_current_is_first():
    net=SimpleNamespace(_context=lambda *args:{'header':'current'},
                        store=SimpleNamespace(one=lambda *args:{'header':'historic'}))
    net.project_store = lambda project:net.store
    assert list(candidates.contexts(net,{'id':1},{'id':2,'unit':'segment','matched_source':'C'}))==['current','historic']


def test_historical_compile_still_needs_exact_byte_and_layout_verification(monkeypatch):
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *args:True)
    net=SimpleNamespace(_context=lambda *args:{'header':'current'},
                        store=SimpleNamespace(one=lambda *args:{'header':'historic'}),
                        _compile=lambda p,h,*args:SimpleNamespace(ok=h=='historic',obj=b'obj',log='missing type'),
                        _evaluate=lambda *args:(None,False,['wrong bytes']),
                        _candidate_checks=lambda *args:{'export':{'state':'unmatched','ready':False}})
    net.project_store = lambda project:net.store
    row={'id':2,'unit':'segment','name':'entry','matched_source':'C'}
    assert candidates.select(net,{'id':1},row)[0] is None
    net._evaluate=lambda *args:(None,True,[])
    net._candidate_checks=lambda *args:{'export':{'ready':True}}
    assert candidates.select(net,{'id':1},row)[0]==('C','historic',False)
