"""Read captured snapshots and run local compiler-output regressions, without adopting source."""
import tempfile
from collections import Counter

from .. import compiler, toolchains
from .repair_replay import replay


def run(feedback, slug, ids, trials, seconds, log, *, minimize=False, diagnostics=False, combinations=False):
    results=[];counts=Counter()
    with tempfile.TemporaryDirectory(prefix='decomp-repair-replay-') as root, compiler.cancellable(log.check_cancel):
        for index,case_id in enumerate(ids):
            log.check_cancel()
            example=feedback.detail(slug,case_id)
            log(f'Example #{case_id}: testing captured compiler output; no source is applied.')
            def progress(value, case_id=case_id, index=index):
                log.progress({'example':case_id,'finished':index,'total':len(ids),**value})
            try:
                result=replay(example,trials=trials,seconds=seconds,root=root,
                              check_cancel=log.check_cancel,progress=progress,minimize=minimize,diagnostics=diagnostics,combinations=combinations)
            except (ValueError,KeyError,OSError,toolchains.ToolchainError) as error:
                result={'id':case_id,'function':example['evidence']['function'],'skipped':str(error)[:500]}
            results.append(result)
            counts.update(result['summary'] if 'summary' in result else {})
            if result.get('skipped'):counts['skipped']+=1
            log.progress({'finished':index+1,'total':len(ids),'example':case_id})
    return {'examples':results,'summary':dict(counts),'message':'Compiler-output regression only. No game progress or review states changed.'}
