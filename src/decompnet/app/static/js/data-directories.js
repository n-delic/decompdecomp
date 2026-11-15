/* Each location is a separate project catalogue. The desktop runtime owns switching. */
let dataDirectoryRequest=0, dataDirectoryBusy=false;
async function renderDataDirectories(){
  const request=++dataDirectoryRequest, root=$('dataDirectories');
  if(!root)return;
  root.innerHTML='<p class="small muted">Loading project directories…</p>';
  try{
    const info=await api('/api/data-directories');
    if(request!==dataDirectoryRequest || $('dataDirectories')!==root)return;
    root.innerHTML=`<div class="field"><span>Projects live in</span>${cmdBox(info.current)}</div>
      <label class="field"><span>Remembered directories</span><select id="dataDirectoryKnown">${info.directories.map(item=>`<option value="${esc(item.path)}"${item.path===info.current ? ' selected' : ''}>${esc(item.path)}${item.available ? '' : ' (unavailable)'}</option>`).join('')}</select></label>
      <label class="field"><span>Project directory</span><span class="pick-row"><input id="dataDirectoryPath" class="mono" value="${esc(info.current)}"><button type="button" class="ghost small-btn" id="dataDirectoryPick">Choose…</button></span></label>
      <p class="small muted">Each directory has its own projects and app state. Switching saves and stops current work, then reopens the app on the selected directory. Existing files stay in place.</p>
      <div class="row"><button type="button" class="primary small-btn" id="dataDirectorySwitch"${info.switchable ? '' : ' disabled'}>Switch directory</button><button type="button" class="ghost small-btn" id="dataDirectoryRemember">Remember directory</button></div>
      ${info.switchable ? '' : '<p class="small muted">Switch directories from the desktop app.</p>'}<pre id="dataDirectoryLog" class="joblog" hidden></pre>`;
    wireCopies(root);
    const field=$('dataDirectoryPath'),known=$('dataDirectoryKnown'),log=$('dataDirectoryLog');
    known.onchange=()=>{field.value=known.value;};
    $('dataDirectoryPick').onclick=async()=>{
      const button=$('dataDirectoryPick');button.disabled=true;
      try{const result=await api('/api/pick',{method:'POST',body:{kind:'folder',title:'Choose project directory'}});if(result.path && $('dataDirectories')===root)field.value=result.path;}
      catch(error){if($('dataDirectories')===root)showJobError(log,error.message);}
      finally{button.disabled=false;}
    };
    async function choose(action){
      if(dataDirectoryBusy)return;
      dataDirectoryBusy=true;
      const controls=[field,known,$('dataDirectoryPick'),$('dataDirectorySwitch'),$('dataDirectoryRemember')];
      const finish=beginWork(log,action==='switch' ? 'Preparing directory switch' : 'Remembering directory',controls,'Directory remembered.');
      showJobError(log,'');
      try{
        const chosen=field.value.trim();
        const state=await api('/api/data-directories',{method:'POST',body:{action,path:chosen}});
        if(action==='switch' && ['queued','restarting'].includes(state.job.state)){
          if($('dataDirectories')===root)log.textContent=state.job.message;
          const error=await followDirectorySwitch(state.job.target);
          if(error && $('dataDirectories')===root)showJobError(log,error);
        }else if($('dataDirectories')===root){dataDirectoryBusy=false;await renderDataDirectories();if($('dataDirectoryPath')){$('dataDirectoryPath').value=state.chosen || chosen;$('dataDirectoryKnown').value=state.chosen || chosen;}}
      }catch(error){if($('dataDirectories')===root)showJobError(log,error.message);}
      finally{dataDirectoryBusy=false;finish();if(!info.switchable && $('dataDirectories')===root && $('dataDirectorySwitch'))$('dataDirectorySwitch').disabled=true;}
    }
    $('dataDirectoryRemember').onclick=()=>choose('remember');
    $('dataDirectorySwitch').onclick=()=>choose('switch');
    if(['queued','restarting'].includes(info.job.state))followDirectorySwitch(info.job.target);
    else if(info.job.state==='error')showJobError(log,info.job.message);
  }catch(error){if(request===dataDirectoryRequest && $('dataDirectories')===root){root.textContent='';showJobError(root,error.message);}}
}
let dataDirectoryFollowing=false;
async function followDirectorySwitch(target){
  if(dataDirectoryFollowing)return;
  dataDirectoryFollowing=true;
  const body=dialog('Switching project directory','<p>Saving current work and reopening the app on the selected directory…</p><pre class="joblog" id="dataDirectorySwitchLog"></pre><button class="ghost" type="button" data-close>Close</button>');
  const log=body.querySelector('#dataDirectorySwitchLog');
  const finish=beginWork(log,'Switching project directory',[],'Directory opened.');
  try{
    for(let count=0;count<180;count++){
      await sleep(1000);
      let state;
      try{state=await api('/api/data-directories');}catch{continue;} // The native window reopens with a new connection identity.
      if(state.current===target){location.hash='#/settings';location.reload();return;}
      log.textContent=state.job.message || 'Waiting for the selected directory to open…';
      if(state.job.state==='error')throw new Error(state.job.message);
    }
    throw new Error('The directory switch has not reconnected. Reopen the desktop app to check its selected directory.');
  }catch(error){showJobError(log,error.message);return error.message;}
  finally{finish();dataDirectoryFollowing=false;}
}
