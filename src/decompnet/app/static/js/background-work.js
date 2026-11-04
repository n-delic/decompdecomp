/* One read at a time, only while visible. Inventory requests never load project overviews. */
let backgroundReading=false, backgroundReady=false;
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
  $('backgroundThreadNames').textContent=data.threads.join('\n');
  $('backgroundRows').innerHTML=data.items.length ? `<div class="background-table"><table><thead><tr><th>Work</th><th>Project</th><th>Status</th><th>Action</th></tr></thead><tbody>${data.items.map((item,index)=>`<tr><td><b>${esc(item.title)}</b><div class="small muted">${esc(item.detail)}</div></td><td>${item.project ? `<a href="#/p/${encodeURIComponent(item.project)}/settings">${esc(item.project)}</a>` : 'App'}</td><td>${esc(item.state)}</td><td>${item.stoppable ? `<button class="ghost small-btn" type="button" data-stop-background="${index}"${item.state==='stopping' ? ' disabled' : ''}>${item.state==='stopping' ? 'Stopping…' : 'Stop'}</button>` : (item.project ? `<a class="small" href="#/p/${encodeURIComponent(item.project)}/settings">Project controls</a>` : '<span class="small muted">Read only</span>')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No tracked background work is active.</p>';
  $('backgroundRows').querySelectorAll('[data-stop-background]').forEach(button=>{
    button.onclick=async()=>{
      const item=data.items[Number(button.dataset.stopBackground)];button.disabled=true;
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
  const finish=backgroundReady ? ()=>{} : beginWork($('backgroundWorkStatus'),'Reading background work');
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
