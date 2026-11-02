/* The native window does not install a service worker or save browser snapshots. */
(() => {
  if (!document.body.dataset.serverMode || !('serviceWorker' in navigator)) return;
  const banner = document.createElement('div');
  banner.className = 'pwa-status'; banner.role = 'status'; banner.hidden = true;
  document.body.append(banner);
  let offline = !navigator.onLine;
  const show = saved => {
    offline = true;
    banner.hidden = false;
    const message = document.createElement('span');
    message.textContent = `Offline — viewing saved data${saved ? ' from ' + new Date(saved).toLocaleString() : ''}. Compiling and changes need a connection to your Mac.`;
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'ghost small-btn'; clear.textContent = 'Clear saved data';
    clear.onclick = async () => { await window.clearDecompOffline(); location.reload(); };
    banner.replaceChildren(message, clear);
  };
  window.decompConnection = (response) => {
    if (response.headers.get('X-Decomp-Offline')) show(response.headers.get('X-Decomp-Saved-At'));
    else if (response.ok) { offline = false; banner.hidden = true; }
  };
  window.decompIsOffline = () => offline;
  window.addEventListener('offline', () => show());
  window.addEventListener('online', () => { banner.textContent = 'Reconnecting to your Mac…'; });
  if (offline) show();
  const warm = () => navigator.serviceWorker.controller?.postMessage({type:'WARM_OFFLINE',slug:location.hash.match(/^#\/p\/([\w-]+)/)?.[1]});
  navigator.serviceWorker.addEventListener('controllerchange', warm);
  navigator.serviceWorker.register('/sw.js', {scope:'/'}).then(async registration => {
    await navigator.serviceWorker.ready;
    (registration.active || navigator.serviceWorker.controller)?.postMessage({type:'WARM_OFFLINE',slug:location.hash.match(/^#\/p\/([\w-]+)/)?.[1]});
  }).catch(() => { /* The regular browser app still works when storage is unavailable. */ });
  window.clearDecompOffline = async () => {
    for (const name of await caches.keys()) if (name.startsWith('decomp-pwa-')) await caches.delete(name);
  };
})();
