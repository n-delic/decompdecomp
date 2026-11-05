/* Boundary corrections are explicit previews over original bytes, with retained history. */
function boundaryRepairPreviewHTML(plan) {
  return `<section class="boundary-preview"><div class="coverage-counts"><div><b>${plan.old_size}</b><span>Current bytes</span></div><div><b>${plan.new_size}</b><span>Proposed bytes</span></div><div><b>${plan.affected.length}</b><span>Retained entries affected</span></div></div>
    <p>${esc(plan.warning)}</p><p class="small">Affected entries: ${plan.affected.map(f=>`<code>${esc(f.name)}</code>`).join(', ')}</p>
    <details open><summary>Surrounding original assembly · highlighted lines are inside the proposed extent</summary><pre class="boundary-assembly">${plan.assembly.map(line=>`<span${line.inside?' class="boundary-inside"':''}>${hex8(line.address)}  ${esc(line.instruction)}</span>`).join('')}</pre></details></section>`;
}
async function openBoundaryReview(initial='') {
  const slug=S.slug;
  const body=dialog('Review function boundaries',`<p>Review suspicious entries before agents spend time on them. Small functions can be valid; use the surrounding original instructions as evidence.</p>
    <div data-boundary-audit></div><div class="cols"><label class="field"><span>Function</span><input class="mono" data-boundary-name value="${esc(initial)}" placeholder="Choose an entry below or enter its name"></label>
    <label class="field"><span>Decision</span><select data-boundary-action><option value="delay_slot">Include missing delay slot</option><option value="join_previous">Join with preceding function</option><option value="adjust">Adjust end address…</option><option value="padding">Mark as padding</option><option value="data">Mark as data</option><option value="keep">Keep as separate entry · allow agents</option></select></label></div>
    <label class="field" data-boundary-end-field hidden><span>New end address · exclusive (hex)</span><input data-boundary-end class="mono" placeholder="0x80001020"></label>
    <button type="button" class="ghost" data-boundary-preview>Preview decision</button><div data-boundary-plan></div>
    <label class="field"><span>Evidence for this decision</span><textarea data-boundary-note rows="3" placeholder="Explain the control flow, mapping or padding evidence you reviewed."></textarea></label>
    <button class="primary" type="button" data-boundary-apply disabled>Verify &amp; save decision</button>
    <p class="small muted">Padding/data entries are retained in history and excluded from detected code; they are not counted as matches. Keeping an entry is an explicit override of automatic boundary review. Shared-room layouts must be left before editing.</p>
    <div data-boundary-work role="status"></div><details class="more"><summary>Verification log</summary><pre class="joblog" data-boundary-log></pre></details><div data-boundary-history></div>`, 'boundary-review');
  const get=q=>body.querySelector(q),work=get('[data-boundary-work]'),log=get('[data-boundary-log]');
  let plan=null,busy=false;
  const active=()=>S.slug===slug && S.dialogKind==='boundary-review' && !$("dialog").hidden;
  const clear=()=>{plan=null;get('[data-boundary-apply]').disabled=true;get('[data-boundary-plan]').innerHTML='';get('[data-boundary-end-field]').hidden=get('[data-boundary-action]').value!=='adjust';};
  for(const field of ['name','action','end'])get(`[data-boundary-${field}]`).onchange=clear;
  get('[data-boundary-name]').oninput=clear;get('[data-boundary-end]').oninput=clear;
  const load=async()=>{
    const data=await api(`/api/boundary-repairs?slug=${encodeURIComponent(slug)}`);
    if(!active())return;
    get('[data-boundary-audit]').innerHTML=`<details class="panel settings-disclosure" open><summary class="panel-head"><h3>Entries to inspect <span class="disclosure-context">— ${data.findings.length}</span></h3></summary><div class="boundary-findings">${data.findings.length?data.findings.map(f=>`<button type="button" class="boundary-finding" data-boundary-pick="${esc(f.function)}" data-boundary-suggest="${esc(f.suggested_action)}"><b class="mono">${esc(f.function)}</b><span class="small muted">${esc(f.reason)}${f.override?' · explicitly kept; agents allowed':' · automatic work paused'}</span></button>`).join(''):'<p>No incomplete entries detected. Enter a function name to review another extent.</p>'}</div></details>`;
    get('[data-boundary-history]').innerHTML=`<details class="panel settings-disclosure"><summary class="panel-head"><h3>Repair history <span class="disclosure-context">— ${data.history.length} recent decisions</span></h3></summary><div class="boundary-findings">${data.history.map(h=>`<div class="boundary-history-row"><span><b class="mono">${esc(h.function)}</b><br><span class="small muted">${esc(h.action.replaceAll('_',' '))} · ${new Date(h.time*1000).toLocaleString()}</span><br><span class="small muted">${esc(h.evidence)}</span></span><button type="button" class="ghost small-btn" data-boundary-undo="${esc(h.key)}">Verify &amp; undo</button></div>`).join('') || '<p>No decisions yet.</p>'}</div></details>`;
  };
  body.addEventListener('click',async event=>{
    const pick=event.target.closest('[data-boundary-pick]');
    if(pick&&!busy){get('[data-boundary-name]').value=pick.dataset.boundaryPick;get('[data-boundary-action]').value=pick.dataset.boundarySuggest;clear();get('[data-boundary-preview]').focus();return;}
    const preview=event.target.closest('[data-boundary-preview]'),apply=event.target.closest('[data-boundary-apply]'),undo=event.target.closest('[data-boundary-undo]');
    if((!preview&&!apply&&!undo)||busy)return;
    busy=true;const controls=[...body.querySelectorAll('button,input,select,textarea')];
    const finish=beginWork(work,preview?'Reading original bytes…':undo?'Verifying undo…':'Verifying existing matches…',controls,preview?'Preview ready. Review the assembly and evidence before saving.':'Boundary review finished.');
    try{
      if(preview){
        const action=get('[data-boundary-action]').value,endText=get('[data-boundary-end]').value.trim();
        const end=action==='adjust'?Number(/^0x/i.test(endText)?endText:'0x'+endText):undefined;
        plan=await api('/api/boundary-repairs',{method:'POST',body:{slug,action:'preview',function:get('[data-boundary-name]').value.trim(),repair:action,end}});
        if(active())get('[data-boundary-plan]').innerHTML=boundaryRepairPreviewHTML(plan);
      }else{
        if(!undo&&!plan)throw new Error('Preview the decision before saving.');
        const payload=undo?{slug,action:'undo',key:undo.dataset.boundaryUndo}:{slug,action:'apply',function:plan.function,repair:plan.action,end:plan.end,revision:plan.revision,image:plan.image,note:get('[data-boundary-note]').value};
        const {job}=await api('/api/boundary-repairs',{method:'POST',body:payload});
        await followJob(job,log,(_lines,state)=>{const p=state.progress;if(active()&&p&&work._workStatus)work._workStatus.textContent=`${p.phase} · ${p.done || 0} / ${p.total || 0}`;},active);
        if(active()){clear();await load();S.ov=await api(`/api/project?slug=${encodeURIComponent(slug)}`);renderStats(S.ov);renderProjectSettings();}
      }
    }catch(error){if(active())showJobError(work,error.message);}
    finally{finish();busy=false;if(active())get('[data-boundary-apply]').disabled=!plan;}
  });
  const done=beginWork(work,'Auditing detected function boundaries…',[], 'Boundary audit loaded.');
  try{await load();}catch(error){showJobError(work,error.message);}finally{done();}
}
$("boundaryReviewBtn").onclick=()=>openBoundaryReview();
$("fnBoundaryRepair").onclick=()=>openBoundaryReview(S.fn);
