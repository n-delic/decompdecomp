/* Explicit agent work and human review; all durable state belongs to the server. */
const assetAgentViews=new Map();let assetAgentEpoch=0,assetReviewEpoch=0;
function assetAgentView(slug=S.slug){if(!assetAgentViews.has(slug))assetAgentViews.set(slug,{page:0,busy:false,state:null,scope:{mode:'unknown'}});return assetAgentViews.get(slug);}
function assetAgentLog(view,fresh=false){
  if(fresh||!view.workElement){view.workElement=document.createElement('div');view.log=document.createElement('pre');view.log.className='joblog';view.workElement.append(view.log);}
  $('assetAgentWork').replaceChildren(view.workElement);return view.log;
}
async function loadAssetAgents(){
  const slug=S.slug,epoch=++assetAgentEpoch,view=assetAgentView(slug),active=()=>S.slug===slug&&S.tab==='rom'&&assetAgentEpoch===epoch;
  clearTimeout(view.timer);$('assetAgentPanel').hidden=false;
  if($('assetAgentItems').dataset.slug!==slug){$('assetAgentItems').replaceChildren();$('assetAgentItems').dataset.slug=slug;$('assetAgentContext').textContent='';$('assetAgentStatus').textContent='';$('assetAgentWorkers').replaceChildren();assetAgentLog(view);$('assetAgentStart').disabled=true;$('assetAgentStop').hidden=true;}
  try{const activity=await api('/api/rom-explorer/discovery?view=activity&slug='+encodeURIComponent(slug));if(!active())return;const creation=activity.creation_job;view.creating=creation?.state==='running'?creation.id:null;if(view.creating)followAssetCreation(slug,view,creation.id);else if(creation&&['error','cancelled'].includes(creation.state)&&view.creationError!==creation.id&&!view.busy){view.creationError=creation.id;showJobError(assetAgentLog(view,true),creation.error||'Agent creation stopped. Review existing workers before trying again.');}
    const state=await api(`/api/rom-explorer/discovery?slug=${encodeURIComponent(slug)}&page=${view.page}`);if(!active())return;view.state=state;renderAssetAgents(view);}
  catch(error){if(active())showJobError(assetAgentLog(view),error.message);}
  finally{if(active()&&!view.busy)view.timer=setTimeout(()=>{if(active())loadAssetAgents();},view.state?.run?.state==='active'?2000:8000);}
}
async function followAssetCreation(slug,view,identity){
  if(view.followingCreation===identity)return;view.followingCreation=identity;
  const active=()=>S.slug===slug&&S.tab==='rom'&&view.creating===identity,log=assetAgentLog(view,true),finish=beginWork(log,'Creating discovery agents',[],'Discovery agents created.');
  try{await followJob(identity,log,(_,job)=>updateWorkProgress(log,job.progress),active);if(active())view.creating=null;}
  catch(error){if(active()){view.creating=null;showJobError(log,error.message);}}
  finally{finish();view.followingCreation=null;if(S.slug===slug&&S.tab==='rom')loadAssetAgents();}
}
function renderAssetAgents(view=assetAgentView()){
  const state=view.state;if(!state)return;const run=state.run;
  $('assetAgentContext').textContent=`— ${view.creating?'Creating agents · ':run?.state==='active'?'Discovery active · ':''}${state.pending||0} proposals awaiting review`;
  const workers=state.workers||[],live=workers.filter(w=>['starting','working','waiting'].includes(w.state));
  $('assetAgentStatus').textContent=run?`${run.done} of ${run.total} regions processed · ${run.counts.submitted||0} proposals · ${run.counts.skipped||0} unresolved · ${(run.counts.claimed||0)+(run.counts.review_claimed||0)} claimed · ${run.counts.review_queued||0} awaiting a different worker’s review · ${live.length} workers running · ${run.state}${run.state==='active'&&!live.length?' — choose agents to continue this queue':''}`:'No discovery pass yet. Choose a scope, then choose agents.';if(!state.ready)$('assetAgentStatus').textContent+=' Finish the current ROM Explorer scan to enable discovery.';
  const bar=$('assetAgentBar');bar.hidden=!run;bar.setAttribute('aria-valuemax',run?.total||1);bar.setAttribute('aria-valuenow',run?.done||0);bar.querySelector('i').style.width=`${run?run.done/Math.max(1,run.total)*100:0}%`;
  $('assetAgentStop').hidden=run?.state!=='active'&&!view.creating;$('assetAgentStop').disabled=view.busy;$('assetAgentStop').textContent=view.stopping?'Stopping…':'Stop discovery';
  $('assetAgentStart').disabled=view.busy||!!view.creating||!state.ready;$('assetAgentStart').textContent=run?.state==='paused'&&!view.newPass?'Resume with agents':run?.state==='active'?'Add discovery agents':'Discover assets';
  if(run?.state==='active')view.newPass=false;
  $('assetAgentNewPass').hidden=run?.state!=='paused'||!!view.newPass;$('assetAgentNewPass').disabled=view.busy;
  const locked=!!view.creating||run?.state==='active'||run?.state==='paused'&&!view.newPass;if(locked&&run)view.scope=run.scope;
  const get=id=>$(id);get('assetAgentScope').value=view.scope.mode;get('assetAgentRange').hidden=view.scope.mode!=='range';get('assetAgentFunctionField').hidden=view.scope.mode!=='function';
  for(const id of ['assetAgentScope','assetAgentStartOffset','assetAgentEndOffset','assetAgentFunction'])get(id).disabled=view.busy||locked;
  if(document.activeElement!==get('assetAgentStartOffset'))get('assetAgentStartOffset').value=view.scope.start??'0';
  if(document.activeElement!==get('assetAgentEndOffset'))get('assetAgentEndOffset').value=view.scope.end??view.state?.image_size??romView().report?.size??'';
  if(document.activeElement!==get('assetAgentFunction'))get('assetAgentFunction').value=view.scope.function||'';
  $('assetAgentWorkers').innerHTML=workers.map(worker=>`<p>${esc(worker.label)} · ${esc(worker.model||'default model')} · ${esc(worker.state)}${worker.why?' · '+esc(worker.why):''}</p>`).join('');
  $('assetAgentItems').innerHTML=(state.findings||[]).map(item=>`<p><button type="button" class="link-btn" data-asset-finding="${esc(item.id)}">${esc(item.body.name||(item.outcome==='critique'?'Independent review: '+item.body.verdict:'Unresolved region'))} · ${esc(item.region)}</button><br><span class="small muted">${esc(item.author)} · ${esc(item.model)} · ${esc(item.body.kind||item.outcome)} · ${esc(item.body.confidence||'unresolved')} · ${esc(item.status)}${item.stale?' · Older image/layout':''}</span></p>`).join('')||'<p class="small muted">Findings and unsuccessful approaches will appear here.</p>';
  $('assetAgentItems').querySelectorAll('[data-asset-finding]').forEach(button=>button.onclick=()=>openAssetFinding(button.dataset.assetFinding));
  $('assetAgentPage').textContent=`Newest first · ${Math.min(view.page*50+(state.findings?.length?1:0),state.finding_total||0)}–${Math.min((view.page+1)*50,state.finding_total||0)} of ${state.finding_total||0}`;
  $('assetAgentPrevious').disabled=view.busy||view.page===0;$('assetAgentNext').disabled=view.busy||(view.page+1)*50>=(state.finding_total||0);
}
async function chooseAssetAgents(){
  const slug=S.slug,view=assetAgentView(slug);if(view.busy||view.creating)return;view.busy=true;
  const work=assetAgentLog(view,true),finish=beginWork(work,'Preparing discovery queue',[],'Discovery queue ready. Choose a model and start its agents.');showJobError(work,'');renderAssetAgents(view);
  try{
    if(view.scope.mode==='range'){view.scope={...view.scope,start:$('assetAgentStartOffset').value,end:$('assetAgentEndOffset').value};}if(view.scope.mode==='function')view.scope.function=$('assetAgentFunction').value;
    const run=view.state?.run,resume=run?.state==='paused'&&!view.newPass;
    const result=await api('/api/rom-explorer/discovery',{method:'POST',body:{slug,action:resume?'resume':'prepare',run:resume||run?.state==='active'?run.id:'',scope:view.scope}});
    if(S.slug!==slug||S.tab!=='rom')return;
    LZ.task='assets';LZ.assetSlug=slug;LZ.assetRun=result.run;LZ.assetScope=view.scope;showLaunch();
  }catch(error){if(S.slug===slug&&S.tab==='rom')showJobError(work,error.message);}
  finally{view.busy=false;finish();if(S.slug===slug&&S.tab==='rom'){renderAssetAgents(view);loadAssetAgents();}}
}
async function stopAssetAgents(){
  const slug=S.slug,view=assetAgentView(slug);if(view.busy)return;view.busy=true;view.stopping=true;
  const work=assetAgentLog(view,true),finish=beginWork(work,'Stopping discovery agents',[],'Discovery stopped. Saved proposals and progress are retained.');showJobError(work,'');renderAssetAgents(view);
  try{await api('/api/rom-explorer/discovery',{method:'POST',body:{slug,action:'stop'}});}
  catch(error){if(S.slug===slug&&S.tab==='rom')showJobError(work,error.message);}
  finally{view.busy=false;view.stopping=false;finish();if(S.slug===slug&&S.tab==='rom'){renderAssetAgents(view);loadAssetAgents();}}
}
async function openAssetFinding(identity){
  const slug=S.slug,epoch=++assetReviewEpoch,body=dialog('Review asset finding','<pre data-asset-review-work></pre>','asset-finding'),active=()=>S.slug===slug&&S.dialogKind==='asset-finding'&&assetReviewEpoch===epoch;
  const log=body.querySelector('[data-asset-review-work]'),finish=beginWork(log,'Reading discovery evidence',[]);showJobError(log,'');
  try{
    const item=await api(`/api/rom-explorer/discovery?slug=${encodeURIComponent(slug)}&finding=${encodeURIComponent(identity)}`);if(!active())return;finish();body.classList.add('rom-region-body');
    const proposal=item.outcome==='proposal',pending=item.status==='pending',value=item.body;
    body.innerHTML=`<p>${esc(item.author)} · ${esc(item.model)} · ${esc(item.region)} · ${esc(item.status)}</p>${item.stale?'<p class="err-msg">This finding belongs to an older image or layout. You can retain or reject it, but cannot accept it.</p>':''}${proposal?assetValidationHTML(value.validation,item.critiques||[]):''}<h3>Evidence</h3>${Object.entries(value).filter(([key,v])=>typeof v==='string'&&!['name','kind','confidence'].includes(key)).map(([key,v])=>`<p><b>${esc(key.replaceAll('_',' '))}:</b> ${esc(v)}</p>`).join('')}<p class="small muted">Citations: ${(value.citations||[]).map(c=>`${c.function?esc(c.function)+' at ':''}${hex8(c.offset)}`).join(' · ')||'No proposal citations'}</p>${item.region_detail?romUsageHTML(item.region_detail)+(proposal?romGroupHTML(item.region_detail):''):''}<p class="small muted">Related regions: ${(value.related_regions||[]).map(id=>`<button type="button" class="link-btn" data-rom-destination="${esc(id)}">${esc(id)}</button>`).join(' · ')||'None proposed'}</p>${proposal?`<h3>Review interpretation</h3><p class="small muted">Current label: ${esc(item.annotation.name||'None')} · ${esc(item.annotation.kind||'Automatic type')}. ${esc(item.annotation.notes||'')}</p><label class="field"><span>Accepted name</span><input data-finding-name maxlength="120" value="${esc(value.name)}"></label><label class="field"><span>Type</span><select data-finding-kind>${['unknown','data','strings','pointer table','compressed','image','palette','audio','3d model','font','container','repeated bytes'].map(kind=>`<option value="${kind}"${kind===value.kind?' selected':''}>${kind}</option>`).join('')}</select></label><div class="rom-asset-fields"><label class="field"><span>Proposed ROM start (advisory)</span><input data-finding-start value="${hex8(value.start)}"></label><label class="field"><span>Proposed exclusive end (advisory)</span><input data-finding-end value="${hex8(value.end)}"></label></div><p class="small muted">Boundaries and related regions remain advisory. Accepting changes local notes and labels, never symbols, bytes or matching. A type is applied to the region only when the proposed span matches its entire extent.</p>${item.region_detail?romAssetHTML({...item.region_detail,annotation:{preview_settings:value.preview_settings,model_settings:value.model_settings}}).replace('Save notes below to retain these preview settings.','Select the layout checkboxes below to retain settings when accepting.'):''}<label class="check"><input type="checkbox" data-finding-use-preview ${Object.keys(value.preview_settings||{}).length?'checked':''}> Save preview settings above</label><label class="check"><input type="checkbox" data-finding-use-model ${Object.keys(value.model_settings||{}).length?'checked':''}> Save geometry settings above</label><details><summary>Edit evidence and format interpretation</summary><label class="field"><span>Evidence</span><textarea data-finding-evidence maxlength="4000">${esc(value.evidence)}</textarea></label>${[['counter','Counter-evidence'],['uncertainty','Remaining uncertainty'],['format_notes','Format details']].map(([key,label])=>`<label class="field"><span>${label}</span><textarea data-finding-${key} maxlength="4000">${esc(value[key]||'')}</textarea></label>`).join('')}<label class="field"><span>Evidence strength</span><select data-finding-confidence>${['strong','tentative','unclear'].map(c=>`<option value="${c}"${c===value.confidence?' selected':''}>${c}</option>`).join('')}</select></label></details><label class="field"><span>Review feedback (optional)</span><textarea data-finding-feedback maxlength="2000"></textarea></label>${pending?`<button type="button" class="primary small-btn" data-finding-accept ${item.stale?'disabled':''}>Accept interpretation</button> <button type="button" class="ghost small-btn" data-finding-reject>Reject proposal</button>`:''}`:''}${proposal&&!item.stale?'<button type="button" class="ghost small-btn" data-finding-similar>Find similar regions</button><div data-finding-similar-results></div>':''}<pre data-finding-work></pre>`;
    if(item.region_detail){setupRomGroup(body,item.region_detail);if(proposal){setupRomAsset(body,item.region_detail,slug,item.image_hash,active);setupRomDiscovery(body,item.region_detail,slug,item.image_hash,active);}body.querySelectorAll('[data-rom-function]').forEach(b=>b.onclick=()=>{closeDialog();openFn(b.dataset.romFunction);});body.querySelectorAll('[data-rom-destination]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.romDestination));}
    const similarButton=body.querySelector('[data-finding-similar]');
    if(similarButton)similarButton.onclick=async()=>{
      if(body._romAssetBusy||!active())return;body._romAssetBusy=true;
      const work=body.querySelector('[data-finding-work]'),done=beginWork(work,'Comparing data regions',[similarButton],'Region comparison ready.');showJobError(work,'');
      try{const result=await api('/api/rom-explorer/discovery',{method:'POST',body:{slug,action:'similar',finding:identity,edits:{page:body._similarPage||0}}});if(!active())return;
        const target=body.querySelector('[data-finding-similar-results]');target.innerHTML=`<p class="small muted">${esc(result.note)} ${result.total} candidates from ${result.examined} data regions.</p>${result.matches.map(m=>`<p><button type="button" class="link-btn" data-similar-region="${esc(m.region)}">${esc(m.region)}</button> · ${esc(m.kind)}<br>${m.reasons.map(esc).join(' · ')}</p>`).join('')}${result.total>50?'<button type="button" class="ghost small-btn" data-similar-next>Next page</button>':''}`;
        target.querySelectorAll('[data-similar-region]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.similarRegion));const next=target.querySelector('[data-similar-next]');if(next)next.onclick=()=>{body._similarPage=((body._similarPage||0)+1)*50>=result.total?0:(body._similarPage||0)+1;similarButton.click();};
      }catch(error){if(active())showJobError(work,error.message);}finally{if(active())body._romAssetBusy=false;done();}
    };
    if(!proposal||!pending)return;
    const review=async action=>{
      if(body._romAssetBusy||!active())return;body._romAssetBusy=true;
      const work=body.querySelector('[data-finding-work]'),done=beginWork(work,action==='accept'?'Saving accepted interpretation':'Rejecting proposal',[...body.querySelectorAll('input,select,button,textarea')],'Review saved.');showJobError(work,'');
      try{
        const edits={name:body.querySelector('[data-finding-name]').value,kind:body.querySelector('[data-finding-kind]').value,start:body.querySelector('[data-finding-start]').value,end:body.querySelector('[data-finding-end]').value,evidence:body.querySelector('[data-finding-evidence]').value,counter:body.querySelector('[data-finding-counter]').value,uncertainty:body.querySelector('[data-finding-uncertainty]').value,format_notes:body.querySelector('[data-finding-format_notes]').value,confidence:body.querySelector('[data-finding-confidence]').value};
        if(item.region_detail){edits.preview_settings=body.querySelector('[data-finding-use-preview]').checked?romAssetOptions(body):{};edits.model_settings=body.querySelector('[data-finding-use-model]').checked?romModelOptions(body):{};}
        await api('/api/rom-explorer/discovery',{method:'POST',body:{slug,action,finding:identity,annotation_version:item.annotation_version,edits,feedback:body.querySelector('[data-finding-feedback]').value}});
        if(active()){closeDialog();await loadAssetAgents();if(S.slug===slug&&S.tab==='rom'){const report=await api('/api/rom-explorer?slug='+encodeURIComponent(slug));if(S.slug===slug&&S.tab==='rom'){romView().report=report;renderRomExplorer();}}}
      }catch(error){if(active())showJobError(work,error.message);}finally{if(active())body._romAssetBusy=false;done();}
    };
    body.querySelector('[data-finding-accept]').onclick=()=>review('accept');body.querySelector('[data-finding-reject]').onclick=()=>review('reject');
  }catch(error){if(active())showJobError(log,error.message);}finally{finish();}
}
$('assetAgentStart').onclick=chooseAssetAgents;$('assetAgentStop').onclick=stopAssetAgents;
$('assetAgentScope').onchange=()=>{const view=assetAgentView();view.scope={mode:$('assetAgentScope').value};if(view.scope.mode==='function')view.scope.function=romView().function||S.fn||'';if(view.scope.mode==='range'&&romView().zoom){view.scope.start=romView().zoom[0];view.scope.end=romView().zoom[1];}renderAssetAgents(view);};
for(const [id,key] of [['assetAgentStartOffset','start'],['assetAgentEndOffset','end'],['assetAgentFunction','function']])$(id).oninput=()=>{assetAgentView().scope[key]=$(id).value;};
for(const [id,delta] of [['assetAgentPrevious',-1],['assetAgentNext',1]])$(id).onclick=()=>{const view=assetAgentView();view.page=Math.max(0,view.page+delta);loadAssetAgents();};

$('assetAgentNewPass').onclick=()=>{const view=assetAgentView();view.newPass=true;view.scope={mode:'unknown'};renderAssetAgents(view);};

function assetValidationHTML(validation,critiques){
  return `<div class="asset-validation"><h3>Automatic checks</h3>${validation?`<p class="small muted">${esc(validation.note)}</p>${validation.checks.map(c=>`<p><b>${esc(c.check)} · ${esc(c.status)}</b><br>${esc(assetCheckEvidence(c.evidence))}</p>`).join('')}`:'<p>No byte validation recorded.</p>'}<h3>Independent agent review</h3>${critiques.length?critiques.map(c=>`<p><b>${esc(c.body.verdict)}</b> · ${esc(c.author)} · ${esc(c.model)}${c.body.same_model?' · Same model, different worker':''}<br>${esc(c.body.evidence)}</p>`).join(''):'<p class="small muted">Awaiting a different worker. Add another discovery agent to challenge this proposal. You can still make your own acceptance decision.</p>'}</div>`;
}

function assetCheckEvidence(value){
  if(value===null||value===undefined)return 'No recorded evidence.';
  if(typeof value!=='object')return String(value);
  if(Array.isArray(value))return value.length?value.map(assetCheckEvidence).join(' · '):'No recorded evidence.';
  return Object.entries(value).map(([key,v])=>key.replaceAll('_',' ')+': '+assetCheckEvidence(v)).join(' · ');
}
