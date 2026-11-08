

from decompnet.app import captured_repairs


def test_coordinated_arity_changes_call_and_prototype():
    source='void send(int,int,int,char); void f(Obj *p){send(0,0,p->x,p->y);}'
    out=captured_repairs.variants(source,'f')
    assert any('void send(int, int);' in text and 'send(0, 0)' in text for family,text in out if family=='call_arity')
    assert not any(f=='call_arity' for f,_ in captured_repairs.variants(source.replace('p->y','read()'),'f'))
    assert not any(f=='call_arity' for f,_ in captured_repairs.variants(source.replace('send(0,0,p->x,p->y);','send(0,0,p->x,p->y);send(0,0,p->x,p->y);'),'f'))


def test_struct_byte_offsets_are_coordinated_and_bounded():
    source='typedef struct {int a,b;} S; extern S *global; void f(){S *local; use(local+12);use(global+20);}'
    out=[s for f,s in captured_repairs.variants(source,'f') if f=='struct_byte_offset']
    assert len(out)==2
    assert '(unsigned char *)local + 12' in out[0] and '(unsigned char *)global + 20' in out[0]
    assert not captured_repairs.variants(source.replace('local+12','local+i').replace('global+20','global++'),'f')


def test_switch_call_distribution_rejects_side_effects_and_fallthrough():
    source='void f(int n){int x;switch(n){case 1:x=10;break;default:x=20;break;}result=send(x,4);finish();}'
    out=[s for f,s in captured_repairs.variants(source,'f') if f=='switch_call']
    assert len(out)==1 and 'send(10, 4)' in out[0] and 'send(20, 4)' in out[0]
    assert out[0].count('result=send')==2
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('x=10','x=read()'),'f'))
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('x=10;break;','x=10;'),'f'))
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('finish();','finish(x);'),'f'))
