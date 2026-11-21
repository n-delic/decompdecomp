/* The Porting tab (docs/PORTING.md): what still stands between a game and a PC port, as a checklist of steps, each
   with how far along it is and the button that does it. Read-only; asked for only while the tab is open. */
let portEpoch = 0;
let portRuntime = null;  // the runtime chosen in the Play step, kept across refreshes of the same report

/* What a runtime still waits for, in a sentence (port/readiness.py, runtimes). */
function runtimeStatus(r) {
  if (r.playable) return "Ready: plays your last save.";
  const needs = r.missing.length ? `Needs: ${r.missing.join(", ")}.` : "";
  if (r.built) return needs;
  return r.missing.length ? `${needs} Then decompdecomp builds and plays it on ${r.name} (planned, ROADMAP.md).`
    : `The steps it needs are done; building and playing on ${r.name} isn't in decompdecomp yet (planned, ROADMAP.md).`;
}

/* The Play step's choice of runtime: one radio each, only a playable one can be chosen. */
function runtimeChoice(runtimes) {
  if (!runtimes.some((r) => r.id === portRuntime && r.playable)) portRuntime = (runtimes.find((r) => r.playable) || {}).id || null;
  const set = document.createElement("fieldset"); set.className = "port-runtimes";
  const legend = document.createElement("legend"); legend.className = "small muted"; legend.textContent = "Run it on";
  set.append(legend);
  for (const r of runtimes) {
    const label = document.createElement("label"); label.className = "port-runtime" + (r.playable ? "" : " unavailable");
    const radio = document.createElement("input");
    radio.type = "radio"; radio.name = "portRuntime"; radio.value = r.id;
    radio.disabled = !r.playable; radio.checked = r.id === portRuntime;
    radio.addEventListener("change", () => { if (radio.checked) portRuntime = r.id; });
    const text = document.createElement("span");
    const name = document.createElement("b"); name.textContent = r.name;
    const summary = document.createElement("span"); summary.className = "small muted"; summary.textContent = r.summary;
    const status = document.createElement("span"); status.className = "small port-runtime-status"; status.textContent = runtimeStatus(r);
    text.append(name, summary, status);
    label.append(radio, text);
    set.append(label);
  }
  return set;
}

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
    portRow("The game's own without C", `${code.game_asm} in assembly, ${code.open} not matched`
      + (code.unread ? `, ${code.unread} matched without readable C` : "")),
    portRow("Data reached by address", report.data.checked === false ? report.limitations.data : `${report.data.symbols.toLocaleString()} symbols`
      + (report.data.units ? ` (${report.data.units.toLocaleString()} data units already in C)` : "")),
    portRow("Addresses written as numbers", `${report.addresses.literals} in ${report.addresses.functions} functions`),
    portRow("ROM outside code", report.rom_data.available === false ? "not measured for this source project" : `${report.rom_data.bytes.toLocaleString()} bytes`
      + (report.rom_data.scanned ? `, ${report.rom_data.unshaped.toLocaleString()} of them not yet recognised by ROM Explorer`
        : ", not yet scanned by ROM Explorer")));
  if (report.source) facts.append(portRow("Project source", `${report.source.files} unit files read`
    + (report.source.inferred ? `, ${report.source.inferred} paths inferred from unit names` : "")
    + "; function bodies inspected without recompiling"));
  if (report.graphics.length) facts.append(portRow("Graphics microcode", report.graphics.map((g) =>
    `${g.name}${g.variant ? "." + g.variant : ""} ${g.version} (${g.support})`).join(", ")));
  if (report.audio.library) facts.append(portRow("Audio", report.audio.need));
  const pc = report.pc;
  facts.append(portRow("Compiled for a PC", report.limitations?.compiles || (!pc ? "not yet: Compile for PC lists what a port build refuses"
    : `${(pc.ok + pc.warned).toLocaleString()} of ${pc.total.toLocaleString()} functions compile, ${pc.failed} don't`
      + `, ${pc.warned} cast pointers to 32-bit integers (${new Date(pc.checked_at * 1000).toLocaleString()})`)));
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
  const calls = report.calls;
  if (calls && calls.calls) {
    facts.append(portRow("Matched but suspicious", `${calls.calls.toLocaleString()} calls in `
      + `${calls.functions.toLocaleString()} functions disagree with the function they call (last WebAssembly build)`));
    for (const k of calls.kinds) facts.append(portRow(`  ${k.about}`, `${k.calls} calls in ${k.functions} functions: `
      + `${k.why}. ` + k.examples.join(", ") + (k.calls > k.examples.length ? ", ..." : "")));
  }
  const behaviour = report.behaviour;
  if (behaviour) {
    facts.append(portRow("Behaves like the original", `${behaviour.same.toLocaleString()} the same`
      + (behaviour.return_addresses ? ` (${behaviour.return_addresses} storing a return address C doesn't have)` : "")
      + ", "
      + `${behaviour.differs} differ, ${behaviour.not_reached.toLocaleString()} not reached, ${behaviour.errors} couldn't `
      + `run (each matched function's first call in ${behaviour.settings.vis} VIs; `
      + `${new Date(behaviour.at * 1000).toLocaleString()})`));
    if (behaviour.examples.length) facts.append(portRow("  differ", behaviour.examples.join("; ")
      + (behaviour.differs > behaviour.examples.length ? "; ..." : "")));
  }
  const recomp = report.recomp;
  if (recomp) {
    facts.append(portRow("Recompiled (N64Recomp)", `${recomp.functions.toLocaleString()} of the split's functions and `
      + `${recomp.found} it doesn't list, as C in ${recomp.files} files: ${recomp.stubs} set aside as stubs, `
      + `${recomp.runtime} for the runtime to provide, `
      + (recomp.errors ? `${recomp.errors} compile errors` : "the C compiles")
      + ` (${new Date(recomp.at * 1000).toLocaleString()})`));
    if (recomp.stub_examples.length) facts.append(portRow("  set aside", recomp.stub_examples.join(", ")
      + (recomp.stubs > recomp.stub_examples.length ? ", ..." : "")));
    if (recomp.found_examples.length) facts.append(portRow("  not in the split", recomp.found_examples.join(", ")
      + (recomp.found > recomp.found_examples.length ? ", ..." : "")));
  }
  const assets = report.assets;
  if (assets) facts.append(portRow("Asset map", `${assets.resources} resources, `
    + Object.entries(assets.groups).map(([g, b]) => `${g} ${b.toLocaleString()} bytes`).join(", ")
    + (assets.unknown ? `; ${assets.unknown.toLocaleString()} bytes ROM Explorer can't name yet` : "")
    + (assets.written ? ` (written to ${assets.folder})` : ", not written yet")
    + (assets.extracted && assets.extracted.current
      ? `; extracted: ${assets.extracted.mapped} of ${assets.extracted.total} are the ROM's bytes` : "")));
  renderPortSteps(report);
  attachPlay(report.play);
  const todo = $("portBlockers");
  todo.replaceChildren(...report.blockers.map((b) => { const li = document.createElement("li"); li.textContent = b; return li; }));
  $("portReady").textContent = report.ready ? "Nothing found in the way of a port." : "Next, in order";
}

