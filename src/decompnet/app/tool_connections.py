"""Bound local startup probes and recognize explicit transport failures after a CLI handshake."""
import re


def failure(session, net):
    # A model's final message is evidence of a transport failure only with an explicit
    # connection/timeout signature, not a generic claim of being blocked.
    row=net.store.one("SELECT text FROM helper_messages WHERE session_id=? AND kind='text' ORDER BY id DESC LIMIT 1",session.session_id)
    text=row['text'] if row else ''
    if re.search(r'(?:MCP[^\n]{0,100}(?:-32001|timed? out|timeout|unavailable)|(?:project_info|claim_function|decomp tools)[^\n]{0,120}(?:timed? out|timeout|unavailable|not (?:exposed|available))|connection (?:check )?(?:failed|failure)[^\n]{0,160}(?:tool|MCP|timed? out|timeout))',text,re.I):
        return 'The CLI reported a local tool connection failure. Saved candidates and held claims are retained; reconnecting without a no-compilation penalty.'
    return None
