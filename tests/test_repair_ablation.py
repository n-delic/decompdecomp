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
