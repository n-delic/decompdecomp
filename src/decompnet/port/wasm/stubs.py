"""stubs.py game.h out.c host.c [more host files]: a logging stub for each import the host doesn't define (in host.c, its
other files, or the game's own, games/<sha1>.c)."""
import re
import sys

h, out, hosts = sys.argv[1], sys.argv[2], sys.argv[3:]
defined = {name for host in hosts for name in re.findall(r"\b(w2c_env_\w+)\(struct w2c_env \*", open(host).read())}
lines = ['#include <stdio.h>', '#include "game.h"', "/* imports the host doesn't implement yet (generated): each logs its first call */"]
for m in re.finditer(r"^(u32|u64|f32|f64|void) (w2c_env_\w+)\(struct w2c_env\*((?:, \w+)*)\);", open(h).read(), re.M):
    ret, name, params = m.groups()
    if name in defined:
        continue
    ps = [p.strip() for p in params.split(",") if p.strip()]
    args = ", ".join(["struct w2c_env *e"] + [f"{t} a{i}" for i, t in enumerate(ps)])
    body = f'static int once; if (!once++) fprintf(stderr, "{name[8:]}: not implemented, skipped\\n");'
    lines.append(f"{ret} {name}({args}) {{ {body} {'' if ret == 'void' else 'return 0;'} }}")
open(out, "w").write("\n".join(lines) + "\n")
print(len(lines) - 3, "stubs")
