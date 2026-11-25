"""Schema setup runs once per version of its code, not once per RepairFeedback / prepare (schema_stamp.py)."""
from decompnet import repair_feedback, schema_stamp
from decompnet.app import repair_outcomes
from tests.test_repair_feedback import setup  # noqa: F401


def count_transactions(store, monkeypatch):
    calls = []
    real = store.tx
    monkeypatch.setattr(store, 'tx', lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def test_a_later_start_takes_no_write_transaction(setup, monkeypatch):  # noqa: F811
    net = setup[0]
    repair_outcomes.prepare(net.repair_feedback)
    assert schema_stamp.current(net.store, 'repair-feedback', *repair_feedback.SCHEMA_MODULES)
    assert schema_stamp.current(net.store, 'repair-outcomes', *repair_outcomes.SCHEMA_MODULES)
    calls = count_transactions(net.store, monkeypatch)
    repair_feedback.RepairFeedback(net)
    repair_outcomes.prepare(net.repair_feedback)
    assert calls == []


def test_changed_schema_code_runs_the_setup_again(setup, monkeypatch):  # noqa: F811
    net = setup[0]
    monkeypatch.setattr(schema_stamp, 'digest', lambda *modules: 'another version')
    assert not schema_stamp.current(net.store, 'repair-feedback', *repair_feedback.SCHEMA_MODULES)
    repair_feedback.RepairFeedback(net)
    assert schema_stamp.current(net.store, 'repair-feedback', *repair_feedback.SCHEMA_MODULES)


def test_interrupted_analysis_is_still_reset(setup, monkeypatch):  # noqa: F811
    net = setup[0]
    repair_outcomes.prepare(net.repair_feedback)
    with net.store.tx() as db:
        db.execute("INSERT INTO repair_outcome_analysis SELECT id,'running','{}',0 FROM repair_examples LIMIT 0")
    # no rows: still no transaction
    calls = count_transactions(net.store, monkeypatch)
    repair_outcomes.prepare(net.repair_feedback)
    assert calls == []
