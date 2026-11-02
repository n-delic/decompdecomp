import hashlib

from decompnet.app import rtl_value_trace as trace

RTL=''';; Function f
(note 1 0 2 ("func.c") 3)
(insn 2 1 3 (set (reg/v:SI 72) (const_int 4)) -1 (nil))
(note 3 2 4 ("context.h") 9)
(insn 4 3 5 (set (reg:SI 73) (reg:SI 72)) -1 (nil))
(note 5 4 6 ("func.c") 4)
(insn 6 5 7 (set (reg:SI 72) (plus:SI (reg:SI 72) (const_int 1))) -1
 (expr_list:REG_DEAD (reg:SI 73) (nil)))
'''
GREG=''';; Function f
;; Register dispositions:
72 in 2 73 in 3
;; Hard regs used: 2 3
(insn 2 1 3 (set (reg:SI 2 v0) (const_int 4)) -1 (nil))
(insn 4 3 5 (set (reg:SI 3 v1) (reg:SI 2 v0)) -1 (nil))
'''


def test_explicit_uid_definition_use_death_and_disposition_join():
    result=trace.summarize({'rtl':RTL,'flow':RTL,'greg':GREG},'f')
    value=result['values'][0]
    assert value['pseudo']==72 and value['hard_register']==2 and value['aliases']==['v0']
    assert value['definitions']==[2,6] and value['uses']==[4,6]
    assert value['source_lines']==[3,4]
    assert result['values'][1]['deaths']==[6]
    assert not trace.summarize({'rtl':RTL,'flow':RTL,'greg':GREG},'unknown')['values']


def test_trace_is_source_and_header_scoped_and_keeps_reused_register_ambiguity():
    source='int f(){\nint a;\na=4;\nreturn a;\n}'
    proof=dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),header_sha256=hashlib.sha256(b'').hexdigest(),
               values=[dict(aliases=['v0'],source_lines=[3]),dict(aliases=['v0'],source_lines=[4])])
    rows=[('r','move $v1, $a0','move $v0, $a0')]
    assert trace.focus(proof,source,rows,'')==[3,4]
    assert trace.focus(proof,source+' ',rows,'')==[]
    assert trace.focus(proof,source,rows,'changed')==[]
    assert trace.focus(dict(proof,truncated=True),source,rows,'')==[]


def test_duplicate_function_sections_do_not_supply_evidence():
    assert trace.function(RTL+RTL,'f')==''


def test_saved_trace_rejects_changed_compiler_without_starting_tools(monkeypatch):
    import json
    from types import SimpleNamespace

    from decompnet import project
    source='int f(){\nint a;\na=4;\nreturn a;\n}'
    profile={'toolchain':'pinned','flags':['-O2']}
    proof=dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),header_sha256=hashlib.sha256(b'').hexdigest(),
               profile_sha256=hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest(),
               values=[dict(aliases=['v0'],source_lines=[3])])
    state=dict(value_trace=proof,value_trace_rows=[('r','move $v1, $a0','move $v0, $a0')])
    net=SimpleNamespace(is_rom=lambda p:True,fork_slug=lambda s:False,cfg=lambda p:{},
                        store=SimpleNamespace(one=lambda *a:dict(state=json.dumps(state))))
    monkeypatch.setattr(project,'compiler_for',lambda *a:profile)
    assert trace.saved(net,dict(slug='p'),dict(id=1,vram=1),source,'')[0]
    monkeypatch.setattr(project,'compiler_for',lambda *a:{**profile,'flags':['-O1']})
    assert trace.saved(net,dict(slug='p'),dict(id=1,vram=1),source,'')==({},[])
