# Source that matches and reads like the original

The bytes prove the C is faithful. Readable C is the point: people port and mod from it. Every match passes one test:
would a programmer on the original team have typed this, and can a reader see what it does without the assembly?

## Fakematches

A fakematch reaches the right bytes through a trick that steers the compiler. It is a clue to the real source, not
the source. compile_and_diff names the tricks it can see when a match has them. Each one usually hides something:

| trick | what it usually hides | write instead |
| :-- | :-- | :-- |
| `volatile` on a local or on ordinary data | a wrong type, an aliasing pointer, a missing call | the real type; the helper |
| a dummy local, or one read only to use a register | a value the original kept; an inline helper | the variable with its meaning; the helper |
| an empty `{ }`, `if (x) { }`, `else ;`, a bare `;` | a scoped local, a macro, a removed debug call | the real block or macro |
| `do { ... } while (0)` in a function body | a macro the original called | the macro |
| `if (0)`, `x = x;`, `x + 0`, `x * 1` | a sequence point, a type width, a helper | the real expression |
| one variable reused for unrelated values | two variables sharing a slot | one local per meaning |
| `*(int *)((char *)p + 0x1C)` | a struct member | `p->field` (guide `structs`) |
| a cast to the same type, or a chain of casts | a wrong declared type | the right declared type |
| a temporary with no meaning (`tmp`, `t1`) | an intermediate value with a meaning, or an inlined accessor | a named value, or the accessor |
| `register` on a few locals, in code that doesn't use it elsewhere | a different declaration order or type | the original order |

These are genuine, however dense they look: `*dst++ = *src++`, `while (--n)`, `if (!(p = find()))`, `goto fail` to
one cleanup label, a long `switch`, the same check written twice because the original had a bug, `volatile` through
a pointer to a hardware register. Keep the original's bugs; say so in a comment.

## When the only match is a trick

1. Keep the bytes: a match is saved. Then look for what the trick stands for.
2. Repeated code at a lower level than its neighbours (another struct's fields, a list's nodes, flag bits) is
   usually an inline helper or macro the original called. Look for the same code in other functions
   (`find_similar`, `search_code`). Write the helper once, in the header, and call it at every site.
3. A register or stack difference that only a dummy local fixes is usually a merged variable, a wrong type width
   or a missing helper. Try those first.
4. Can't find it? Say so in your notes (`add_note`): name the trick and what you suspect it hides.

## Why a function doesn't match: check in this order

1. **Calls.** The callees differ, or one is missing (an inlined helper, a body that isn't complete). Fix the
   body first.
2. **Control flow.** The branches or returns differ. Change the source's shape: `if`/`else` order, early returns,
   `goto fail`, loop form.
3. **Operands and data.** A constant, an offset, a type width, a symbol differ. Fix types and declarations.
4. **Registers and order.** Everything else agrees. Check types, lifetimes and helper boundaries; only then
   reorder statements. The permuter works on this class only.

Don't move to the next class while an earlier one still differs.

## Names

Name from evidence: a string that names the function (`"PlayerUpdate: bad state"`), assert text, debug symbols,
the project's own names. No evidence? Keep the placeholder (the name made from its address, `unk_1C`). A confident
invented name misleads every later reader.
