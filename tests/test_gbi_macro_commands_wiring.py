"""decomprepair's `gbi_macro_commands` reaches the drafts, the agents' families and the deep permuter's local lane.

m2c reads an N64 function's libultra display-list macros back as one read of the cursor, the words stored at byte
offsets and every advance in a row; the family writes such a run as the SDK's Gbi macros again (on the 653-instruction
display-list function of an N64 game, 14912 -> 13862 with the app's judge). Without the wiring none of its variants
is ever proposed."""
import inspect

from decompnet.app import deep_permuter, draft_repairs

SOURCE = '''extern s32 *sCursor;
extern s32 gTex;
void f(s32 w, s32 h)
{
    s32 *temp_v1;
    temp_v1 = sCursor;
    *((s32 *) (((char *) temp_v1) + 0)) = 0xFD900000;
    *((s32 *) (((char *) temp_v1) + 4)) = gTex;
    *((s32 *) (((char *) temp_v1) + 8)) = 0xE7000000;
    *((s32 *) (((char *) temp_v1) + 0xC)) = 0;
    sCursor = temp_v1 + 8;
    sCursor = temp_v1 + 0x10;
}
'''


def test_the_drafts_propose_the_macro_form():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert {'gbi_macro_commands', 'gbi_raw_commands', 'gbi_macro_local'} <= set(draft_repairs.AGENT_FAMILIES)
    macro = [text for text in result if labels.get(text) == 'gbi_macro_commands']
    assert macro and 'gDPSetTextureImage(sCursor++, 4, 2, 1, gTex);' in macro[0]
    assert 'gDPPipeSync(sCursor++);' in macro[0] and 'extern Gfx *sCursor;' in macro[0]


def test_the_deep_permuters_local_lane_calls_the_family():
    assert "'gbi_macro_commands.variants'" in inspect.getsource(deep_permuter)
