/* Locations belong to one compiled source snapshot, not to the next edit. */
let editorSourceMapping = null;
function sourceLineMarkup(source) {
  const mapped = editorSourceMapping?.source === source ? editorSourceMapping.source_map?.lines || [] : [];
  const lines = new Map(mapped.map(entry => [entry.line, entry]));
  return highlightC(source).split('\n').map((text, index) => {
    const entry = lines.get(index + 1);
    return `<span class="source-line${entry ? ` source-${entry.severity === 'operand' ? 'operand' : 'different'}` : ''}">${text || ' '}</span>`;
  }).join('\n');
}
function paintSourceGutter() {
  const ta = $('fnEdit'), gutter = $('fnGutter'), note = $('fnSourceMapNote');
  const info = editorSourceMapping?.source_map;
  const fresh = editorSourceMapping?.source === ta.value;
  const mapped = new Map((fresh ? info?.lines || [] : []).map(entry => [entry.line, entry]));
  gutter.innerHTML = ta.value.split('\n').map((_, index) => {
    const line = index + 1, entry = mapped.get(line);
    return entry ? `<button type="button" data-source-line="${line}" class="source-${entry.severity === 'operand' ? 'operand' : 'different'}" aria-label="C line ${line}: ${entry.instructions.length} differing instructions">${entry.severity === 'operand' ? '≠' : '!'} ${line}</button>` : `<span>${line}</span>`;
  }).join('');
  gutter.style.transform = `translateY(${-ta.scrollTop}px)`;
  note.textContent = info ? (!fresh ? 'Source changed since this comparison. Compile again to refresh line highlights.' :
    `${info.available ? 'Red: different/extra/moved instruction. Yellow: operands differ. Click a marked line number for details. ' : ''}${info.note}${info.unmapped ? ` ${info.unmapped} differences have no C location.` : ''}`) : 'Compile and compare to show source-line associations when debug information is available.';
  gutter.querySelectorAll('[data-source-line]').forEach(button => { button.onclick = () => {
    const line = Number(button.dataset.sourceLine), entry = mapped.get(line);
    if (!entry || editorSourceMapping?.source !== $('fnEdit').value) return;
    dialog(`C line ${line} · ${entry.instructions.length} differing instructions`,
      `<p class="small muted">Compiler association under optimization; this does not prove this line caused the difference.</p>` +
      entry.instructions.map(ins => `<section class="source-instruction source-${['r','s','i'].includes(ins.marker) ? 'operand' : 'different'}"><p>${esc(MARK_TEXT[ins.marker] || 'Instruction differs')}</p><span class="small muted">TARGET</span><pre class="code">${esc(ins.target || '— no instruction —')}</pre><span class="small muted">COMPILED</span><pre class="code">${esc(ins.compiled || '— no instruction —')}</pre></section>`).join(''));
  }; });
}
function setEditorSourceMapping(data) {
  editorSourceMapping = data || null;
  paintEditor();
}
