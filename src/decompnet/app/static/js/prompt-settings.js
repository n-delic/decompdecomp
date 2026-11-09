// Keep progress visible while requests run, without leaving a generic completion message.
function promptSettingsWork(log, label, controls = []) {
  const finish = beginWork(log, label, controls);
  return () => {
    finish();
    log._workStatus?.remove();
    delete log._workStatus;
  };
}

/* Prompt overrides are explicit; unchecked fields inherit the next lower scope. */
async function loadPromptSettings(host, log, helper = '') {
  const done=promptSettingsWork(log,'Loading prompt settings');
  try {
    const info=await api(`/api/agent-prompts${helper ? `?helper=${encodeURIComponent(helper)}` : ''}`);
    if (!host.isConnected) return;
    const opened=new Set((host._promptFields || []).filter(f=>f.box.open).map(f=>f.key));
    if (!helper) { const count=info.fields.filter(f=>f.custom).length; disclosureContext('agPromptsContext', count ? `${count} custom override${count===1 ? '' : 's'}` : 'Built-in defaults'); }
    const fields=info.fields.map(field=>{
      const box=document.createElement('details'); box.className='panel settings-disclosure prompt-setting'; box.open=opened.has(field.key);
      const summary=document.createElement('summary'); summary.className='panel-head';
      const heading=document.createElement('h3'), status=document.createElement('span');
      heading.textContent=field.label; status.className='prompt-setting-status';
      const body=document.createElement('div'); body.className='prompt-setting-body';
      summary.append(heading,status);
      const label=document.createElement('label'), check=document.createElement('input'), title=document.createElement('span');
      check.type='checkbox'; check.checked=field.custom; title.textContent='Use a custom override';
      label.className='check'; label.append(check,title);
      const text=document.createElement('textarea'); text.value=field.value; text.rows=12; text.disabled=!field.custom;
      text.setAttribute('aria-label',field.label); text.spellcheck=false;
      const update=()=>{text.disabled=!check.checked;status.dataset.custom=String(check.checked);status.textContent=check.checked ? 'Custom' : helper ? 'Global default' : 'Built-in';};
      check.onchange=update;update();
      const hint=document.createElement('p'); hint.className='small muted';
      hint.textContent=field.placeholders.length ? `Placeholders: ${field.placeholders.map(key=>'{'+key+'}').join(', ')}. Write literal braces as {{ and }}.` : 'Literal app instructions; no template placeholders.';
      body.append(label,hint,text);box.append(summary,body);return {box,check,text,key:field.key};
    });
    host._promptFields=fields;
    const controls=document.createElement('div'); controls.className='prompt-setting-actions';
    const save=document.createElement('button'),reset=document.createElement('button');
    save.textContent='Save prompts';save.className='primary';reset.className='ghost';reset.textContent=helper?'Use global defaults':'Restore built-in defaults';
    for(const button of [save,reset]) button.type='button';
    const help=document.createElement('span');help.className='small muted';help.textContent='Changes apply to new sessions.';
    const values=()=>Object.fromEntries(fields.filter(f=>f.check.checked).map(f=>[f.key,f.text.value]));
    async function submit(overrides) {
      const finish=promptSettingsWork(log,'Saving prompts',[save,reset]);
      log.textContent='';log.hidden=true;
      try {
        await api('/api/agent-prompts',{method:'POST',body:{helper,action:'save',overrides}});
        if(!host.isConnected)return;
        toast('Prompts saved. Applies to the next session.','match');await loadPromptSettings(host,log,helper);
      } catch(error){showJobError(log,error.message);}
      finally{finish();}
    }
    save.onclick=()=>submit(values());reset.onclick=()=>submit({});
    controls.append(save,reset,help);host.replaceChildren(...fields.map(f=>f.box),controls);
  } catch(error){showJobError(log,error.message);}
  finally{done();}
}
function loadGlobalPrompts(){return loadPromptSettings($('globalPromptSettings'),$('globalPromptLog'));}
async function showAgentPrompts(session){
  if(!session)return;
  const panel=dialog(`Prompts · ${session.label}`,'<p class="small muted">Overrides global defaults for this agent’s next session. Unchecked fields inherit global defaults. App instructions are the decompdecomp instructions, not the CLI provider’s internal system prompt.</p><div data-prompt-settings></div><pre data-prompt-log hidden></pre><button data-close-prompts type="button">Close</button>');
  panel.querySelector('[data-close-prompts]').onclick=closeDialog;
  await loadPromptSettings(panel.querySelector('[data-prompt-settings]'),panel.querySelector('[data-prompt-log]'),session.id);
}
