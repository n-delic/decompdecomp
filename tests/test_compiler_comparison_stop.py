"""Stopping a comparison terminates its compiler and preserves only completed comparisons."""
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from decompnet import compiler
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs


def test_stop_kills_active_compiler_keeps_results_and_resumes(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind':'rom', 'platform':'n64', 'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo','Demo',str(tmp_path),json.dumps(cfg))).lastrowid
        for i in range(2):
            db.execute('INSERT INTO functions(project_id,name,vram,n_insns,best_source,best_score) VALUES(?,?,?,?,?,?)',
                       (pid, f'func_{i}', 0x80001000 + i * 16, 16, f'int func_{i}(void){{return 0;}}', 160))
    monkeypatch.setattr(net, 'compiler_map', lambda *a, **k: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(net, '_comparison_candidates', lambda *a: [('gcc-2.7.2-kmc', ('-O2',))])
    monkeypatch.setattr(net, '_comparison_digest', lambda proj, fn, *args: fn['name'])
    started = threading.Event()
    processes = []
    popen = subprocess.Popen
    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        started.set()
        return process
    monkeypatch.setattr(compiler.subprocess, 'Popen', launch)
    def judge(proj, fn, *args):
        assert compiler._CANCEL_CHECK.get() is not None
        if fn['name'] == 'func_1':
            compiler._exec(compiler.CompilerConfig([], sandbox='none'), tmp_path,
                           [sys.executable, '-c', 'import time; time.sleep(60)'], 60)
        return False, SimpleNamespace(percent=95), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    jobs = Jobs()
    jid = jobs.start('installed compilers', lambda log: net.run_compiler_comparison('demo',log=log),
                     cancel_message='Compiler comparison stopped. Completed results are saved.')
    try:
        assert started.wait(5)
        assert jobs.cancel(jid)
        deadline = time.monotonic() + 5
        while jobs.jobs[jid]['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        job = jobs.jobs[jid]
        assert job['state'] == 'cancelled'
        assert job['error'] == 'Compiler comparison stopped. Completed results are saved.'
        assert processes[0].poll() is not None
        assert job['progress']['completed'] == 1
        assert len(job['progress']['results']) == 1
        assert net.store.one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 1
        monkeypatch.setattr(net, '_judge_with', lambda *a: (False,SimpleNamespace(percent=95),''))
        result = net.run_compiler_comparison('demo',log=lambda *_: None)
        assert result['tried'] == result['skipped'] == 1
        assert compiler._CANCEL_CHECK.get() is None
    finally:
        jobs.cancel(jid)
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait()
