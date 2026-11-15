/* Browser guests use only the scoped room API. No private app/window credentials. */
function sha1(bytes) {
  // SHA-1 is the existing game identity, not a password. Fallback for HTTP LAN
  // addresses where browsers intentionally disable SubtleCrypto.
  const length = bytes.length, padded = new Uint8Array(Math.ceil((length + 9) / 64) * 64);
  padded.set(bytes); padded[length] = 128;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(length / 0x20000000));
  view.setUint32(padded.length - 4, (length * 8) >>> 0);
  let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80), rol = (n, s) => (n << s) | (n >>> (32 - s));
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
    for (let i = 16; i < 80; i++) w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      const f = i < 20 ? (b & c) | (~b & d) : i < 40 ? b ^ c ^ d : i < 60 ? (b & c) | (b & d) | (c & d) : b ^ c ^ d;
      const k = i < 20 ? 0x5a827999 : i < 40 ? 0x6ed9eba1 : i < 60 ? 0x8f1bbcdc : 0xca62c1d6;
      const next = (rol(a, 5) + f + e + k + w[i]) | 0;
      e = d; d = c; c = rol(b, 30); b = a; a = next;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0; h4 = (h4 + e) | 0;
  }
  return [h0, h1, h2, h3, h4].map(n => (n >>> 0).toString(16).padStart(8, '0')).join('');
}

