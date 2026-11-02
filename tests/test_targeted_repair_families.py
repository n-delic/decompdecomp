"""New source hypotheses still go through the ordinary mode-specific judge."""
import pytest

from decompnet.app import draft_repairs
from decompnet.app.permute import State
from tests.test_draft_repairs import setup_repair_net
from tests.test_permute import AGENT, ProjectStub, ScriptedPermuter


@pytest.mark.parametrize('imported', [False, True])
@pytest.mark.parametrize('family,source', [
    ('store_value_lifetime', 'void f1(S *p){p->value=125;}'),
    ('assignment_chain_lifetime', 'int f1(int *p){int a;int b;if ((a=b=p[1]<<4)>0) return a+b;return 0;}'),
    ('pointer_index_order', 'void f1(S *p,int i){p->table+i;}'),
    ('inferred_record_layout', 'void f1(void*p){*(int*)((char*)p+20)=1;use(*(int*)((char*)p+20));*(short*)((char*)p+24)=2;}'),
    ('store_address_lifetime', 'void f1(void*p,int base){*((short *)((char *)((*(int *)p*20)+base)+12))=0;}'),
    ('stack_padding', 'void f1(void){int pad[4];work();}'),
    ('local_width', 'void f1(int bits){unsigned int value;value=bits&15;store((char)value);}'),
    ('pointer_cast_inline', 'void f1(unsigned char *ptr){int x;x=(int)ptr;use(x);x=(int)ptr;use(x);}'),
])
def test_targeted_families_use_the_normal_mode_judge(imported, family, source):
    net = setup_repair_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    if imported:
        net.fork_slug = lambda slug: 'fork'
        class Fork(ProjectStub):
            def permuter_header(self, slug, function):
                return ''
        net.forks = Fork()
    labels = {}
    choices = draft_repairs.variants(source, 'f1', 'mips', labels=labels)
    expected = next(s for s in choices if labels[s] == family)
    permuter = ScriptedPermuter(net, {expected: 0})
    state = State()
    permuter._repair_round(AGENT, net.project_row('game'), state)
    assert state.matched == 1
