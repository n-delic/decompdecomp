/* Optional tool policy uses the same catalog as the historical transcript and MCP setup. */
async function loadToolSettings(host, log, helper = '') {
  const done = beginWork(log, 'Loading agent tools');
  try {
    const info = await api(`/api/agent-tools${helper ? `?helper=${encodeURIComponent(helper)}` : ''}`);
    if (!host.isConnected) return;
    const context=()=>{if(!helper)disclosureContext('agToolsContext', `${info.tools.filter(t=>t.enabled!==false).length} / ${info.tools.length} tools enabled`);};
    context();
    host.replaceChildren(toolCatalog([...info.tools].sort((a,b)=>a.calls-b.calls || a.name.localeCompare(b.name)), {editable:true, helper, onToggle:async(name, enabled, toggle)=>{
      log.textContent=''; log.hidden=true;
      const finish = beginWork(log, 'Saving tool settings', [toggle]);
      try {
        await api('/api/agent-tools',{method:'POST',body:{helper,name,enabled}});
        const tool=info.tools.find(t=>t.name===name);if(tool)tool.enabled=enabled;context();
        toast('Tool settings saved. Start a new session or reconnect MCP to refresh the tool list.','match');
      } catch(error) { toggle.checked=!enabled; showJobError(log,error.message); }
      finally { finish(); }
    }}));
    if (info.presets?.length) {
      const controls = document.createElement('div'); controls.className = 'row tool-preset-controls';
      const label = document.createElement('label'); label.className = 'field';
      const title = document.createElement('span'); title.textContent = 'Tool preset';
      const select = document.createElement('select');
      for (const preset of info.presets) {
        const option = document.createElement('option'); option.value = preset.id;
        option.textContent = `${preset.label} · ${preset.tools} tools`; select.append(option);
      }
      const apply = document.createElement('button'); apply.type = 'button'; apply.className = 'ghost small-btn'; apply.textContent = 'Apply preset';
      label.append(title, select); controls.append(label, apply);
      apply.onclick = async () => {
        const finish = beginWork(log, 'Saving tool preset', [apply, select]);
        try {
          await api('/api/agent-tools', {method:'POST', body:{helper, preset:select.value}});
          await loadToolSettings(host, log, helper);
          toast('Preset saved. It replaces optional tool toggles for this scope. Refresh at the next session or MCP reconnect.');
        } catch(error) { showJobError(log, error.message); }
        finally { finish(); }
      };
      const summary = document.createElement('p'); summary.className = 'small muted';
      const enabled = info.tools.filter(t => t.enabled !== false);
      summary.textContent = `${enabled.length} enabled tools · ${enabled.reduce((n,t)=>n+(t.schema_chars || 0),0).toLocaleString()} tool-definition characters. Presets replace optional tool toggles; global restrictions still apply.`;
      host.prepend(controls, summary);
    }
    if (!helper) $('agentToolCoverage').textContent='Least used first. ' + info.coverage;
  } catch(error) { showJobError(log,error.message); }
  finally { done(); }
}
function loadGlobalAgentTools() { $('agentToolsRefresh').onclick=loadGlobalAgentTools; return loadToolSettings($('agentToolCatalog'),$('agentToolLog')); }
async function showAgentTools(session) {
  if (!session) return;
  const panel=dialog(`Tools · ${session.label}`, '<p class="small muted">Disable additional optional tools for this agent. Counts are across all agents on this computer. Globally disabled tools and core tools cannot be changed here. Tool lists refresh at the next session or MCP reconnect.</p><div data-tool-catalog></div><pre data-tool-log hidden></pre><button data-close-tools type="button">Close</button>');
  panel.querySelector('[data-close-tools]').onclick=closeDialog;
  await loadToolSettings(panel.querySelector('[data-tool-catalog]'),panel.querySelector('[data-tool-log]'),session.id);
}
