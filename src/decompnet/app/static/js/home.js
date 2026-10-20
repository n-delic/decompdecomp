"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ home */
let picked = null;

/* Once, after this version's first start: the old `decomp mcp` entries it took out of the agents' settings
   (app/cleanup.py), each a sentence from the app, until the user dismisses them. Those entries started decompdecomp
   again whenever it was closed. */
function renderBridgesNotice() {
  const st = S.st || {}, removed = Array.isArray(st.bridges_removed) ? st.bridges_removed : [];
  const box = $("bridgesNotice");
  box.hidden = !removed.length || !!(st.settings && st.settings.bridges_removed_seen);
  if (box.hidden) return;
  setHtml(box, `<span><b>decompdecomp removed its old connection from your agents.</b> It isn't needed any more: helpers get
    what they need from the app itself.<ul class="small">${removed.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></span>
    <button class="ghost small-btn" type="button" id="bridgesOk">Dismiss</button>`);
  $("bridgesOk").onclick = async () => {
    box.hidden = true;
    try { S.st.settings = await api("/api/settings", { method: "POST", body: { bridges_removed_seen: true } }); } catch { /* it shows again next time */ }
  };
}

async function showHome() {
  show("home");
  resetDrop();
  loadState().catch(() => {});  // the version and the compiler lists: the projects don't wait for them
  let projects;
  try { projects = S.projects = await api("/api/projects"); } catch { projects = S.projects || []; }
  renderNav();
  const box = $("projects");
  box.innerHTML = "";
  $("recent").hidden = !projects.length;
  $("home").classList.toggle("returning", projects.length > 0);  // your projects first once you have some
  for (const p of projects) {
    const pct = p.bytes ? (100 * p.matched_bytes) / p.bytes : 0;
    const a = document.createElement("a");
    a.className = "card";
    a.href = `#/p/${p.slug}`;
    if (p.broken) {
      a.innerHTML = `<div class="cc-top"><span class="plat">?</span></div><div><h4>${esc(p.title)}</h4>
        <p class="meta err-text">Can't be opened: ${esc(p.broken)}</p></div>`;
      box.append(a);
      continue;
    }
    const meta = p.kind === "fork"
      ? `${p.origin === "scratch" ? "from scratch" : "public decomp"}${p.version ? " · " + esc(p.version) : ""}${p.ready ? "" : " · needs setup"}`
      : `${esc(p.rom?.game_code || "")}${p.rom?.region ? " · " + esc(p.rom.region) : ""} · ${esc(p.compiler?.name || "")}`;
    a.innerHTML = `<div class="cc-top"><span class="plat">${esc(p.platform_name || "?")}</span><span class="grow"></span><span class="meta">${esc(since(p.last_activity))}</span><span class="swatch pc-${colourOf(p.slug)}" aria-hidden="true"></span></div>
      <div><h4>${esc(p.title)}</h4><p class="meta">${meta}</p></div>
      <div class="meter"><i></i></div><p class="meta"><b>${pctNum(pct)}%</b> of code · ${(p.matched || 0).toLocaleString()} / ${(p.functions || 0).toLocaleString()} functions</p>`;
    box.append(a);
    requestAnimationFrame(() => { a.querySelector(".meter i").style.width = pct + "%"; });
  }
}

function resetDrop() {
  picked = null;
  $("dropIdle").hidden = false;
  $("dropPicked").hidden = true;
  $("dropBusy").hidden = true;
  $("dropError").hidden = true;
  $("own").checked = false;
  $("analyse").disabled = true;
  $("file").value = "";
}

const drop = $("drop");
drop.addEventListener("click", (e) => { if (!$("dropIdle").hidden && !e.target.closest("button")) $("file").click(); });
drop.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !$("dropIdle").hidden && e.target === drop) { e.preventDefault(); $("file").click(); } });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]); });
$("file").addEventListener("change", () => { if ($("file").files[0]) pickFile($("file").files[0]); });
$("own").addEventListener("change", updateAnalyse);
$("cancelPick").onclick = resetDrop;

