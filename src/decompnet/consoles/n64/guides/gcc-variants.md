# Additional N64 GCC profiles

Choose a compiler by its code generation and assembler output, then judge a small function byte for byte. A similar version number is not evidence that two compiler builds emit the same code.

- `gcc-2.8.1-pm` is the Paper Mario matching build with its GNU assembler. `gcc-2.8.1-pm-sn-as` pairs its code generator with the original SN ASN64 2.56 assembler; compare these separately when assembler encodings differ.
- `gcc-egcs-2.91.66` is a reconstructed EGCS 1.1.2 profile; `gcc-egcs-1.1.2-4` is an original Linux release and needs its pinned i386 runtime. Both are available for explicit comparison, including iQue-era code.
- `gcc-4.4.0-n64` is a later MIPS64 ELF compiler configured for a 32-bit big-endian ABI. Retail N64 use is unconfirmed. It needs pinned host math libraries and runs on Linux x86_64 or Windows through WSL.

The Paper Mario and reconstructed EGCS packs have native macOS builds; the original Linux EGCS and GCC 4.4.0 packs do not. Keep the toolchain, flags, source, and assembler fixed when comparing candidates. A match still requires the normal linked-byte judge.
