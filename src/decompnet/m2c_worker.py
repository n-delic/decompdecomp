"""Private warmed m2c interpreter; invoked by its pinned virtualenv, not the app."""
import contextlib
import gc
import io
import json
import sys

from m2c.main import parse_flags, run

sys.setrecursionlimit(10000)
for line in sys.stdin:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(parse_flags(json.loads(line))) or 0
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
    except Exception as error:
        code = 1
        err.write(str(error))
    print(json.dumps({'returncode':code,'stdout':out.getvalue(),'stderr':err.getvalue()}),flush=True)
    gc.collect()
