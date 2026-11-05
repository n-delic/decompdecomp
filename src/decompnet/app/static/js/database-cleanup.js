/* Database transcript retention uses the shared cancellable job/progress controls. */
let databaseCleanupJob=null;
function databaseCleanupOptions() {
  return {removed:$("databaseCleanupRemoved").checked,before:$("databaseCleanupBefore").value,automatic:$("databaseCleanupAutomatic").checked};
}
function renderDatabaseCleanup(data) {
  const number=n=>Number(n || 0).toLocaleString();
  const size=(data.disk_bytes/1073741824).toLocaleString(undefined,{maximumFractionDigits:2});
  $("databaseCleanupContext").textContent=`— ${size} GiB on disk`;
  $("databaseCleanupEstimate").textContent=data.messages
    ? `${number(data.messages)} transcript records in ${number(data.sessions)} sessions are eligible. Saved work and usage totals are kept.`
    : 'No transcripts match these cleanup rules.';
}
async function loadDatabaseCleanup() {
  if(databaseCleanupJob)return;
  const data=await api('/api/database-cleanup');
  $("databaseCleanupRemoved").checked=data.options.removed;
  $("databaseCleanupBefore").value=data.options.before;
  $("databaseCleanupAutomatic").checked=data.options.automatic;
  $("databaseCleanupBefore").max=new Date().toISOString().slice(0,10);
  renderDatabaseCleanup(data);
}
$("databaseCleanupPreview").onclick=async()=>{
  const log=$("databaseCleanupLog");showJobError(log,'');
  const done=beginWork(log,'Checking eligible transcripts',[$("databaseCleanupPreview"),$("databaseCleanupStart")],'Preview ready');
  try{renderDatabaseCleanup(await api('/api/database-cleanup',{method:'POST',body:{action:'preview',...databaseCleanupOptions()}}));}
  catch(error){showJobError(log,error.message);}finally{done();}
};
$("databaseCleanupStart").onclick=async()=>{
  if(databaseCleanupJob)return;
  const log=$("databaseCleanupLog"),stop=$("databaseCleanupStop");showJobError(log,'');
  const controls=['databaseCleanupStart','databaseCleanupPreview','databaseCleanupRemoved','databaseCleanupBefore','databaseCleanupAutomatic','databaseCleanupSave'].map($);
  const done=beginWork(log,'Clearing old agent transcripts',controls,'Transcript cleanup complete');
  try{
    const {job}=await api('/api/database-cleanup',{method:'POST',body:{action:'start',...databaseCleanupOptions()}});
    databaseCleanupJob=job;stop.hidden=false;
    const result=await followJob(job,log,(_lines,state)=>{
      updateWorkProgress(log,state.progress);
      if(state.progress && log._workStatus)log._workStatus.textContent+=` · ${Number(state.progress.deleted_messages || 0).toLocaleString()} records removed`;
    });
    if(result)$("databaseCleanupEstimate").textContent=`Removed ${Number(result.messages || 0).toLocaleString()} transcript records from ${Number(result.sessions || 0).toLocaleString()} sessions.`;
  }catch(error){showJobError(log,error.message);}
  finally{databaseCleanupJob=null;stop.hidden=true;stop.disabled=false;done();}
};
$("databaseCleanupStop").onclick=async()=>{
  if(!databaseCleanupJob)return;
  $("databaseCleanupStop").disabled=true;
  try{await api('/api/job/cancel',{method:'POST',body:{id:databaseCleanupJob}});}
  catch(error){$("databaseCleanupStop").disabled=false;showJobError($("databaseCleanupLog"),error.message);}
};

$("databaseCleanupSave").onclick=async()=>{
  const log=$("databaseCleanupLog");showJobError(log,'');
  const done=beginWork(log,'Saving cleanup rules',[$("databaseCleanupSave"),$("databaseCleanupStart")],'Cleanup rules saved');
  try{await api('/api/database-cleanup',{method:'POST',body:{action:'save',...databaseCleanupOptions()}});}
  catch(error){showJobError(log,error.message);}finally{done();}
};
$("databaseCleanupBefore").onchange=()=>{
  $("databaseCleanupAutomatic").checked=Boolean($("databaseCleanupBefore").value);
  $("databaseCleanupEstimate").textContent='Rules changed. Preview to check eligible transcripts, then save or clean up now.';
};
