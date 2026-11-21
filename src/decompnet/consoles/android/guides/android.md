# Matching Android native ARMv7 code

This beta imports the largest armeabi-v7a shared library in an APK or XAPK, or a shared ELF directly.
Other native libraries, Java/Kotlin DEX code and assets remain outside the project. The original
package stays on your disk; no APK is rebuilt or signed yet. iOS and ARM64 are not supported.

Functions use the ELF's own sized symbols; aliases share one function. A fully stripped library
without sized function symbols needs a future analysis pass. Names may be C++ mangled identifiers;
use the exact target name for a C definition until proper class declarations have been established.

Start with Clang 3.5, GCC 4.8 or GCC 4.9 from NDK r10e. Compiler stamps describe everything linked
into the library; they don't prove the compiler of each function. ARM versus Thumb is selected
from the function symbol, after the project's flags. Try optimization, floating-point and ABI
flags with try_compiler and compiler ranges. The baseline is ARMv7, softfp, VFPv3-D16 and PIC.

The shared ARM judge demands identical linked bytes, including common PIC/GOT references, PLT calls,
Thumb-2 branches and MOVW/MOVT pairs. It verifies referenced symbols and locally defined data, rather than
masking relocation bytes. Unknown forms, including TLS, fail explicitly. Report an unsupported form;
don't substitute numeric addresses to bypass it.

Install Ghidra in Setup for background machine drafts and header-aware redraft. On macOS its bundled
pinned decompiler source builds locally with Xcode Command Line Tools. Autopilot tries twins, tiny ARM
instruction shapes and compilable Ghidra drafts, saving exact matches and near misses. A readable draft
is not proof: compile and judge it. Supply prototypes and struct declarations in the shared header or
redraft context to improve types. Automatic class/struct analysis and whole-package export remain planned.
