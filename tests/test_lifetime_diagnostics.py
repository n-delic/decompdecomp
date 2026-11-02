from decompnet.app import lifetime_diagnostics as diag


def test_changed_diagnostic_output_stops_comparison(monkeypatch):
    calls=[]
    monkeypatch.setattr(diag.compiler_diagnostics,'diagnose',lambda *a, **kw: calls.append(a) or {'output_unchanged':False})
    assert 'skipped' in diag.investigate({},'', 'before','after','f')
    assert len(calls)==1


def test_only_preserved_dumps_are_compared(monkeypatch):
    monkeypatch.setattr(diag.compiler_diagnostics,'diagnose',lambda *a, **kw: {'output_unchanged':True,'phases':{}})
    result=diag.investigate({},'', 'before','after','f')
    assert result['before']['output_unchanged'] and result['after']['output_unchanged']


def test_capture_budget_counts_actual_compiles_and_keeps_reports_separate(monkeypatch):
    calls=[]
    def capture(*a, on_compile):
        on_compile();on_compile()
        return {'output_unchanged':True,'phases':{}}
    monkeypatch.setattr(diag.compiler_diagnostics,'diagnose',capture)
    result=diag.investigate({},'', 'before','after','f',on_compile=lambda: calls.append(1))
    assert len(calls)==4
    assert 'matched' not in result and 'score' not in result


def test_optional_artifact_failure_is_advisory(monkeypatch):
    def fail(*a, **kw):
        raise OSError('Temporary capture folder unavailable')
    monkeypatch.setattr(diag.compiler_diagnostics,'diagnose',fail)
    result=diag.investigate({},'', 'before','after','f')
    assert 'OSError' in result['skipped'] and 'matched' not in result
