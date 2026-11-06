from types import SimpleNamespace

from decompnet.app import related_source_context as related
from decompnet.arch.mips_runtime import specialized_argument_setup, temporary_arguments


def test_neighbour_context_needs_same_project_exact_status_shared_records_and_calls():
    donor={'name':'nearby','asm':'jal common\nnop','matched_source':'void nearby(Item *p){}','unit':'private','vram':4100,'verified_header':'typedef struct {int x,y;} Item;'}
    class Store:
        def one(self,*args):return True
        def all(self,sql,*args):
            assert "status='matched' AND f.best_score=0" in sql and args[0]==7
            return [donor]
    net=SimpleNamespace(store=Store(),is_rom=lambda p:True,fork_slug=lambda s:None,
                        _context=lambda *a:{'header':'typedef struct {int x,y;} Item;'})
    proj={'id':7,'slug':'game','arch':'mips'};fn={'id':1,'vram':4096,'asm':'jal common\nnop'}
    found=related.candidates(net,proj,fn,'void f(Item *p){}','typedef struct {int x;} Item;')
    assert len(found)==1 and found[0]['function']=='nearby' and found[0]['types']==['Item']
    assert not related.candidates(net,proj,fn,'void f(int x){}','typedef int Item;')
    donor['asm']='jal unrelated\nnop'
    assert not related.candidates(net,proj,fn,'void f(Item *p){}','typedef struct {int x;} Item;')
    net.fork_slug=lambda s:'imported'
    assert not related.candidates(net,proj,fn,'void f(Item *p){}','typedef struct {int x;} Item;')


def test_temporary_arguments_need_caller_definitions_and_callee_incoming_reads():
    callee='lw $v0, 0($t2)\naddu $v0, $v0, $t3\njr $ra\nnop'
    caller='or $t2, $s0, $zero\nmove $t3, $s1\njal reader\nnop'
    evidence=specialized_argument_setup(callee,caller,'reader')
    assert evidence and evidence['registers']==['$t2','$t3'] and len(evidence['setup'])==2
    assert not specialized_argument_setup(callee,caller,'other')
    assert not specialized_argument_setup(callee,'jal reader\nnop','reader')
    assert not temporary_arguments('sw $t2, 0($sp)\nli $t2, 4\naddu $v0, $t2, $a0\njr $ra')
    assert not specialized_argument_setup('li $t2, 4\nli $t3, 3\naddu $v0, $t2, $t3\njr $ra',caller,'reader')
