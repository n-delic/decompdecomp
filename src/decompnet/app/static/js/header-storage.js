/* Shared, exact header history; pruning and compaction are explicit cancellable jobs. */
let headerStorageJob=null;
function headerStorageSize(bytes) {
  return `${(Number(bytes || 0)/1048576).toLocaleString(undefined,{maximumFractionDigits:1})} MiB`;
}
function renderHeaderStorage(data) {
  $('headerStorageContext').textContent=`— ${Number(data.versions || 0).toLocaleString()} versions · ${Number(data.contents || 0).toLocaleString()} shared headers`;
  $('headerStorageLogical').textContent=headerStorageSize(data.logical_bytes);
  $('headerStorageStored').textContent=headerStorageSize(data.stored_bytes);
  if(data.unused_versions==null)$('headerStorageEstimate').textContent='Preview to check which old versions are safe to remove.';
  else $('headerStorageEstimate').textContent=`${Number(data.unused_versions).toLocaleString()} unused versions can be removed. Current and referenced headers are kept.`;
}
async function loadHeaderStorage() {
  if(headerStorageJob)return;
  const data=await api('/api/header-storage');
  if(!headerStorageJob)renderHeaderStorage(data);
}
function closeHeaderStorageMenu(control) {
  const menu=control.closest?.('details');
  if(menu){menu.open=false;menu.querySelector('summary').focus();}
}
$('headerStoragePreview').onclick=async()=>{
  closeHeaderStorageMenu($('headerStoragePreview'));
  const log=$('headerStorageLog');showJobError(log,'');
  const done=beginWork(log,'Checking header references',[$('headerStoragePreview'),$('headerStorageStart'),$('headerStorageCompact')],'Header preview ready');
  try{renderHeaderStorage(await api('/api/header-storage',{method:'POST',body:{action:'preview'}}));}
  catch(error){showJobError(log,error.message);}finally{done();}
};
async function runHeaderStorage(action,existing=null) {
  if(headerStorageJob)return;
  headerStorageJob='starting';
  const log=$('headerStorageLog'),stop=$('headerStorageStop');showJobError(log,'');
  const done=beginWork(log,action.startsWith('compact')?'Preparing database compaction':'Clearing unused C headers',
    ['headerStorageStart','headerStoragePreview','headerStorageCompact','databaseStorageCompact','headerStorageCatalogCompact'].map($),'Database maintenance complete');
  try{
    const job=existing?.id || (await api('/api/header-storage',{method:'POST',body:{action}})).job;
    headerStorageJob=job;stop.hidden=false;
    $('headerStoragePanel').open=true;
    $('headerStorageContext').textContent=action.startsWith('compact')?'— Database compaction running':'— Header cleanup running';
    if(existing?.progress)updateWorkProgress(log,existing.progress);
    const result=await followJob(job,log,(_lines,state)=>updateWorkProgress(log,state.progress));
    if(result?.headers)renderHeaderStorage(result.headers);
    if(result)$('headerStorageEstimate').textContent=action.startsWith('compact')
      ? `Compaction reclaimed ${headerStorageSize(result.reclaimed_bytes)} on disk.`
      : `Removed ${Number(result.removed_versions || 0).toLocaleString()} unused versions and ${Number(result.removed_contents || 0).toLocaleString()} unreferenced contents. Current and referenced headers kept.`;
  }catch(error){showJobError(log,error.message);}
  finally{headerStorageJob=null;stop.hidden=true;stop.disabled=false;done();
    $('databaseStorageCompact').disabled=typeof databaseStorageJob!=='undefined' && Boolean(databaseStorageJob);
    if($('headerStorageContext').textContent.includes('running'))$('headerStorageContext').textContent='— Shared C headers';
  }
}
$('headerStorageStart').onclick=()=>runHeaderStorage('headers');
$('headerStorageCompact').onclick=()=>{closeHeaderStorageMenu($('headerStorageCompact'));return runHeaderStorage('compact');};
$('headerStorageStop').onclick=async()=>{
  if(!headerStorageJob)return;
  $('headerStorageStop').disabled=true;
  try{await api('/api/job/cancel',{method:'POST',body:{id:headerStorageJob}});}
  catch(error){$('headerStorageStop').disabled=false;showJobError($('headerStorageLog'),error.message);}
};

$('headerStorageCatalogCompact').onclick=()=>{closeHeaderStorageMenu($('headerStorageCatalogCompact'));return runHeaderStorage('compact-catalog');};
