"""Read current project state during helper startup; file synchronization belongs to its watcher."""
import functools
import time
from contextlib import nullcontext


def current_projects(function):
    @functools.wraps(function)
    def run(manager, *args, **kwargs):
        progress = getattr(manager.net, 'progress', None)
        with progress.view() if progress is not None else nullcontext():
            return function(manager, *args, **kwargs)
    return run


def stage(session, label):
    now = time.time()
    previous = getattr(session, 'startup_stage', '')
    began = getattr(session, 'startup_stage_at', None)
    if previous and began:
        session.push('info', f'Startup · {previous}: {max(0, now-began):.1f}s')
    session.startup_stage = label
    session.startup_stage_at = now if label else None


def project_facts(net, slug):
    """Prompt metadata only; never build a dashboard, scan donors or contact rooms."""
    project = net._project(slug)
    config = net.cfg(project)
    console = net.console(project)
    return {"title": console.display_title(project["name"], config.get("rom", {})), "kind": config.get("kind", "rom"),
            "origin": config.get("origin"), **console.project_facts(),
            "compiler": {"toolchain": "project"} if config.get("kind") == "fork" else config.get("compiler", {})}
