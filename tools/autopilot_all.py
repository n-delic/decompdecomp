#!/usr/bin/env python3
"""Run Autopilot on projects sequentially using the running decompdecomp app.

Run python3 tools/autopilot_all.py --home PATH. Ctrl-C stops the Autopilot
started by this script. Already running Autopilots are followed, not stopped.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

from cross_game_all import call


def run(info, projects, interval=5):
    failures = []
    owned = None
    try:
        for index, project in enumerate(projects, 1):
            slug = project['slug']
            print(f'[{index}/{len(projects)}] {project.get("title") or slug} ({slug})', flush=True)
            try:
                path = '/api/project?' + urllib.parse.urlencode({'slug': slug})
                status = (call(info, path).get('autopilot') or {})
                if status.get('state') != 'running':
                    previous_started = status.get('started_at')
                    overview = call(info, '/api/autopilot', {'slug': slug, 'action': 'start'})
                    status = overview.get('autopilot') or {}
                    if status.get('state') != 'running' and (not status.get('started_at') or status.get('started_at') == previous_started):
                        raise RuntimeError('Autopilot did not start; check for an active cross-game search.')
                    owned = slug
                else:
                    print('  Following the Autopilot already running.', flush=True)
                last = None
                while True:
                    state = status.get('state')
                    phase = (status.get('phase'), status.get('tried'), status.get('total'), status.get('matched'))
                    if phase != last:
                        print(f'  {phase[0] or state}: {phase[1] or 0}/{phase[2] or 0} tried, {phase[3] or 0} matched', flush=True)
                        last = phase
                    if state != 'running' and not status.get('paused_for_search'):
                        owned = None
                        if state != 'done':
                            raise RuntimeError(f'Autopilot ended with state {state or "unknown"}.')
                        if status.get('no_drafts'):
                            print(f'  Drafts unavailable: {status["no_drafts"]}', flush=True)
                        break
                    time.sleep(interval)
                    status = call(info, path).get('autopilot') or {}
            except RuntimeError as error:
                if 'shutting down' in str(error).lower():
                    raise RuntimeError(f'App is shutting down; batch stopped at {slug}. Run it again after reopening.') from error
                print(f'  Failed: {error}', file=sys.stderr, flush=True)
                failures.append(slug)
                owned = None
    except KeyboardInterrupt:
        if owned:
            try:
                call(info, '/api/autopilot', {'slug': owned, 'action': 'stop'})
                print('\nStopping Autopilot; completed work is saved.', file=sys.stderr)
            except (OSError, RuntimeError) as error:
                print(f'\nCould not stop Autopilot for {owned}: {error}. Stop it in the app.', file=sys.stderr)
        raise
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('DECOMP_HOME', '~/Decomp-RetroReversing')).expanduser())
    parser.add_argument('--project', action='append', metavar='SLUG', help='only run this project; repeat to select more')
    args = parser.parse_args()
    try:
        info = json.loads((args.home.expanduser() / '.decomp' / 'server.json').read_text())
        projects = call(info, '/api/projects?brief=1')
        if args.project:
            selected = set(args.project)
            missing = selected - {project['slug'] for project in projects}
            if missing:
                raise RuntimeError('Unknown projects: ' + ', '.join(sorted(missing)))
            projects = [project for project in projects if project['slug'] in selected]
        failures = run(info, projects)
        if failures:
            print('Failed projects: ' + ', '.join(failures), file=sys.stderr)
            return 1
        print(f'Finished {len(projects)} projects.')
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f'Could not run Autopilot: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
