/* The Globals tab: the game's data symbols, searchable by the name they have now or the D_ address name they started
   as (D_800A4DFC → sDirectKey). Read-only, like the Functions list; renames happen through the agents' rename_symbol. */
const G = { slug: null, rows: [], supported: true, reason: "", q: "", filter: "all", sort: "address", dir: 1, limit: 500, request: 0, learned: new Set() };
const GLOBAL_ROWS = 500;
const GLOBAL_COLUMNS = [["address", "Address"], ["name", "Name"], ["original", "Address name"], ["type", "Type"], ["size", "Size", "r"], ["kind", "Kind"], ["preview", "Value"]];

async function loadGlobals(slug) {
  if (!slug) return;
  if (G.slug !== slug) Object.assign(G, { slug, rows: [], q: "", filter: "all", limit: GLOBAL_ROWS });
  const request = ++G.request;
  $("globalsShown").textContent = "Loading globals…";
  try {
    let data = await api(`/api/globals?slug=${encodeURIComponent(slug)}`);
    if (request !== G.request || S.slug !== slug) return;  // another project or a newer answer
    if (data.supported && !G.learned.has(slug)) {
      // types and sizes from the code that already matched (app/global_types.py), once per project and session
      G.learned.add(slug);
      const learned = await api("/api/global-types/learn", { method: "POST", body: { slug } }).catch(() => ({ changed: 0 }));
      if (learned.changed) data = await api(`/api/globals?slug=${encodeURIComponent(slug)}`);
      if (request !== G.request || S.slug !== slug) return;
    }
    G.supported = data.supported; G.reason = data.reason || "";
    G.rows = data.rows.map((r) => Object.fromEntries(data.keys.map((k, i) => [k, r[i]])));
    renderGlobals();
  } catch (error) {
    if (request === G.request && S.slug === slug) $("globalsShown").textContent = `Could not load globals: ${error.message}`;
  }
}

function renderGlobals() {
  if (S.tab !== "globals" || G.slug !== S.slug) return;
  const named = (r) => r.name !== r.original;
  const counts = { all: G.rows.length, named: G.rows.filter(named).length };
  counts.unnamed = counts.all - counts.named;
  $("globalsFilter").innerHTML = [["all", "All"], ["named", "Named"], ["unnamed", "Not named yet"]].map(([k, label]) =>
    `<button type="button" role="radio" aria-checked="${G.filter === k}" class="${G.filter === k ? "on" : ""}" data-gfilter="${k}">${label}<span>${counts[k].toLocaleString()}</span></button>`).join("");
  $("globalsNote").hidden = G.supported;
  $("globalsNote").textContent = G.reason;
  const q = G.q.trim().toLowerCase();
  let rows = G.rows.filter((r) => (G.filter === "all" || (G.filter === "named") === named(r)) &&
    (!q || r.name.toLowerCase().includes(q) || r.original.toLowerCase().includes(q) || hex8(r.address).toLowerCase().includes(q.replace(/^0x/, ""))
      || (r.preview || "").toLowerCase().includes(q) || (r.type || "").toLowerCase().includes(q)));
  rows.sort((a, b) => {
    const x = a[G.sort], y = b[G.sort];
    const missing = (v) => v === "" || v == null;
    if (missing(x) !== missing(y)) return missing(x) ? 1 : -1;  // missing values last, whichever the direction
    return ((typeof x === "string" ? x.toLowerCase() : x) < (typeof y === "string" ? y.toLowerCase() : y) ? -1 :
      (typeof x === "string" ? x.toLowerCase() : x) > (typeof y === "string" ? y.toLowerCase() : y) ? 1 : a.address - b.address) * G.dir;
  });
  $("globalsHead").innerHTML = `<tr>${GLOBAL_COLUMNS.map(([k, label, cls]) =>
    `<th scope="col" class="${cls || ""}" aria-sort="${G.sort === k ? (G.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" class="sort-btn" data-gsort="${k}">${label}${G.sort === k ? (G.dir > 0 ? " ↑" : " ↓") : ""}</button></th>`).join("")}</tr>`;
  $("globalsRows").innerHTML = rows.slice(0, G.limit).map((r) =>
    `<tr class="clickable" tabindex="0" data-gname="${esc(r.name)}"><td class="mono dim">${hex8(r.address)}</td><td class="mono name">${esc(r.name)}</td><td class="mono dim">${named(r) ? esc(r.original) : ""}</td><td class="mono" title="${r.type_source ? `recorded by ${esc(r.type_source)}` : ""}">${r.type ? esc(r.type) : `<span class="dim">–</span>`}</td>
      <td class="r mono">${r.size ? r.size.toLocaleString() : `<span class="dim">–</span>`}</td><td>${esc(r.kind)}</td><td class="mono dim">${esc(r.preview || "")}</td></tr>`).join("")
    || `<tr class="none"><td colspan="7">${G.rows.length ? `No global matches${q ? ` “${esc(G.q)}”` : ""}.` : "No data symbols recorded for this project."}</td></tr>`;
  $("globalsShown").textContent = rows.length > G.limit ? `${G.limit.toLocaleString()} of ${rows.length.toLocaleString()} shown` : plural(rows.length, "global");
  $("globalsMore").hidden = rows.length <= G.limit;
  $("gTabCount").textContent = G.rows.length ? G.rows.length.toLocaleString() : "";
}

