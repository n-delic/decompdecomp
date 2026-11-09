"""Replay one private donor transfer through the normal compiler and linked byte judge.

Usage: PYTHONPATH=src python tools/replay_parameter_transfer.py /private/fixture.json
Fixture keys: donor/destination (name, vram, target hex, relocs JSON, source),
compiler (ordinary toolchain/flags/timeout), header, big_endian. Optional symbols
are dictionaries with name/address/size/kind. No live app/database is accessed.
Keep fixtures containing game code or bytes outside the repository.
"""
import argparse
import hashlib
import json
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from decompnet.app.parameter_transfers import destination_signature, variants
from decompnet.app.twins import apply
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom.match import RomTarget, link
from decompnet.rom.symbols import Symbol, SymbolTable


def replay(fixture):
    donor, destination = (dict(fixture[k]) for k in ('donor', 'destination'))
    for fn in (donor, destination):
        fn['target'] = bytes.fromhex(fn['target'])
    big = bool(fixture['big_endian'])
    source = apply(donor['source'], {donor['name']:destination['name']})
    started = time.monotonic()
    candidates = variants(source, donor, destination, big)
    candidates = list(dict.fromkeys([destination_signature(c, destination['name'], destination['source'])
                                    for c in candidates] + candidates))
    target = RomTarget(destination['name'], destination['vram'], destination['target'],
                       json.loads(destination['relocs']), big)
    symbols = SymbolTable([Symbol(**s) for s in fixture.get('symbols', [])])
    report = {'candidates':len(candidates), 'compiles':0, 'exact':False,
              'donor_source_sha256':hashlib.sha256(donor['source'].encode()).hexdigest(),
              'target_sha256':hashlib.sha256(destination['target']).hexdigest()}
    # Caller can place all temporary compiler artifacts on a data volume via TMPDIR.
    with tempfile.TemporaryDirectory(prefix='parameter-replay-') as root:
        compiler = compiler_config(Path(root), {'compiler':fixture['compiler']})
        for candidate in candidates:
            report['compiles'] += 1
            compiled = compile_source(compiler, fixture['header'], candidate)
            if not compiled.ok:
                report['compile_error'] = compiled.log
                continue
            judged = link(compiled.obj, destination['name'], target, symbols, lambda addr, size:None)
            if judged.exact:
                report.update(exact=True, bytes=len(judged.linked_code),
                              source_sha256=hashlib.sha256(candidate.encode()).hexdigest())
                break
    report['seconds'] = round(time.monotonic() - started, 3)
    return report


def autopilot(fixture):
    """Discover and adopt through a real isolated engine; only fixture functions are present.

    Targets must be self-contained. The miniature analysis image is not a playable ROM.
    No live home, existing database, server connection or proprietary input is accessed.
    """
    from decompnet import consoles
    from decompnet.app.engine import RomNetwork
    from decompnet.app.twins import Job

    rows = [fixture['donor'], fixture['destination']]
    base = min(r['vram'] for r in rows)
    end = max(r['vram'] + len(bytes.fromhex(r['target'])) for r in rows)
    if end - base > 16 * 1024 * 1024:
        raise ValueError('Fixture targets must fit in one 16 MB analysis window.')
    image = bytearray(end - base)
    for row in rows:
        code = bytes.fromhex(row['target'])
        image[row['vram']-base:row['vram']-base+len(code)] = code
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='autopilot-replay-') as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        (root / 'image.bin').write_bytes(image)
        net = RomNetwork(root / 'state.sqlite3', root)
        cleanup.callback(net.store.conn.close)
        reader = net.store._reader()
        if reader is not None:
            cleanup.callback(reader.close)
        platform = fixture['platform']
        cfg = {'kind':'rom', 'platform':platform, 'big_endian':fixture['big_endian'],
               'compiler':fixture['compiler'], 'segments':[{'name':'fixture', 'rom_start':0,
               'rom_end':len(image), 'vram':base}], 'rom':{'file':'image.bin', 'platform':platform,
               'sha1':hashlib.sha1(image).hexdigest(), 'title':'Private fixture'}}
        with net.store.tx() as db:
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,?,?,?,?,?,?)',
                       ('fixture', 'Private fixture', consoles.get(platform).arch,
                        int(fixture['big_endian']), str(root), json.dumps(cfg)))
            db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,1,?,?)',
                       (fixture['header'], ''))
            for fid, row in enumerate(rows, 1):
                code = bytes.fromhex(row['target'])
                db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,unit,vram,rom_offset,status,matched_source,best_source,best_score) '
                           'VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (fid, row['name'], len(code), len(code)//4, code, row['relocs'], '',
                            row['vram'], row['vram']-base, 'matched' if fid==1 else 'open',
                            row['source'] if fid==1 else None, row['source'], 0 if fid==1 else None))
        # No fixed donor/candidate is passed to Autopilot: discovery and ordinary adoption both run.
        job = net.twins.full_pass('fixture', Job())
        saved = net.project_store(1).one('SELECT status,best_score FROM functions WHERE id=2')
        evidence = net.project_store(1).all('SELECT outcome,reason FROM cross_game_attempts WHERE function_id=2')
        return {'exact':saved['status']=='matched' and saved['best_score']==0,
                'general_attempts':job.reuse_attempts, 'similar_attempts':getattr(job,'similar_attempts',0),
                'state':job.state, 'destination_status':saved['status'], 'seconds':round(time.monotonic()-started,3),
                'evidence':[dict(row) for row in evidence],
                'diagnostics':[dict(row) for row in net.project_store(1).all('SELECT id,status,size,length(target) AS target_size FROM functions')],
                'indexed':net.project_store(1).one('SELECT COUNT(*) n FROM cross_game_index')['n']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--autopilot', action='store_true', help='Run discovery and adoption in an isolated engine (fixture needs platform).')
    args = parser.parse_args()
    result = (autopilot if args.autopilot else replay)(json.loads(args.fixture.read_text()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['exact'] else 1)
