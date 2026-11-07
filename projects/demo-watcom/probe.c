/* Homemade compiler evidence for #81. No game code or platform headers. */
int probe_global;

extern int probe_external(int);

int probe_leaf(int a, int b) {
    return a + b;
}

int probe_relocations(int value) {
    probe_global += value;
    return probe_external(probe_global);
}

int probe_switch(int value) {
    switch (value) {
    case 0: return probe_external(11);
    case 1: return probe_external(23);
    case 2: return probe_external(37);
    case 3: return probe_external(41);
    case 4: return probe_external(59);
    default: return probe_global;
    }
}
