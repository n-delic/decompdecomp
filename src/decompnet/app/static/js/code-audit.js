/* Saved human code-boundary reviews; a classification never adopts or pauses a function. */
let codeAuditRequest=0;
function renderCodeAudit(result) {
  codeAuditRequest++;
  const panel=$("codeAuditResult"), slug=S.slug;
  if (!result.available && !result.candidates?.length) {panel.replaceChildren();if(result.stale) panel.textContent="The saved audit predates a project layout or ROM change. Run the audit again.";return;}
  const boundaries=result.boundary_candidates || [];
  const boundaryHtml=boundaries.length ? `<h4>Incomplete detected entries (${boundaries.length})</h4>${boundaries.map(r=>`<p><button class="link-btn" data-audit-source="${esc(r.function)}">${esc(r.function)}</button> · ${r.size} bytes · continuation at ROM <code>${hex8(r.continuation_offset)}</code><br>${esc(r.reason)}</p>`).join("")}` : "";
  const states={unreviewed:"Not reviewed",needs_investigation:"Needs investigation",confirmed_code:"Confirmed code",dismissed:"Dismissed"};
  panel.innerHTML=`<div class="coverage-result-heading"><b>${result.candidates.length} possible code regions</b><span class="small muted">${result.gaps} raw gaps · ${result.interior_regions || 0} interior ranges</span></div><p class="small muted">Expand a candidate to inspect its evidence and save a review.</p><details class="coverage-help"><summary>Scan scope and limits</summary><p>${esc(result.note)}${result.pointer_probe_limit || result.candidate_limit || result.mapping_limit || result.callback_scan_limit || result.hint_scan_limit ? " Search limits reached; results are incomplete." : ""}</p></details><label class="small"><input type="checkbox" data-audit-show-reviewed> Show confirmed and dismissed reviews</label>`+
    boundaryHtml+result.candidates.map(r=>`<details class="card coverage-candidate" data-audit-row data-state="${esc(r.review?.state || "unreviewed")}"><summary><code>${hex8(r.vram)}</code><span>${esc(r.evidence)}</span><span class="small muted" data-coverage-review-label>${esc(states[r.review?.state || "unreviewed"])}</span></summary><div class="coverage-candidate-body"><p class="muted small">${esc(r.region || "raw gap")} · ${r.decoded_instructions} decoded instructions · ${r.direct_flow_closed ? "direct flow closes" : r.flow_closed ? "bounded flow closes with reviewed table edges" : "flow unresolved"}${(r.unresolved_transfers || []).map(t=>" · indirect jump at "+hex8(t.address)+" via register "+t.register).join("")}</p><p>${r.incoming.map(i=>`<button class="link-btn" data-audit-source="${esc(i.function)}">${esc(i.function)}</button>`).join(", ")}${(r.pointer_references || []).map(p=>` ROM pointer word: <code>${hex8(p.rom_offset)}</code>`).join(", ")}</p>${(r.jump_tables || []).map(t=>`<p class="small">Jump table at <code>${hex8(t.table_address || 0)}</code> · ${t.entry_count || 0} entries · ${esc(t.state)} · ${esc(t.reason)}</p>`).join("")}${(r.callback_consumers || []).map(c=>`<p class="small">${c.state==="cell_load_call" ? "Cell load followed by indirect call" : "Possible callback consumer"}: <button class="link-btn" data-audit-source="${esc(c.function)}">${esc(c.function)}</button> at <code>${hex8(c.address)}</code> · ${c.mapping_verified ? "reviewed mapping" : "mapping hypothesis"}${c.indexed ? " · possible index "+c.possible_index+", stride "+c.stride : ""} · ${esc(c.reason || "")}${(c.evidence_chain || []).map(e=>`<br><code>${hex8(e.address)}</code> → ${esc(e.operation)}`).join("")}</p>`).join("")}<label>Review state <select data-review-state>${Object.entries(states).map(([id,label])=>`<option value="${id}"${(r.review?.state || "unreviewed")===id ? " selected" : ""}>${label}</option>`).join("")}</select></label><label>Evidence / next steps<textarea data-review-notes rows="3" maxlength="4000">${esc(r.review?.notes || "")}</textarea></label><button type="button" class="ghost small-btn" data-review-save="${esc(r.review_id || "")}">Save review</button><p class="muted small">Review only. Confirming does not import code; dismissing does not pause a function.</p></div></details>`).join("");
  if (result.unresolved_callbacks?.length) panel.insertAdjacentHTML("beforeend",`<details><summary>Unlinked indirect calls (${result.unresolved_callbacks.length})</summary><p class="muted small">These chains have no established link to a candidate. Runtime pointer contents and reachability remain unresolved.</p>${result.unresolved_callbacks.map(c=>`<section class="card"><button class="link-btn" data-audit-source="${esc(c.function)}">${esc(c.function)}</button> · <code>${hex8(c.address)}</code>${(c.evidence_chain || []).map(e=>`<p class="small"><code>${hex8(e.address)}</code> → ${esc(e.operation)}</p>`).join("")}</section>`).join("")}</details>`);
  const filter=()=>panel.querySelectorAll("[data-audit-row]").forEach(row=>row.hidden=!panel.querySelector("[data-audit-show-reviewed]").checked && ["confirmed_code","dismissed"].includes(row.dataset.state));
  panel.querySelector("[data-audit-show-reviewed]").onchange=filter;filter();
  panel.querySelectorAll("[data-audit-source]").forEach(b=>b.onclick=()=>openFn(b.dataset.auditSource));
  panel.querySelectorAll("[data-review-save]").forEach(button=>button.onclick=async()=>{
    const row=button.closest("[data-audit-row]");button.disabled=true;
    const controls=[...row.querySelectorAll("select,textarea")];controls.forEach(c=>c.disabled=true);
    try {
      const updated=await api("/api/code-audit-review",{method:"POST",body:{slug,review_id:button.dataset.reviewSave,state:row.querySelector("[data-review-state]").value,notes:row.querySelector("[data-review-notes]").value}});
      if (S.slug===slug) {
        codeAuditRequest++;
        const saved=updated.candidates.find(r=>r.review_id===button.dataset.reviewSave);
        if (saved) { row.dataset.state=saved.review.state; row.querySelector("[data-coverage-review-label]").textContent=states[saved.review.state] || saved.review.state; }
        filter();toast("Review saved.");
      }
    } catch(e) {toast(esc(e.message),"err");} finally {button.disabled=false;controls.forEach(c=>c.disabled=false);}
  });
}

function loadCodeAudit() {
  const panel=$("codeAuditResult"), slug=S.slug;
  if (panel.dataset.slug===slug) return;
  const request=++codeAuditRequest;
  panel.dataset.slug=slug;panel.replaceChildren();
  if (!S.ov?.code_audit) return;
  api("/api/code-audit-results?slug="+encodeURIComponent(slug)).then(result=>{
    if (S.slug===slug && request===codeAuditRequest) renderCodeAudit(result);
  }).catch(e=>{if(S.slug===slug){panel.dataset.slug="";toast(esc(e.message),"err");}});
}
