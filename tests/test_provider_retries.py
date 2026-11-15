"""A transient provider retry must not kill a healthy CLI stream."""
from decompnet import agents
from decompnet.app import provider_retries


def test_provider_failures_need_repetition_and_quiet_time(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(provider_retries.time, 'monotonic', lambda: now[0])
    retries = provider_retries.Retries()
    assert not retries.observe('safe overload category')
    now[0] = 129
    assert not retries.observe('safe overload category')
    assert not retries.observe('safe overload category')
    now[0] = 130
    assert retries.observe('safe overload category') == 'safe overload category'


def test_model_progress_and_new_failure_category_reset_retry_history(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(provider_retries.time, 'monotonic', lambda: now[0])
    retries = provider_retries.Retries()
    for _ in range(2):assert not retries.observe('overload')
    now[0] = 120
    retries.progress()
    assert not retries.observe('overload')
    now[0] = 240
    assert not retries.observe('overload')  # only two since progress
    assert not retries.observe('different failure')
    assert not retries.observe('')
    assert retries.count == 1 and retries.message == 'different failure'


def test_opencode_overload_requires_selected_primary_error():
    agent = agents.get('opencode')
    line = ('level=ERROR message="stream error" providerID=opencode-go modelID=example small=false '
            'agent=build mode=primary error.error="AI_APICallError: The inference service is overloaded. Please try again later. private-secret"')
    assert 'No capacity available' in agent.stderr_retry(line, 'opencode-go/example#high')
    assert 'private-secret' not in agent.stderr_retry(line, 'opencode-go/example')
    for invalid in [line.replace('small=false','small=true'), line.replace('level=ERROR','level=INFO'),
                    line.replace('providerID=opencode-go','providerID=opencode'),
                    line.replace('modelID=example','modelID=title-model'),
                    line.replace('The inference service is overloaded. Please try again later.', 'HTTP 502 Bad Gateway; retrying'),
                    'The inference service is overloaded. Please try again later.',
                    'level=ERROR error.error="The inference service is overloaded. Please try again later. providerID=opencode-go modelID=example small=false"']:
        assert not agent.stderr_retry(invalid, 'opencode-go/example')
    assert not agent.stderr_retry(line)
    assert not agents.get('claude').stderr_retry(line, 'opencode-go/example')
