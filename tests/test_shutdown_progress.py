"""Shutdown messages stay visible while individual stages block and never change stop order."""
import io
import logging
import threading

import pytest

from decompnet.app.shutdown_progress import Progress, terminal_note


class Terminal(io.StringIO):
    def __init__(self):super().__init__();self.flushes=0;self.waiting=threading.Event()
    def flush(self):self.flushes+=1
    def write(self,text):
        result=super().write(text)
        if 'Still waiting:' in text:self.waiting.set()
        return result


def test_stage_is_flushed_before_action_and_slow_stages_keep_reporting():
    terminal=Terminal();logger=logging.getLogger('test.shutdown')
    with Progress(logger,stream=terminal,interval=.01) as status:
        def slow():
            assert 'Saving remaining progress...' in terminal.getvalue()
            assert terminal.flushes>0
            assert terminal.waiting.wait(2)
            return 'saved'
        assert status.step('Saving remaining progress',slow)=='saved'
        status.step('Stopping runtime',lambda:None)
    assert not status.thread.is_alive()
    text=terminal.getvalue()
    assert 'Still waiting: saving remaining progress' in text
    assert text.index('Saving remaining progress complete')<text.index('Stopping runtime...')


def test_stage_failure_is_reported_without_changing_the_exception():
    terminal=Terminal()
    with Progress(logging.getLogger('test.shutdown'),stream=terminal) as status:
        with pytest.raises(ValueError,match='cannot save'):
            status.step('Saving project',lambda:(_ for _ in ()).throw(ValueError('cannot save')))
    assert 'Failed while saving project' in terminal.getvalue()
    assert 'Saving project complete' not in terminal.getvalue()


def test_missing_terminal_does_not_interrupt_cleanup(caplog):
    class Closed:
        def write(self,text):raise OSError('terminal closed')
    logger=logging.getLogger('test.shutdown')
    with caplog.at_level(logging.INFO):
        terminal_note('Saving finished',logger,stream=Closed())
    assert 'Saving finished' in caplog.text


def test_project_notifications_update_the_waiting_message():
    terminal=Terminal();clock=[0]
    with Progress(logging.getLogger('test.shutdown'),stream=terminal,clock=lambda:clock[0]) as status:
        status.update('Saving project game-one');clock[0]=7;status.waiting()
        status.update('Saving project game-two');clock[0]=10;status.waiting()
    assert 'saving project game-one (7s elapsed)' in terminal.getvalue()
    assert 'saving project game-two (3s elapsed)' in terminal.getvalue()


def test_quit_reports_stages_and_failed_project_saves_before_exit(tmp_path,monkeypatch):
    from decompnet.app import server, window
    from decompnet.app.engine import RomNetwork
    home=tmp_path/'.decomp';home.mkdir()
    net=RomNetwork(home/'test.db',tmp_path)
    monkeypatch.setattr(window,'WINDOW',None)
    _,manager,_,quit_app=server.build(net,home,6496,'test-secret')
    terminal=Terminal();calls=[]
    monkeypatch.setattr(server,'ShutdownProgress',lambda logger:Progress(logger,stream=terminal))
    originals=[]
    for obj,method,label in ((net.startup,'close','Stopping startup maintenance'),
                            (net.autosave,'close','Stopping project autosave workers'),
                            (net.deep_sweeps,'shutdown','Stopping deep searches and queued repairs'),
                            (net.permuter,'close','Stopping the permuter process'),
                            (manager,'shutdown','Saving agent recovery and releasing claims')):
        originals.append(getattr(obj,method))
        def action(*args,label=label,**kwargs):
            assert label+'...' in terminal.getvalue()
            calls.append(label)
        monkeypatch.setattr(obj,method,action)
    monkeypatch.setattr(window,'close_app',lambda:calls.append('window'))
    def flush(*,notify):
        calls.append('flush');notify('example-game')
        return {'example-game':'folder is read-only'}
    monkeypatch.setattr(net.progress,'flush',flush)
    monkeypatch.setattr(server.wine,'stop',lambda:calls.append('wine'))
    monkeypatch.setattr(server.os,'_exit',lambda code:calls.append(('exit',code)))
    # The fake shutdown above must not leave this test's real services behind.
    monkeypatch.setattr(manager,'_strays',lambda:set())
    try:quit_app()
    finally:
        for stop in originals:stop()
    assert calls==['Stopping startup maintenance','Stopping project autosave workers','window',
                   'Stopping deep searches and queued repairs','Stopping the permuter process',
                   'Saving agent recovery and releasing claims','flush','wine',('exit',0)]
    text=terminal.getvalue()
    assert 'Saving project example-game...' in text
    assert 'Could not save project example-game: folder is read-only' in text
    assert 'Shutdown complete. Exiting.' in text


def test_closed_file_log_cannot_prevent_terminal_status_or_cleanup():
    class ClosedLog:
        def info(self,*args):raise ValueError('file log closed')
    terminal=Terminal();done=[]
    with Progress(ClosedLog(),stream=terminal) as status:
        status.step('Stopping workers',lambda:done.append(True))
    assert done==[True] and 'Stopping workers complete' in terminal.getvalue()
