/* Database transcript retention uses the shared cancellable job/progress controls. */
let databaseCleanupJob=null;
function databaseCleanupOptions() {
  return {removed:$("databaseCleanupRemoved").checked,days:$("databaseCleanupAge").value===''?null:Number($("databaseCleanupAge").value)*Number($("databaseCleanupUnit").value),automatic:$("databaseCleanupAutomatic").checked};
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
  // Round legacy fixed dates up so conversion does not widen the deletion scope.
  const days=data.options.days ?? (data.options.before ? Math.max(1,Math.ceil((Date.now()-Date.parse(data.options.before+'T00:00:00Z'))/86400000)) : null);
  $("databaseCleanupUnit").value=days && days%7===0?'7':'1';
  $("databaseCleanupAge").value=days===null?'':days/Number($("databaseCleanupUnit").value);
  $("databaseCleanupAutomatic").checked=data.options.automatic;
  renderDatabaseCleanup(data);
  if(data.options.before)$("databaseCleanupEstimate").textContent+=' Save these rules to replace the previous fixed date with the displayed rolling age.';
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
  const controls=['databaseCleanupStart','databaseCleanupPreview','databaseCleanupRemoved','databaseCleanupAge','databaseCleanupUnit','databaseCleanupAutomatic','databaseCleanupSave'].map($);
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
$("databaseCleanupAge").onchange=$("databaseCleanupUnit").onchange=()=>{
  $("databaseCleanupAutomatic").checked=Boolean($("databaseCleanupAge").value);
  $("databaseCleanupEstimate").textContent='Rules changed. Preview to check eligible transcripts, then save or clean up now.';
};

let databaseStorageJob=null;
async function loadDatabaseStorage() {
  if(databaseStorageJob)return;
  const data=await api('/api/database-storage');
  const size=n=>`${(Number(n)/1048576).toLocaleString(undefined,{maximumFractionDigits:1})} MiB`;
  $('databaseStorageSize').textContent=`— ${size(data.stored_bytes)} shared content`;
  $('databaseStorageEstimate').textContent=`${size(data.logical_bytes)} of shared text stored in ${size(data.stored_bytes)}. ${size(data.free_bytes)} reusable inside the database.`;
  $('repairEvidenceEnabled').checked=data.repair_evidence.enabled;
  $('repairEvidenceAutomatic').value=data.repair_evidence.automatic_limit;
  $('repairEvidenceManual').value=data.repair_evidence.manual_limit;
  updateRepairEvidenceFields();
  $('databaseStorageDays').value=data.retention.days;
  $('databaseStorageAutomatic').checked=data.retention.automatic;
}
async function runDatabaseStorage(action) {
  if(databaseStorageJob)return;
  const log=$('databaseStorageLog'),stop=$('databaseStorageStop');
  const controls=['Pack','Compact','Save','Prune','Days','Automatic'].map(x=>$('databaseStorage'+x));
  const done=beginWork(log,action==='pack'?'Compressing saved history':action==='save'?'Saving retention rules':'Cleaning old unsuccessful source',controls,'Storage maintenance complete');
  try {
    const result=await api('/api/database-storage',{method:'POST',body:{action,days:Number($('databaseStorageDays').value),automatic:$('databaseStorageAutomatic').checked}});
    if(result.job){databaseStorageJob=result.job;stop.hidden=false;await followJob(result.job,log,(_lines,state)=>updateWorkProgress(log,state.progress));}
  }catch(error){showJobError(log,error.message);}
  finally{databaseStorageJob=null;stop.hidden=true;stop.disabled=false;done();}
  try{await loadDatabaseStorage();}catch(error){showJobError(log,error.message);}
}
for(const action of ['pack','save','prune'])$('databaseStorage'+action[0].toUpperCase()+action.slice(1)).onclick=()=>runDatabaseStorage(action);
$('databaseStorageStop').onclick=async()=>{
  if(!databaseStorageJob)return;
  $('databaseStorageStop').disabled=true;
  try{await api('/api/job/cancel',{method:'POST',body:{id:databaseStorageJob}});}
  catch(error){$('databaseStorageStop').disabled=false;showJobError($('databaseStorageLog'),error.message);}
};

$('databaseStorageCompact').onclick=()=>{$('headerStoragePanel').open=true;$('headerStorageCompact').click();};

function updateRepairEvidenceFields(){
  for(const id of ['repairEvidenceAutomatic','repairEvidenceManual'])$(id).disabled=!$('repairEvidenceEnabled').checked;
}
$('repairEvidenceEnabled').onchange=updateRepairEvidenceFields;
$('repairEvidenceSave').onclick=async()=>{
  const log=$('repairEvidenceLog');
  const done=beginWork(log,'Saving repair evidence settings',[$('repairEvidenceSave'),$('repairEvidenceEnabled'),$('repairEvidenceAutomatic'),$('repairEvidenceManual')],'Repair evidence settings saved');
  try{
    await api('/api/database-storage',{method:'POST',body:{action:'save-repair-evidence',enabled:$('repairEvidenceEnabled').checked,automatic_limit:Number($('repairEvidenceAutomatic').value),manual_limit:Number($('repairEvidenceManual').value)}});
  }catch(error){showJobError(log,error.message);}
  finally{done();updateRepairEvidenceFields();}
};
