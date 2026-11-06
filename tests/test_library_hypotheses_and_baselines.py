import hashlib
import json
from types import SimpleNamespace

from decompnet.app import baseline_status, sdk_hypotheses
from decompnet.elfobj import FunctionObj


def test_similar_library_is_only_a_hint(monkeypatch):
    rows=[dict(corpus='local',name='format',ops=json.dumps(['lw']*40),size=160)]
    net=SimpleNamespace(store=SimpleNamespace(all=lambda *args:rows),cfg=lambda p:{},
                        _target_obj=lambda f,p:FunctionObj('f','mips',True,b'',[]))
    monkeypatch.setattr(sdk_hypotheses.scoring,'disassemble',lambda obj:[SimpleNamespace(mnemonic='lw')]*40)
    hints=sdk_hypotheses.hints(net,{'arch':'mips','big_endian':1},{'size':160})
    assert hints[0]['kind']=='similar library code; unverified'
    assert hints[0]['name']=='format'
    net.cfg=lambda p:{'sdk_libraries':[]}
    assert sdk_hypotheses.hints(net,{'arch':'mips','big_endian':1},{'size':160})==[]


def test_historical_baseline_records_why():
    settings={'compiler':{'toolchain':'gcc','flags':['-O1']},'context_sha256':'old','scoring_revision':'old'}
    net=SimpleNamespace(store=SimpleNamespace(one=lambda *args:{'settings':json.dumps(settings)}),
                        is_rom=lambda p:True,cfg=lambda p:{'compiler':{'toolchain':'gcc','flags':['-O2']}},
                        _context=lambda *a:{'header':'new'})
    fn={'id':1,'status':'open','best_source':'source','best_score':10,'vram':0,'unit':''}
    result=baseline_status.status(net,{'id':1},fn)
    assert result['state']=='historical' and len(result['reasons'])==3
    settings.update(compiler={'toolchain':'gcc','flags':['-O2']},context_sha256=hashlib.sha256(b'new').hexdigest(),scoring_revision=baseline_status.revision())
    assert baseline_status.status(net,{'id':1},fn)['state']=='current'


def test_import_retains_needed_macro_dependencies_only():
    from decompnet.app.sdk_macros import context
    text='#define BASE 3\n#define SCALE(x) ((x)*BASE)\n#define UNUSED 9\n#if SECRET\n#define CONDITIONAL 8\n#endif\n'
    result=context(text,'int f(int x) { return SCALE(x); }')
    assert '#define SCALE' in result and '#define BASE' in result
    assert 'UNUSED' not in result and 'CONDITIONAL' not in result
