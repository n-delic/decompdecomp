/* Settings › Compiler services: how to make this computer (or another) verify a hosted project's submissions, with
   the commands filled in for this computer (app/compiler_service.py, docs/HOSTED.md). Tokens are never asked for or
   kept here: the commands read them from the environment. */

const shellWord = (text) => /^[A-Za-z0-9_./:@%+=,-]+$/.test(text) ? text : `"${String(text).replaceAll('"', '\\"')}"`;

async function loadCompilerService() {
  const info = await api("/api/compiler-service");
  const box = $("compilerService");
  const compilers = info.compilers.length;
  $("compilerServiceContext").textContent = compilers
    ? `— This computer has ${compilers} compiler${compilers === 1 ? "" : "s"}`
    : "— No compilers installed here yet";
  box.innerHTML = `
    <p>A hosted project lets agents on other people's computers work on a game. They compile on their own machines and
      submit their C. A match only counts once a <b>compiler service</b> has compiled the same C and got the same bytes.
      A compiler service is a computer you trust that has the game's compilers. This computer can be one.</p>
    <p class="small muted">A compiler service doesn't need the game. For each project it may verify, it downloads a
      small verification pack (the game's code, data and symbols, not its assets). Agents never get packs. It judges
      with this app's own judge, so a match there is a match here.</p>
    <div class="field"><span>This computer</span>
      <p class="small">${compilers ? `Compilers installed: ${esc(info.compilers.join(", "))}. It will only take submissions built with these.`
        : "No compilers are installed yet. Install the ones your games use under Setup, or with <code>decomp setup</code>."}</p>
      <p class="small ${info.gateway ? "muted" : ""}">${info.gateway ? `Gateway program: <code>${esc(info.gateway)}</code>`
        : "The gateway program (decomp-mcp-gateway) isn't on this computer. Packaged releases include it; in a checkout, build it with <code>cd gateway &amp;&amp; cargo build --release</code>."}</p>
    </div>
    <div class="cs-fields">
      <label class="field"><span>Hosted server's address</span><input id="csServer" class="mono" placeholder="https://decomp.example"></label>
      <label class="field"><span>Service name (as the server lists it)</span><input id="csName" class="mono" value="${esc(info.name)}"></label>
      <label class="field"><span>Compiles at once (${info.cores} cores)</span><input id="csSlots" type="number" min="1" value="${info.slots}"></label>
      <label class="field"><span>Folder for verification packs</span><input id="csPacks" class="mono" value="${esc(info.packs)}"></label>
    </div>
    <div id="csSteps"></div>
    <h3>Taking matches into this app</h3>
    <div id="csLinks"></div>
    <p class="small"><a class="link" href="${esc(info.docs)}" target="_blank" rel="noopener">How hosted projects work</a></p>`;
  const render = () => {
    const server = ($("csServer").value.trim() || "https://decomp.example").replace(/\/+$/, "");
    const name = $("csName").value.trim() || "compiler-service";
    const slots = Math.max(1, parseInt($("csSlots").value, 10) || 1);
    const packs = $("csPacks").value.trim() || info.packs;
    const gateway = shellWord(info.gateway || "decomp-mcp-gateway");
    const decomp = shellWord(info.decomp);
    const account = JSON.stringify({ name, role: "verifier" });
    $("csSteps").innerHTML = `
      <ol class="steps">
        <li><b>On the server, create the service's account.</b> It prints a token once: keep it private.
          ${cmdBox(`decomp-mcp-gateway hosted add-user --dir /srv/decomp --name ${shellWord(name)} --role verifier`)}
          <p class="small muted">Or, with an admin's token, from here:</p>
          ${cmdBox(`curl -X POST ${server}/v1/users -H "Authorization: Bearer $DECOMP_ADMIN_TOKEN" -H "Content-Type: application/json" -d '${account}'`)}
          <p class="small muted">On an invite-only server, also give it the projects it may verify:
            <code>decomp-mcp-gateway hosted grant --dir /srv/decomp --name ${esc(shellWord(name))} --project SLUG</code>.</p></li>
        <li><b>Here, put the token in your terminal</b> (it stays out of this app and its files):
          ${cmdBox("export DECOMP_VERIFIER_TOKEN=dd_...")}
          <p class="small muted">In Windows PowerShell: <code>$env:DECOMP_VERIFIER_TOKEN = "dd_..."</code></p></li>
        <li><b>Start the service.</b>
          ${cmdBox(`${gateway} verifier --server ${server} --name ${shellWord(name)} --slots ${slots} --packs ${shellWord(packs)} --judge ${decomp} --judge-arg judge-worker`)}
          <p class="small muted">It registers the projects and compilers it can judge, and downloads their packs (and newer
            ones when the owner publishes). Then it compiles submissions as they come, up to ${slots} at once, and keeps
            going until you stop it. Two services never judge the same submission. Add <code>--idle-exit 60</code> to stop
            when there's nothing to do, for a scheduled job.</p></li>
        <li><b>Check it's registered</b> (with an admin's token):
          ${cmdBox(`curl ${server}/v1/verifiers -H "Authorization: Bearer $DECOMP_ADMIN_TOKEN"`)}</li>
      </ol>`;
    $("csLinks").innerHTML = (info.links.length ? `
      <p class="small">These projects take in matches verified on a hosted server, every minute while this app runs.
        Each is compiled here again before it's recorded.</p>
      <table class="small"><thead><tr><th>Project</th><th>Server</th><th>Last check</th></tr></thead><tbody>
        ${info.links.map((l) => `<tr><td>${esc(l.slug)}</td><td class="mono">${esc(l.server)}/p/${esc(l.remote)}</td><td>${
          !l.last ? "not yet" : l.last.error ? `<span class="err-msg">${esc(l.last.error)}</span>`
          : `${l.last.recorded} new, ${l.last.not_reproduced} not reproduced here`}</td></tr>`).join("")}
      </tbody></table>` : `<p class="small">No project here takes matches from a hosted server yet.</p>`) + `
      <p class="small muted">To publish a project to a server, then take its verified matches back into it (an admin's
        token to publish, a verifier's to take matches in):</p>
      ${cmdBox(`${decomp} hosted-publish SLUG --server ${server} --token $DECOMP_HOSTED_TOKEN`)}
      ${cmdBox(`${decomp} hosted-link SLUG --server ${server} --token $DECOMP_VERIFIER_TOKEN`)}`;
    wireCopies(box);
  };
  ["csServer", "csName", "csSlots", "csPacks"].forEach((id) => { $(id).oninput = render; });
  render();
}
