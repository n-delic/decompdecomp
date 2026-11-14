/* Boundary evidence and advisory agents lead to explicit, guarded layout decisions. */
const BOUNDARY_CHOICES = [
  ['delay_slot','Include missing delay slot','Extend by one instruction when the final branch or jump requires it.'],
  ['join_previous','Join with preceding function','Use when control flow supports this entry continuing the preceding function. Adjacency alone is insufficient.'],
  ['keep','Keep as separate entry','Keep an intentional entry point and allow agents to work on its current bytes.'],
  ['padding','Mark as padding','Exclude alignment bytes from detected code; a small size or no known callers is insufficient.'],
  ['data','Mark as data','Exclude bytes supported by data-use evidence, rather than executable instructions.'],
  ['adjust','Adjust end address…','Advanced: enter a different exclusive end; any shortened tail stays available for review.']
];
function boundaryAssemblyHTML(lines, prefix, labels={}) {
  let previous=null;
  return `<pre class="boundary-assembly">${lines.map(line=>{
    const gap=previous!==null&&line.address!==previous+4?'… instruction window omitted …\n':'';previous=line.address;
    return `${gap}<span id="${prefix}-${line.address}"${line.inside?' class="boundary-inside"':''}>${hex8(line.address)}  ${esc(line.instruction)}${labels[line.address]?`  ← ${esc(labels[line.address])}`:''}${line.return?'  [return]':''}${line.delay_slot_of!=null?`  [delay slot for ${hex8(line.delay_slot_of)}]`:''}${line.target!=null?`  [→ ${hex8(line.target)}]`:''}${line.delay_slot?'  [requires delay slot]':''}</span>`;
  }).join('')}</pre>`;
}
function boundaryLayoutHTML(entries) {
  return `<div class="boundary-layout">${entries.map(entry=>`<div><b class="mono">${esc(entry.name)}</b><span class="mono small">${hex8(entry.start)} → ${hex8(entry.end)} (exclusive)</span></div>`).join('')}</div>`;
}
function boundaryRepairPreviewHTML(plan) {
  const changed=['delay_slot','join_previous','adjust'].includes(plan.action);
  const consequence=changed?`<b>${esc(plan.owner)}</b> remains the active name. ${plan.affected.filter(f=>f.name!==plan.owner).map(f=>`<code>${esc(f.name)}</code>`).join(', ') || 'No other entry'}${plan.affected.length>1?' will be archived with history retained.':' will be archived.'} The resized function needs a fresh match.`:
    plan.action==='keep'?'The current entry remains separate. This explicitly allows automatic work on its current bytes.':'This entry is archived with history retained and excluded from detected code. This adds no match.';
  return `<section class="boundary-preview"><h3>Review the proposed change</h3><div class="coverage-counts"><div><b>${plan.old_size}</b><span>Current bytes</span></div><div><b>${plan.new_size}</b><span>Proposed bytes</span></div><div><b>${plan.affected.length}</b><span>Retained entries affected</span></div></div>
    <p><b>Before</b></p>${boundaryLayoutHTML(plan.affected.map(f=>({name:f.name,start:f.start,end:f.end})))}
    <p><b>After</b></p>${['padding','data'].includes(plan.action)?'<p>Archived as '+esc(plan.action)+'; excluded from detected code.</p>':boundaryLayoutHTML([{name:plan.owner,start:plan.start,end:plan.end},...(plan.end<plan.old_end?[{name:'Retained tail · needs review',start:plan.end,end:plan.old_end}]:[])])}
    <p>${consequence}</p><p class="small">${esc(plan.warning)}</p>
    <details open><summary>Original instructions around the disputed boundaries · highlighted = proposed extent</summary>${boundaryAssemblyHTML(plan.assembly,'boundary-preview-line',{[plan.old_end]:'current end (exclusive)',[plan.end]:'proposed end (exclusive)'})}</details></section>`;
}
function boundaryEvidenceHTML(packet) {
  return `<h3>Why this entry needs review</h3><p><b>${esc(packet.diagnosis)}</b></p>
    <p class="small">A boundary is the detected start and end of a function. This warning means the current extent needs review; it does not identify the correct repair.</p>
    <p class="small">Mapping: ${esc(packet.segment.name)} · ${hex8(packet.segment.start)} → ${hex8(packet.segment.end)} (exclusive)</p>
    ${boundaryLayoutHTML([...packet.neighbours.map(n=>({...n})),{name:packet.function,start:packet.start,end:packet.end}].sort((a,b)=>a.start-b.start))}
    <ul class="small">${packet.evidence.map(e=>`<li><a data-boundary-cite="boundary-evidence-line-${e.address}" href="#boundary-evidence-line-${e.address}">${hex8(e.address)}</a>: ${esc(e.text)}</li>`).join('')}</ul>
    <details open><summary>Original instructions · highlighted = current extent</summary>${boundaryAssemblyHTML(packet.assembly,'boundary-evidence-line',{[packet.start]:'selected entry',[packet.end]:'current end (exclusive)'})}</details>
    <details><summary>Direct calls and branches (${packet.edges.length}) · symbols (${packet.symbols.length})</summary>
      ${packet.edges.map(e=>`<p class="small" id="boundary-edge-${e.from}"><code>${esc(e.function)}</code>: ${hex8(e.from)} → ${hex8(e.to)} · ${esc(e.kind)}<br><code>${esc(e.instruction)}</code></p>`).join('')||'<p>No direct edges found within this scan.</p>'}
      ${packet.symbols.map(s=>`<p class="small"><code>${esc(s.name)}</code> · ${hex8(s.address)} · ${esc(s.kind)}</p>`).join('')}
    </details><p class="small muted">${esc(packet.limits)}</p>`;
}
function boundarySuggestionsHTML(packet) {
  return (packet.suggestions||[]).map((item,index)=>`<section class="boundary-suggestion"><h3>${item.action==='unclear'?'No clear recommendation':esc(BOUNDARY_CHOICES.find(c=>c[0]===item.action)?.[1]||item.action)}</h3>
    <p class="small">${esc(item.agent)} · ${esc(item.model||'provider default')} · ${esc(item.confidence)} evidence · ${esc(new Date(item.time*1000).toLocaleString())}${item.stale?' · <b>Inputs changed: investigate again before using this advice.</b>':''}</p>
    ${item.end?`<p>Proposed exclusive end: <code>${hex8(item.end)}</code></p>`:''}
    <p><b>Supporting evidence:</b> ${esc(item.supporting)}</p><p><b>Counter-evidence:</b> ${esc(item.counter)}</p><p><b>Remaining uncertainty:</b> ${esc(item.uncertainty)}</p>
    <p class="small">Instructions: ${item.citations.map(address=>`<a data-boundary-cite="${packet.assembly.some(l=>l.address===address)?'boundary-evidence-line':'boundary-edge'}-${address}" href="#${packet.assembly.some(l=>l.address===address)?'boundary-evidence-line':'boundary-edge'}-${address}">${hex8(address)}</a>`).join(', ')}</p>
    ${!item.stale&&item.action!=='unclear'?`<button type="button" class="ghost" data-boundary-use="${index}">Review this proposal</button>`:''}</section>`).join('') || '<p class="small muted">No agent recommendation saved for this entry.</p>';
}
async function openBoundaryReview(initial='') {
  const slug=S.slug;
  const body=dialog('Review function boundaries',`<p>Inspect the original instructions, compare possible repairs, then review the exact change. An agent can investigate and explain a recommendation.</p>
    <section class="boundary-safe"><p class="small">Apply safe fixes repairs every entry whose original bytes settle its boundary: a jump missing its delay slot, or code that runs on into the next piece without returning. Existing matches are checked first, each fix can be undone from Repair history, and the rest stay here for review.</p>
      <div class="row"><button class="primary" type="button" data-boundary-safe>Apply safe fixes</button><button class="ghost" type="button" data-boundary-safe-stop hidden>Stop</button></div>
      <details class="more" data-boundary-safe-frame><summary>Safe fix log</summary><pre class="joblog" data-boundary-safe-log></pre></details></section>
    <div data-boundary-audit-work></div><div data-boundary-audit></div><div class="row"><label class="field grow"><span>Selected function</span><input class="mono" data-boundary-name value="${esc(initial)}" placeholder="Choose an entry or enter its name"></label><button class="ghost" data-boundary-read>Read evidence</button></div>
    <div data-boundary-evidence-work></div><div data-boundary-evidence></div>
    <details class="panel" data-boundary-agent><summary>Ask an agent to investigate · uses AI tokens</summary><p class="small">Investigates this entry for up to 10 minutes, then stops after submitting advice. You review and apply any layout change.</p>
      <div class="cols"><label class="field">Agent<select data-boundary-kind></select></label><label class="field" data-boundary-endpoint-field hidden>Saved endpoint<select data-boundary-endpoint></select></label>
      <label class="field">Model<input data-boundary-model list="boundary-models" placeholder="Agent default"><datalist id="boundary-models"></datalist></label></div>
      <div class="row"><button class="ghost" data-boundary-investigate>Ask agent to investigate</button><button class="ghost" data-boundary-stop hidden>Stop investigation</button><button class="ghost" data-boundary-refresh>Refresh recommendations</button></div><p class="small" data-boundary-agent-status role="status"></p><div data-boundary-agent-work></div>
    </details><div data-boundary-suggestions></div>
    <fieldset class="boundary-decisions"><legend>Compare possible decisions</legend>${BOUNDARY_CHOICES.map(([value,title,help])=>`${value==='adjust'?'<details><summary>Advanced boundary edit</summary>':''}<label class="boundary-choice"><input type="radio" name="boundary-action" data-boundary-action value="${value}"><span><b>${esc(title)}</b><span class="small muted">${esc(help)}</span></span></label>${value==='adjust'?'<label class="field" data-boundary-end-field><span>New end address · exclusive (hex)</span><input data-boundary-end class="mono" placeholder="0x80001020"></label></details>':''}`).join('')}</fieldset>
    <button type="button" class="ghost" data-boundary-preview>Preview selected decision</button><div data-boundary-preview-work></div><div data-boundary-plan></div>
    <label class="field"><span>Evidence for this decision</span><textarea data-boundary-note rows="3" placeholder="Record the instructions, addresses and uncertainty you reviewed."></textarea></label>
    <button class="primary" type="button" data-boundary-apply disabled>Run safety checks &amp; save decision</button>
    <p class="small muted">Safety checks protect mappings and existing matches; they do not prove function ownership. Original records and history are retained, with guarded undo. Shared-room layouts must be left before editing.</p>
    <div data-boundary-work role="status"></div><details class="more"><summary>Verification and startup log</summary><pre class="joblog" data-boundary-log></pre></details><div data-boundary-history></div>`, 'boundary-review');
  const reviewToken=Symbol('boundary-review');body._boundaryReview=reviewToken;
  const get=q=>body.querySelector(q),work=get('[data-boundary-work]'),log=get('[data-boundary-log]');
  let plan=null,packet=null,busy=false,reading=0,watching=0,stopWatching=()=>{};
  const active=()=>body._boundaryReview===reviewToken && S.slug===slug && S.dialogKind==='boundary-review' && !$("dialog").hidden;
  const action=()=>body.querySelector('[data-boundary-action]:checked')?.value||'';
  const clear=()=>{plan=null;get('[data-boundary-apply]').disabled=true;get('[data-boundary-plan]').innerHTML='';};
  const invalidate=()=>{clear();packet=null;reading++;watching++;stopWatching();get('[data-boundary-investigate]').disabled=!available.length;get('[data-boundary-stop]').hidden=true;get('[data-boundary-agent-status]').textContent='';get('[data-boundary-evidence]').innerHTML='';get('[data-boundary-suggestions]').innerHTML='';get('[data-boundary-note]').value='';body.querySelectorAll('[data-boundary-action]').forEach(r=>{r.checked=false;});};
  get('[data-boundary-name]').oninput=invalidate;get('[data-boundary-end]').oninput=clear;
  body.querySelectorAll('[data-boundary-action]').forEach(r=>{r.onchange=clear;});
  const available=(S.st.agents||[]).filter(a=>a.installed||a.uses_endpoints),kind=get('[data-boundary-kind]'),endpoint=get('[data-boundary-endpoint]'),model=get('[data-boundary-model]');
  for(const a of available){const o=document.createElement('option');o.value=a.id;o.textContent=a.name;kind.append(o);}
  for(const e of S.st.settings.endpoints||[]){const o=document.createElement('option');o.value=e.id;o.textContent=e.name;endpoint.append(o);}
  const updateModel=()=>{const a=available.find(a=>a.id===kind.value),e=(S.st.settings.endpoints||[]).find(e=>e.id===endpoint.value);get('[data-boundary-endpoint-field]').hidden=!a?.uses_endpoints;model.value=a?.uses_endpoints?e?.model||'':a?.default_model||'';const list=get('#boundary-models');list.replaceChildren();for(const m of a?.models||[]){const o=document.createElement('option');o.value=m.id;o.textContent=m.name||m.id;list.append(o);}};
  kind.onchange=endpoint.onchange=updateModel;updateModel();get('[data-boundary-investigate]').disabled=!available.length;
  if(!available.length)get('[data-boundary-agent-status]').textContent='Configure an agent in Agents & models to request advice.';
  const read=async()=>{
    const name=get('[data-boundary-name]').value.trim(),seq=++reading;
    clear();packet=null;get('[data-boundary-evidence]').innerHTML='';get('[data-boundary-suggestions]').innerHTML='';
    const evidenceWork=get('[data-boundary-evidence-work]');
    const done=beginWork(evidenceWork,'Reading original boundary evidence…',[get('[data-boundary-read]')],'Boundary evidence loaded.');
    try{const data=await api(`/api/boundary-evidence?slug=${encodeURIComponent(slug)}&function=${encodeURIComponent(name)}`);if(!active()||seq!==reading)return;packet=data;get('[data-boundary-evidence]').innerHTML=boundaryEvidenceHTML(data);get('[data-boundary-suggestions]').innerHTML=boundarySuggestionsHTML(data);}
    catch(error){if(active()&&seq===reading)showJobError(evidenceWork,error.message);}finally{done();}
  };
  const safeLog=get('[data-boundary-safe-log]'),safeStop=get('[data-boundary-safe-stop]');
  safeLog._frame=get('[data-boundary-safe-frame]');safeLog._showJobProgress=true;
  let safeJob=null;const safeFollowed=new Set();
  // Follows a run started here, or found running when the dialog opens: one follower per run. `start` is the id of
  // a running job, or a function that starts one, so the click shows progress before its first request.
  const followSafe=async(start)=>{
    if(safeJob)return;safeJob=start;busy=true;showJobError(safeLog,'');
    const controls=[...body.querySelectorAll('button,input,select,textarea')].filter(c=>c!==safeStop);
    const finish=beginWork(safeLog,typeof start==='function'?'Starting safe boundary fixes…':'Applying safe boundary fixes…',controls,'Safe fixes finished.');
    let id=null;
    safeStop.hidden=false;safeStop.disabled=typeof start==='function';
    safeStop.onclick=async()=>{if(!id)return;safeStop.disabled=true;safeLog._workStatus.textContent='Stopping after the current fix…';
      try{await api('/api/job/cancel',{method:'POST',body:{id}});}catch(error){safeStop.disabled=false;if(active())showJobError(safeLog,error.message);}};
    try{
      id=typeof start==='function'?(await start()).job:start;if(!active())return;
      safeJob=id;safeFollowed.add(id);safeStop.disabled=false;
      const result=await followJob(id,safeLog,undefined,active);
      if(active()&&result){safeLog._workStatus.textContent=`Applied ${result.fixed.length} safe ${result.fixed.length===1?'fix':'fixes'}; ${result.remaining} ${result.remaining===1?'entry needs':'entries need'} review.`;safeLog._workStatus.dataset.state='done';}
    }catch(error){if(active())showJobError(safeLog,error.message);}
    finally{finish();safeStop.hidden=true;busy=false;safeJob=null;
      if(active()){get('[data-boundary-apply]').disabled=!plan;invalidate();
        try{await load();S.ov=await api(`/api/project?slug=${encodeURIComponent(slug)}`);if(active()){renderStats(S.ov);renderProjectSettings();}}
        catch(error){if(active())showJobError(get('[data-boundary-audit-work]'),error.message);}}}
  };
  get('[data-boundary-safe]').onclick=()=>{if(!busy)void followSafe(()=>api('/api/boundary-repairs',{method:'POST',body:{slug,action:'apply_safe'}}));};
  const load=async()=>{
    const data=await api(`/api/boundary-repairs?slug=${encodeURIComponent(slug)}`);if(!active())return;
    if(data.safe_job&&!safeJob&&!safeFollowed.has(data.safe_job.id))void followSafe(data.safe_job.id);
    get('[data-boundary-audit]').innerHTML=`<details class="panel settings-disclosure" ${initial?'':'open'}><summary class="panel-head"><h3>Entries to inspect <span class="disclosure-context">— ${data.findings.length}</span></h3></summary><div class="boundary-findings">${data.findings.map(f=>`<button type="button" class="boundary-finding" data-boundary-pick="${esc(f.function)}"><b class="mono">${esc(f.function)}</b><span class="small muted">${f.suggested_action==='delay_slot'?'Final instruction may be missing its delay slot.':'Inspect the entry and surrounding control flow.'}${f.override?' · reviewed; agents allowed':' · automatic work paused'}</span></button>`).join('')||'<p>No incomplete entries detected. Enter a function name to review another extent.</p>'}</div></details>`;
    get('[data-boundary-history]').innerHTML=`<details class="panel settings-disclosure"><summary class="panel-head"><h3>Repair history <span class="disclosure-context">— ${data.history.length} recent decisions</span></h3></summary><div class="boundary-findings">${data.history.map(h=>`<div class="boundary-history-row"><span><b class="mono">${esc(h.function)}</b><br><span class="small muted">${esc(h.action.replaceAll('_',' '))} · ${esc(new Date(h.time*1000).toLocaleString())}</span><br><span class="small muted">${esc(h.evidence)}</span></span><button type="button" class="ghost small-btn" data-boundary-undo="${esc(h.key)}">Check &amp; undo</button></div>`).join('')||'<p>No decisions yet.</p>'}</div></details>`;
  };
  const watch=async(id,name)=>{
    stopWatching();
    const seq=++watching,valid=()=>active()&&seq===watching&&get('[data-boundary-name]').value.trim()===name;
    const finish=beginWork(get('[data-boundary-agent-work]'),'Agent investigating original boundary evidence…',[],'Investigation finished. Review its evidence below.');
    let ended=false;const endWatch=()=>{if(!ended){ended=true;finish();}};
    stopWatching=endWatch;get('[data-boundary-investigate]').disabled=true;
    get('[data-boundary-stop]').hidden=false;
    get('[data-boundary-stop]').onclick=async()=>{try{await api('/api/agents/stop',{method:'POST',body:{id}});}catch(error){if(valid())showJobError(get('[data-boundary-agent-work]'),error.message);}};
    try{while(valid()){
      const data=await api(`/api/boundary-investigations?slug=${encodeURIComponent(slug)}&function=${encodeURIComponent(name)}`);if(!valid())return;
      const session=data.sessions.find(s=>s.id===id);if(!session)throw new Error('Investigation no longer exists. Refresh recommendations.');
      get('[data-boundary-agent-status]').textContent=`${session.label}: ${session.state}${session.why?' · '+session.why:''}`;
      if(['done','error','stopped','paused'].includes(session.state)){await read();if(session.error)throw new Error(session.error);break;}
      await sleep(2000);
    }}catch(error){if(valid())showJobError(get('[data-boundary-agent-work]'),error.message);}finally{endWatch();if(seq===watching){get('[data-boundary-stop]').hidden=true;get('[data-boundary-investigate]').disabled=!available.length;stopWatching=()=>{};}}
  };
  const resumeWatch=async()=>{
    if(!packet||!active())return;
    const name=packet.function;
    try{const data=await api(`/api/boundary-investigations?slug=${encodeURIComponent(slug)}&function=${encodeURIComponent(name)}`);
      if(!active()||packet?.function!==name)return;
      const running=data.sessions.find(s=>['starting','working','waiting'].includes(s.state));
      if(running)void watch(running.id,name);
    }catch(error){if(active())showJobError(work,error.message);}
  };
  body.addEventListener('click',async event=>{
    const citation=event.target.closest('[data-boundary-cite]');
    if(citation){
      // The app uses the URL hash for project navigation; citations stay inside this review.
      event.preventDefault();const line=body.querySelector('#'+citation.dataset.boundaryCite);
      if(line){let parent=line.parentElement;while(parent&&parent!==body){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
        body.querySelectorAll('.boundary-cited').forEach(el=>el.classList.remove('boundary-cited'));
        line.classList.add('boundary-cited');line.tabIndex=-1;line.focus({preventScroll:true});line.scrollIntoView({block:'center',behavior:'smooth'});}
      return;
    }
    const pick=event.target.closest('[data-boundary-pick]');
    if(pick&&!busy){get('[data-boundary-name]').value=pick.dataset.boundaryPick;invalidate();get('[data-boundary-audit]').querySelector('details').open=false;await read();await resumeWatch();return;}
    if(event.target.closest('[data-boundary-read],[data-boundary-refresh]')&&!busy){await read();await resumeWatch();return;}
    const use=event.target.closest('[data-boundary-use]');
    if(use&&!busy&&packet){const item=packet.suggestions[Number(use.dataset.boundaryUse)];if(!item||item.stale)return;clear();body.querySelector(`[data-boundary-action][value="${item.action}"]`).checked=true;if(item.end){get('[data-boundary-end]').value=hex8(item.end);get('[data-boundary-end-field]').closest('details').open=true;}get('[data-boundary-note]').value=`Agent proposal (${item.agent}): ${item.supporting}\nCounter-evidence: ${item.counter}\nUncertainty: ${item.uncertainty}\nInstructions: ${item.citations.map(hex8).join(', ')}`;get('[data-boundary-preview]').focus();return;}
    const investigate=event.target.closest('[data-boundary-investigate]'),preview=event.target.closest('[data-boundary-preview]'),apply=event.target.closest('[data-boundary-apply]'),undo=event.target.closest('[data-boundary-undo]');
    if((!investigate&&!preview&&!apply&&!undo)||busy)return;
    busy=true;const controls=[...body.querySelectorAll('button,input,select,textarea')];
    const foreground=investigate?get('[data-boundary-agent-work]'):preview?get('[data-boundary-preview-work]'):work;
    const finish=beginWork(foreground,investigate?'Creating boundary investigator…':preview?'Reading proposed original bytes…':undo?'Checking undo…':'Checking mappings and existing matches…',controls,investigate?'Agent created.':preview?'Preview ready. Review the evidence before saving.':'Boundary review finished.');
    let started=null;
    try{
      if(investigate){
        if(!packet)throw new Error('Read the selected function’s evidence first.');
        const a=available.find(a=>a.id===kind.value);if(!a)throw new Error('Configure an agent in Agents & models first.');
        if(a.uses_endpoints&&!endpoint.value)throw new Error('Save an API endpoint in Agents & models first.');
        const options={task:'boundary',boundary_function:packet.function};if(model.value.trim())options.model=model.value.trim();if(a.uses_endpoints)options.endpoint=endpoint.value;
        const response=await api('/api/agents/start',{method:'POST',body:{slug,kind:a.id,count:1,options,background:true}});
        const result=response.job?await followJob(response.job,log,undefined,active):response;
        if(!active())return;if(!result?.started?.[0])throw new Error('No investigator was created. Check Agents before retrying.');started={id:result.started[0],name:packet.function};
      }else if(preview){
        if(!packet||packet.function!==get('[data-boundary-name]').value.trim())throw new Error('Read the selected function’s evidence first.');
        if(!action())throw new Error('Choose a decision after reviewing the evidence.');
        clear();const endText=get('[data-boundary-end]').value.trim(),end=action()==='adjust'?Number(/^0x/i.test(endText)?endText:'0x'+endText):undefined;
        plan=await api('/api/boundary-repairs',{method:'POST',body:{slug,action:'preview',function:packet.function,repair:action(),end}});
        if(active())get('[data-boundary-plan]').innerHTML=boundaryRepairPreviewHTML(plan);
      }else{
        if(!undo&&!plan)throw new Error('Preview the decision before saving.');
        const payload=undo?{slug,action:'undo',key:undo.dataset.boundaryUndo}:{slug,action:'apply',function:plan.function,repair:plan.action,end:plan.end,revision:plan.revision,image:plan.image,note:get('[data-boundary-note]').value};
        const {job}=await api('/api/boundary-repairs',{method:'POST',body:payload});
        await followJob(job,log,(_lines,state)=>{const p=state.progress;if(active()&&p&&work._workStatus)work._workStatus.textContent=`${p.phase} · ${p.done||0} / ${p.total||0}`;},active);
        if(active()){invalidate();await load();S.ov=await api(`/api/project?slug=${encodeURIComponent(slug)}`);if(active()){renderStats(S.ov);renderProjectSettings();}}
      }
    }catch(error){if(active())showJobError(foreground,error.message);}
    finally{finish();busy=false;if(active()){get('[data-boundary-apply]').disabled=!plan;get('[data-boundary-investigate]').disabled=!available.length;}}
    if(started&&active())await watch(started.id,started.name);
  });
  const auditWork=get('[data-boundary-audit-work]');
  const done=beginWork(auditWork,'Auditing detected function boundaries…',[],'Boundary audit loaded.');
  try{await load();}catch(error){if(active())showJobError(auditWork,error.message);}finally{done();}
  if(initial&&active()){await read();await resumeWatch();}
}
$("boundaryReviewBtn").onclick=()=>openBoundaryReview();
$("fnBoundaryRepair").onclick=()=>openBoundaryReview(S.fn);
