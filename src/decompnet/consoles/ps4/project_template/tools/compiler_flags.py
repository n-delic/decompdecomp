"""Starting flags for public clang 5.0.2, using its PS4 ABI and global stack guard."""

# Settings (decompdecomp wrote these for this game; change them if the game was built differently)
CL_FLAGS = ["-target", "x86_64-scei-ps4", "-ffreestanding", "-O2", "-mavx", "-fno-omit-frame-pointer", "-fPIE", "-mllvm", "-x86-use-vzeroupper=0",
            "-fstack-protector-strong", "-fno-asynchronous-unwind-tables", "-ffunction-sections",
            "-mllvm", "-no-x86-call-frame-opt", "-mbmi", "-mllvm", "-unroll-runtime=false", "-w"]
