"""Agent settings use keyboard-accessible disclosures and one shared connection row."""
from html.parser import HTMLParser
from pathlib import Path


def test_agent_panels_are_disclosures_and_connections_share_columns():
    class Panels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack=[]
            self.panels=[]
            self.groups=[]
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if tag in ('input','meta','link','br','hr','img'):
                return
            node=(tag,attrs)
            if any(a.get('id')=='agentsPage' for _,a in self.stack):
                if 'panel' in attrs.get('class','').split():
                    self.panels.append(node)
                    self.groups.append(next((a for _,a in reversed(self.stack) if 'ag-cols' in a.get('class','').split()),None))
            self.stack.append(node)
        def handle_endtag(self, tag):
            for i in range(len(self.stack)-1,-1,-1):
                if self.stack[i][0]==tag:
                    del self.stack[i:]
                    break
    parser=Panels()
    parser.feed(Path('src/decompnet/app/static/index.html').read_text())
    assert len(parser.panels)==6
    assert all(tag=='details' and 'settings-disclosure' in a['class'] for tag,a in parser.panels)
    assert parser.groups[-2] is parser.groups[-1] and parser.groups[-1] is not None
