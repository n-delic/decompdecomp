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
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'ghost small-btn'; retry.textContent = 'Reconnect';
    retry.onclick = () => probe();
    banner.replaceChildren(message, retry, clear);
  };
  window.decompConnection = (response) => {
    if (response.headers.get('X-Decomp-Offline')) show(response.headers.get('X-Decomp-Saved-At'));
    else if (response.ok) { offline = false; banner.hidden = true; }
  };
  window.decompIsOffline = () => offline;
  window.addEventListener('offline', () => show());
  let probing = false;
  const probe = async () => {
    if (probing) return;
    probing = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/health', {cache:'no-store',signal:controller.signal});
      const data = await response.json();
      if (response.ok && data.ok && !response.headers.get('X-Decomp-Offline')) {
        const wasOffline = offline;
        offline = false; banner.hidden = true;
        if (wasOffline) window.dispatchEvent(new Event('decomp-reconnected'));
      }
    } catch { /* Keep the saved view; the next probe tries again. */ }
    finally { clearTimeout(timer); probing = false; }
  };
  window.addEventListener('online', probe);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && offline) probe(); });
  setInterval(() => { if (offline && !document.hidden) probe(); }, 10000);
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