async function gameDigest(file) {
  if (file.size > 256 * 1024 * 1024) throw new Error('Select a file up to 256 MiB. For a disc project, ask the host which extracted program to use.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  // Canonicalize the two byte-swapped cartridge dump formats before hashing.
  if (bytes[0] === 0x37 && bytes[1] === 0x80 && bytes[2] === 0x40 && bytes[3] === 0x12) {
    for (let i = 0; i + 1 < bytes.length; i += 2) [bytes[i], bytes[i + 1]] = [bytes[i + 1], bytes[i]];
  } else if (bytes[0] === 0x40 && bytes[1] === 0x12 && bytes[2] === 0x37 && bytes[3] === 0x80) {
    for (let i = 0; i + 3 < bytes.length; i += 4) {
      [bytes[i], bytes[i + 1], bytes[i + 2], bytes[i + 3]] = [bytes[i + 3], bytes[i + 2], bytes[i + 1], bytes[i]];
    }
  }
  if (!globalThis.crypto?.subtle) return sha1(bytes);
  return [...new Uint8Array(await crypto.subtle.digest('SHA-1', bytes))].map(n => n.toString(16).padStart(2, '0')).join('');
}

(async function workspace() {
  const $ = id => document.getElementById(id);
  const room = new URLSearchParams(location.hash.slice(1)).get('room') || '';
  const invite = decodeURIComponent(location.pathname.split('/')[2] || '');
  const key = `decomp-room:${room}`, notice = text => { $('notice').textContent = text; };
  let token = '', functions = [], selected = '', held = '', busy = false, timer;
  try { token = sessionStorage.getItem(key) || ''; } catch (_) { /* storage can be disabled */ }
  function saveToken(value) { token = value; try { value ? sessionStorage.setItem(key, value) : sessionStorage.removeItem(key); } catch (_) {} }
  async function api(path, data) {
    const url = new URL(`/collab/${path}`, location.origin);
    if (room) url.searchParams.set('room', room);
    const response = await fetch(url, {method: data === undefined ? 'GET' : 'POST', credentials: 'omit',
      headers: {...(token ? {Authorization: `Bearer ${token}`} : {}), ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
      ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    const out = await response.json();
    if (!response.ok) throw new Error(out.error || `Request failed (${response.status}).`);
    return out;
  }
  function renderList() {
    const query = $('search').value.toLowerCase();
    $('functions').replaceChildren(new Option('Select a function…', ''));
    for (const fn of functions.filter(f => f.name.toLowerCase().includes(query) || f.name === selected)) {
      $('functions').add(new Option(`${fn.name} · ${fn.status === 'matched' ? 'matched' : fn.best_score == null ? 'not checked' : `score ${fn.best_score}`}`, fn.name));
    }
    $('functions').value = selected;
    const fn = functions.find(f => f.name === selected);
    if (fn) $('score').textContent = fn.status === 'matched' ? 'Verified match · score 0' : fn.best_score == null ? 'No saved score' : `Best score ${fn.best_score}`;
  }
  async function refresh() { const out = await api('browser/functions'); functions = out.functions; held = out.held || ''; renderList(); controls(); }
  function controls() {
    $('compile').disabled = busy || !selected || functions.find(f => f.name === selected)?.status === 'matched';
    $('source').disabled = busy || !selected || functions.find(f => f.name === selected)?.status === 'matched';
    $('functions').disabled = busy;
    $('release').disabled = busy || !held;
    $('refresh').disabled = busy;
    $('disconnect').disabled = busy;
    $('progress').hidden = !busy;
  }
  async function connected() {
    clearTimeout(timer); $('disconnect').hidden = false;
    try {
      const who = await api('whoami');
      await refresh();
      $('join').hidden = true; $('workspace').hidden = false; $('disconnect').hidden = false;
      notice(`Connected as ${who.name}. Checks run on the host; improvements are saved there.`);
    } catch (error) {
      if (error.message.includes('waiting for the host')) {
        notice('Waiting for the host to let you in…');
        $('join').hidden = true; timer = setTimeout(connected, 5000);
      } else { notice(error.message); $('join').hidden = false; saveToken(''); }
    }
  }
  $('join-form').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true; $('join-progress').hidden = false;
    try {
      notice('Checking your game on this device…');
      const sha = await gameDigest($('game').files[0]);
      const result = await api('join', {invite, name: $('name').value.trim(), sha1: sha});
      saveToken(result.token); $('game').value = ''; await connected();
    } catch (error) { notice(error.message); }
    finally { button.disabled = false; $('join-progress').hidden = true; }
  };
  $('refresh').onclick = () => refresh().catch(error => notice(error.message));
  $('search').oninput = renderList;
  $('functions').onchange = async () => {
    const next = $('functions').value;
    if (selected && $('source').value !== $('source').dataset.saved && !confirm('Discard your unsent edits?')) { $('functions').value = selected; return; }
    selected = next; busy = true; controls(); $('result').textContent = '';
    try {
      if (!next) { $('source').value = ''; return; }
      const task = await taskApi(next);
      $('source').value = task.source; $('source').dataset.saved = task.source;
      $('header').textContent = task.header; $('context').hidden = !task.header;
      $('function-title').textContent = task.function; $('score').textContent = task.status === 'matched' ? 'Verified match · score 0' : task.score == null ? 'No saved score' : `Best score ${task.score}`;
    } catch (error) { selected = ''; notice(error.message); }
    finally { busy = false; controls(); }
  };
  // URLSearchParams keeps C++ function names intact.
  async function taskApi(name) {
    const url = new URL('/collab/browser/task', location.origin);
    if (room) url.searchParams.set('room', room);
    url.searchParams.set('function', name);
    const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}, credentials: 'omit'});
    const out = await response.json(); if (!response.ok) throw new Error(out.error); return out;
  }
  $('compile').onclick = async () => {
    busy = true; controls(); $('result').textContent = 'Compiling and checking on the host…';
    try {
      if (held && held !== selected) throw new Error('Release your previous function before checking another.');
      const out = await api('browser/compile', {function: selected, source: $('source').value});
      held = out.matched ? '' : selected; $('result').textContent = out.message;
      $('source').dataset.saved = $('source').value; await refresh();
    } catch (error) { $('result').textContent = error.message; }
    finally { try { await refresh(); } catch (_) {} busy = false; controls(); }
  };
  $('release').onclick = async () => {
    try { await api('browser/release', {function: held}); held = ''; controls(); notice('Function released.'); }
    catch (error) { notice(error.message); }
  };
  $('disconnect').onclick = async () => {
    if (!confirm('Disconnect? Copy any unsent C edits first.')) return;
    try { await api('leave', {}); } catch (_) { /* host may have gone away */ }
    saveToken(''); selected = ''; location.reload();
  };
  setInterval(async () => {
    if (!token || busy || $('workspace').hidden) return;
    try { await api('whoami'); }
    catch (error) { notice(error.message); $('compile').disabled = true; }
  }, 15000);
  window.addEventListener('beforeunload', event => {
    if (selected && $('source').value !== $('source').dataset.saved) { event.preventDefault(); event.returnValue = ''; }
  });
  try {
    // Existing members survive invite rotation; new members need a current invite.
    if (token) { await connected(); return; }
    const url = new URL('/collab/hello', location.origin); if (room) url.searchParams.set('room', room); url.searchParams.set('invite', invite);
    const response = await fetch(url, {credentials: 'omit'}), info = await response.json();
    if (!response.ok) throw new Error(info.error);
    $('title').textContent = info.title || 'Decompile together';
    notice('Join with your own copy of the same game.'); $('join').hidden = false;
  } catch (error) { notice(error.message); }
})();
