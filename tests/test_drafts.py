"""Machine drafts (m2c): what they are made from, when they are made again, and how agents get them."""

from decompnet import seeds

HEADER = """typedef signed int s32;   /* a word */
typedef float f32;
#define COUNT 4
typedef struct Vec { f32 x, y, z; } Vec;
struct Node { struct Node *next; Vec pos; s32 kind; };  /* 0x18 */
enum Kind { KIND_A, KIND_B = 2 };
typedef s32 (*Callback)(struct Node *node, s32 arg);
extern struct Node *g_nodes[COUNT];
extern Callback g_callback;
s32 node_kind(struct Node *node);
void unrelated(Vec *v);
#ifdef __cplusplus
class CThing { public: s32 m_id; };
#else
typedef struct CThing { s32 m_id; } CThing;
#endif
"""


def test_the_header_m2c_reads_leaves_out_what_is_only_for_cplusplus():
    """GTA San Andreas's header keeps its classes in an `#ifdef __cplusplus` block: kept for m2c, it made every
    draft's context fail to parse."""
    text = seeds.c_only(HEADER)
    assert "class CThing" not in text and "typedef struct CThing" in text and "#if" not in text and "#define" in text
    assert seeds.c_only("#ifndef __cplusplus\ns32 c;\n#else\nclass CThing {};\n#endif\n") == "s32 c;\n"
    assert "hidden" not in seeds.c_only("#if 0\ns32 hidden;\n#endif\ns32 shown;\n")
