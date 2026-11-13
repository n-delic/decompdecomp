/* The Porting tab (docs/PORTING.md): what still stands between a game and a PC port, as a checklist of steps, each
   with how far along it is and the button that does it. Read-only; asked for only while the tab is open. */
let portEpoch = 0;

function portRow(label, value) {
  const row = document.createElement("li");
  const name = document.createElement("span"); name.className = "muted"; name.textContent = label + ": ";
  const text = document.createElement("span"); text.textContent = value;
  row.append(name, text);
  return row;
}

function renderPortReadiness(report) {
  const code = report.code;
  $("portKit").textContent = report.kit ? `Kit: ${report.kit.name}. ${report.kit.summary}`
    : `${report.console} has no port kit yet; the counts below don't depend on one.`;
  const facts = $("portFacts");
  facts.replaceChildren(
    portRow("Functions", `${code.total.toLocaleString()}, ${code.c.toLocaleString()} in C`),
    portRow("Library code", `${code.replaced} the kit replaces, ${code.native} compiled as it is, `
      + `${code.glue || 0} the port's glue supplies, ${code.library_other} other`),
    portRow("The game's own without C", `${code.game_asm} in assembly, ${code.open} not matched`),
    portRow("Data reached by address", `${report.data.symbols.toLocaleString()} symbols`
      + (report.data.units ? ` (${report.data.units.toLocaleString()} data units already in C)` : "")),
    portRow("Addresses written as numbers", `${report.addresses.literals} in ${report.addresses.functions} functions`),
    portRow("ROM outside code", `${report.rom_data.bytes.toLocaleString()} bytes`
      + (report.rom_data.scanned ? `, ${report.rom_data.unshaped.toLocaleString()} of them not yet recognised by ROM Explorer`
        : ", not yet scanned by ROM Explorer")));
  if (report.graphics.length) facts.append(portRow("Graphics microcode", report.graphics.map((g) =>
    `${g.name}${g.variant ? "." + g.variant : ""} ${g.version} (${g.support})`).join(", ")));
  if (report.audio.library) facts.append(portRow("Audio", report.audio.need));
  const pc = report.pc;
  facts.append(portRow("Compiled for a PC", !pc ? "not yet: Compile for PC lists what a port build refuses"
    : `${(pc.ok + pc.warned).toLocaleString()} of ${pc.total.toLocaleString()} functions compile, ${pc.failed} don't`
      + `, ${pc.warned} cast pointers to 32-bit integers (${new Date(pc.checked_at * 1000).toLocaleString()})`));
  for (const k of pc ? pc.kinds : []) facts.append(portRow(`  ${k.kind}`, `${k.functions}: ${k.about}. `
    + k.examples.join(", ") + (k.functions > k.examples.length ? ", ..." : "")));
  const link = report.link;
  if (link) {
    facts.append(portRow("Linked for a PC", `${link.undefined.toLocaleString()} symbols needed and defined nowhere`
      + (link.provided ? `, ${link.provided} more the kit provides` : "")
      + (link.duplicates.length ? `, ${link.duplicates.length} defined twice` : "")
      + (link.zeroed ? `; ${link.zeroed.toLocaleString()} pieces of zeroed memory defined in port/src` : "")
      + (link.unread ? ` (${link.unread} functions don't compile yet, so what they need isn't known)` : "")));
    for (const g of link.groups) facts.append(portRow(`  ${g.kind}`, `${g.symbols} (needed by ${g.users} functions): `
      + `${g.about}. ` + g.examples.join(", ") + (g.symbols > g.examples.length ? ", ..." : "")));
    if (link.duplicates.length) facts.append(portRow("  defined twice", link.duplicates.slice(0, 8)
      .map((d) => `${d.name} (${d.in.join(", ")})`).join("; ")));
  }
  const assets = report.assets;
  if (assets) facts.append(portRow("Asset map", `${assets.resources} resources, `
    + Object.entries(assets.groups).map(([g, b]) => `${g} ${b.toLocaleString()} bytes`).join(", ")
    + (assets.unknown ? `; ${assets.unknown.toLocaleString()} bytes ROM Explorer can't name yet` : "")
    + (assets.written ? ` (written to ${assets.folder})` : ", not written yet")
    + (assets.extracted && assets.extracted.current
      ? `; extracted: ${assets.extracted.mapped} of ${assets.extracted.total} are the ROM's bytes` : "")));
  renderPortSteps(report);
  const todo = $("portBlockers");
  todo.replaceChildren(...report.blockers.map((b) => { const li = document.createElement("li"); li.textContent = b; return li; }));
  $("portReady").textContent = report.ready ? "Nothing found in the way of a port." : "Next, in order";
}

