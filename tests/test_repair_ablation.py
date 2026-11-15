from decompnet.app.repair_ablation import minimize


def test_unnecessary_edits_are_reverted_with_fixed_budget():
    before='int a=1;\nint required=1;\nint b=1;\n'
    after='int a=2;\nint required=2;\nint b=2;\n'
    # Separate edits with unchanged lines so each ablation is isolated.
    before=before.replace(';\n',';\n/* divider */\n')
    after=after.replace(';\n',';\n/* divider */\n')
    report=minimize(before,after,lambda s:'required=2' in s,trials=6)
    assert report['reverted']==2 and len(report['retained'])==1
    assert 'a=1' in report['source'] and 'required=2' in report['source']
    assert len(report['trials'])<=6
    assert 'not semantic necessity' in report['verdict']


def test_stopped_or_failed_measurement_never_removes_an_edit():
    assert minimize('a\n','b\n',lambda s:False)['source']=='b\n'
    assert minimize('a\n','b\n',lambda s:True,stopped=lambda:True)['trials']==[]


def test_independent_checks_do_not_combine_dispensable_edits():
    before='int a=1; /* separate */ int b=1;'
    after='int a=2; /* separate */ int b=2;'
    preserves=lambda s:'a=2' in s or 'b=2' in s
    result=minimize(before,after,preserves,trials=4,individual=True)
    assert result['individual_checks']['output_preserved']==[0,1]
    assert result['reverted']==1 and preserves(result['source'])
    assert len(result['trials'])==2 and not result['exhausted']


def test_independent_checks_report_untested_edits_and_failed_controls():
    result=minimize('a=1; b=1; c=1;','a=2; b=2; c=2;',lambda s:False,trials=1,individual=True)
    assert result['individual_checks']=={'output_preserved':[],'output_not_preserved':[0],'untested':[1,2]}
    assert result['source']=='a=2; b=2; c=2;' and result['exhausted']
    assert not minimize('a','b',lambda s:True,individual=True,stopped=lambda:True)['trials']
