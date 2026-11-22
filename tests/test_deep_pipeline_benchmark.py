import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

RANK = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/benchmark_deep_pipeline.py'))['ranked_replay']
TARGET = {'id': 1, 'target': bytes.fromhex('3c02000f3442423f03e0000800821021')}


@pytest.mark.parametrize('backend', ['cpu', 'metal'])
def test_ranked_replay_reorders_the_same_proposals(monkeypatch, backend):
    original, _ = RANK([TARGET], 32, 'current', None)

    def helper(command, *, input, **_):
        assert command == ['ranker', backend]
        payload = json.loads(input)
        assert payload['candidates'] == original[1]
        assert payload['targets'] == [999999] * 32
        scores = [(candidate ^ target).bit_count()
                  for candidate, target in zip(payload['candidates'], payload['targets'])]
        return SimpleNamespace(stdout=json.dumps({'scores': scores}))

    monkeypatch.setattr('subprocess.run', helper)
    ordered, _ = RANK([TARGET], 32, backend, Path('ranker'))
    assert ordered[1] == sorted(original[1], key=lambda candidate: (candidate ^ 999999).bit_count())
    assert ordered[1][0] == 999999


def test_ranked_replay_rejects_backend_score_mismatches(monkeypatch):
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: SimpleNamespace(stdout='{"scores": [1]}'))
    with pytest.raises(RuntimeError, match='disagrees'):
        RANK([TARGET], 1, 'metal', Path('ranker'))
