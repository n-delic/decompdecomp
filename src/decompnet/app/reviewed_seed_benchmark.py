"""Replay an original checkpoint plus an explicitly reviewed root in an isolated project copy.

Unlike normalized-output repair replays, this calls the ROM engine's real linker judge.
No model is invoked. A changed signature ends at a diagnostic review boundary, not match credit.
"""
import hashlib
import time
from pathlib import Path

from ..network import NetworkError
from ..project import compiler_for
from . import compiler_experiments, deep_permuter, reviewed_search_seeds, reviewed_seed_search


def run(net, slug, function, checkpoint, reviewed_source, review_note, isolated_root, log, *, minutes=2, trials=24,
        rebuild=None):
    root = Path(isolated_root).resolve()
    proj = net.project_row(slug)
    # An explicit sentinel plus containment checks prevent accidentally replaying in the live home.
    if (not (root/'.reviewed-search-replay').is_file() or not Path(net.store.path).resolve().is_relative_to(root)
            or not Path(proj['root']).resolve().is_relative_to(root)
            or not Path(net.workspace).resolve().is_relative_to(root)):
        raise NetworkError('Replay requires a disposable isolated home, copied project and .reviewed-search-replay marker.')
    deep_permuter.validate(net,slug,function,minutes,trials)
    fn=net._function(function,slug)
    if fn['best_source'] != checkpoint:
        raise NetworkError('Restore the original saved checkpoint in the isolated copy before replay.')
    profile=compiler_for(net.cfg(proj),fn['vram'],fn)
    flags=profile['flags'].split() if isinstance(profile['flags'],str) else profile['flags']
    agent,_=net.local_agent('reviewed-seed-replay')
    start=time.monotonic()
    ok,diff,error=net._judge_with(proj,fn,profile['toolchain'],flags,checkpoint)
    baseline_seconds=time.monotonic()-start
    if ok or diff is None:raise NetworkError('Replay needs a compiling nonmatching original checkpoint: '+error)
    ok,seed_diff,error=net._judge_with(proj,fn,profile['toolchain'],flags,reviewed_source)
    if not ok and seed_diff is None:raise NetworkError('Reviewed seed failed to compile: '+error)
    trial_id=hashlib.sha256((reviewed_source+str(time.time_ns())).encode()).hexdigest()[:20]
    header=net._context(proj['id'],fn['unit'])['header']
    compiler_experiments.save_trial(net,fn,trial_id,dict(agent=agent.id,slug=slug,function=fn["name"],source=reviewed_source,header=header,project_config=net.cfg(proj),
        compiler=profile['toolchain'],flags=' '.join(flags),seed_scope=reviewed_search_seeds.scope(net,proj,fn,reviewed_source),
        result={'exact':ok,'score':0 if ok else max(1,seed_diff.score)}))
    reviewed_search_seeds.review(net,agent,slug,function,trial_id,review_note)
    search=deep_permuter.Search(net,minutes,trials,log,fn)
    outcome=reviewed_seed_search.run(search,agent,proj,fn)
    report=dict(checkpoint_sha256=hashlib.sha256(checkpoint.encode()).hexdigest(),checkpoint_score=diff.score,
        seed_score=0 if ok else seed_diff.score,setup_judge_compiles=2,baseline_seconds=baseline_seconds,
        **outcome,search_compiles=search.tested,total_judge_or_diagnostic_compiles=2+search.tested,
        rebuild_seconds=None,rebuild_verified=None)
    if outcome['matched'] and rebuild is not None:
        began=time.monotonic();report['rebuild_verified']=bool(rebuild());report['rebuild_seconds']=time.monotonic()-began
    report['elapsed_seconds']=time.monotonic()-start
    report['verification']='Real target linker; a diagnostic exact is not adopted match credit. Rebuild is unmeasured unless explicitly supplied and run.'
    return report


def main():
    import argparse
    import json
    import os

    from .engine import RomNetwork
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--function',required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--reviewed-source',type=Path,required=True)
    parser.add_argument('--review-note',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.home.resolve()
    # Check before opening an engine: construction can migrate a database.
    if (not (root/'.reviewed-search-replay').is_file() or not args.database.resolve().is_relative_to(root)
            or not args.database.is_file()):
        parser.error('Choose an existing disposable database under a marked isolated home.')
    os.environ['DECOMP_HOME']=str(root)
    os.environ['DECOMP_WINDOW']='0'
    os.environ['DECOMP_TRAY']='0'
    class Log:
        def __call__(self,text):print(text)
        def check_cancel(self):pass
        def progress(self,value):pass
    report=run(RomNetwork(args.database,root),args.project,args.function,args.checkpoint.read_text(),
               args.reviewed_source.read_text(),args.review_note,root,Log())
    args.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
