#!/usr/bin/env python3
"""Run the app's Matches from other games search on each project in turn.

Start the app first, then run `python3 tools/cross_game_all.py`. Use
--home for a different app home, or --project SLUG to select projects. Ctrl-C
asks the app to stop the current search; completed work remains saved.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def call(info, path, body=None):
    port = info['port']
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'User-Agent': f"decompdecomp-window/{info['secret']}", 'X-Decomp': '1',
                 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get('error', str(error))
        except (ValueError, AttributeError):
            detail = str(error)
        raise RuntimeError(detail) from error


def run(info, projects, interval=5):
    failures = []
    current = None
    try:
        for index, project in enumerate(projects, 1):
            slug = project['slug']
            print(f'[{index}/{len(projects)}] {project.get("title") or slug} ({slug})', flush=True)
            retries = 0
            while True:
                try:
                    current = call(info, '/api/cross-game/run', {'slug': slug})['job']
                    last = None
                    while True:
                        job = call(info, '/api/job?' + urllib.parse.urlencode({'id': current}))
                        progress = job.get('progress') or {}
                        status = progress.get('message') or progress.get('phase')
                        if status and status != last:
                            print(f'  {status}', flush=True)
                            last = status
                        if job['state'] == 'done':
                            result = job.get('result') or {}
                            print(f'  {result.get("matched", 0)} matches, {result.get("improved", 0)} improvements', flush=True)
                            current = None
                            break
                        if job['state'] in ('error', 'cancelled'):
                            current = None
                            raise RuntimeError(job.get('error') or f"Job {job['state']}.")
                        time.sleep(interval)
                    break
                except RuntimeError as error:
                    if 'background worker stopped during a pass' in str(error).lower() and retries < 3:
                        retries += 1
                        print(f'  Worker stopped; resuming saved work (retry {retries}/3).', flush=True)
                        continue
                    if 'shutting down' in str(error).lower():
                        raise RuntimeError(f'App is shutting down; batch stopped at {slug}. Run it again after reopening.') from error
                    print(f'  Failed: {error}', file=sys.stderr, flush=True)
                    failures.append(slug)
                    break
    except KeyboardInterrupt:
        if current:
            try:
                call(info, '/api/job/cancel', {'id': current})
                print('\nStopping the current search; completed work is saved.', file=sys.stderr)
            except (OSError, RuntimeError) as error:
                print(f'\nCould not stop job {current}: {error}. Stop it in the app.', file=sys.stderr)
        raise
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('DECOMP_HOME', '~/Decomp-RetroReversing')).expanduser(),
                        help='app data home (default: DECOMP_HOME or ~/Decomp-RetroReversing)')
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
        print(f'Could not run searches: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
