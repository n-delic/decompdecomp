"""Warmed draft generators must agree with the CLI and stop with their owner."""
import sys
import threading
import time

import pytest

from decompnet import compiler, m2c_sessions, seeds
from decompnet.network import NetworkError
from decompnet.rom.split import draft_asm
from tests.test_overlay_drafts import ADDRESS, ASSEMBLY


@pytest.mark.skipif(not seeds.available(),reason='needs pinned m2c')
def test_warmed_generator_matches_cli_across_inputs_and_reuses_process():
    target=seeds.m2c_target('gcc-2.7.2-kmc')
    asm=draft_asm(ASSEMBLY,'example',ADDRESS)
    inputs=[(asm,'example',''),(asm.replace('example','another_entry'),'another_entry','')]
    cold=[seeds.run_m2c(asm,name,target,header,valid=True,strict=True) for asm,name,header in inputs]
    enabled=threading.Event();enabled.set()
    with m2c_sessions.scope(enabled):
        warm=[seeds.run_m2c(asm,name,target,header,valid=True,strict=True) for asm,name,header in inputs]
        session=m2c_sessions._current.get()
        assert session.requests==2
        with pytest.raises(seeds.DraftError):
            seeds.run_m2c('glabel broken\nbeqz $a0, .Lmissing\nnop','broken',target,valid=True,strict=True)
        assert seeds.run_m2c(inputs[0][0],inputs[0][1],target,valid=True,strict=True)==cold[0]
        old=session.process
        session.requests=64
        assert seeds.run_m2c(inputs[0][0],inputs[0][1],target,valid=True,strict=True)==cold[0]
        assert old.poll() is not None and session.process.pid!=old.pid
        proc=session.process
    assert proc.poll() is not None
    assert warm==cold


def test_stop_terminates_stuck_generator(tmp_path,monkeypatch):
    tool=tmp_path/'m2c';tool.write_text('test')
    original=m2c_sessions.spawner.Popen
    processes=[]
    def launch(*args,**kwargs):
        proc=original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
        processes.append(proc);return proc
    monkeypatch.setattr(m2c_sessions.spawner,'Popen',launch)
    enabled=threading.Event();enabled.set()
    stop=threading.Event()
    def check():
        if stop.is_set():raise NetworkError('stopped')
    timer=threading.Timer(.1,stop.set);timer.start()
    started=time.monotonic()
    try:
        with m2c_sessions.scope(enabled),compiler.cancellable(check),pytest.raises(NetworkError,match='stopped'):
            m2c_sessions.run([str(tool)],20)
    finally:timer.cancel()
    assert time.monotonic()-started<5 and processes[0].poll() is not None