/* A log with what beginWork and showJobError put before it (its status line, progress bar and error), moved
   together: a refresh that moved the log alone left a running build's bar in the step list it replaced. The log
   moves in its frame (base.js, frameJoblog): moving the pre alone left its Copy button behind in an empty frame. */
const withWork = (log) => [log._workStatus, log._workBar, log._errorBox, log._frame || log].filter(Boolean);

const pct = (part, whole) => (whole > 0 ? Math.max(0, Math.min(100, Math.round(100 * part / whole))) : null);

/* The steps, from the report: each {title, pct (null: done or not, no share to show), detail, buttons and logs (the
   panel's own elements, moved into the step)}. */
function portSteps(report) {
  const code = report.code, pc = report.pc, link = report.link, assets = report.assets, calls = report.calls;
  const behaviour = report.behaviour, compared = behaviour ? behaviour.same + behaviour.differs : 0;
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
    { title: "Calls agree with what they call", pct: calls ? pct(code.total - calls.functions, code.total) : null,
      detail: calls ? (calls.calls ? `${calls.calls.toLocaleString()} calls in ${calls.functions.toLocaleString()} `
        + "functions match but disagree with the function they call: a port can behave differently there"
        : "every call agrees with the function it calls") : "after Play builds the game",
      buttons: calls && calls.calls ? ["portFixCalls"] : [], logs: ["portFixCallsLog"] },
    { title: "Behaves like the original", pct: compared ? pct(behaviour.same, compared) : null,
      detail: !report.behaviour_available ? "needs a reversing emulator and a static recompiler for this console"
        : !behaviour ? "not checked yet: Check behaviour runs each matched function's first call both ways"
        : `${behaviour.same.toLocaleString()} of ${compared.toLocaleString()} functions the game reached behave the same`
          + (behaviour.differs ? `, ${behaviour.differs} differ` : "")
          + `; ${behaviour.not_reached.toLocaleString()} not reached in ${behaviour.settings.vis} VIs`,
      buttons: report.behaviour_available ? ["portBehaviour", "portBehaviourStop", "portBehaviourSettings"] : [],
      logs: ["portBehaviourLog"] },
    { title: "Assets extracted", pct: assets ? (assets.extracted && assets.extracted.current ? pct(assets.extracted.mapped, assets.extracted.total)
        : assets.written ? 50 : 0) : 0,
      detail: assets ? `${assets.resources} resources mapped` + (assets.extracted && assets.extracted.current
        ? `, ${assets.extracted.mapped} of ${assets.extracted.total} extracted` : assets.written ? ", not extracted yet" : ", map not written yet")
        : "map not written yet", buttons: ["portAssets", "portExtract"], logs: ["portAssetsLog", "portExtractLog"] },
    { title: "Play it", pct: null, play: true, runtimes: report.runtimes || [],
      detail: (report.runtimes || []).length > 1
        ? "Your last save, played on the runtime you choose: WebAssembly now, the native runtimes once the steps they need are done"
        : "The WebAssembly build of your last save, in the browser: no native port needed",
      buttons: ["portPlay", "portStop"],
      logs: ["portPlayLog"] },
  ];
  if (report.mode === "project") {
    const limits = report.limitations;
    if (limits.library) steps[1].detail += ` ${limits.library}`;
    steps[0].detail += code.unread ? `; ${code.unread} matched without readable C/C++` : "";
    for (const [index, key] of [[2, "data"], [4, "compiles"], [5, "links"], [8, "assets"], [9, "play"]]) {
      steps[index].pct = null;
      steps[index].detail = limits[key];
      steps[index].buttons = [];
      steps[index].logs = [];
      steps[index].unavailable = true;
    }
    if (!code.total) steps[0].detail = "Finish setup and choose Check again to measure the project's functions.";
    steps[3].detail = `${report.addresses.literals} in ${report.addresses.functions} inspected functions. ${limits.addresses}`;
    if (code.unread || !code.total || report.addresses.checked === false) steps[3].pct = null;
    steps[9].runtimes = report.runtimes || [];
    steps[6].detail = "Call checks need a project port build.";
  }
  return steps;
}

