/* Display-only grouping: argument order and compiler identity remain significant. */
function compilerPartGroups(parts, mode = "settings", sort = "count") {
  const key = (p) => mode === "range" ? JSON.stringify([p.start, p.end, p.file || ""])
    : mode === "family" ? p.family || p.compiler
    : JSON.stringify([p.toolchain || p.compiler, p.flags || ""]);
  const groups = new Map();
  for (const part of parts) {
    const id = key(part);
    if (!groups.has(id)) groups.set(id, {id, compiler: part.compiler, family: part.family || part.compiler, flags: part.flags || "", parts: []});
    groups.get(id).parts.push(part);
  }
  const direction = sort === "address-desc" ? -1 : 1;
  const statusRank = p => ({confirmed: 0, checking: 1, waiting: 2, quirk: 3})[p.check] ?? 4;
  const result = [...groups.values()];
  for (const group of result) {
    group.parts.sort((a, b) => (sort === "status" ? statusRank(a) - statusRank(b) : 0)
      || direction * (a.start - b.start) || direction * (a.end - b.end));
    group.statusRank = Math.min(...group.parts.map(statusRank));
    group.start = Math.min(...group.parts.map(p => p.start));
    group.functions = group.parts.every(p => Number.isFinite(p.functions)) ? group.parts.reduce((n, p) => n + p.functions, 0) : null;
    group.default = group.parts.some(p => p.default);
    group.profiles = new Set(group.parts.map(p => JSON.stringify([p.toolchain || p.compiler, p.flags || ""]))).size;
  }
  return result.sort((a, b) => Number(b.default) - Number(a.default) || (sort === "count" ? b.parts.length - a.parts.length
    : sort === "status" ? a.statusRank - b.statusRank
      : sort === "compiler" ? (mode === "family" ? a.family.localeCompare(b.family) : a.compiler.localeCompare(b.compiler)) : direction * (a.start - b.start)) || a.start - b.start);
}
