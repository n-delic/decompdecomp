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
  const result = [...groups.values()];
  for (const group of result) {
    group.parts.sort((a, b) => direction * (a.start - b.start) || direction * (a.end - b.end));
    group.start = Math.min(...group.parts.map(p => p.start));
    group.functions = group.parts.every(p => Number.isFinite(p.functions)) ? group.parts.reduce((n, p) => n + p.functions, 0) : null;
    group.ranges = group.parts.filter(p => !p.function_only).length;
    group.default = group.parts.some(p => p.default);
    group.profiles = new Set(group.parts.map(p => JSON.stringify([p.toolchain || p.compiler, p.flags || ""]))).size;
  }
  return result.sort((a, b) => Number(b.default) - Number(a.default) || (sort === "count" ? b.ranges - a.ranges : sort === "functions" ? (b.functions ?? -1) - (a.functions ?? -1) : sort === "compiler" ? a.compiler.localeCompare(b.compiler) : direction * (a.start - b.start)) || a.start - b.start);
}

/* Hover help for compiler arguments: each argument is its own element whose title says what it does.
   Unknown arguments get no title rather than a guess. Display only: the flags string is never changed. */
const COMPILER_FLAG_HELP = [
  [/^-fno-schedule-insns2?$/, "Turn off instruction scheduling (reordering instructions to hide delays). -fno-schedule-insns is the pass before register allocation, -fno-schedule-insns2 the pass after it."],
  [/^-fschedule-insns2?$/, "Turn on instruction scheduling (reordering instructions to hide delays). -fschedule-insns runs before register allocation, -fschedule-insns2 after it."],
  [/^-fno-peephole2?$/, "Turn off peephole optimisation, which replaces short instruction sequences with better ones."],
  [/^-fpeephole2?$/, "Turn on peephole optimisation, which replaces short instruction sequences with better ones."],
  [/^-fno-gcse$/, "Turn off global common-subexpression elimination across basic blocks."],
  [/^-fgcse$/, "Turn on global common-subexpression elimination across basic blocks."],
  [/^-fno-cse-follow-jumps$|^-fno-cse-skip-blocks$/, "Make common-subexpression elimination stop at jumps and conditional blocks."],
  [/^-fcse-follow-jumps$|^-fcse-skip-blocks$/, "Let common-subexpression elimination continue past jumps and conditional blocks."],
  [/^-fno-rerun-cse-after-loop$|^-fno-rerun-loop-opt$/, "Skip the extra optimisation pass after loop optimisation."],
  [/^-fno-expensive-optimizations$/, "Turn off the slower optimisation passes."],
  [/^-fexpensive-optimizations$/, "Turn on the slower optimisation passes."],
  [/^-fno-defer-pop$/, "Pop each function call's arguments right away instead of batching the stack adjustment."],
  [/^-fdefer-pop$/, "Batch the stack clean-up of several calls' arguments."],
  [/^-fno-function-cse$/, "Always call functions by their symbol instead of keeping their address in a register."],
  [/^-fforce-addr$/, "Copy memory addresses into registers before using them."],
  [/^-fforce-mem$/, "Copy memory operands into registers before using them."],
  [/^-fno-optimize-sibling-calls$/, "Don't turn a call at the end of a function into a plain jump (tail-call optimisation)."],
  [/^-foptimize-sibling-calls$/, "Turn calls at the end of a function into plain jumps (tail-call optimisation)."],
  [/^-fstrength-reduce$/, "Replace expensive loop arithmetic (multiplies) with cheaper repeated additions."],
  [/^-fno-strength-reduce$/, "Don't replace loop multiplies with cheaper additions."],
  [/^-fthread-jumps$/, "Redirect jumps whose target is another jump straight to the final destination."],
  [/^-fcaller-saves$/, "Allow values to stay in registers across calls by saving and restoring them around each call."],
  [/^-fno-caller-saves$/, "Never keep values in call-clobbered registers across calls."],
  [/^-fno-keep-static-consts$/, "Drop unused static const variables."],
  [/^-fno-gnu-linker$/, "Don't assume the GNU linker, so global constructors are emitted differently."],
  [/^-fverbose-asm$/, "Add explanatory comments to the assembly output; no effect on the code."],
  [/^-fno-ident$/, "Don't write the compiler version string into the object file."],
  [/^-fno-merge-constants$/, "Don't merge identical constants into one."],
  [/^-fshort-enums$/, "Use the smallest integer type that fits each enum."],
  [/^-fno-short-enums$/, "Make every enum a full int."],
  [/^-fpack-struct/, "Pack struct members with no padding."],
  [/^-fvolatile/, "Treat memory accesses through pointers as volatile (not optimised away)."],
  [/^-fno-unit-at-a-time$/, "Compile functions one at a time in source order instead of whole-file."],
  [/^-fno-toplevel-reorder$/, "Keep functions and variables in the order written in the source."],
  [/^-ffunction-sections$/, "Put each function in its own section."],
  [/^-fdata-sections$/, "Put each variable in its own section."],
  [/^-mno-check-zero-division$/, "Don't insert a divide-by-zero check after division."],
  [/^-mcheck-zero-division$/, "Insert a divide-by-zero check after division."],
  [/^-mno-fix-vr4120$/, "Don't apply VR4120 hardware workarounds."],
  [/^-mfix4300$/, "Work around the VR4300 multiply/divide hardware bug."],
  [/^-mno-explicit-relocs$/, "Use assembler macros for address loads instead of explicit %hi/%lo relocations."],
  [/^-mexplicit-relocs$/, "Write address loads as explicit %hi/%lo relocations."],
  [/^-mno-embedded-data$|^-membedded-data$/, "Control whether read-only data goes into the small-data area."],
  [/^-mno-gpopt$/, "Don't use gp-relative addressing for global data."],
  [/^-mgpopt$/, "Use gp-relative addressing for global data in the small-data area."],
  [/^-mno-mips16$/, "Don't generate compressed MIPS16 code."],
  [/^-mabicalls$/, "Generate System V-style position-independent call sequences."],
  [/^-mno-shared$/, "Generate code for a fixed address, not a shared library."],
  [/^-mdivide-breaks$/, "Check for division by zero with a break instruction."],
  [/^-mdivide-traps$/, "Check for division by zero with a trap instruction."],
  [/^-mtune=/, "Tune scheduling for this CPU without limiting which CPUs can run the code."],
  [/^-O0$/, "No optimisation: every variable lives in memory and code follows the source literally."],
  [/^-O1$/, "Basic optimisation: simple register allocation and dead-code removal."],
  [/^-O2$/, "Standard optimisation: scheduling, common-subexpression elimination and loop work. The usual setting for retail games."],
  [/^-O3$/, "Aggressive optimisation: -O2 plus more inlining and loop transformations."],
  [/^-O4$/, "Highest optimisation level in this compiler (CodeWarrior/IDO); includes the most inlining."],
  [/^-Os$/, "Optimise for size rather than speed."],
  [/^-O,?p$/, "CodeWarrior: optimise for performance."],
  [/^-O4,p$/, "CodeWarrior: highest optimisation level, favouring speed."],
  [/^-O4,s$/, "CodeWarrior: highest optimisation level, favouring size."],
  [/^-g[0-3]?$/, "Emit debug information. It can change code layout in some compilers, so it must match the original build."],
  [/^-G\s*0$|^-G0$/, "Never use the small-data (gp-relative) area: every global is addressed with full lui/addiu pairs."],
  [/^-G\s*\d+$|^-G\d+$/, "Small-data threshold in bytes: globals this size or smaller are addressed relative to the gp register."],
  [/^-mips[1-5]$/, "Target MIPS instruction set level (mips3 = 64-bit MIPS III as used by N64 and PS2 era CPUs)."],
  [/^-mips2$/, "Target the MIPS II instruction set (adds branch-likely, ll/sc and more)."],
  [/^-mgp32$/, "Treat general-purpose registers as 32-bit, so 64-bit values use register pairs."],
  [/^-mgp64$/, "Treat general-purpose registers as 64-bit."],
  [/^-mfp32$/, "Use 32-bit floating-point registers (doubles take an even/odd register pair)."],
  [/^-mfp64$/, "Use 64-bit floating-point registers."],
  [/^-mhard-float$/, "Emit real FPU instructions instead of software floating-point calls."],
  [/^-msoft-float$/, "Emit software floating-point library calls instead of FPU instructions."],
  [/^-mgas$/, "Produce output for the GNU assembler (changes assembler directives and delay-slot handling)."],
  [/^-mel$|^-EL$/, "Little-endian output."],
  [/^-meb$|^-EB$/, "Big-endian output."],
  [/^-mcpu=VR4300$/, "Schedule instructions for the VR4300, the N64's CPU."],
  [/^-mcpu=R3000$|^-mcpu=r3000$/, "Schedule instructions for the R3000, the PlayStation's CPU."],
  [/^-mcpu=/, "Tune instruction scheduling for this specific CPU."],
  [/^-march=/, "Generate code for this specific CPU architecture."],
  [/^-mabi=/, "Calling convention and register-size rules the code follows."],
  [/^-mno-abicalls$/, "Don't generate position-independent (PIC) call sequences."],
  [/^-fno-pic$|^-mno-pic$/, "Don't generate position-independent code."],
  [/^-fpic$|^-fPIC$/, "Generate position-independent code (calls go through the global offset table)."],
  [/^-fno-builtin$/, "Treat memcpy, strlen and friends as ordinary functions instead of inlining them."],
  [/^-fno-inline$/, "Never inline functions."],
  [/^-finline-functions$/, "Allow inlining of functions even when not marked inline."],
  [/^-fomit-frame-pointer$/, "Don't keep a frame pointer register; the stack pointer is used instead."],
  [/^-fno-omit-frame-pointer$/, "Always keep a frame pointer register."],
  [/^-funroll-loops$/, "Unroll loops with a known iteration count."],
  [/^-fno-exceptions$/, "C++: exceptions are not used, so no unwind tables are emitted."],
  [/^-fno-rtti$/, "C++: no runtime type information."],
  [/^-fno-common$/, "Give uninitialised globals their own definition instead of merging 'common' symbols."],
  [/^-fno-strict-aliasing$/, "Assume pointers of different types may point to the same memory."],
  [/^-fsigned-char$/, "Plain char is signed."],
  [/^-funsigned-char$/, "Plain char is unsigned."],
  [/^-ffast-math$/, "Allow floating-point maths shortcuts that break strict IEEE rules."],
  [/^-fno-delayed-branch$/, "Don't fill branch delay slots with useful instructions."],
  [/^-mno-long-calls$/, "Use direct jal calls rather than loading the address into a register first."],
  [/^-mlong-calls$/, "Load each call target into a register and use jalr, for calls beyond jal's range."],
  [/^-mno-split-addresses$/, "Don't split address loads into separate high/low relocations."],
  [/^-msplit-addresses$/, "Split address loads into lui/addiu relocation pairs."],
  [/^-mno-memcpy$/, "Inline block copies instead of calling memcpy."],
  [/^-w$/, "Suppress all warnings."],
  [/^-W/, "Warning control; has no effect on the generated code."],
  [/^-Wall$/, "Enable common warnings; has no effect on the generated code."],
  [/^-c$/, "Compile to an object file without linking."],
  [/^-D\w*/, "Define a preprocessor macro, which can change which code is compiled."],
  [/^-I/, "Add an include directory."],
  [/^-std=/, "Language standard the source is compiled as."],
  [/^-nostdinc$/, "Don't search the system include directories."],
  [/^-pipe$/, "Pass data between compiler stages through pipes; no effect on output."],
  [/^-non_shared$/, "IDO: build for a statically linked program, not shared libraries (no PIC calls)."],
  [/^-KPIC$/, "IDO: generate position-independent code."],
  [/^-mips2$/, "IDO/GCC: target MIPS II."],
  [/^-Xcpluscomm$/, "IDO: allow // comments in C."],
  [/^-nodefaults$/, "CodeWarrior: no default include paths or libraries."],
  [/^-fp$/, "CodeWarrior: floating-point mode (hard = FPU instructions)."],
  [/^-proc$/, "CodeWarrior: target processor."],
  [/^-inline$/, "CodeWarrior: inlining mode (auto, deferred, off ...)."],
  [/^-sdata$/, "CodeWarrior: size limit for the small-data area."],
  [/^-sdata2$/, "CodeWarrior: size limit for the read-only small-data area."],
  [/^-enum$/, "CodeWarrior: how big enums are (int = always 4 bytes)."],
  [/^-lang$/, "CodeWarrior: source language (c, c99, c++ ...)."],
  [/^-str$/, "CodeWarrior: where string literals go and whether duplicates merge."],
  [/^-use_lmw_stmw$/, "CodeWarrior: allow lmw/stmw multi-register load/store instructions."],
  [/^-char$/, "CodeWarrior: whether plain char is signed or unsigned."],
  [/^\/O1$/, "MSVC: optimise for size."],
  [/^\/O2$/, "MSVC: optimise for speed."],
  [/^\/Od$/, "MSVC: disable optimisation."],
  [/^\/Ox$/, "MSVC: maximum optimisation."],
  [/^\/Oy-?$/, "MSVC: frame-pointer omission (/Oy on, /Oy- keep the frame pointer)."],
  [/^\/Ob\d$/, "MSVC: inlining level (0 off, 1 marked inline only, 2 any suitable)."],
  [/^\/Gy$/, "MSVC: package each function separately so the linker can drop or reorder it."],
  [/^\/G[3-7BDF]$/, "MSVC: tune scheduling for a CPU generation."],
  [/^\/GX$|^\/EHsc?$/, "MSVC: enable C++ exception handling."],
  [/^\/GR-?$/, "MSVC: runtime type information on (/GR) or off (/GR-)."],
  [/^\/MT$|^\/MD$|^\/MTd$|^\/MDd$/, "MSVC: which C runtime library is linked (static or DLL, release or debug)."],
  [/^\/Z[7iI]$/, "MSVC: debug information format."],
  [/^\/c$/, "MSVC: compile only, don't link."],
  [/^\/nologo$/, "MSVC: hide the startup banner."],
];

