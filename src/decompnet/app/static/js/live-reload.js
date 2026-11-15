"use strict";
/* Loaded only by a checkout. CSS swaps in place; scripts/markup reload the page,
   never the Python process. Edited text and open dialogs postpone automatic reload. */
(() => {
  const config = document.querySelector('meta[name="decomp-live-reload"]');
  if (!config) return;
  let current = {css:config.dataset.css, page:config.dataset.page}, pending = null, edited = false, reloading = false;
  const key = 'decomp-frontend-view';
  const banner = document.createElement('div');
  banner.className = 'pwa-status'; banner.role = 'status'; banner.hidden = true;
  const message = document.createElement('span'), button = document.createElement('button');
  button.className = 'ghost small-btn'; button.type = 'button'; button.textContent = 'Reload interface';
  banner.append(message, button); document.body.append(banner);
  document.addEventListener('input', event => {
    if (event.target.matches('textarea, input:not([type="checkbox"]):not([type="radio"]):not([type="range"])')) edited = true;
  });
  const blocked = () => edited || S.editDirty || !$('dialog').hidden || !$('launchPop').hidden;
  const reload = () => {
    if (reloading) return;
    reloading = true;
    try { sessionStorage.setItem(key, JSON.stringify({hash:location.hash, slug:S.slug, fn:S.fn,
      scroll:$('main').scrollTop, details:[...document.querySelectorAll('details[id][open]')].map(el=>el.id)})); } catch { /* Storage can be disabled. */ }
    location.reload();
  };
  button.onclick = reload;
  const swapCSS = revision => Promise.all([...document.querySelectorAll('link[rel="stylesheet"]')].map(old => new Promise((resolve, reject) => {
      const link = old.cloneNode(); const url = new URL(old.href); url.searchParams.set('live', revision); link.href = url.href;
      link.onload = () => { old.remove(); resolve(); };
      link.onerror = () => { link.remove(); reject(new Error("Stylesheet reload failed")); };
      old.after(link);
  })));
  // The hash restores the project/tab; reopen the function after its project finishes loading.
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(key)); sessionStorage.removeItem(key); } catch { /* No previous view. */ }
  if (saved && saved.hash === location.hash) {
    let tries = 0;
    const restore = async () => {
      if (saved.hash !== location.hash) return;
      if (saved.slug && (S.ov?.slug !== saved.slug || S.slug !== saved.slug)) {
        if (++tries < 60) setTimeout(() => restore().catch(()=>{}), 500);
        return;
      }
      if (saved.fn) await openFn(saved.fn);
      for (const id of saved.details || []) { const el = $(id); if (el?.tagName === 'DETAILS') el.open = true; }
      $('main').scrollTop = saved.scroll || 0;
    };
    restore().catch(()=>{});
  }
  const poll = async () => {
    try {
      const revision = await api('/api/frontend-revision');
      if (!revision.enabled) return;
      // Require two equal observations so multi-file saves settle before reloading.
      if (!pending || pending.css !== revision.css || pending.page !== revision.page) pending = revision;
      else {
        if (revision.css !== current.css) { await swapCSS(revision.css); current.css = revision.css; }
        if (revision.page !== current.page) {
          if (!blocked()) { reload(); return; }
          banner.hidden = false;
          message.textContent = 'Interface updated. Reload when ready; edited text or an open dialog is keeping this view in place. Workers continue running.';
        }
      }
    } catch { /* A save or temporary connection failure must not disrupt the app. */ }
    if (!reloading) setTimeout(poll, 1000);
  };
  setTimeout(poll, 1000);
})();
