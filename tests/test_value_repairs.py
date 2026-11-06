"""Repair suggestions become bounded hypotheses and go through the existing agent judge."""
import json

import pytest

from decompnet import repair_changes
from decompnet.app import agent_tokens, tools, value_repairs
from tests.test_claims import _game


def test_integer_formal_width_requires_mask_or_narrow_store_evidence():
    source='void other(s32 a){} void f(s32 a,s32 b,void *p){send(a&0xFFFF);*(s16 *)(p+4)=a;}'
    out=value_repairs.variants(source,'f')
    assert any('void f(u16 a,s32 b' in s for _,s in out)
    assert any('void f(s16 a,s32 b' in s for _,s in out)
    assert all(s.startswith('void other(s32 a){}') for _,s in out)
    assert not value_repairs.variants('void f(s32 a){send(a);}','f')
    assert not value_repairs.variants('void f(s32 *a){send(*a&0xFFFF);}','f')


def test_pointer_to_scalar_lifetime_split_keeps_volatile_load_in_place():
    source='void f(){volatile s32 *p;s32 *base;s32 field;base=read();p=base;do{base=(s32 *)p[10];i++;if((s32)base==n)use(p);}while(i<n);}'
    out=[s for k,s in value_repairs.variants(source,'f') if k=='value_lifetime']
    assert len(out)==1 and 'field = p[10];i++;if(field==n)' in out[0]
    assert out[0].count('p[10]')==1 and 'volatile s32 *p' in out[0]
    assert not any(k=='value_lifetime' for k,_ in value_repairs.variants(source.replace('use(p)','use(base)'),'f'))
    assert not any(k=='value_lifetime' for k,_ in value_repairs.variants(source.replace('base=read();','field=read();base=read();'),'f'))


def test_sum_shapes_reject_destination_dependence_and_calls():
    source='void f(){void *q;q=((n%(*p))*8)+(char *)(p+8);}'
    assert any('q = (char *)(((n%(*p))*8)); q += (s32)((char *)(p+8));' in s for k,s in value_repairs.variants(source,'f') if k=='sum_shape')
    source='void f(){send(off+*(s32 *)(base+4));}'
    assert any('send(*(s32 *)(base+4) + off)' in s for _,s in value_repairs.variants(source,'f'))
    for source in ('void f(){s32 x;x=a+x;}', 'void f(){s32 x;x=a+next();}', 'void f(){volatile s32 x;x=a+b;}'):
        assert not any(k=='sum_shape' for k,_ in value_repairs.variants(source,'f'))


@pytest.mark.parametrize('before,after,expected',[
    (None,'int f(){}','missing_before'),
    ('int f(){}','int f(){}','unchanged'),
    ('/* compiler A */\nint f(){return 1;}','/* compiler B */\nint f(){return 1;}','comments_or_spacing'),
    ('int f(){x();\n}else{}','int f(){x(); }else{}','layout_only'),
    ('char *f(){return "a";}','char *f(){return "b";}','source_change'),
    ('int f(){return a++;}','int f(){return a+ +b;}','source_change'),
    ('int f(){return 1;}','int f(){return 2;}','source_change')])
def test_source_change_labels_keep_literals_operators_and_line_boundaries(before,after,expected):
    assert repair_changes.kind(before,after)==expected


@pytest.mark.parametrize('mode',['rom','fork'])
@pytest.mark.parametrize('family,source',[
    ('parameter_width','void f(s32 a){send(a&0xFFFF);}'),
    ('sum_shape','void f(){s32 x;x=a+b;}'),
    ('value_lifetime','void f(){s32 *base;s32 field;base=(s32 *)p[10];use((s32)base);}')])
def test_new_families_use_normal_tool_judge(tmp_path,monkeypatch,mode,family,source):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'gcc','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?,best_score=10',(source,))
    net.claim_row(a,'game','f');seen=[]
    def compile(agent,function,text,slug):
        seen.append(text);net._record(net._function('f','game'),agent,9,text,1)
        return 'score 9'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    # Dispatch coverage substitutes the judge; imported build preflight is tested separately.
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,revision=agent_tokens.candidate(net,a,'game','f')
    result=tools.call(net,a,'game','compile_family',{'function':'f','revision':revision,'family':family})
    assert seen and len(seen)<=6 and 'Current candidate revision' in result
