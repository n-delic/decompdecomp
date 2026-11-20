"""The app's event loop (every async handler) never waits for a lock: one lock held by a worker thread there froze
every request, the window's included. Waiting work goes through `app.call`/`app.read`/`app.control`, which run it
in a worker thread. A non-blocking try (`acquire(blocking=False)`) can't wait, so it is allowed."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src' / 'decompnet'
# Helpers that take the jobs lock (app/support.py): call them in a worker, never straight from an async handler.
LOCKING_CALLS = ('jobs.active', 'jobs.start')
WAITING_CALLS = ('time.sleep', 'threading.Event().wait')


def _own_nodes(function):
    """The nodes an async function runs itself, not those of the functions it defines (they run where they're sent)."""
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _non_blocking(call: ast.Call) -> bool:
    return any(k.arg == 'blocking' and isinstance(k.value, ast.Constant) and k.value.value is False
               for k in call.keywords) or (call.args and isinstance(call.args[0], ast.Constant)
                                           and call.args[0].value is False)


def offences():
    found = []
    for path in sorted(SRC.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for function in ast.walk(tree):
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            for node in _own_nodes(function):
                what = None
                if isinstance(node, ast.With):
                    names = [ast.unparse(item.context_expr) for item in node.items]
                    what = next((n for n in names if 'lock' in n.lower()), None)
                elif isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name.endswith('.acquire') and not _non_blocking(node):
                        what = name
                    elif name.endswith(LOCKING_CALLS) or name in WAITING_CALLS:
                        what = name
                if what:
                    found.append(f'{path.relative_to(SRC)}:{node.lineno} {function.name}: {what}')
    return found


def test_async_handlers_never_wait_for_a_lock():
    assert offences() == []


def test_lane_watch_records_requests_without_a_lock():
    from decompnet.lane_watch import Watch
    watch = Watch('/nonexistent/report.log')
    assert not hasattr(watch, 'lock')
    with watch.queued('ui', 'route') as key:
        watch.running(key)
        assert watch.requests[key]['started'] is not None
    assert watch.requests == {}
