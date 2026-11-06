from decompnet.app import repair_sequences as sequences
from tests.test_permute import FakeNet


def test_only_connected_exact_sequences_are_learned_under_the_original_profile():
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    lineage=sequences.Lineage(net,proj,fn,'start',[('r','a','b')])
    lineage.retain('unknown','bad','other',exact=True)
    lineage.retain('start','middle','call_initialization')
    assert lineage.preferred('start')==[]
    lineage.retain('middle','exact','masked_value_reuse',exact=True)
    assert lineage.preferred('start')==['call_initialization']
    assert lineage.preferred('middle')==['masked_value_reuse']
    assert sequences.next_families(net,'different-profile',lineage.focus,[])==[]
    assert sequences.next_families(net,lineage.profile,'stack',[])==[]
    stored=net.store.one('SELECT * FROM repair_sequences')
    assert stored['successes']==1 and 'start' not in stored['steps']


def test_unconnected_repeated_sources_and_long_paths_do_not_train():
    net=FakeNet();lineage=sequences.Lineage(net,net.project_row('game'),net.function(),'start',[])
    lineage.retain('start','start','noop',exact=True)
    for i in range(6):lineage.retain('start' if i==0 else str(i-1),str(i),'step',exact=i==5)
    sequences.prepare(net)
    assert not net.store.one('SELECT 1 FROM repair_sequences')


def test_shared_family_order_promotes_learned_starts_without_removing_exploration():
    from decompnet.app import adaptive_repairs
    net=FakeNet();proj=net.project_row('game');fn=net.function();rows=[('r','a','b')]
    lineage=sequences.Lineage(net,proj,fn,'start',rows)
    lineage.retain('start','middle','learned');lineage.retain('middle','end','finish',exact=True)
    order=adaptive_repairs.order(net,proj,fn,rows,['unexplored','learned','other'])
    assert order[0]=='learned' and set(order)=={'unexplored','learned','other'}


def test_new_sequences_share_only_with_same_shape_and_keep_legacy_fallback():
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    rows=[('r','move $s0, $a0','move $s1, $a0')]
    lineage=sequences.Lineage(net,proj,fn,'before',rows)
    lineage.retain('before','middle','lifetime')
    lineage.retain('middle','after','initialization',exact=True)
    assert sequences.starting(net,proj,fn,rows)==['lifetime']
    stack=[('s','lw $t0, 0x24($sp)','lw $t0, 0x2c($sp)')]
    assert sequences.starting(net,proj,fn,stack)==[]
    sequences.learn(net,lineage.profile,'register',['legacy','finish'])
    assert sequences.starting(net,proj,fn,rows)==['lifetime','legacy']