/* Read the header in the browser so the user sees what they picked before anything happens. */
function parseRom(buf) {
  const b = new Uint8Array(buf);
  const magic = [b[0], b[1], b[2], b[3]].map((x) => x.toString(16).padStart(2, "0")).join("");
  const order = { "80371240": "z64", "37804012": "v64", "40123780": "n64" }[magic];
  if (!order) throw new Error("This doesn't look like an N64 ROM (.z64, .n64 or .v64).");
  const z = new Uint8Array(Math.min(b.length, 0x101000));
  for (let i = 0; i < z.length; i += 4) {
    const w = order === "z64" ? [b[i], b[i + 1], b[i + 2], b[i + 3]] : order === "v64" ? [b[i + 1], b[i], b[i + 3], b[i + 2]] : [b[i + 3], b[i + 2], b[i + 1], b[i]];
    z.set(w, i);
  }
  const text = (from, to) => Array.from(z.slice(from, to)).map((c) => (c >= 32 && c < 127 ? String.fromCharCode(c) : "")).join("").trim();
  const dv = new DataView(z.buffer);
  let jumps = 0, branches = 0;
  for (let i = 0x1000; i + 4 <= z.length; i += 4) {
    const w = dv.getUint32(i);
    if (w >>> 26 === 2) jumps++;
    else if ((w & 0xffff0000) >>> 0 === 0x10000000) branches++;
  }
  const regions = { E: "North America", J: "Japan", P: "Europe", D: "Germany", F: "France", U: "Australia", I: "Italy", S: "Spain", A: "All regions", X: "Europe", Y: "Europe" };
  const code = text(0x3b, 0x3f);
  return {
    title: text(0x20, 0x34) || "Untitled", code, region: regions[code[3]] || "Unknown",
    size: b.length, order, entry: dv.getUint32(8), compiler: branches > jumps ? "IDO" : "KMC",
  };
}

