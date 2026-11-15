# Decompiling C++

The symbol is the mangled name; it encodes the class, the parameter types and const-ness, so the
definition must have exactly that signature or objdiff won't find it.
- CodeWarrior (GameCube, Wii, DS): `update__7CPlayerFf` = `CPlayer::update(float)`;
  `__ct__7CPlayerFv` constructor, `__dt__7CPlayerFv` destructor, `__vt__7CPlayer` vtable,
  `__sinit_file_cpp` static initialisers.
- Itanium (Switch Clang, many 3DS/PS2/PS3 compilers): `_ZN7CPlayer6updateEf`; `C1`/`C2` and `D0`/`D1`/`D2`
  are the constructor/destructor variants.
- MSVC (PC, Xbox): `?update@CPlayer@@QAEXM@Z`; `??0CPlayer@@QAE@XZ` constructor, `??1CPlayer@@QAE@XZ`
  destructor, `??_GCPlayer@@UAEPAXI@Z` deleting destructor. A PC game started from scratch names its functions by
  address instead: guide("x86") says how to write its constructors and destructors.
The brief shows the demangled name when there is one.

Structure
- `this` is the first argument (r3 on PPC, r0 on ARM, x0 on AArch64, ecx with MSVC thiscall).
- Destructors take a hidden flag (CodeWarrior: in r4) and call `operator delete` when it's > 0.
- Virtual calls load the vtable pointer, then the function at a fixed offset. CodeWarrior places the
  vtable pointer after the members of the class that first declares virtual functions, so it's often
  not at offset 0; Itanium and MSVC put it at offset 0.
- Getters, setters and small helpers in class bodies are inline: the target shows direct field access.
- Templates (containers, math types) are instantiated as weak functions, often in many files.
- `new`/`delete`: `__nw__FUl` / `__dl__FPv` (CodeWarrior), `_Znwm` / `_ZdlPv` (Itanium).

Matching tips
- Get the class layout from offsets used across many methods (`search_code` the class name first:
  it's probably already declared).
<!-- only: mips -->
  In ROM projects (MIPS), decompdecomp has done this already: `get_class(function)` shows the class of a virtual function, constructor or destructor, with every
  field its functions touch (offset, type, which functions) and its virtual functions by slot, and
  `get_function` says which fields of `this` the function uses. Field types are what the loads and
  stores say (lwc1: float, lh: s16, a word used as an address: a pointer); the names are yours to give.
<!-- end -->
<!-- only: x86 -->
  In PC projects started from scratch, `get_class()` lists the program's classes: by their own names where it was
  built with run-time type information (RTTI), else `Class_<vtable address>` (found from the vtables constructors
  store). `get_class(function)` shows a virtual function's class, its base classes, its constructors and
  destructors, every field its functions touch through `this` (offset, type, which functions; its bases' too) and
  its virtual functions by slot, and `get_function` says which fields the function uses. A method's machine draft
  is written as `Class::func_<address>(...)` with `this`. Field types are what the instructions say (fld or movss:
  float, movsx word: s16, a word used as an address: a pointer); the names are yours to give.
- Xbox projects, and PC ones built with link-time code generation, get `get_class` from structs with member
  functions too: functions that use `this` (ecx under __thiscall; under LTCG the register the linker chose) as
  the base of field accesses, grouped by the object they share (a vtable, a global object they're called on,
  `this` passed on). The brief says "Looks like a member function of ...": write it as one
  (`int Struct_00011570::func_000115A0(int edx)`), with `struct` for plain data and `class` only when there's a
  vtable, a constructor/destructor pair or a base class. When the symbol gives the real signature, the brief
  gives that instead: use it.
<!-- end -->
- Member initialisation order in constructors follows declaration order in the class, not the
  initialiser list order.
- Inlined base-class constructors show up as stores of the base vtable before the derived one.
- Exceptions and RTTI are often off in console games; if the target has EH tables, the project says so.