$("globalsFilter").addEventListener("click", (e) => { const b = e.target.closest("[data-gfilter]"); if (b) { G.filter = b.dataset.gfilter; G.limit = GLOBAL_ROWS; renderGlobals(); } });
$("globalsSearch").addEventListener("input", () => { G.q = $("globalsSearch").value; G.limit = GLOBAL_ROWS; renderGlobals(); });
$("globalsMore").onclick = () => { G.limit += GLOBAL_ROWS; renderGlobals(); };
$("globalsHead").addEventListener("click", (e) => {
  const b = e.target.closest("[data-gsort]"); if (!b) return;
  if (G.sort === b.dataset.gsort) G.dir = -G.dir; else { G.sort = b.dataset.gsort; G.dir = 1; }
  renderGlobals();
});

/* Click a global: the side drawer lists the functions whose code references it, each opening like a row of the
   Functions list. */
async function openGlobal(name) {
  const slug = S.slug, g = G.rows.find((r) => r.name === name);
  if (!g) return;
  const body = dialog(g.name, `<p class="muted small mono">${hex8(g.address)}${g.name !== g.original ? ` · ${esc(g.original)}` : ""}${g.type ? ` · ${esc(g.type)}` : ""}${g.size ? ` · ${g.size.toLocaleString()} bytes` : ""}</p><form id="globalType" class="row" autocomplete="off"><label for="globalTypeInput">C type</label>
      <input id="globalTypeInput" class="mono" value="${esc(g.type || "")}" placeholder="s32, u8[16], struct Actor *" spellcheck="false" maxlength="80" ${G.supported ? "" : "disabled"}>
      <button class="primary" type="submit">Change type</button></form>
    <p class="muted small">Rewrites every declaration of ${esc(g.name)} (headers and matched code), then rebuilds the matched functions that use it. Nothing is changed if one stops matching.</p>
    <pre id="globalTypeLog" class="log" hidden></pre>
    <div id="globalUsers" class="muted">Finding the functions that use it…</div>`);
  const box = body.querySelector("#globalUsers");
  wireGlobalType(body, g, slug);
  try {
    const data = await api(`/api/global-users?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(name)}`);
    if (!box.isConnected || S.slug !== slug) return;
    box.className = "";
    box.innerHTML = data.users.length
      ? `<h3>${plural(data.users.length, "function")} use it</h3><div class="compiler-table"><table><thead><tr><th>Function</th><th>Address</th><th>Status</th><th class="r">Uses</th></tr></thead><tbody>${data.users.map((u) =>
        `<tr><td><a href="#" class="mono" data-fn="${esc(u.name)}">${esc(u.name)}</a></td><td class="mono dim">${u.address == null ? "" : hex8(u.address)}</td><td>${esc(u.status || "")}</td><td class="r mono" title="${u.inside ? `${u.inside} reach it through a table or struct that starts elsewhere` : ""}">${u.references}${u.inside ? ` <span class="dim">(${u.inside} inside)</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">No function's code references this global.</p>`;
    box.querySelectorAll("[data-fn]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); openFn(a.dataset.fn); }; });
  } catch (error) {
    if (box.isConnected) { box.className = "muted"; box.textContent = `Could not load the functions: ${error.message}`; }
  }
}
$("globalsRows").addEventListener("click", (e) => { const tr = e.target.closest("[data-gname]"); if (tr) openGlobal(tr.dataset.gname); });
$("globalsRows").addEventListener("keydown", (e) => { if (e.key !== "Enter") return; const tr = e.target.closest("[data-gname]"); if (tr) openGlobal(tr.dataset.gname); });

/* Change the type: a job that edits the declarations and checks the functions that use the global. The first click
   disables the form; the log keeps the bar, the progress and any refusal. */
function wireGlobalType(body, g, slug) {
  const form = body.querySelector("#globalType"), input = form.querySelector("input"), button = form.querySelector("button");
  const log = body.querySelector("#globalTypeLog");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const c_type = input.value.trim();
    if (!c_type || c_type === (g.type || "")) { input.focus(); return; }
    log.hidden = false; log.textContent = "";
    showJobError(log, "");
    const finish = beginWork(log, `Changing ${g.name} to ${c_type}`, [input, button], "Type changed.");
    try {
      const { job } = await api("/api/global-types/change", { method: "POST", body: { slug, name: g.name, type: c_type } });
      const result = await followJob(job, log, undefined, () => S.slug === slug);
      if (result) {
        g.type = result.type; g.type_source = "user";
        if (S.slug === slug) { const row = G.rows.find((r) => r.name === g.name); if (row) Object.assign(row, { type: result.type, type_source: "user" }); renderGlobals(); loadGlobals(slug); }
        log.textContent = result.message;
      }
    } catch (error) {
      showJobError(log, error.message);
    } finally { finish(); }
  };
}
