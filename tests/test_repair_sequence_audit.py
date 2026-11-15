from decompnet.app.repair_sequence_audit import audit


def evaluate(source):
    return {'score':0 if source=='done' else 10,'output_fingerprint':source}


def test_literal_generation_selection_and_retention_gaps_are_distinct():
    stages=[{'source':s} for s in ['seed','middle','done']]
    generate=lambda source,report:[('loop','middle')] if source=='seed' else [('constant','done')]
    result=audit(stages,evaluate,generate,select=lambda *a:[],retains=lambda *a:False,probes=0)
    assert result['steps'][0]['literal_rank']==1 and not result['steps'][0]['selected']
    assert result['steps'][0]['status']=='generated_not_retained'
    assert result['first_gap']==0


def test_budget_does_not_prove_equivalent_outputs_absent_or_use_scores():
    result=audit([{'source':'seed'},{'source':'different'}],evaluate,lambda *a:[('test','not-it')],probes=0)
    assert result['steps'][0]['status']=='literal_step_not_generated'
    assert result['steps'][0]['unmeasured_proposals']==1
    assert 'does not exclude' in result['verdict']
    assert audit([{'source':'seed'},{'source':'done'}],evaluate,lambda *a:[],stopped=lambda:True)['interrupted']


def test_equivalent_output_can_recover_differently_named_source():
    def measure(source):return {'score':10,'output_fingerprint':'control' if source in {'other','wanted'} else 'before'}
    report=audit([{'source':'seed'},{'source':'wanted'}],measure,lambda *a:[('loop','other')],probes=1)
    assert report['steps'][0]['status']=='equivalent_output_generated'
    report=audit([{'source':'seed'},{'source':'wanted'}],measure,lambda *a:[('loop','other')],
                 select=lambda *a:[],probes=1)
    assert report['steps'][0]['status']=='equivalent_output_not_selected' and report['first_gap']==0


def test_failed_control_is_not_mistaken_for_missing_generation():
    report=audit([{'source':'seed'},{'source':'wanted'}],lambda s:{'score':None},lambda *a:[])
    assert report['steps'][0]['status']=='control_unavailable'
