#!/usr/bin/env python3
"""Run Autopilot one project at a time, resuming after app restarts.

Progress is stored in HOME/.decomp/autopilot-batch.json. A completed run starts
again next time. Use --progress-file for independent project selections.
Ctrl-C requests cancellation of an Autopilot started by this script.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from cross_game_all import project_path
from project_batch import App, Reconnected, finish_project, queue


def run_project(app, slug, interval=5):
    owned = False
    try:
        while True:
            try:
                status = app.request(project_path(slug)).get('autopilot') or {}
                owned = False
                if status.get('state') != 'running' and not status.get('paused_for_search'):
                    previous = status.get('started_at')
                    status = app.request('/api/autopilot', {'slug': slug, 'action': 'start'}).get('autopilot') or {}
                    if status.get('state') != 'running' and (not status.get('started_at') or status.get('started_at') == previous):
                        raise RuntimeError('Autopilot did not start; check for an active cross-game search.')
                    owned = True
                last = None
                while True:
                    state = status.get('state')
                    phase = (status.get('phase'), status.get('tried'), status.get('total'), status.get('matched'))
                    if phase != last:
                        print(f'  {phase[0] or state}: {phase[1] or 0}/{phase[2] or 0} tried, {phase[3] or 0} matched', flush=True)
                        last = phase
                    if state != 'running' and not status.get('paused_for_search'):
                        if state != 'done':
                            raise RuntimeError(f'Autopilot ended with state {state or "unknown"}.')
                        if status.get('no_drafts'):
                            print(f'  Drafts unavailable: {status["no_drafts"]}', flush=True)
                        return
                    time.sleep(interval)
                    status = app.request(project_path(slug)).get('autopilot') or {}
            except Reconnected:
                # A restarted app retains the last finished status, but not its in-flight Autopilot.
                continue
    except KeyboardInterrupt:
        if owned:
            try:
                app.request('/api/autopilot', {'slug': slug, 'action': 'stop'})
                print('\nStopping Autopilot; completed work is saved.', file=sys.stderr)
            except (OSError, RuntimeError, Reconnected):
                print('\nCould not confirm cancellation; stop Autopilot in the app.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('DECOMP_HOME', '~/Decomp-RetroReversing')).expanduser())
    parser.add_argument('--project', action='append', metavar='SLUG')
    parser.add_argument('--progress-file', type=Path)
    args = parser.parse_args()
    app = App(args.home)
    path = args.progress_file or app.home / '.decomp' / 'autopilot-batch.json'
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
        print(f'Autopilot batch stopped: {error}. Progress is saved in {path}.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
