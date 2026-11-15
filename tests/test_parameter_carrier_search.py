import json

from decompnet.app import iterative_repairs, repair_ranking, residual_followups, search_frontier


def diagnostics():
    target=['addiu $sp, $sp, -8']+['addu $a0, $a1, $v1']*13+['lw $t0, 0($a2)','lw $t1, 2($a2)']+['addiu $sp, $sp, 8']
    before=[(' ',s,s) for s in target]
    before[14]=('o',target[14],target[15]);before[15]=('o',target[15],target[14])
    after=[('r',s,'addu $v1, $a1, $a0') if 1<=i<=13 else (' ',s,s) for i,s in enumerate(target)]
    return before,after


def test_worse_scheduled_sibling_survives_a_crowded_frontier():
    before,after=diagnostics()
    assert search_frontier.allocation_bridge(before,after,'mips')
    frontier=search_frontier.Frontier()
    frontier.offer('seed',60,before,arch='mips',baseline=before,baseline_score=60)
    frontier.offer('bridge',65,after,arch='mips',baseline=before,baseline_score=60)
    for i in range(12):
        frontier.offer('other'+str(i),61+i,before,arch='mips',baseline=before,baseline_score=60)
    assert {'seed','bridge'} <= {n['source'] for n in frontier.nodes}
    assert frontier.nodes[1]['source']=='bridge'
    assert len(frontier.nodes)<=search_frontier.WIDTH


def test_temporarily_worse_repair_resumes_to_exact_without_relaxing_judge():
    before,after=diagnostics();calls=[]
    state={'pending':[{'source':'seed','depth':0,'ceiling':60}], 'next':[], 'seen':['seed']}
    def judge(source):
        calls.append(source)
        return {'score':{'seed':60,'bridge':65,'exact':0}[source], 'matched':source=='exact',
                'rows':before if source=='seed' else after}
    def proposals(source,report):
        return {'seed':[('narrow_load_subtraction','bridge')],
                'bridge':[('parameter_carriers','exact')]}.get(source,[])
    assert not iterative_repairs.advance(state,judge,proposals,lambda:len(calls)>=2,arch='mips')
    state=json.loads(json.dumps(state))
    assert iterative_repairs.advance(state,judge,proposals,lambda:False,arch='mips')
    assert calls==['seed','bridge','exact']


def test_residual_and_standalone_ranking_target_the_complementary_repair():
    before,after=diagnostics()
    assert repair_ranking.order(['declaration_order','parameter_carriers','stack_padding'],after)[0]=='parameter_carriers'
    assert repair_ranking.order(['declaration_order','one_shot_scope','narrow_load_subtraction'],before)[0]=='narrow_load_subtraction'
    items=[('other','a'),('parameter_carriers','b')]
    assert residual_followups.order(items,before,after,'mips')[0]==items[1]


def test_first_targeted_page_reserves_applicable_carriers_after_localisation():
    source='''void f(int input,int *out) { int product; int prev; int next;
 product=input*3; input=product+7; prev=input-2; next=input+2;
 out[0]=input; out[1]=prev; out[2]=next; }'''
    _,after=diagnostics()
    page=iterative_repairs.targeted_variants(source,{'rows':after},{'name':'f','asm':''},'mips',[],
                                            tagged=True,selection={},limit=8)
    assert page and page[0][0]=='parameter_carriers'
    assert page.more  # unrelated exploration remains available through the existing cursor
