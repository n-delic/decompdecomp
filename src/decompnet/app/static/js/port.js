/* The port readiness panel (docs/PORTING.md): what still stands between a game and a native PC port. Read-only;
   asked for only while the panel is open, never on its own. */
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
    portRow("Library code", `${code.replaced} the kit replaces, ${code.native} compiled as it is, ${code.library_other} other`),
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
  const todo = $("portBlockers");
  todo.replaceChildren(...report.blockers.map((b) => { const li = document.createElement("li"); li.textContent = b; return li; }));
  $("portReady").textContent = report.ready ? "Nothing found in the way of a port." : "To do, in order:";
}

async function loadPortReadiness() {
  const panel = $("portPanel");
  if (!panel || !panel.open || !S.slug) return;
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

if ($("portPanel")) {
  $("portDrafts").onclick = draftPortData;
  $("portCompile").onclick = compileForPc;
  $("portPanel").addEventListener("toggle", loadPortReadiness);
  $("portReload").onclick = loadPortReadiness;
}