function renderPortSteps(report) {
  const list = $("portSteps");
  if (!list) return;
  const steps = portSteps(report), parked = $("portParked");
  for (const id of ["portDrafts", "portCompile", "portRenameCalls", "portFixCalls", "portAssets", "portExtract", "portPlay", "portStop",
    "portBehaviour", "portBehaviourStop", "portBehaviourSettings"]) {
    if (parked && $(id)) parked.append($(id));  // back where they wait, then into their step
  }
  for (const id of ["portDraftLog", "portCompileLog", "portRenameLog", "portFixCallsLog", "portAssetsLog", "portExtractLog", "portPlayLog",
    "portBehaviourLog"]) {
    if (parked && $(id)) parked.append(...withWork($(id)));
  }
  const shown = steps.filter((s) => s.pct != null);
  const totalSteps = report.mode === "project" ? steps.length : shown.length;
  const overall = totalSteps ? Math.round(shown.reduce((a, s) => a + s.pct, 0) / totalSteps) : 0;
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
    share.textContent = step.unavailable ? "not checked" : step.pct == null ? (step.play ? "" : "to do") : step.pct + "%";
    head.append(title, share);
    const detail = document.createElement("p"); detail.className = "small muted"; detail.textContent = step.detail;
    body.append(head);
    if (step.pct != null) {
      const meter = document.createElement("div"); meter.className = "meter";
      const fill = document.createElement("i"); fill.style.width = step.pct + "%"; meter.append(fill);
      body.append(meter);
    }
    body.append(detail);
    if (step.runtimes && step.runtimes.length) body.append(runtimeChoice(step.runtimes));
    const actions = document.createElement("div"); actions.className = "row port-actions";
    for (const id of step.buttons || []) if ($(id)) actions.append($(id));
    if (actions.children.length) body.append(actions);
    for (const id of step.logs || []) if ($(id)) body.append(...withWork($(id)));
    row.append(mark, body);
    return row;
  }));
}

