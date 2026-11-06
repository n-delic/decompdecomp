"""One bounded, advisory GCC phase comparison after an ordinary judged improvement."""
from . import compiler_diagnostics


def investigate(profile, header, before, after, name, *, on_compile=lambda: None):
    """No verdict, settings change or extra search budget is supplied by diagnostic builds."""
    def capture(source):
        try:
            return compiler_diagnostics.diagnose(profile, header, source, name, on_compile=on_compile)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            # Optional artifacts must not discard the ordinary judge's measured improvement.
            return {'skipped':'Compiler phase capture unavailable ('+type(error).__name__+').'}
    left = capture(before)
    if not left.get('output_unchanged') or left.get('skipped'):
        return {'skipped':left.get('skipped') or 'Diagnostic output changed; phase evidence ignored.'}
    right = capture(after)
    comparison = compiler_diagnostics.compare(left, right)
    return {'comparison':comparison, 'before':left, 'after':right}


def single(profile, header, source, name, *, on_compile=lambda: None):
    """Initial trace needs two already-budgeted compiler runs, never a match verdict."""
    try:
        result=compiler_diagnostics.diagnose(profile,header,source,name,on_compile=on_compile)
    except (OSError,ValueError,KeyError,RuntimeError) as error:
        return {'skipped':'Value trace unavailable ('+type(error).__name__+').'}
    if result.get('output_unchanged') and not result.get('skipped'):
        return result.get('value_trace',{})
    return {'skipped':result.get('skipped') or 'Diagnostic flags changed normal allocated output.'}
