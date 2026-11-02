/* Saved human code-boundary reviews; a classification never adopts or pauses a function. */
let codeAuditRequest=0;
function renderCodeAudit(result) {
  codeAuditRequest++;
  const panel=$("codeAuditResult"), slug=S.slug;
  if (!result.available && !result.candidates?.length) {panel.replaceChildren();if(result.stale) panel.textContent="The saved audit predates a project layout or ROM change. Run the audit again.";return;}
  const states={unreviewed:"Not reviewed",needs_investigation:"Needs investigation",confirmed_code:"Confirmed code",dismissed:"Dismissed"};
  panel.innerHTML=`<p><b>${result.candidates.length} candidates</b> · ${result.gaps} raw gaps · ${result.interior_regions || 0} interior ranges. ${esc(result.note)}${result.pointer_probe_limit || result.candidate_limit || result.mapping_limit ? " Search limits reached; results are incomplete." : ""}</p><label><input type="checkbox" data-audit-show-reviewed> Show confirmed and dismissed reviews</label>`+
    result.candidates.map(r=>`<section class="card" data-audit-row data-state="${esc(r.review?.state || "unreviewed")}"><h4><code>${hex8(r.vram)}</code> · ${esc(r.evidence)}</h4><p class="muted small">${esc(r.region || "raw gap")} · ${r.decoded_instructions} decoded instructions · ${r.direct_flow_closed ? "direct flow closes" : "flow unresolved"}${(r.unresolved_transfers || []).map(t=>" · indirect jump at "+hex8(t.address)+" via register "+t.register).join("")}</p><p>${r.incoming.map(i=>`<button class="link-btn" data-audit-source="${esc(i.function)}">${esc(i.function)}</button>`).join(", ")}${(r.pointer_references || []).map(p=>` ROM pointer word: <code>${hex8(p.rom_offset)}</code>`).join(", ")}</p><label>Review state <select data-review-state>${Object.entries(states).map(([id,label])=>`<option value="${id}"${(r.review?.state || "unreviewed")===id ? " selected" : ""}>${label}</option>`).join("")}</select></label><label>Evidence / next steps<textarea data-review-notes rows="3" maxlength="4000">${esc(r.review?.notes || "")}</textarea></label><button type="button" class="ghost small-btn" data-review-save="${esc(r.review_id || "")}">Save review</button><p class="muted small">Review only. Confirming does not import code; dismissing does not pause a function.</p></section>`).join("");
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
        if (saved) row.dataset.state=saved.review.state;
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