/* What the user dropped, from its first bytes (nothing is uploaded until they confirm). */
const platformName = (p) => ({ gcwii: "GameCube or Wii", playstation: "PlayStation 1 or 2" }[p] || consoleOf(p)?.name || "");
/* What installing a console's proprietary compiler pack (its first pack) means, and how its compiler is picked. */
const CW_PACK = {
  mwcc: { title: "CodeWarrior", text: "Install CodeWarrior, the compilers GameCube and Wii games were built with (about 80 MB). It's proprietary software, kept by the decomp community: only if you accept that.",
    note: "Not sure? Keep the default; you can change it later in configure.py if functions don't match." },
  msvc: { title: "Visual C++", text: "Install Visual C++ 6.0, 7.0 and 7.1, the compilers most PC games of 1998-2005 were built with (about 50 MB). It's proprietary software, kept by the decomp community: only if you accept that.",
    note: "decompdecomp reads which Visual C++ built the game from the program itself when it can; you can change it later in tools/configure.py. The functions are found by Ghidra (installed once, 540 MB, needs Java 21+)." },
  mwccarm: { title: "CodeWarrior for DS", text: "Install CodeWarrior for DS, the 24 compiler versions DS games were built with (about 50 MB). It's proprietary software, kept by the decomp community: only if you accept that.",
    note: "Not sure? Keep the default. Games mix versions: a file the game built with another one gets it later, when a helper finds the version its functions match with." },
};
const packOf = (p) => CW_PACK[consoleOf(p)?.packs[0]] ? consoleOf(p).packs[0] : "mwcc";
const MB = (n) => (n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KiB` : `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MiB`);

/* A DS ROM (consoles/nds/scratch.is_nds): the header's own CRC-16 is right and the ARM9 program sits in main RAM. */
function isNds(head, dv) {
  if (head.length < 0x200) return false;
  let crc = 0xffff;
  for (let i = 0; i < 0x15e; i++) {
    crc ^= head[i];
    for (let b = 0; b < 8; b++) crc = crc & 1 ? (crc >>> 1) ^ 0xa001 : crc >>> 1;
  }
  const off = dv.getUint32(0x20, true), entry = dv.getUint32(0x24, true), ram = dv.getUint32(0x28, true), size = dv.getUint32(0x2c, true);
  return crc === dv.getUint16(0x15e, true) && off >= 0x200 && ram >= 0x02000000 && ram < 0x02400000 && entry >= ram && entry < ram + size;
}

function identifyHead(head, file) {
  const dv = new DataView(head.buffer);
  const text = (a, b) => String.fromCharCode(...head.slice(a, b));
  const ascii = (a, b) => text(a, b).split("\0")[0].replace(/[^\x20-\x7e]/g, "").trim();
  const stem = file.name.replace(/\.[^.]*$/, "");
  const base = { title: stem, size: file.size };
  const pe = text(0, 2) === "MZ" && head.length >= 0x40 ? dv.getUint32(0x3c, true) : 0;
  if (isNds(head, dv)) return { ...base, platform: "nds", order: "DS ROM", code: ascii(12, 16), title: ascii(0, 12) || stem, entry: dv.getUint32(0x24, true) };
  if (pe && pe + 0x1a <= head.length && text(pe, pe + 4) === "PE\0\0" && dv.getUint16(pe + 4, true) === 0x14c && dv.getUint16(pe + 24, true) === 0x10b)
    return { ...base, platform: "win32", order: "Windows program (.exe)", entry: dv.getUint32(pe + 52, true) + dv.getUint32(pe + 40, true) };
  if (text(0, 8) === "PS-X EXE") return { ...base, platform: "ps", order: "PS-X EXE program", entry: dv.getUint32(0x10, true) };
  // which PlayStation a disc is for, its SYSTEM.CNF says (the server reads it)
  if (text(0x8001, 0x8006) === "CD001" || text(0x9319, 0x931e) === "CD001" || text(0x9311, 0x9316) === "CD001") return { ...base, platform: "playstation", order: "disc image" };
  if (text(0, 8) === "MComprHD") return { ...base, platform: "playstation", order: "CHD disc image" };
  if (text(0, 4) === "\x7fELF" && head[5] === 1 && dv.getUint16(18, true) === 8) return { ...base, platform: "ps2", order: "ELF program", entry: dv.getUint32(24, true) };
  if (head.length > 0x60 && dv.getUint32(0x1c) === 0xc2339f3d) return { ...base, platform: "gc", order: "disc image", code: ascii(0, 6), title: ascii(0x20, 0x80) || stem };
  if (head.length > 0x60 && dv.getUint32(0x18) === 0x5d1c9ea3) return { ...base, platform: "wii", order: "disc image", code: ascii(0, 6), title: ascii(0x20, 0x80) || stem };
  const packed = { "RVZ\x01": "RVZ", "WIA\x01": "WIA", WBFS: "WBFS", CISO: "CISO" }[text(0, 4)] || (dv.getUint32(0, true) === 0xb10bc001 ? "GCZ" : null);
  if (packed) return { ...base, platform: "gcwii", order: `${packed} disc image` };
  if (head.length >= 0x100) {  // a DOL: section offsets, addresses and sizes that make sense, entry in RAM
    const entry = dv.getUint32(0xe0);
    let used = 0, ok = entry >= 0x80000000 && entry < 0x81800000;
    for (let i = 0; i < 18 && ok; i++) {
      const off = dv.getUint32(i * 4), addr = dv.getUint32(72 + i * 4), size = dv.getUint32(144 + i * 4);
      if (!size) continue;
      used++;
      ok = off >= 0x100 && off + size <= file.size && addr >= 0x80000000 && addr < 0x81800000;
    }
    if (ok && used) return { ...base, platform: "gc", order: "program (DOL)", entry };
  }
  return null;
}

/* Files people try that decompdecomp can't start from: say what they are and why, not just "no". */
function notSupported(head, file) {
  const text = (a, b) => String.fromCharCode(...head.slice(a, b));
  const ext = (file.name.match(/\.([^.]+)$/) || [])[1]?.toLowerCase() || "";
  if (text(0, 2) === "MZ") return "That's a 64-bit or non-x86 Windows program (or a DOS one). decompdecomp starts PC games from their 32-bit x86 .exe.";
  if (ext === "nds") return "That doesn't look like a DS ROM: its header's checksum is wrong. Try a clean dump of your cartridge.";
  if (ext === "gba") return "Game Boy Advance games aren't supported in this version. For those, continue a public decomp.";
  if (text(0, 4) === "XEX2" || ext === "xex") return "That's an Xbox 360 program. Xbox 360 games aren't supported yet.";
  if (["nsp", "xci", "nso", "nro", "nca"].includes(ext) || text(0, 4) === "NSO0" || text(0, 4) === "PFS0") return "That's a Nintendo Switch file. Switch games aren't supported yet.";
  if (["sfc", "smc"].includes(ext)) return "That's a Super Nintendo ROM. SNES games were written in assembly, not compiled from C, so there's no C source to match.";
  if (["nes", "gb", "gbc", "md", "gen", "sms", "gg", "pce"].includes(ext)) return "Games for that console were written in assembly, not compiled from C, so there's no C source to match.";
  if (ext === "cue") return "That's the .cue sheet: pick the .bin file next to it (the track with the game's data, usually Track 1).";
  if (["zip", "7z", "rar"].includes(ext)) return "That's an archive: unpack it first, then drop the game file inside.";
  if (ext === "apk") return "Android apps aren't supported yet.";
  if (ext === "pbp" || ext === "cso") return "That's a PSP file. PSP games aren't supported yet.";
  return null;
}
// 1.0.3 rewrote most of the app: every platform is being tested again, and reports are how a platform moves up
const TRY_AND_TELL = `This version is new, so <a href="https://github.com/n-delic/decompdecomp/issues" target="_blank" rel="noopener">tell us how it goes</a> on your game.`;
const NOT_A_GAME = "That isn't a game file decompdecomp can start from scratch (yet). For other platforms, continue a public decomp.";

async function pickFile(file) {
  const head = new Uint8Array(await file.slice(0, 0x9400).arrayBuffer());
  let info = identifyHead(head, file), buf = file;
  if (!info) {
    const why = notSupported(head, file);
    if (why || file.size > 64 * 1024 * 1024) return toast(esc(why || NOT_A_GAME), "err");
    buf = await file.arrayBuffer();
    try { info = parseRom(buf); info.platform = "n64"; } catch (e) {
      return toast(esc(NOT_A_GAME), "err");
    }
  }
  picked = { buf, info, name: file.name };
  if (!S.st) await loadState().catch(() => {});  // dropped before the page finished loading: the compiler lists
  $("romFile").textContent = file.name;
  $("romTitle").textContent = info.title;
  const p = info.platform;
  const facts = p === "n64"
    ? [["Game code", info.code || "—"], ["Region", info.region], ["Size", MB(info.size)], ["Format", info.order],
       ["Entry", hex8(info.entry)], ["Looks like", info.compiler === "IDO" ? "IDO" : "GCC (KMC)"]]
    : [["Platform", platformName(p)], ["Format", info.order], ["Size", MB(info.size)],
       info.code ? ["Game ID", info.code] : ["Entry", info.entry != null ? hex8(info.entry) : "from the disc"],
       ["Project", consoleOf(p)?.layout || ""]];
  $("romFacts").innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  const level = platformStatus(p);
  $("romStatus").hidden = !level;
  $("romStatus").className = `plat-status ${level || ""}`;
  $("romStatus").innerHTML = level === "recommended"
    ? `<b>★ Recommended platform.</b> The most tested path to a full decomp you can port and mod. ${TRY_AND_TELL}`
    : `<b>Beta.</b> ${esc(platformName(p) || "This platform")} works, and needs more games tried on it. ${TRY_AND_TELL}`;
  setupCompilers(info);
  $("dropIdle").hidden = true;
  $("dropPicked").hidden = false;
  updateAnalyse();
}

function setupCompilers(info) {
  const p = info.platform, select = $("compilerPick"), note = $("compilerNote");
  const con = consoleOf(p) || { mode: "", compilers: [], packs: [], default_compiler: "" };
  $("compilerRow").hidden = false; note.hidden = true; $("mwConsent").hidden = true; $("mwAccept").checked = false;
  if (con.mode === "rom") {
    if (info.order.endsWith("disc image")) {
      $("compilerRow").hidden = true; note.hidden = false;
      note.textContent = "The compiler is picked from the disc's program, then tested on its code. You can change it later.";
    } else fillCompilers(select, info.compiler === "IDO" ? "ido-7.1" : con.default_compiler, con.id);
    return;
  }
  // a project of the community's kind: the compiler versions its build knows (a compressed GameCube or Wii disc: both)
  const shown = p === "gcwii" ? [consoleOf("gc"), consoleOf("wii")].filter(Boolean) : [con];
  select.innerHTML = shown.map((c) => `<optgroup label="${esc(c.name)}">${c.compilers.map((v) => `<option value="${esc(v.id)}" ${v.id === c.default_compiler && (p !== "gcwii" || c.id === "gc") ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</optgroup>`).join("")
    + (con.default_compiler ? "" : `<option value="" selected>Let decompdecomp pick (from the program itself)</option>`);
  const pack = packOf(p);
  note.hidden = false;
  note.textContent = CW_PACK[pack].note;
  $("mwText").textContent = CW_PACK[pack].text;
  $("mwConsent").hidden = !!(S.st?.toolchains || []).find((t) => t.id === pack && t.installed);
}

function updateAnalyse() {
  const needsConsent = !$("mwConsent").hidden;
  $("analyse").disabled = !$("own").checked || (needsConsent && !$("mwAccept").checked);
}
$("mwAccept").addEventListener("change", updateAnalyse);

function fillCompilers(select, current, platform) {
  const own = (consoleOf(platform)?.compilers || []).map((c) => c.id);  // the console's compilers
  const tcs = (S.st?.toolchains || []).filter((t) => t.kind === "compiler" && (!own.length || own.includes(t.id)));
  if (own.length && !tcs.length) {  // versions of a compiler pack (DS's CodeWarriors), not toolchains of their own
    select.innerHTML = consoleOf(platform).compilers.map((c) => `<option value="${esc(c.id)}" ${c.id === current ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    return;
  }
  select.innerHTML = tcs.map((t) => {
    const note = !t.supported ? " (not available on this computer)" : t.installed ? "" : t.license === "proprietary" ? " (install in Compilers & tools)" : " (will be installed)";
    return `<option value="${esc(t.id)}" ${t.id === current ? "selected" : ""} ${t.supported ? "" : "disabled"}>${esc(t.name)} · ${esc(t.use)}${note}</option>`;
  }).join("");
}

async function runImport(query, file, title, name = "") {
  $("dropIdle").hidden = true;
  $("dropPicked").hidden = true;
  $("dropBusy").hidden = false;
  $("busyTitle").textContent = title;
  $("busyStep").textContent = "starting";
  $("jobLog").textContent = "";
  $("impUp").hidden = !(file instanceof Blob);
  const t0 = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    $("busyTime").textContent = s >= 3 ? ` · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "";
  }, 1000);
  try {
    let started;
    if (file instanceof Blob) {
      started = await uploadFile(`/api/import?${query}&name=${encodeURIComponent(name || file.name || "game.bin")}`, file,
        (p) => { $("impUp").firstElementChild.style.width = (p * 100).toFixed(1) + "%"; $("busyTitle").textContent = p < 1 ? `Copying ${title.replace(/^\w+ /, "")} into decompdecomp… ${(p * 100).toFixed(0)}%` : title; });
    } else {
      started = await api(`/api/import?${query}`, { method: "POST", raw: file ?? new ArrayBuffer(0) });
    }
    $("impUp").hidden = true;
    const res = await followJob(started.job, $("jobLog"), (log) => {
      const last = log.filter((l) => l.trim()).pop();
      if (last) $("busyStep").textContent = last.trim().replace(/\s+/g, " ").slice(0, 120);
    });
    loadNav();
    location.hash = `#/p/${res.slug}`;
  } catch (e) {
    importFailed(name || title.replace(/^\w+ |…$/g, ""), e, $("jobLog").textContent);
  } finally { clearInterval(tick); $("busyTime").textContent = ""; }
}

/* A failed import stays on screen with what happened, ready to report: a toast is gone before it's read. */
function importFailed(name, e, logText) {
  const job = e.job || {};
  const log = (job.log ? job.log.join("\n") : logText || "").trim();
  $("dropIdle").hidden = true; $("dropPicked").hidden = true; $("dropBusy").hidden = true;
  $("dropError").hidden = false;
  $("errName").textContent = name || "the game";
  $("errMsg").textContent = e.message;
  $("errLog").textContent = [log, job.trace].filter(Boolean).join("\n\n") || "(nothing was logged)";
  // the log shows here: a link would leave the app's window for the user's browser, which the app doesn't answer
  $("errLogLink").onclick = async () => {
    try {
      const res = await fetch("/api/log");
      $("errLog").textContent = await res.text();
      $("errLog").closest("details").open = true;
      $("errLog").scrollTop = $("errLog").scrollHeight;
    } catch (e) { toast(esc(e.message), "err"); }
  };
  $("errCopy").onclick = async () => {
    if (await copyText(bugReport([`Importing: ${name}`, `Error: ${e.message}`], [log, job.trace].filter(Boolean).join("\n")))) toast("Copied: paste it into a GitHub issue");
    else toast("Couldn't reach the clipboard: select the text under What happened instead", "err");
  };
}
$("errBack").onclick = (e) => { e.stopPropagation(); resetDrop(); };

async function ensureCodeWarrior() {
  if ($("mwConsent").hidden) return;
  const id = packOf(picked?.info.platform);
  $("busyTitle").textContent = `Installing ${CW_PACK[id].title}…`;
  const { job } = await api("/api/toolchain", { method: "POST", body: { id, accept_license: true } });
  await followJob(job, $("jobLog"));
  await loadState();
}

$("analyse").onclick = async () => {
  if (!picked || $("analyse").disabled) return;
  const tc = $("compilerRow").hidden ? "" : $("compilerPick").value;
  const title = `Analysing ${picked.info.title}…`;
  if (!$("mwConsent").hidden) {
    $("dropPicked").hidden = true; $("dropBusy").hidden = false; $("impUp").hidden = true;
    try { await ensureCodeWarrior(); } catch (e) { importFailed(CW_PACK[packOf(picked.info.platform)].title, e, $("jobLog").textContent); return; }
  }
  const body = picked.buf instanceof Blob ? picked.buf : new Blob([picked.buf]);
  runImport(`confirmed=1${tc ? `&toolchain=${encodeURIComponent(tc)}` : ""}`, body, title, picked.name);
};
$("pathPick").onclick = (e) => {
  e.stopPropagation();
  const body = dialog("Use a file on this computer", `<p class="muted">For big disc images: decompdecomp reads the file where it is instead of copying it through the browser.</p>
    <label class="field"><span>Path to the game file</span><input id="ppPath" class="mono" placeholder="/home/me/games/my-game.iso"></label>
    <label class="check"><input type="checkbox" id="ppOwn"><span>This is my own copy of this game, obtained legally. The results stay with me for my own use.</span></label>
    <div class="row"><button class="primary" id="ppGo" type="button" disabled>Start decompiling</button><button class="ghost" data-close type="button">Cancel</button></div>`);
  body.querySelector("#ppOwn").onchange = () => { $("ppGo").disabled = !$("ppOwn").checked; };
  body.querySelector("#ppGo").onclick = () => {
    const path = $("ppPath").value.trim();
    if (!path) return;
    closeDialog();
    runImport(`confirmed=1&path=${encodeURIComponent(path)}`, undefined, "Analysing your game…");
  };
};
$("demoBtn").onclick = () => runImport("demo=1", undefined, "Opening Demo Quest 64…");

/* A "Choose…" button beside a path: the system's own chooser (app/picker.py) fills it in. Typing still works. */
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-pick]");
  if (!b) return;
  e.preventDefault();  // (inside a label: not a click on its input)
  b.disabled = true;
  try {
    const { path } = await api("/api/pick", { method: "POST", body: { kind: b.dataset.pick, title: b.dataset.title || "" } });
    if (path) { $(b.dataset.for).value = path; $(b.dataset.for).dispatchEvent(new Event("input")); }
  } catch (err) { toast(esc(err.message), "err"); } finally { b.disabled = false; }
});

