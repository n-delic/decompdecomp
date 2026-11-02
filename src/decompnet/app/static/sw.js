/* Private offline snapshots. No credentials, ROMs, writes or background submissions are stored. */
'use strict';
const PREFIX = 'decomp-pwa-';
const CACHE = PREFIX + '__BUILD__';
const READS = new Set(['/api/state', '/api/projects', '/api/project', '/api/map', '/api/live', '/api/stats', '/api/function', '/api/diff']);
const LIMIT = 60;
const MAX_BYTES = 2 * 1024 * 1024;
const local = path => new URL(path, self.location.origin).href;
let generation = 0;
async function clearSnapshots() {
  generation++;
  for (const name of await caches.keys()) if (name.startsWith(PREFIX)) await caches.delete(name);
}
async function tell(message) {
  for (const client of await self.clients.matchAll()) client.postMessage(message);
}
function dataKey(url) {
  const key = new URL(url);
  if (key.pathname === '/api/live') for (const part of ['e','a','s']) key.searchParams.delete(part);
  return key.href;
}
async function remember(key, response, epoch = generation) {
  if (!response.ok || response.redirected) return;
  let bytes = await response.clone().arrayBuffer();
  if (bytes.byteLength > MAX_BYTES) return;
  const headers = new Headers(response.headers);
  headers.set('X-Decomp-Saved-At', new Date().toISOString());
  headers.delete('Content-Length');
  headers.delete('Content-Encoding');
  if (new URL(key).pathname === '/api/state') {
    const state = JSON.parse(new TextDecoder().decode(bytes));
    // Credentials and connection configuration never belong in offline storage.
    if (state.settings) { delete state.settings.endpoints; delete state.settings.api; }
    state.agents_detecting = false;
    bytes = JSON.stringify(state);
  }
  const cache = await caches.open(CACHE);
  if (epoch !== generation) return;
  await cache.delete(key);
  await cache.put(key, new Response(bytes, {status:200, headers}));
  const apiKeys = (await cache.keys()).filter(request => new URL(request.url).pathname.startsWith('/api/') && !['/api/state','/api/projects'].includes(new URL(request.url).pathname));
  for (const request of apiKeys.slice(0, Math.max(0, apiKeys.length - LIMIT))) await cache.delete(request);
}
async function shell(response, epoch = generation) {
  if (!response.ok || response.redirected) return;
  const html = await response.clone().text();
  if (!html.includes('data-server-mode="1"')) return;
  await remember(local('/'), response, epoch);
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)].map(match => match[1]);
  assets.push('/assets/fonts/Archivo-400_800.woff2','/assets/fonts/IBMPlexMono-400.woff2','/assets/fonts/IBMPlexMono-600.woff2','/assets/server.css');
  const cache = await caches.open(CACHE);
  await Promise.allSettled([...new Set(assets)].map(async path => {
    const res = await fetch(path);
    if (res.ok) await cache.put(local(path), res);
  }));
}
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener('message', event => {
  if (event.data?.type === 'CLEAR_OFFLINE') event.waitUntil(clearSnapshots());
  if (event.data?.type === 'WARM_OFFLINE') event.waitUntil((async () => {
    const epoch = generation;
    await shell(await fetch('/'), epoch);
    // State and project list make the first offline launch useful without revisiting these pages.
    const paths = ['/api/state','/api/projects'];
    if (/^[\w-]+$/.test(event.data.slug || '')) for (const route of ['project','map','stats','live']) paths.push(`/api/${route}?slug=${event.data.slug}`);
    for (const path of paths) await remember(local(path), await fetch(path), epoch);
    await tell({type:'OFFLINE_READY'});
  })().catch(() => {}));
});
async function readNetwork(request) {
  const controller = new AbortController();
  // Large project reads can take tens of seconds while agents are compiling.
  // A short fallback deadline must never turn a healthy, uncached read into an offline error.
  const budget = new URL(request.url).pathname.startsWith('/api/') ? 120000 : 15000;
  const cancel = () => controller.abort();
  request.signal?.addEventListener('abort', cancel, {once:true});
  if (request.signal?.aborted) controller.abort();
  const timer = setTimeout(cancel, budget);
  try { return await fetch(request, {signal:controller.signal}); }
  finally { clearTimeout(timer); request.signal?.removeEventListener('abort', cancel); }
}
async function handle(request) {
  const epoch = generation;
  const url = new URL(request.url);
  if (request.method !== 'GET') {
    if (url.pathname === '/logout') await clearSnapshots();
    try { return await fetch(request); }
    catch { return Response.json({error:'Offline: connect to your Mac to make changes. Nothing has been queued.'}, {status:503}); }
  }
  const navigation = request.mode === 'navigate';
  const asset = url.pathname.startsWith('/assets/');
  const read = READS.has(url.pathname) && !(url.pathname === '/api/map' && url.searchParams.has('since'));
  const key = navigation ? local('/') : dataKey(url);
  try {
    const response = await readNetwork(request);
    if ((response.status === 401 || response.status === 403) && url.pathname.startsWith('/api/')) await clearSnapshots();
    if (navigation && response.redirected && new URL(response.url).pathname === '/login') await clearSnapshots();
    if (navigation && url.pathname === '/') await shell(response.clone(), epoch).catch(() => {});
    if (asset || read) await remember(key, response.clone(), epoch).catch(() => {});
    return response;
  } catch {
    if (asset || read || (navigation && url.pathname === '/')) {
      const cached = await (await caches.open(CACHE)).match(key);
      if (cached) {
        const headers = new Headers(cached.headers);
        headers.set('X-Decomp-Offline', '1');
        return new Response(await cached.arrayBuffer(), {status:200,headers});
      }
    }
    if (navigation) return new Response('<!doctype html><meta name="viewport" content="width=device-width"><title>decompdecomp offline</title><h1>Connect to your Mac</h1><p>Open and sign in to the app once while connected to save an offline view.</p>', {status:503,headers:{'Content-Type':'text/html'}});
    return Response.json({error:'Could not reach your Mac, and this view has no saved offline copy. Reconnecting automatically…'}, {status:503,headers:{'X-Decomp-Offline':'1'}});
  }
}
self.addEventListener('fetch', event => {
  if (new URL(event.request.url).origin === self.location.origin) event.respondWith(handle(event.request));
});
