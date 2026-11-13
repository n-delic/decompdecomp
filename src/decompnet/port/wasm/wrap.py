"""wrap.py c-dir name [opts]: log each call of a game function in the translated C (a debugging aid).

opts: argument numbers (0-based) to print also as text; "e" to log on entry as well; "r" to print the
result as text too."""
import glob
import re
import sys

cdir, name, *opts = sys.argv[1:]
mangled = "w2c_game_" + name + "0x2E1"
for f in glob.glob(cdir + "/game_*.c"):
    s = open(f).read()
    m = re.search(r"^(\w+) " + re.escape(mangled) + r"\(w2c_game\* instance((?:, \w+ var_p\d+)*)\) \{", s, re.M)
    if not m:
        continue
    ret, params = m.group(1), m.group(2)
    names = re.findall(r"var_p\d+", params)
    head = f"{ret} {mangled}(w2c_game* instance{params}) {{"
    s = s[:m.start()] + f"{ret} {mangled}_real(w2c_game* instance{params});\n" + s[m.start():]
    s = s.replace(head, f"{ret} {mangled}_real(w2c_game* instance{params}) {{", 1)

    fmt, args = [], []
    for i, n in enumerate(names):
        fmt.append("%08X" + (" [%.40s]" if str(i) in opts else ""))
        args.append(f"(unsigned){n}")
        if str(i) in opts:
            args.append(f"(const char *)instance->w2c_memory.data + {n}")
    fmt = " ".join(fmt)
    argtext = "".join(", " + a for a in args)
    lines = ["#include <stdio.h>", head]
    if "t" in opts:  # a backtrace when the second argument points at an empty string
        lines.insert(0, "void port_trace(void);")
        lines.append(f"  if (!instance->w2c_memory.data[{names[1]}]) port_trace();")
    if "e" in opts:
        lines.append(f'  fprintf(stderr, "> {name}({fmt})\\n"{argtext});')
    call = ", ".join(["instance"] + names)
    if ret == "void":
        lines.append(f"  {mangled}_real({call});")
        lines.append(f'  fprintf(stderr, "{name}({fmt})\\n"{argtext});')
    else:
        lines.append(f"  {ret} r = {mangled}_real({call});")
        lines.append(f'  fprintf(stderr, "{name}({fmt}) = %08X\\n"{argtext}, (unsigned)r);')
        if "r" in opts:
            lines.append('  if (r) { fputs("  -> ", stderr); for (int q = 0; q < 64; q++) { unsigned char ch = '
                         'instance->w2c_memory.data[r + q]; fputc(ch >= 32 && ch < 127 ? ch : 46, stderr); } '
                         'fputc(10, stderr); }')
        lines.append("  return r;")
    lines.append("}")
    open(f, "w").write(s + "\n" + "\n".join(lines) + "\n")
    print("wrapped", name, "in", f)