const pct = (part, whole) => (whole > 0 ? Math.max(0, Math.min(100, Math.round(100 * part / whole))) : null);

/* The steps, from the report: each {title, pct (null: done or not, no share to show), detail, buttons and logs (the
   panel's own elements, moved into the step)}. */
function portSteps(report) {
  const code = report.code, pc = report.pc, link = report.link, assets = report.assets;
  const library = code.replaced + code.native + (code.glue || 0);
  const steps = [
    { title: "The game's code in C", pct: pct(code.c, code.total),
      detail: `${code.c.toLocaleString()} of ${code.total.toLocaleString()} functions`
        + (code.game_asm || code.open ? `; ${code.game_asm} still in assembly, ${code.open} not matched yet` : "") },
    { title: "Library code accounted for", pct: pct(library, library + code.library_other),
      detail: `${code.replaced} the kit replaces, ${code.native} compiled as it is, ${code.glue || 0} from the port's glue`
        + (code.library_other ? `, ${code.library_other} other` : "") },
    { title: "The game's data as C", pct: report.data.units != null && report.data.symbols ? pct(report.data.units, report.data.symbols) : null,
      detail: `${(report.data.units || 0).toLocaleString()} data units in C, for ${report.data.symbols.toLocaleString()} symbols the code reaches`,
      buttons: ["portDrafts"], logs: ["portDraftLog"] },
    { title: "No addresses written as numbers", pct: report.addresses.literals ? pct(code.total - report.addresses.functions, code.total) : 100,
      detail: report.addresses.literals ? `${report.addresses.literals} in ${report.addresses.functions} functions` : "none left" },
    { title: "Compiles for a PC", pct: pc ? pct(pc.ok + pc.warned, pc.total) : 0,
      detail: pc ? `${(pc.ok + pc.warned).toLocaleString()} of ${pc.total.toLocaleString()} functions compile, ${pc.failed} don't`
        : "not tried yet", buttons: ["portCompile", "portRenameCalls"], logs: ["portCompileLog", "portRenameLog"] },
    { title: "Links for a PC", pct: link ? (link.undefined || link.duplicates.length ? null : 100) : 0,
      detail: link ? `${link.undefined.toLocaleString()} symbols needed and defined nowhere`
        + (link.duplicates.length ? `, ${link.duplicates.length} defined twice` : "") : "after Compile for PC" },
    { title: "Assets extracted", pct: assets ? (assets.extracted && assets.extracted.current ? pct(assets.extracted.mapped, assets.extracted.total)
        : assets.written ? 50 : 0) : 0,
      detail: assets ? `${assets.resources} resources mapped` + (assets.extracted && assets.extracted.current
        ? `, ${assets.extracted.mapped} of ${assets.extracted.total} extracted` : assets.written ? ", not extracted yet" : ", map not written yet")
        : "map not written yet", buttons: ["portAssets", "portExtract"], logs: ["portAssetsLog", "portExtractLog"] },
    { title: "Play it", pct: null, play: true,
      detail: "The WebAssembly build of your last save, in the browser: no native port needed",
      buttons: ["portPlay", "portStop"], logs: ["portPlayLog"] },
  ];
  return steps;
}

function renderPortSteps(report) {
  const list = $("portSteps");
  if (!list) return;
  const steps = portSteps(report), parked = $("portParked");
  for (const id of ["portDrafts", "portCompile", "portRenameCalls", "portAssets", "portExtract", "portPlay", "portStop",
    "portDraftLog", "portCompileLog", "portRenameLog", "portAssetsLog", "portExtractLog", "portPlayLog"]) {
    if (parked && $(id)) parked.append($(id));  // back where they wait, then into their step
  }
  const shown = steps.filter((s) => s.pct != null);
  const overall = shown.length ? Math.round(shown.reduce((a, s) => a + s.pct, 0) / shown.length) : 0;
  $("portOverall").textContent = overall + "%";
  $("portOverallBar").style.width = overall + "%";
  list.replaceChildren(...steps.map((step, i) => {
    const row = document.createElement("li");
    const state = step.play ? "play" : step.pct === 100 ? "done" : step.pct > 0 ? "part" : "todo";
    row.className = "port-step " + state;
    const mark = document.createElement("span"); mark.className = "port-mark";
    mark.textContent = state === "done" ? "✓" : state === "play" ? "▶" : String(i + 1);
    const body = document.createElement("div"); body.className = "port-body";
    const head = document.createElement("div"); head.className = "port-step-head";
    const title = document.createElement("b"); title.textContent = step.title;
    const share = document.createElement("span"); share.className = "port-pct";
    share.textContent = step.pct == null ? (step.play ? "" : "to do") : step.pct + "%";
    head.append(title, share);
    const detail = document.createElement("p"); detail.className = "small muted"; detail.textContent = step.detail;
    body.append(head);
    if (step.pct != null) {
      const meter = document.createElement("div"); meter.className = "meter";
      const fill = document.createElement("i"); fill.style.width = step.pct + "%"; meter.append(fill);
      body.append(meter);
    }
    body.append(detail);
    const actions = document.createElement("div"); actions.className = "row port-actions";
    for (const id of step.buttons || []) if ($(id)) actions.append($(id));
    if (actions.children.length) body.append(actions);
    for (const id of step.logs || []) if ($(id)) body.append($(id));
    row.append(mark, body);
    return row;
  }));
}

