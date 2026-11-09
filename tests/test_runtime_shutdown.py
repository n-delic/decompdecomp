"""Real terminal signals must reach cleanup even while an HTTP stream never ends."""
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from decompnet.app.runtime_server import Server


def test_repeated_signals_report_progress_without_skipping_cleanup(capfd):
    entered=threading.Event();release=threading.Event();calls=[]
    def cleanup():
        calls.append('cleanup');entered.set();release.wait(2)
    server=Server(uvicorn.Config(lambda *a:None),cleanup)
    try:
        server.handle_exit(signal.SIGINT,None)
        assert entered.wait(1)
        server.handle_exit(signal.SIGINT,None)
        assert server.should_exit and not server.force_exit
        assert not server._captured_signals
        output=capfd.readouterr().err
        assert 'Ctrl-C received' in output and 'Shutdown is already in progress' in output
        assert calls==['cleanup']
    finally:
        release.set();server._shutdown_thread.join(2)


@pytest.mark.skipif(os.name=='nt',reason='POSIX terminal signals; Windows handler uses the same cleanup callback')
@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
def test_signal_stops_with_open_stream_and_database_recovers(tmp_path,sig):
    ready=tmp_path/'ready';database=tmp_path/'work.db'
    script=r'''
import asyncio,logging,os,sqlite3,sys
from pathlib import Path
from decompnet.app.cli import server_config
from decompnet.app.runtime_server import Server
from decompnet.app.shutdown_progress import Progress
ready,db=map(Path,sys.argv[1:])
connection=sqlite3.connect(db)
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('CREATE TABLE saved(value TEXT)')
connection.execute("INSERT INTO saved VALUES('committed match')")
connection.commit()
async def app(scope,receive,send):
    connection.execute("INSERT INTO saved VALUES('unfinished transaction')")
    await send({'type':'http.response.start','status':200,'headers':[]})
    await send({'type':'http.response.body','body':b'working\n','more_body':True})
    await asyncio.Event().wait()  # An MCP/room stream that never completes.
def quit_app():
    with Progress(logging.getLogger('test.shutdown'),interval=.05) as status:
        status.note('Shutting down decompdecomp; preserving saved work.')
        status.step('Stopping workers',lambda:None)
        status.note('Shutdown complete. Exiting.')
    os._exit(0)  # Same final exit as the application, with WAL recovery on reopen.
config=server_config(app)
config.host='127.0.0.1';config.port=0;config.lifespan='off'
server=Server(config,quit_app)
async def run():
    task=asyncio.create_task(server.serve())
    while not server.started:await asyncio.sleep(.01)
    ready.write_text(str(server.servers[0].sockets[0].getsockname()[1]))
    await task
asyncio.run(run())
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    proc=subprocess.Popen([sys.executable,'-c',script,str(ready),str(database)],env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    stream=None
    try:
        deadline=time.monotonic()+15
        while not ready.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert ready.exists(), 'Synthetic server did not start'
        stream=urllib.request.urlopen('http://127.0.0.1:'+ready.read_text(),timeout=3)
        assert stream.read(8)==b'working\n'
        began=time.monotonic();proc.send_signal(sig)
        stdout,stderr=proc.communicate(timeout=5)
        assert proc.returncode==0 and time.monotonic()-began<5, stdout+stderr
        assert ('Ctrl-C received' if sig==signal.SIGINT else 'Termination signal received') in stderr
        assert 'Stopping workers complete' in stderr and 'Shutdown complete' in stderr
        with sqlite3.connect(database) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
            assert db.execute('SELECT value FROM saved').fetchall()==[('committed match',)]
    finally:
        if stream:stream.close()
        if proc.poll() is None:proc.kill();proc.communicate(timeout=5)
