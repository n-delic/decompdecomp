#!/usr/bin/env python3
"""Run cross-game searches one project at a time, resuming after app restarts.

Progress is stored in HOME/.decomp/cross-game-batch.json. A completed run
starts over next time. Use --progress-file for independent project selections.
Ctrl-C requests cancellation of the search started by this script.
"""
import argparse
import os
import sys
import time
import urllib.parse
from pathlib import Path

from project_batch import App, Reconnected, finish_project, queue


def project_path(slug):
    return '/api/project?' + urllib.parse.urlencode({'slug': slug})


def run_project(app, slug, interval=5):
    current = None
    owned = False
    try:
        while True:
            try:
                overview = app.request(project_path(slug))
                twins = overview.get('twins') or {}
                current = twins.get('cross_job') if twins.get('state') == 'running' else None
                owned = False
                if not current:
                    current = app.request('/api/cross-game/run', {'slug': slug})['job']
                    owned = True
                last = None
                while True:
                    job = app.request('/api/job?' + urllib.parse.urlencode({'id': current}))
                    status = (job.get('progress') or {}).get('phase')
                    if status and status != last:
                        print(f'  {status}', flush=True)
                        last = status
                    if job['state'] == 'done':
                        result = job.get('result') or {}
                        print(f'  {result.get("matched", 0)} matches, {result.get("improved", 0)} improvements', flush=True)
                        return
                    if job['state'] in ('error', 'cancelled'):
                        error = job.get('error') or f"Job {job['state']}."
                        if 'background worker stopped during a pass' in error.lower():
                            print('  Worker stopped; resuming saved work.', flush=True)
                            break
                        raise RuntimeError(error)
                    time.sleep(interval)
            except Reconnected:
                # The app may have accepted a start before the connection was lost. Reattach if it did.
                continue
            except RuntimeError as error:
                if 'already running' in str(error).lower():
                    time.sleep(interval)
                    continue
                raise
    except KeyboardInterrupt:
        if current and owned:
            try:
                app.request('/api/job/cancel', {'id': current})
                print('\nStopping the current search; completed work is saved.', file=sys.stderr)
            except (OSError, RuntimeError, Reconnected):
                print('\nCould not confirm cancellation; stop the search in the app.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('DECOMP_HOME', '~/Decomp-RetroReversing')).expanduser())
    parser.add_argument('--project', action='append', metavar='SLUG')
    parser.add_argument('--progress-file', type=Path)
    args = parser.parse_args()
    app = App(args.home)
    path = args.progress_file or app.home / '.decomp' / 'cross-game-batch.json'
    try:
        projects = app.read('/api/projects?brief=1')
        state = queue(path, projects, args.project)
        while state['remaining']:
            slug = state['remaining'][0]
            print(f'{slug}: {len(state["remaining"])} projects remaining', flush=True)
            run_project(app, slug)
            finish_project(path, state)
        print('All projects completed. The next run starts from the first project.')
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f'Cross-game batch stopped: {error}. Progress is saved in {path}.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
