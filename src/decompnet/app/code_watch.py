"""Notice that the checkout this app runs from has new code on disk (`git pull`, `uv sync`), so it can offer a restart.

The server keeps its modules in memory and can't swap them safely, but a restart is cheap (updates.restart replaces
the process in place: same port, same window, workers and agents carry on). This only watches: the app's page shows a
"Restart" button when the code on disk is no longer what is running, and an optional setting restarts by itself once
nothing is working (routes/life.py). It compares two cheap facts, the checkout's commit and the installed versions of
the libraries, so a pull that is still waiting for its `uv sync` counts as one change in progress: a restart is only
automatic once the facts have stopped changing for SETTLE_SECONDS. Only a checkout (edition.DEV) is watched; an installed
copy updates through updates.py."""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from ..hostenv import NO_WINDOW
from . import edition, library_skew

CHECK_SECONDS = 20  # how often the watcher looks
SETTLE_SECONDS = 60  # the facts must stay the same this long before an automatic restart (a pull, then its uv sync)


def commit(root: Path) -> str:
    """The checked-out commit (a few milliseconds), or '' when git can't say."""
    try:
        done = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                              timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout.strip() if done.returncode == 0 else ''


class Watch:
    def __init__(self, root: Path | None = None, *, read_commit=commit, versions=library_skew.installed_versions,
                 enabled: bool | None = None, clock=time.monotonic):
        self.root = Path(root) if root is not None else edition.CHECKOUT_ROOT
        self.enabled = edition.DEV if enabled is None else enabled
        self._commit, self._versions, self._clock = read_commit, versions, clock
        self._lock = threading.Lock()
        self.started = self._facts() if self.enabled else {}
        self.latest = dict(self.started)
        self.changed_at = clock()  # when `latest` last differed from what it was a moment before

    def _facts(self) -> dict:
        return {'commit': self._commit(self.root), 'libraries': dict(self._versions())}

    def check(self) -> dict:
        """Read the facts now and return the status."""
        if self.enabled:
            facts = self._facts()
            with self._lock:
                if facts != self.latest:
                    self.latest, self.changed_at = facts, self._clock()
        return self.status()

    def what(self) -> list[str]:
        """Plain-English differences between what runs and what is on disk."""
        with self._lock:
            started, latest = dict(self.started), dict(self.latest)
        found = []
        if started.get('commit') and latest.get('commit') and started['commit'] != latest['commit']:
            found.append(f"the checkout moved from {started['commit'][:7]} to {latest['commit'][:7]}")
        for name, was in (started.get('libraries') or {}).items():
            now = (latest.get('libraries') or {}).get(name)
            if was and now and was != now:
                found.append(f'{name} {was} → {now}')
        return found

    def status(self) -> dict:
        what = self.what() if self.enabled else []
        with self._lock:
            settled = self._clock() - self.changed_at >= SETTLE_SECONDS
        return {'enabled': self.enabled, 'changed': bool(what), 'what': what, 'settled': bool(what) and settled}
