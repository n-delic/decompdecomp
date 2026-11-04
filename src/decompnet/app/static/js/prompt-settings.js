/* Prompt overrides are explicit; unchecked fields inherit the next lower scope. */
async function loadPromptSettings(host, log, helper = '') {
  const done=beginWork(log,'Loading prompt settings');
  try {
    const info=await api(`/api/agent-prompts${helper ? `?helper=${encodeURIComponent(helper)}` : ''}`);
    if (!host.isConnected) return;
    if (!helper) { const count=info.fields.filter(f=>f.custom).length; disclosureContext('agPromptsContext', count ? `${count} custom override${count===1 ? '' : 's'}` : 'Built-in defaults'); }
    const fields=info.fields.map(field=>{
      const box=document.createElement('div'); box.className='prompt-setting';
      const label=document.createElement('label'), check=document.createElement('input'), title=document.createElement('span');
      check.type='checkbox'; check.checked=field.custom; title.textContent=`Override ${field.label}`;
      label.className='check'; label.append(check,title);
      const text=document.createElement('textarea'); text.value=field.value; text.rows=12; text.disabled=!field.custom;
      text.setAttribute('aria-label',field.label); text.spellcheck=false;
      check.onchange=()=>{text.disabled=!check.checked;};
      const hint=document.createElement('p'); hint.className='small muted';
      hint.textContent=field.placeholders.length ? `Placeholders: ${field.placeholders.map(key=>'{'+key+'}').join(', ')}. Write literal braces as {{ and }}.` : 'Literal app instructions; no template placeholders.';
      box.append(label,hint,text);return {box,check,text,key:field.key};
    });
    const controls=document.createElement('div'); controls.className='row';
    const save=document.createElement('button'),preview=document.createElement('button'),reset=document.createElement('button');
    save.textContent='Save prompts';save.className='primary';preview.textContent='Preview';reset.textContent=helper?'Use global defaults':'Restore built-in defaults';
    for(const button of [save,preview,reset]) button.type='button';
    const result=document.createElement('pre');result.className='joblog';result.hidden=true;
    const values=()=>Object.fromEntries(fields.filter(f=>f.check.checked).map(f=>[f.key,f.text.value]));
    async function submit(action,overrides) {
      const finish=beginWork(log,action==='preview'?'Preparing prompt preview':'Saving prompts',[save,preview,reset]);
      log.textContent='';log.hidden=true;
      try {
        const response=await api('/api/agent-prompts',{method:'POST',body:{helper,action,overrides}});
        if(!host.isConnected)return;
        if(action==='preview'){result.hidden=false;result.textContent=`APP INSTRUCTIONS\n${response.instructions}\n\nINITIAL PROMPT${helper?'':' TEMPLATES'}\n${response.initial}`;}
        else {toast('Prompts saved. Applies to the next session.','match');await loadPromptSettings(host,log,helper);}
      } catch(error){showJobError(log,error.message);}
      finally{finish();}
    }
    save.onclick=()=>submit('save',values());preview.onclick=()=>submit('preview',values());reset.onclick=()=>submit('save',{});
    controls.append(save,preview,reset);host.replaceChildren(...fields.map(f=>f.box),controls,result);
  } catch(error){showJobError(log,error.message);}
  finally{done();}
}
function loadGlobalPrompts(){return loadPromptSettings($('globalPromptSettings'),$('globalPromptLog'));}
async function showAgentPrompts(session){
  if(!session)return;
  const panel=dialog(`Prompts · ${session.label}`,'<p class="small muted">Overrides global defaults for this agent’s next session. Unchecked fields inherit global defaults. Preview includes the current project and continuation context. App instructions are the decompdecomp instructions, not the CLI provider’s internal system prompt.</p><div data-prompt-settings></div><pre data-prompt-log hidden></pre><button data-close-prompts type="button">Close</button>');
  panel.querySelector('[data-close-prompts]').onclick=closeDialog;
  await loadPromptSettings(panel.querySelector('[data-prompt-settings]'),panel.querySelector('[data-prompt-log]'),session.id);
}
