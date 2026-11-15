-- A fresh export (attached as `incoming`, written by hosted_export.write_fresh) merged into a live hosted project
-- (`main`), in one transaction. The Python exporter and the server (gateway/src/hosted.rs, which includes this file)
-- both run it, so a local export and a remote publish merge alike.
-- The project's facts are the owner's. The server's claims, submissions and notes stay, and so does a match or a
-- better score made on the server. Functions gone from the project become 'removed'.
INSERT OR REPLACE INTO main.meta(key, value) SELECT key, value FROM incoming.meta;
DELETE FROM main.contexts;
INSERT INTO main.contexts(unit, version, header) SELECT unit, version, header FROM incoming.contexts;
INSERT INTO main.functions(id, name, unit, vram, rom_offset, size, n_insns, target, relocs, asm, compiler, status,
    best_score, best_source, matched_source, matched_by, matched_at)
SELECT id, name, unit, vram, rom_offset, size, n_insns, target, relocs, asm, compiler, status,
    best_score, best_source, matched_source, matched_by, matched_at FROM incoming.functions WHERE true
ON CONFLICT(id) DO UPDATE SET name=excluded.name, unit=excluded.unit, vram=excluded.vram,
    rom_offset=excluded.rom_offset, size=excluded.size, n_insns=excluded.n_insns, target=excluded.target,
    relocs=excluded.relocs, asm=excluded.asm, compiler=excluded.compiler,
    status=CASE WHEN status='matched' THEN 'matched' ELSE excluded.status END,
    best_source=CASE WHEN best_score IS NOT NULL AND (excluded.best_score IS NULL OR best_score<=excluded.best_score)
        THEN best_source ELSE excluded.best_source END,
    best_score=CASE WHEN best_score IS NOT NULL AND (excluded.best_score IS NULL OR best_score<=excluded.best_score)
        THEN best_score ELSE excluded.best_score END,
    matched_source=COALESCE(matched_source, excluded.matched_source),
    matched_by=COALESCE(matched_by, excluded.matched_by),
    matched_at=COALESCE(matched_at, excluded.matched_at);
UPDATE main.functions SET status='removed'
    WHERE status NOT IN ('matched', 'removed') AND id NOT IN (SELECT id FROM incoming.functions);