/* Open a saved project (app/saves.py): a save folder, or the git repository it was pushed to, and the user's own copy
   of the game (never in the save). Like an import, it runs in the background and opens the project when it's done. */
function openRestore() {
  const body = dialog("Open a saved project", `<p class="muted">Your progress on a game, saved by decompdecomp (on this computer or another one): the matched C, notes, names and settings. The game itself is never in a save: you add your own copy, and decompdecomp checks it's the same game.</p>
    <label class="field"><span>The save: its folder, or the git address you pushed it to</span><span class="pick-row"><input id="rsSource" class="mono" placeholder="/home/you/saves/my-game, or a git address"><button class="ghost small-btn" type="button" data-pick="folder" data-for="rsSource" data-title="The saved project's folder">Choose…</button></span></label>
    <label class="field"><span>Your copy of the game: its path on this computer</span><span class="pick-row"><input id="rsGame" class="mono" placeholder="/home/you/games/my-game.z64"><button class="ghost small-btn" type="button" data-pick="file" data-for="rsGame" data-title="Your copy of the game">Choose…</button></span></label>
    <label class="check"><input type="checkbox" id="rsOwn"><span>This is my own copy of this game, obtained legally. The results stay with me for my own use.</span></label>
    <div class="row"><button class="primary" id="rsGo" type="button" disabled>Open it</button><button class="ghost" data-close type="button">Cancel</button></div>
    <pre class="joblog tall" id="rsLog" hidden></pre>`, "restore");
  body.querySelector("#rsOwn").onchange = (e) => { $("rsGo").disabled = !e.target.checked; };
  body.querySelector("#rsGo").onclick = async () => {
    const source = $("rsSource").value.trim(), path = $("rsGame").value.trim();
    if (!source || !path) return showJobError($("rsLog"), "Say where the save is and where your copy of the game is.");
    $("rsGo").disabled = true;
    showJobError($("rsLog"), "");
    try {
      const { job } = await api("/api/restore", { method: "POST", body: { source, path, confirmed: true } });
      const r = await followJob(job, $("rsLog"));
      toast(`${plural(r.matched || 0, "matched function")} back${r.lost && r.lost.length ? `; ${r.lost.length} no longer match` : ""}.`, "match");
      closeDialog();
      loadNav();
      location.hash = `#/p/${r.slug}`;
    } catch (e) { if ($("rsLog")) { showJobError($("rsLog"), e.message); $("rsGo").disabled = false; } }
  };
}