async function loadPortReadiness() {
  if (!$("portPanel") || S.tab !== "porting" || !S.slug) return;
  const slug = S.slug, epoch = ++portEpoch, log = $("portWork");
  showJobError(log, "");
  const exportOptions = loadPortExportOptions(slug);
  const finish = beginWork(log, "Reading the project", [$("portReload")], "Read.");
  try {
    const report = await api("/api/port-readiness?slug=" + encodeURIComponent(slug));
    if (epoch === portEpoch && S.slug === slug) renderPortReadiness(report);
  } catch (error) {
    if (epoch === portEpoch) showJobError(log, error.message);
  } finally { finish(); }
  await exportOptions;
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

/* Check behaviour (app/behaviour.py): every matched function's first call through its recompiled and decompiled C.
   Long: its bar counts functions; Stop keeps what was checked; a check running when the panel is drawn again is
   followed, not started again (report.play.behaviour_job). */
let behaviourJob = null;

async function checkBehaviour(existing = null) {
  if (behaviourJob) return;
  const slug = S.slug, log = $("portBehaviourLog"), stop = $("portBehaviourStop");
  behaviourJob = existing ? existing.id : "starting";
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  stop.hidden = false; stop.disabled = false; stop.textContent = "Stop checking";
  const finish = beginWork(log, "Checking each matched function against the original", [$("portBehaviour")],
                           "Checked.");
  if (existing?.progress) updateWorkProgress(log, existing.progress);
  try {
    const vis = Number($("portBehaviourVis").value) || 3600, input = $("portBehaviourInput").value || "";
    let result = existing ? { job: existing.id }
      : await api("/api/behaviour-check", { method: "POST", body: { slug, vis, input } });
    if (result.job) { behaviourJob = result.job; await followJob(result.job, log, null, () => S.slug === slug); }
  } catch (error) {
    showJobError(log, error.message);
  } finally {
    behaviourJob = null; finish();
    stop.hidden = true;
  }
  if (S.slug === slug) loadPortReadiness();
}

async function stopBehaviour() {
  const stop = $("portBehaviourStop");
  if (!behaviourJob || behaviourJob === "starting") return;
  stop.disabled = true; stop.textContent = "Stopping…";
  try { await api("/api/job/cancel", { method: "POST", body: { id: behaviourJob } }); }
  catch (error) { showJobError($("portBehaviourLog"), error.message); stop.disabled = false; stop.textContent = "Stop checking"; }
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

async function fixDisagreeingCalls() {
  const slug = S.slug, log = $("portFixCallsLog");
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Making functions agree with their callers", [$("portFixCalls"), $("portReload")], "Done.");
  try {
    const { job } = await api("/api/fix-calls", { method: "POST", body: { slug } });
    await followJob(job, log, null, () => S.slug === slug);
  } catch (error) {
    showJobError(log, error.message);
  } finally { finish(); }
  if (S.slug === slug) loadPortReadiness();
}

/* Play: build the game for the PC and open it in the browser (port/wasm/play.py). The first build takes minutes,
   later ones only rebuild what changed; a game already running opens again at once. The bar counts the build's
   steps and each step's files; Stop cancels the build, then stops the game. A build still running when the panel
   is drawn again (another project, another window) is followed, not started again (report.play.job). */
let playJob = null;

function playStopLabel() {
  $("portStop").textContent = playJob ? "Stop the build" : "Stop the game";
}

async function playGame(existing = null) {
  if (playJob) return;
  const slug = S.slug, log = $("portPlayLog");
  playJob = existing ? existing.id : "starting";
  log.hidden = false; log._showJobProgress = true;
  showJobError(log, "");
  const finish = beginWork(log, "Building the game for the PC", [$("portPlay")], "Playing.");
  if (existing?.progress) updateWorkProgress(log, existing.progress);
  $("portStop").hidden = false; $("portStop").disabled = false; playStopLabel();
  let playing = false;
  try {
    let result = existing ? { job: existing.id }
      : await api("/api/play", { method: "POST", body: { slug, runtime: portRuntime || "wasm" } });
    if (result.job) { playJob = result.job; result = await followJob(result.job, log, null, () => S.slug === slug); }
    if (result && result.url) {
      const link = document.createElement("a");
      link.href = result.url; link.target = "_blank"; link.rel = "noopener"; link.textContent = result.url;
      if (result.window) log.append("\nThe game opened in its own window; closing it stops the game. Or play in the browser at ", link, ".");
      else { log.append("\nThe game runs at ", link, " (opened in your browser)."); window.open(result.url, "_blank", "noopener"); }
      playing = true;
    }
  } catch (error) {
    showJobError(log, error.message);
  } finally {
    playJob = null; finish();
    $("portStop").hidden = !playing; $("portStop").disabled = false; playStopLabel();
  }
}

async function stopGame() {
  const slug = S.slug, stop = $("portStop");
  stop.disabled = true;
  if (playJob) {
    stop.textContent = "Stopping the build…";
    // the build ends at its next step or file, and playGame's finally gives the buttons back
    try { if (playJob !== "starting") await api("/api/job/cancel", { method: "POST", body: { id: playJob } }); }
    catch (error) { showJobError($("portPlayLog"), error.message); stop.disabled = false; playStopLabel(); }
    return;
  }
  stop.textContent = "Stopping the game…";
  try {
    await api("/api/play", { method: "POST", body: { slug, stop: true } });
    stop.hidden = true;
  } catch (error) { showJobError($("portPlayLog"), error.message); }
  finally { stop.disabled = false; playStopLabel(); }
}

/* Export options belong to the project. The server owns the batch and continues it across navigation. */
const EXPORT_CHOICES = ["App", "Window", "Web", "WebSimd", "Ios", "Miyoo", "SteamDeck"];
const EXPORT_LABELS = { app: "Standalone app (browser)", window: "Standalone app (Mac window via SDL3)",
  web: "Website", ios: "iPhone / iPad", miyoo: "Miyoo Mini", steamdeck: "Steam Deck" };
let exportState = null;

function exportChoices() {
  return EXPORT_CHOICES.map((suffix) => $(`portExport${suffix}`));
}

function currentExport(state) {
  return state === exportState && S.slug === state.slug;
}

function renderExportControls(state) {
  const busy = !!state.job || !state.ready;
  $("portExportWindowChoice").hidden = !state.mac;
  for (const input of exportChoices()) {
    input.checked = state.targets.includes(input.value);
    input.disabled = busy || (input.value === "window" && !state.mac)
      || (input.value === "simd" && !state.targets.includes("web"));
  }
  $("portExportStart").disabled = busy || state.saving || !exportChoices().some((input) => input.checked && !input.disabled);
}

async function loadPortExportOptions(slug) {
  const log = $("portExportLog"), panel = $("portExportPanel");
  if (exportState?.slug !== slug) {
    exportState?.finish?.();
    exportState = {slug, targets: [], ready: false, revision: 0, read: 0};
    panel.hidden = true;
    log.hidden = true;
    log.textContent = "";
    showJobError(log, "");
    if (log._workStatus) log._workStatus.textContent = "";
    $("portExportStatus").textContent = "";
    $("portExportStop").hidden = true;
    $("portExportNext").hidden = true;
    $("portExportNext").replaceChildren();
    renderExportControls(exportState);
  }
  const state = exportState, revision = state.revision, read = ++state.read;
  try {
    const options = await api("/api/port-export-options?slug=" + encodeURIComponent(slug));
    if (!currentExport(state) || state.revision !== revision || state.read !== read || state.saving) return;
    if (state.loadError) $("portExportStatus").textContent = "";
    state.loadError = false;
    state.targets = options.targets || [];
    state.ready = true;
    state.mac = options.mac;
    panel.hidden = !options.available;
    renderExportControls(state);
    if (options.job && !state.job) exportGame(options.job);
  } catch (error) {
    if (!currentExport(state) || state.revision !== revision || state.read !== read) return;
    state.loadError = true;
    $("portExportStatus").textContent = `Could not load export options. Refresh to retry. ${error.message}`;
    panel.hidden = false;
  }
}

async function saveExportTargets() {
  const state = exportState;
  if (!state || !currentExport(state) || state.job || !state.ready) return;
  let saved = state.targets;
  const status = $("portExportStatus");
  state.targets = exportChoices().filter((input) => input.checked).map((input) => input.value)
    .filter((value, _, targets) => value !== "simd" || targets.includes("web"));
  state.revision++;
  if (state.saving) { renderExportControls(state); return; }
  state.saving = true;
  status.textContent = "Saving export selections…";
  renderExportControls(state);
  try {
    // Keep changes in click order even if the user ticks another target before the first save returns.
    while (saved !== state.targets) {
      const pending = state.targets;
      const {targets} = await api("/api/port-export-options", {
        method: "POST", body: {slug: state.slug, targets: pending},
      });
      saved = targets;
      if (state.targets === pending) state.targets = targets;
    }
    if (currentExport(state)) status.textContent = "Export selections saved.";
  } catch (error) {
    state.targets = saved;
    if (currentExport(state)) status.textContent = `Could not save export selections. Try again. ${error.message}`;
  } finally {
    state.saving = false;
    state.revision++;
    if (currentExport(state)) renderExportControls(state);
  }
}

function showExportResults(state, results) {
  const box = $("portExportNext"), log = $("portExportLog");
  for (const result of results) {
    const label = EXPORT_LABELS[result.kind];
    log.append(`\nExported ${label} to ${result.path}.`);
    const entry = document.createElement("section");
    const head = document.createElement("b"); head.textContent = label;
    const show = document.createElement("button");
    show.className = "ghost small-btn"; show.type = "button"; show.textContent = "Show folder";
    show.setAttribute("aria-label", `Show folder for ${label}`);
    show.onclick = () => showExport(state, result.kind, show);
    const actions = document.createElement("div"); actions.className = "row port-actions";
    actions.append(head, show);
    entry.append(actions);
    if (result.instructions?.length) {
      const list = document.createElement("ol");
      for (const step of result.instructions) {
        const li = document.createElement("li"); li.textContent = step; list.append(li);
      }
      entry.append(list);
    }
    box.append(entry);
  }
  box.hidden = !results.length;
}

async function exportGame(existing = null) {
  const state = exportState, start = $("portExportStart");
  if (!state || !currentExport(state) || state.job || (!existing && start.disabled)) return;
  const log = $("portExportLog"), stop = $("portExportStop"), next = $("portExportNext");
  const targets = exportChoices().filter((input) => input.checked && !input.disabled).map((input) => input.value);
  const active = () => currentExport(state);
  state.revision++;
  state.job = existing?.id || "starting";
  renderExportControls(state);
  log.hidden = false; log.textContent = ""; log._showJobProgress = true;
  showJobError(log, "");
  next.replaceChildren(); next.hidden = true;
  stop.hidden = false; stop.disabled = !existing; stop.textContent = "Stop the export";
  state.finish = beginWork(log, "Exporting selected targets", [], "Exported selected targets.");
  if (existing?.progress) updateWorkProgress(log, existing.progress);
  try {
    if (!existing) {
      const result = await api("/api/play-export", {method: "POST", body: {slug: state.slug, targets}});
      if (!active()) return;
      state.job = result.job;
      stop.disabled = false;
    }
    const result = await followJob(state.job, log, null, active);
    if (!active() || !result) return;
    showExportResults(state, result.exports || (result.path ? [result] : []));
    if (result.failures?.length) showJobError(log, result.failures.join("\n"));
  } catch (error) {
    if (active()) showJobError(log, error.message);
  } finally {
    state.job = null;
    state.revision++;
    if (state === exportState) {
      state.finish();
      state.finish = null;
      stop.hidden = true;
      renderExportControls(state);
    }
  }
}

async function stopExport() {
  const state = exportState, stop = $("portExportStop");
  if (!state?.job || stop.disabled || !currentExport(state)) return;
  stop.disabled = true;
  stop.textContent = "Stopping the export…";
  try { await api("/api/job/cancel", {method: "POST", body: {id: state.job}}); }
  catch (error) {
    if (!currentExport(state) || !state.job) return;
    showJobError($("portExportLog"), error.message);
    stop.disabled = false; stop.textContent = "Stop the export";
  }
}

async function showExport(state, kind, button) {
  if (!currentExport(state)) return;
  button.disabled = true;
  try {
    const result = await api("/api/play-export", {method: "POST", body: {slug: state.slug, kind, show: true}});
    if (currentExport(state) && result?.ok === false) showJobError($("portExportLog"), result.message);
  } catch (error) {
    if (currentExport(state)) showJobError($("portExportLog"), error.message);
  } finally { button.disabled = false; }
}

/* Reattach to the project's running Play or behaviour job from the readiness report. */
function attachPlay(play) {
  if (!play) return;
  if (play.behaviour_job && !behaviourJob) checkBehaviour(play.behaviour_job);
  if (playJob) return;
  if (play.job) { playGame(play.job); return; }
  $("portStop").hidden = !play.url; playStopLabel();
}

if ($("portPanel")) {
  $("portPlay").onclick = () => playGame();
  $("portStop").onclick = stopGame;
  for (const input of exportChoices()) input.onchange = saveExportTargets;
  $("portExportStart").onclick = () => exportGame();
  $("portExportStop").onclick = stopExport;
  $("portRenameCalls").onclick = fixRenamedCalls;
  $("portFixCalls").onclick = fixDisagreeingCalls;
  $("portExtract").onclick = extractAssets;
  if ($("portBehaviour")) $("portBehaviour").onclick = () => checkBehaviour();
  if ($("portBehaviourStop")) $("portBehaviourStop").onclick = stopBehaviour;
  $("portAssets").onclick = writeAssetMap;
  $("portDrafts").onclick = draftPortData;
  $("portCompile").onclick = compileForPc;
  $("portReload").onclick = loadPortReadiness;
}
