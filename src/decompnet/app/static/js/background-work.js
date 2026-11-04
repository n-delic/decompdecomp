/* One read at a time, only while visible. Inventory requests never load project overviews. */
let backgroundReading=false, backgroundReady=false, backgroundThreadDetails=[];
async function showBackgroundWork(){
  show('backgroundPage');
  backgroundReady=false;
  await readBackgroundWork();
}
function renderBackgroundWork(data){
  if(!backgroundReady)$('backgroundLimit').value=data.worker_setting || 0;
  $('backgroundCpu').textContent=data.cpu_percent==null ? 'Measuring…' : `${data.cpu_percent.toFixed(1)}%`;
  $('backgroundThreads').textContent=data.threads.length.toLocaleString();
  $('backgroundCount').textContent=`— ${data.items.length} active jobs & services`;
  $('backgroundCpuNote').textContent=data.cpu_note;
  backgroundThreadDetails=mergeBackgroundActivity(data);
  renderBackgroundActivity();
}
function mergeBackgroundActivity(data){
  const threads=data.thread_details || data.threads.map(name=>({name,role:'Other',purpose:'Thread details unavailable; update the app.',cpu_state:'unavailable'}));
  const used=new Set(), rows=[];
  for(const item of data.items){
    const associated=threads.filter(t=>t.native_id!=null && (item.thread_ids || []).includes(t.native_id) && !used.has(t.native_id));
    associated.forEach(t=>used.add(t.native_id));
    const complete=associated.length && associated.every(t=>t.cpu_percent!=null);
    rows.push({...item,role:item.title,threads:associated,cpu_percent:complete ? associated.reduce((n,t)=>n+t.cpu_percent,0) : null,
      cpu_state:complete ? 'measured' : associated.some(t=>t.cpu_state==='measuring') ? 'measuring' : 'unavailable'});
  }
  for(const thread of threads){
    if(thread.native_id!=null && used.has(thread.native_id))continue;
    rows.push({title:thread.name,role:thread.role,detail:thread.purpose,threads:[thread],cpu_percent:thread.cpu_percent,cpu_state:thread.cpu_state,state:'thread',stoppable:false,threadOnly:true});
  }
  return rows;
}
function renderBackgroundActivity(){
  const sort=$('backgroundThreadSort').value;
  const rows=[...backgroundThreadDetails].sort((a,b)=>sort==='name' ? a.title.localeCompare(b.title) : sort==='role' ? a.role.localeCompare(b.role) || a.title.localeCompare(b.title) : (b.cpu_percent ?? -1)-(a.cpu_percent ?? -1) || a.title.localeCompare(b.title));
  $('backgroundRows').innerHTML=rows.length ? `<div class="background-table"><table><thead><tr><th>Work / thread</th><th>Purpose &amp; threads</th><th>Project</th><th>Status</th><th>CPU</th><th>Action</th></tr></thead><tbody>${rows.map((item,index)=>`<tr><td><b>${esc(item.title)}</b></td><td>${item.threadOnly ? `<b>${esc(item.role)}</b>` : ''}<div class="small muted">${esc(item.detail)}</div>${item.threads.map(t=>`<div class="small mono">${item.threadOnly ? '' : esc(t.name)+' · '}ID ${esc(String(t.native_id ?? 'unavailable'))}</div>${!item.threadOnly && t.purpose ? `<div class="small muted">${esc(t.purpose)}</div>` : ''}`).join('')}</td><td>${item.project ? `<a href="#/p/${encodeURIComponent(item.project)}/settings">${esc(item.project)}</a>` : 'App'}</td><td>${esc(item.state)}</td><td class="thread-cpu">${item.cpu_percent!=null ? `${item.cpu_percent.toFixed(1)}%` : item.cpu_state==='measuring' ? 'Measuring…' : 'Unavailable'}${!item.threadOnly && item.threads.length ? '<div class="small muted">Listed threads</div>' : ''}</td><td>${item.stoppable ? `<button class="ghost small-btn" type="button" data-stop-background="${index}"${item.state==='stopping' ? ' disabled' : ''}>${item.state==='stopping' ? 'Stopping…' : 'Stop'}</button>` : item.threadOnly ? '<span class="small muted">Read only</span>' : (item.project ? `<a class="small" href="#/p/${encodeURIComponent(item.project)}/settings">Project controls</a>` : '<span class="small muted">Read only</span>')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No background work or Python-managed threads are visible.</p>';
  $('backgroundRows').querySelectorAll('[data-stop-background]').forEach(button=>{
    button.onclick=async()=>{
      const item=rows[Number(button.dataset.stopBackground)];button.disabled=true;
      const finish=beginWork($('backgroundWorkStatus'),'Requesting stop',[],'Stop requested. Saved work is kept.');
      try{await api('/api/background-work',{method:'POST',body:{kind:item.kind,key:item.key}});await readBackgroundWork();}
      catch(error){showJobError($('backgroundWorkStatus'),error.message);button.disabled=false;}
      finally{finish();}
    };
  });
}

async function readBackgroundWork(){
  if(backgroundReading || $('backgroundPage').hidden || document.hidden)return;
  backgroundReading=true;
  const finish=backgroundReady ? ()=>{} : beginWork($('backgroundWorkStatus'),'Reading background work',[],'Activity refreshed.');
  try{
    const data=await api('/api/background-work');
    if(!$('backgroundPage').hidden){renderBackgroundWork(data);backgroundReady=true;}
  }catch(error){showJobError($('backgroundWorkStatus'),error.message);}
  finally{backgroundReading=false;finish();}
}
$('backgroundRefresh').onclick=readBackgroundWork;
$('backgroundLimitSave').onclick=async()=>{
  const value=Number($('backgroundLimit').value);
  if(!Number.isInteger(value) || value<0){showJobError($('backgroundWorkStatus'),'Enter a whole number of threads, or 0 for automatic.');return;}
  const finish=beginWork($('backgroundWorkStatus'),'Updating background limit',[$('backgroundLimitSave')],'Limit saved; already queued work finishes first.');
  try{await api('/api/settings',{method:'POST',body:{worker_threads:value}});await readBackgroundWork();}
  catch(error){showJobError($('backgroundWorkStatus'),error.message);}
  finally{finish();}
};
setInterval(readBackgroundWork,3000);

$('backgroundThreadSort').onchange=renderBackgroundActivity;