async function loadPortReadiness() {
  if (!$("portPanel") || S.tab !== "porting" || !S.slug) return;
  const slug = S.slug, epoch = ++portEpoch, log = $("portWork");
  showJobError(log, "");
  const finish = beginWork(log, "Reading the project", [$("portReload")], "Read.");
  try {
    const report = await api("/api/port-readiness?slug=" + encodeURIComponent(slug));
    if (epoch === portEpoch && S.slug === slug) renderPortReadiness(report);
  } catch (error) {
    if (epoch === portEpoch) showJobError(log, error.message);
  } finally { finish(); }
}

async function draftPortData() {
  const slug = S.slug, log = $("portDraftLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Drafting the game's data from its code", [$("portDrafts"), $("portReload")],
                           "Data drafted.");
  try {
    const { job } = await api("/api/data-drafts", { method: "POST", body: { slug } });
    await followJob(job, log, null, () => S.slug === slug);
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

async function compileForPc() {
  const slug = S.slug, log = $("portCompileLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Compiling the game's C for a PC", [$("portCompile"), $("portReload")], "Compiled.");
  try {
    const { job } = await api("/api/pc-compile", { method: "POST", body: { slug } });
    await followJob(job, log, null, () => S.slug === slug);
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

async function writeAssetMap() {
  const slug = S.slug, log = $("portAssetsLog");
  log.hidden = false;
  showJobError(log, "");
  const finish = beginWork(log, "Writing the asset map", [$("portAssets"), $("portReload")], "Written.");
  try {
    const result = await api("/api/asset-map", { method: "POST", body: { slug } });
    log.textContent = `${result.resources} resources (${result.bytes.toLocaleString()} bytes) written to ${result.path}: `
      + result.files.join(", ");
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

async function extractAssets() {
  const slug = S.slug, log = $("portExtractLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Extracting the assets with Torch", [$("portExtract"), $("portAssets"), $("portReload")],
                           "Extracted.");
  try {
    const { job } = await api("/api/asset-extract", { method: "POST", body: { slug } });
    await followJob(job, log, null, () => S.slug === slug);
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

async function fixRenamedCalls() {
  const slug = S.slug, log = $("portRenameLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Rewriting calls by old names", [$("portRenameCalls"), $("portReload")], "Done.");
  try {
    const { job } = await api("/api/rename-calls", { method: "POST", body: { slug } });
    await followJob(job, log, null, () => S.slug === slug);
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

/* Play: build the game for the PC and open it in the browser (port/wasm/play.py). The first build takes minutes,
   later ones only rebuild what changed; a game already running opens again at once. */
async function playGame() {
  const slug = S.slug, log = $("portPlayLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Building the game for the PC", [$("portPlay")], "Playing.");
  try {
    let result = await api("/api/play", { method: "POST", body: { slug } });
    if (result.job) result = await followJob(result.job, log, null, () => S.slug === slug);
    if (result && result.url) {
      const link = document.createElement("a");
      link.href = result.url; link.target = "_blank"; link.rel = "noopener"; link.textContent = result.url;
      log.append("\nThe game runs at ", link, " (opened in your browser).");
      window.open(result.url, "_blank", "noopener");
      $("portStop").hidden = false;
    }
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
}

async function stopGame() {
  const slug = S.slug;
  try { await api("/api/play", { method: "POST", body: { slug, stop: true } }); } catch (error) { showJobError($("portPlayLog"), error.message); }
  $("portStop").hidden = true;
}

if ($("portPanel")) {
  $("portPlay").onclick = playGame;
  $("portStop").onclick = stopGame;
  $("portRenameCalls").onclick = fixRenamedCalls;
  $("portExtract").onclick = extractAssets;
  $("portAssets").onclick = writeAssetMap;
  $("portDrafts").onclick = draftPortData;
  $("portCompile").onclick = compileForPc;
  $("portReload").onclick = loadPortReadiness;
}
