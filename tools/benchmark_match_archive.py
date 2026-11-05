"""Measure local archive CPU/wall time using synthetic matches, never an existing game.

uv run python tools/benchmark_match_archive.py --matches 100000
Pass --baseline /tmp/old_match_library.py to compare a saved revision of that module.
Fingerprints are supplied, so this measures archiving separately from a cold fingerprint build.
"""
import argparse
import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matches',type=int,default=5000)
    parser.add_argument('--symbols',type=int,default=50000)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args()
    if args.matches<1 or args.symbols<1:parser.error('Match and symbol counts must be positive.')
    with tempfile.TemporaryDirectory(prefix='decomp-archive-bench-') as directory:
        os.environ.update(DECOMP_HOME=directory,DECOMP_WINDOW='0',DECOMP_TRAY='0',
                          DECOMP_DETECT_AGENTS='off',DECOMP_REPAIR_ANALYSIS='off',DECOMP_UPDATES='off')
        from decompnet.app import maintenance
        from decompnet.app.engine import RomNetwork
        from decompnet.app.match_library import SCHEMA, Library
        from decompnet.app.twins import Print
        from decompnet.rom.symbols import Symbol, SymbolTable
        root=Path(directory)
        net=RomNetwork(root/'decomp.db',root)
        cfg={'kind':'rom','platform':'n64','compiler':{'toolchain':'gcc','flags':['-O2']}}
        with net.store.tx() as db:
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,?,?,?,?,?,?)',
                       ('synthetic','Synthetic game','mips',1,directory,json.dumps(cfg)))
            db.executemany("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source,matched_at) "
                           "VALUES(?,1,?,'matched',32,?,?,1)",
                           ((i,f'f{i}',4096+i*32,f'int f{i}(void) {{return symbol_{i%args.symbols}+42;}}')
                            for i in range(1,args.matches+1)))
            db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,?)",
                       ('/* shared context */\n'+'extern int common;\n'*3500,))
        net._syms[1]=SymbolTable([Symbol(f'symbol_{i}',0x80000000+i*4) for i in range(args.symbols)])
        prints={i:Print(f'code-{i%100}',[],32) for i in range(1,args.matches+1)}
        net.twins.prints=lambda proj:prints
        versions=[]
        if args.baseline:
            spec=importlib.util.spec_from_file_location('decompnet.app.archive_baseline',args.baseline)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            versions.append(('baseline',module.Library))
        versions.append(('current',Library))
        for label,cls in versions:
            lib=cls(net)
            lib.path=root/f'{label}.sqlite3'
            with lib.connect() as db:db.executescript(SCHEMA)
            stop=threading.Event()
            wall,cpu=time.perf_counter(),time.thread_time()
            with maintenance.slot(stop), maintenance.cancellation(stop):
                lib.archive_project(net.project_row('synthetic'))
            result={'version':label,'matches':args.matches,'symbols':args.symbols,
                    'wall_seconds':round(time.perf_counter()-wall,3),
                    'cpu_seconds':round(time.thread_time()-cpu,3)}
            assert lib.summary()['occurrences']==args.matches
            print(json.dumps(result),flush=True)
        net.store.conn.close()


if __name__=='__main__':
    main()
