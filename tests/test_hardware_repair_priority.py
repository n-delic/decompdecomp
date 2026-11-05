"""Target-backed operation repairs survive generic delay-slot and learned queue priorities."""
from decompnet.app import allocation_evidence, repair_ranking


def test_target_backed_families_precede_generic_delay_slot_rewrites():
    rows=[(' ','jr $ra','jr $ra'),('|','move $v0, $a0','nop')]
    assert allocation_evidence.diagnose(rows,'mips')['delay_slot']
    families=['debug_statement_boundary','live_return_address_initializers','live_return_address','mips_debug_break','structured_backedge']
    assert repair_ranking.order(families,rows)[:3]==list(repair_ranking.GUARDED)
    # A hardware family is never invented when its target/source guard generated nothing.
    assert repair_ranking.order(['structured_backedge','debug_statement_boundary'],rows)[0]=='debug_statement_boundary'


def test_finite_guarded_candidates_survive_learned_queue_reordering_without_dropping_others():
    labels={'line-a':'debug_statement_boundary','local':'pointer_local_reuse',
            'bundle':'live_return_address_initializers','raw':'live_return_address','line-b':'debug_statement_boundary'}
    queue=['line-a','local','bundle','raw','line-b']
    assert repair_ranking.guarded_first(queue,labels)==['raw','bundle','line-a','local','line-b']
    assert queue==['line-a','local','bundle','raw','line-b']
    assert repair_ranking.guarded_first(['line-a','local','line-b'],labels)==['line-a','local','line-b']


def test_generic_learned_success_does_not_override_guarded_operation(monkeypatch):
    from decompnet.app import adaptive_repairs, experiment_evidence, repair_sequences
    monkeypatch.setattr(adaptive_repairs,'profile',lambda *a:{})
    monkeypatch.setattr(adaptive_repairs,'factors',lambda *a:{'debug_statement_boundary':4})
    monkeypatch.setattr(experiment_evidence,'factors',lambda *a:{})
    monkeypatch.setattr(repair_sequences,'starting',lambda *a:['debug_statement_boundary'])
    assert adaptive_repairs.order(None,{}, {},[],['debug_statement_boundary','live_return_address'])==['live_return_address','debug_statement_boundary']
