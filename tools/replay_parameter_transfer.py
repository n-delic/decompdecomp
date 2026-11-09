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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    args = parser.parse_args()
    result = replay(json.loads(args.fixture.read_text()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['exact'] else 1)
