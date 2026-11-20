"""A repair family that runs for seconds is a bug (decomp-repair 0.5.27's exponential pattern held a deep pass for
hours): it is cut off for that function and the other families go on, with no change to what they propose."""
import asyncio
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import deep_proposals, draft_repairs, repair_generation
from decompnet.network import NetworkError

CAPPED = r"""
import re, signal, sys
sys.path[:0] = [{path!r}]
from decompnet.app import repair_generation
repair_generation.FAMILY_SECONDS = 0.2


def stuck(source, name):
    # (a pattern that backtracks exponentially, the shape of the 0.5.27 bug)
    re.match(r'(a+)+b', 'a'*60)
    return ['never']


def quick(source, name):
    return [source+' /* quick */']


def partial(source, name):
    yield source+' /* first */'
    re.match(r'(a+)+b', 'a'*60)
    yield 'never'


previous = signal.getsignal(signal.SIGALRM)
out = repair_generation.call(stuck, 'int f(void){}', 'f') + repair_generation.call(quick, 'int f(void){}', 'f')
assert out == ['int f(void){} /* quick */'], out
assert repair_generation.cut_off() == [('__main__.stuck', 'f')]
assert repair_generation.call(partial, 'int g(void){}', 'g') == ['int g(void){} /* first */']
assert repair_generation.call(quick, 'x', 'f') == ['x /* quick */']
assert signal.getitimer(signal.ITIMER_REAL)[0] == 0 and signal.getsignal(signal.SIGALRM) is previous
print('ok')
"""


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="requires the Unix family timer")
def test_a_stuck_family_is_cut_off_and_the_others_still_yield():
    # (in a process of its own: the cap runs on a main thread, and the test runner's workers aren't one)
    from pathlib import Path
    code = CAPPED.replace('{path!r}', repr(str(Path(repair_generation.__file__).parents[2])))
    run = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == 'ok', run.stderr
    assert 'stuck was cut off after 0.2s on f' in run.stderr


SOURCE = '''s32 func_80001000(s32 arg0, s32 *arg1) {
    s32 i;
    s32 sum = 0;
    for (i = 0; i < arg0; i++) {
        sum += arg1[i] * 2;
    }
    if (sum > 0x100) {
        return sum - 1;
    }
    return sum;
}
'''


def test_another_thread_generates_in_a_worker_with_the_same_proposals():
    labels_here = {}
    here = draft_repairs._variants(SOURCE, 'func_80001000', 'mips', '', ['-O2'], labels_here, limit=None)
    out = {}
    def other():
        labels = {}
        out['choices'] = draft_repairs.variants(SOURCE, 'func_80001000', 'mips', '', ['-O2'], labels, limit=None)
        out['labels'] = labels
    thread = threading.Thread(target=other)
    thread.start()
    thread.join(300)
    assert here and out['choices'] == here and out['labels'] == labels_here
    assert draft_repairs._IDLE_WORKERS  # (it was generated in a worker process, which stays for the next call)


def test_local_repair_session_keeps_families_and_cache_in_worker(monkeypatch):
    monkeypatch.setattr(draft_repairs, '_IDLE_WORKERS', [])
    net = SimpleNamespace()
    families = set(draft_repairs.AGENT_FAMILIES)
    name = 'func_80001000'

    def discover(requested):
        with repair_generation.session(net, 'scope', time.monotonic() + 10, requested) as state:
            labels = {}
            choices = draft_repairs.variants(SOURCE, name, 'mips', labels=labels, limit=None)
            return choices, {labels[candidate] for candidate in choices}, dict(state.metrics)

    async def check():
        first = await asyncio.to_thread(discover, families)
        second = await asyncio.to_thread(discover, families)
        assert first[0] and second[0] == first[0]
        family = next(iter(first[1] & families))
        selected = await asyncio.to_thread(discover, {family})
        assert family in selected[1]
        assert second[2]['cache_hits'] > first[2]['cache_hits']
        assert second[2]['generated_stages'] < first[2]['generated_stages']

    try:
        asyncio.run(check())
    finally:
        for worker in draft_repairs._IDLE_WORKERS:
            worker.close()


def test_a_stuck_local_proposal_worker_cannot_block_the_event_loop(monkeypatch):
    monkeypatch.setattr(draft_repairs, '_IDLE_WORKERS', [])
    popen = subprocess.Popen
    children = []

    def unresponsive_worker(*_args, **kwargs):
        process = popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(process)
        return process

    def must_not_run(*_args, **_kwargs):
        pytest.fail('Repair generator ran in the request process')

    monkeypatch.setattr(deep_proposals.subprocess, 'Popen', unresponsive_worker)
    monkeypatch.setattr(draft_repairs, '_variants', must_not_run)

    def discover():
        with repair_generation.session(SimpleNamespace(), 'scope', time.monotonic() + 2, draft_repairs.AGENT_FAMILIES) as state:
            assert draft_repairs.variants(SOURCE, 'func_80001000', 'mips') == []
            assert state.incomplete

    async def check():
        pending = asyncio.create_task(asyncio.to_thread(discover))
        async def heartbeat():
            while not children:
                await asyncio.sleep(.01)
            assert not pending.done()
            assert children[0].poll() is None

        await asyncio.wait_for(heartbeat(), 1.5)
        await asyncio.wait_for(pending, 6)
        assert children[0].poll() is not None
        assert not draft_repairs._IDLE_WORKERS

        with pytest.raises(NetworkError, match='outside the server event loop'):
            draft_repairs.variants(SOURCE, 'func_80001000', 'mips')

    asyncio.run(check())
