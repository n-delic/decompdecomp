"""Explicit, origin-bound local browser capabilities. No window/agent credentials cross this boundary.

Initial bridge: manual KMC ROM tasks only. Imported builds need an object/task-context adapter before
participating; never let an unsupported project fall through to arbitrary build commands.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..project import compiler_for
from .source_preparation import inputs

FLAGS = {'-G0', '-G8', '-mips3', '-mgp32', '-mfp32', '-O0', '-O1', '-O2', '-O3', '-g', '-g0', '-g1', '-g2', '-g3'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def origin_value(value):
    parsed = urlsplit(value)
    if (parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or not parsed.hostname or parsed.scheme not in ('http', 'https')
            or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))):
        raise ValueError('Enter an exact HTTPS browser origin, or an HTTP loopback origin such as http://127.0.0.1:4179.')
    return value.rstrip('/')


def validate_source(source):
    # Native compilers are not an OS sandbox. Do not expose preprocessing file reads or assembler
    # directives via this restricted bridge; richer source needs a separate isolated runner first.
    folded = re.sub(r'\\\r?\n', '', source)
    if '#' in folded or '%:' in folded or '??' in folded or re.search(r'\b(?:asm|__asm|__asm__|__attribute|__attribute__)\b', folded):
        raise ValueError('This initial bridge accepts plain C only: no preprocessor directives, inline assembly or attributes. Keep declarations in the host context.')


class Bridge:
    def __init__(self, net):
        self.net = net
        self.lock = threading.RLock()
        self.codes = {}
        self.sessions = {}
        self.tasks = {}
        self.failures = {}

    def prune(self):
        now = time.time()
        self.codes = {k: v for k, v in self.codes.items() if v['expires'] > now}
        self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
        self.tasks = {k: v for k, v in self.tasks.items() if v['expires'] > now and v['session'] in self.sessions}

    def project(self, slug):
        proj = self.net.project_row(slug)
        if self.net.fork_slug(slug) or not self.net.is_rom(proj) or slug in self.net.links:
            raise ValueError('This browser bridge supports local ROM projects only; joined rooms and imported builds need a task adapter.')
        if self.net.cfg(proj).get('compiler', {}).get('toolchain') != 'gcc-2.7.2-kmc':
            raise ValueError('Choose a project using the KMC GCC 2.7.2 compiler.')
        return proj

    def create_code(self, slug, origin):
        self.project(slug)
        origin = origin_value(origin)
        with self.lock:
            self.prune()
            if len(self.codes) >= 16:
                raise ValueError('Too many pending pairings. Revoke browser access first.')
            code = secrets.token_hex(6).upper()
            self.codes[digest(code.encode())] = {'slug': slug, 'origin': origin, 'expires': time.time()+300}
        return {'code': code, 'expiresIn': 300, 'origin': origin, 'project': slug}

    def allowed(self, origin):
        with self.lock:
            self.prune()
            return bool(origin) and any(x['origin'] == origin for x in [*self.codes.values(), *self.sessions.values()])

    def pair(self, origin, code):
        with self.lock:
            self.prune()
            since, count = self.failures.get(origin, (time.time(), 0))
            if time.time()-since > 300:
                since, count = time.time(), 0
            if count >= 8:
                raise ValueError('Too many pairing attempts. Wait five minutes and create a new code.')
            key = digest(str(code).strip().upper().encode())
            pending = self.codes.get(key)
            if not pending or pending['origin'] != origin:
                self.failures[origin] = (since, count+1)
                raise ValueError('Invalid or expired pairing code.')
            if len(self.sessions) >= 16:
                raise ValueError('Too many paired browsers. Revoke browser access first.')
            del self.codes[key]
            token = secrets.token_urlsafe(32)
            session = {'slug': pending['slug'], 'origin': origin, 'expires': time.time()+3600, 'busy': False, 'submissions': 0}
            self.sessions[digest(token.encode())] = session
        return {'token': token, 'project': session['slug'], 'expiresIn': 3600}

    def auth(self, origin, token):
        key = digest(token.encode())
        with self.lock:
            self.prune()
            session = self.sessions.get(key)
            if not session or session['origin'] != origin:
                raise PermissionError('Browser authorization expired or was revoked. Pair again in the desktop app.')
            return key, session

    def revoke(self, slug):
        with self.lock:
            self.codes = {k:v for k,v in self.codes.items() if v['slug'] != slug}
            self.sessions = {k:v for k,v in self.sessions.items() if v['slug'] != slug}
            self.prune()
        return {'revoked': True}

    def listing(self, session, search):
        proj = self.project(session['slug'])
        rows = self.net.store.all("SELECT id,name,size,best_score,n_insns FROM functions WHERE project_id=? "
                                  "AND status='open' AND size BETWEEN 4 AND 16384 AND name LIKE ? "
                                  "ORDER BY size,id LIMIT 100",
                                  proj['id'], '%'+str(search)[:80]+'%')
        return {'project': session['slug'], 'functions': [dict(r) for r in rows]}

    def snapshot(self, proj, fn):
        return (fn['name'], fn['rom_offset'], self.net._context(proj['id'], fn['unit'])['header'], inputs(self.net, proj, fn))

    def compiler_variants(self, compiler):
        variants=[]
        for flags in [compiler['flags'], *self.net.flag_variants(compiler['toolchain'], list(compiler['flags']))]:
            if any(flag not in FLAGS for flag in flags):
                continue
            item={'id':digest(json.dumps([compiler['toolchain'], flags]).encode())[:16],
                  'compiler':compiler['toolchain'], 'flags':list(flags)}
            if item not in variants:
                variants.append(item)
        return variants

    def task(self, key, session, fid):
        proj = self.project(session['slug'])
        fn = self.net.store.one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='open'", fid, proj['id'])
        if not fn or not re.fullmatch(r'[A-Za-z_]\w*', fn['name']) or not 0 < len(fn['target'] or b'') <= 16384:
            raise ValueError('Choose an open function in this project with a valid C name and at most 16 KiB of code.')
        # Reading a saved candidate does not acquire, release or replace an agent's claim.
        # Submission still uses the ordinary claim and freshness gates.
        cfg = self.net.cfg(proj)
        compiler = compiler_for(cfg, fn['vram'])
        flags = compiler.get('flags', [])
        if compiler.get('toolchain') != 'gcc-2.7.2-kmc' or compiler.get('cmd') or any(f not in FLAGS for f in flags):
            raise ValueError('This function uses compiler flags the browser does not support yet.')
        context = self.net._context(proj['id'], fn['unit'])['header']
        if len(context.encode()) > 128*1024 or re.search(r'^\s*#\s*include\b', context, re.M):
            raise ValueError('This function needs external or oversized headers. The initial browser bridge needs self-contained declarations.')
        rom = (Path(proj['root']) / cfg['rom']['file']).read_bytes()
        offset = fn['rom_offset']
        if offset is None or offset < 0 or rom[offset:offset+len(fn['target'])] != fn['target']:
            raise ValueError('The function does not map directly to this ROM. Refresh the project or choose main-ROM code.')
        source = fn['best_source'] or fn['seed'] or f"int {fn['name']}(void) {{ return 0; }}"
        if len(source.encode()) > 128*1024:
            raise ValueError('This saved source is too large for a browser task.')
        task_id = secrets.token_urlsafe(24)
        packet = {'schema': 'n64-browser-lab/task-v1', 'id': task_id, 'project': session['slug'], 'function': fn['name'],
                  'functionId': fn['id'], 'source': source, 'context': context, 'romSha256': digest(rom),
                  'romOffset': offset, 'vram': fn['vram'], 'targetSha256': digest(fn['target']),
                  'byteCount': len(fn['target']), 'compiler': {'id':'gcc-2.7.2-kmc', 'flags':flags},
                  'compilerVariants': self.compiler_variants(compiler),
                  'symbols': {r['name']:r['address'] for r in self.net.store.all('SELECT name,address FROM symbols WHERE project_id=?', proj['id'])
                              if r['name'] != fn['name'] and re.fullmatch(r'[A-Za-z_]\w*', r['name'])}, 'expiresIn':300}
        if len(json.dumps(packet).encode()) > 256*1024:
            raise ValueError('This task has too many symbols for the initial browser bridge.')
        snapshot = self.snapshot(proj, fn)
        with self.lock:
            self.prune()
            if self.sessions.get(key) is not session:
                raise PermissionError('Browser access expired or was revoked. Pair again.')
            # One pending task per capability; fetching a replacement invalidates the previous task.
            self.tasks = {k:v for k,v in self.tasks.items() if v['session'] != key}
            self.tasks[task_id] = {'session':key, 'expires':time.time()+300, 'fid':fn['id'], 'snapshot':snapshot, 'packet':packet}
        return packet

    def consume(self, key, session, task_id):
        with self.lock:
            self.prune()
            task = self.tasks.get(task_id)
            if not task or task['session'] != key:
                raise ValueError('Task expired, was already submitted, or belongs to another browser. Fetch it again.')
            if session['busy'] or session['submissions'] >= 30:
                raise ValueError('A submission is running, or this pairing reached its 30-submission limit.')
            del self.tasks[task_id]
            session['busy'] = True
            session['submissions'] += 1
        return task

    def check(self, key, session, task):
        with self.lock:
            self.prune()
            if self.sessions.get(key) is not session:
                raise PermissionError('Browser access expired or was revoked before the result could be saved.')
        proj = self.project(session['slug'])
        fn = self.net.store.one('SELECT * FROM functions WHERE id=? AND project_id=?', task['fid'], proj['id'])
        if not fn or fn['status'] != 'open' or self.snapshot(proj, fn) != task['snapshot']:
            raise ValueError('The function, context or compiler settings changed. Fetch a fresh task before submitting.')
        return proj, fn

    def submit(self, key, session, task, source, variant_id=None):
        try:
            proj, fn = self.check(key, session, task)
            agent, _ = self.net.local_agent('browser-worker-'+key[:10])
            variants=task['packet'].get('compilerVariants', [])
            variant=next((v for v in variants if v['id']==variant_id), None) if variant_id else None
            if variant_id and variant is None:
                raise ValueError('Choose a compiler variation offered by this host task.')
            if variant and variant['flags'] != task['packet']['compiler']['flags']:
                result=self.submit_variant(key, session, task, proj, fn, agent, source, variant)
            else:
                result = self.net.try_quietly(agent, proj, fn, source, before_record=lambda: self.check(key, session, task))
            current = self.net.store.one('SELECT best_score,status FROM functions WHERE id=?', fn['id'])
            return {'matched': bool(result.get('matched')), 'score': result.get('score'), 'bestScore':current['best_score'],
                    'improved': result.get('score') is not None and (fn['best_score'] is None or current['best_score'] < fn['best_score']),
                    'hostVerified': True, 'function':fn['name']}
        finally:
            with self.lock:
                session['busy'] = False

    def submit_variant(self, key, session, task, proj, fn, agent, source, variant):
        """Only an independently exact bundle may publish a function-local profile change."""
        from ..rom.export import _single_definition
        from .prepared_evidence import scoped_config
        if not self.net.permuter._hold(agent, fn, count_turn=False, seconds=60):
            raise ValueError('An agent holds this function. Fetch it again after it is released.')
        try:
            self.check(key, session, task)
            expected=inputs(self.net, proj, fn)
            settings={'toolchain':variant['compiler'], 'flags':variant['flags']}
            private={**dict(proj), 'config':json.dumps(scoped_config(proj, fn, settings))}
            header=self.net._context(proj['id'], fn['unit'])['header']
            compiled=self.net._compile(private, header, source, fn)
            if not compiled.ok or not _single_definition(compiled.obj, fn['name']):
                return {'matched':False, 'score':None}
            candidate, verdict, _=self.net._evaluate(fn, proj, compiled.obj)
            if candidate is None or verdict is not True:
                return {'matched':False, 'score':None}
            self.net._record(fn, agent, 0, source, 0, matched=True, via='browser-compiler-variant',
                             effective_settings=compiled.effective_settings, prepared_header=header,
                             prepared_inputs=expected, prepared_compiler=settings,
                             before_record=lambda:self.check(key, session, task))
            return {'matched':True, 'score':0}
        finally:
            self.net.permuter._let_go(agent, fn)
