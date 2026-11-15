/* Tool schema cards shared by transcripts, MCP setup and tool controls. */
function toolCatalog(tools, options = {}) {
  const catalog = document.createElement("div"); catalog.className = "tool-catalog transcript-tools" + (options.editable ? " tool-controls-catalog" : "");
  const heading = document.createElement("p"); heading.className = "small muted";
  heading.textContent = `${tools.length} tools`; catalog.append(heading);
  for (const item of tools) {
    const tool = item.function || item, schema = tool.input_schema || tool.inputSchema || tool.parameters || {};
    const card = document.createElement("details"); card.className = "transcript-tool";
    const summary = document.createElement("summary"), name = document.createElement("code"), description = document.createElement("p");
    name.textContent = tool.name; summary.append(name); description.textContent = tool.description || "No description provided.";
    if (options.editable) {
      const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.checked = item.enabled !== false;
      toggle.disabled = !!item.required || (!!options.helper && item.global_disabled);
      toggle.setAttribute('aria-label', `Enable ${tool.name}`);
      toggle.onclick = event => event.stopPropagation();
      toggle.onchange = () => options.onToggle(tool.name, toggle.checked, toggle);
      summary.append(toggle);
    }
    let usageDetail = null;
    if (item.calls != null) {
      const count = document.createElement('span'); count.className = 'small muted';
      count.textContent = `${item.calls.toLocaleString()}${options.editable ? ' calls' : ' recorded calls'}${item.required ? ' · required' : options.helper && item.global_disabled ? ' · disabled globally' : ''}`;
      summary.append(count);
      if (item.task_calls || item.schema_chars != null) {
        const detail = document.createElement('p'); detail.className = 'small muted';
        detail.textContent = [...Object.entries(item.task_calls || {}).map(([task,n])=>`${task}: ${n.toLocaleString()} calls`),
          item.schema_chars == null ? '' : `${item.schema_chars.toLocaleString()} tool-definition characters`].filter(Boolean).join(' · ');
        usageDetail = detail;
      }
    }
    card.append(summary, description);
    if (usageDetail) card.append(usageDetail);
    const properties = schema.properties;
    if (properties && typeof properties === "object" && Object.keys(properties).length) {
      const list = document.createElement("dl"); list.className = "transcript-tool-params";
      for (const [key, value] of Object.entries(properties)) {
        const param = value && typeof value === "object" ? value : {};
        const term = document.createElement("dt"), detail = document.createElement("dd");
        term.textContent = `${key}${Array.isArray(schema.required) && schema.required.includes(key) ? " · required" : " · optional"}`;
        const type = Array.isArray(param.type) ? param.type.join(" / ") : param.type || (param.enum ? "enum" : "value");
        detail.textContent = [type, param.description, param.enum ? `Choices: ${JSON.stringify(param.enum)}` : "", Object.hasOwn(param, "default") ? `Default: ${JSON.stringify(param.default)}` : ""].filter(Boolean).join(" · ");
        list.append(term, detail);
      }
      card.append(list);
    } else {
      const empty = document.createElement("p"); empty.className = "small muted";
      empty.textContent = "No named parameters."; card.append(empty);
    }
    catalog.append(card);
  }
  return catalog;
}