function compilerFlagHelp(arg) {
  for (const [pattern, text] of COMPILER_FLAG_HELP) if (pattern.test(arg)) return text;
  // Generic fallbacks, only after every specific rule had its chance.
  if (/^-mno-/.test(arg)) return "Turns off a machine-specific code-generation feature.";
  if (/^-fno-/.test(arg)) return "Turns off a code-generation or optimisation feature.";
  return "";
}

/* Wraps each argument in a hoverable, keyboard-focusable span. A flag's value (-G 0, -mips 3) stays with it
   when the next token is not itself an argument. */
function compilerFlagsHtml(flags, escape = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c])) {
  const tokens = (Array.isArray(flags) ? flags.join(" ") : String(flags || "")).split(/\s+/).filter(Boolean);
  const out = [];
  for (let i = 0; i < tokens.length; i++) {
    let arg = tokens[i];
    const next = tokens[i + 1];
    const takesValue = /^-(G|I|D|o|mips|proc|fp|inline|sdata2?|enum|lang|str|char|std)$/.test(arg) && next && !/^[-/]/.test(next);
    if (takesValue) arg += " " + tokens[++i];
    const help = compilerFlagHelp(arg) || compilerFlagHelp(tokens[i]);
    out.push(help ? `<span class="compiler-flag" tabindex="0" title="${escape(arg + ": " + help)}">${escape(arg)}</span>` : escape(arg));
  }
  return out.join(" ");
}
