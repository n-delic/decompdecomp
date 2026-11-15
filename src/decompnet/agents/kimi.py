"""Kimi Code 2.x: isolated configuration, MCP-only tools, and a stdin-fed Node launcher."""
from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path

from . import SAID_CHARS, Agent, _node, args_brief, number, tool_name, usage_item

TOKEN_ENV = 'DECOMP_KIMI_TOKEN'
# The supported CLI takes -p on argv. Set it inside the existing Node process after reading
# stdin, so neither the prompt nor the bearer token enters an OS process command line.
STDIN_LAUNCHER = """const fs=require('node:fs');const {pathToFileURL}=require('node:url');
const [entry,...args]=process.argv.slice(1);
const path=require('node:path');
function wires(){const home=process.env.KIMI_CODE_HOME;if(!home)return [];
const base=path.join(home,'sessions');if(!fs.existsSync(base))return [];
return fs.readdirSync(base,{withFileTypes:true}).filter(x=>x.isDirectory()).flatMap(w=>{
const wd=path.join(base,w.name);return fs.readdirSync(wd,{withFileTypes:true}).filter(x=>x.isDirectory())
.map(s=>path.join(wd,s.name,'agents','main','wire.jsonl')).filter(p=>fs.existsSync(p));});}
const before=new Map(wires().map(p=>[p,fs.statSync(p).size]));
process.once('exit',()=>{try{const usage={input_tokens:0,output_tokens:0,cache_read_tokens:0,cache_write_tokens:0};let found=false;
for(const file of wires())for(const line of fs.readFileSync(file).subarray(before.get(file)||0).toString('utf8').split('\\n')){
if(!line.trim())continue;let e;try{e=JSON.parse(line)}catch{continue}
if(e.type!=='usage.record'||e.agentId!=='main'||e.usageScope!=='turn')continue;
found=true;for(const [out,key] of Object.entries({input_tokens:'inputOther',output_tokens:'output',cache_read_tokens:'inputCacheRead',cache_write_tokens:'inputCacheCreation'}))
usage[out]+=Number(e.usage?.[key]||0);}
if(found)fs.writeSync(1,JSON.stringify({role:'meta',type:'decomp.usage',usage})+'\\n');}catch{}});
const prompt=fs.readFileSync(0,'utf8').replace(/^\\x01/,'');
process.argv=[process.execPath,entry,...args,'-p',prompt];
import(pathToFileURL(entry).href).catch(e=>{console.error(e.message);process.exitCode=1});"""


def kimi_home() -> Path:
    return Path(os.environ.get('KIMI_CODE_HOME') or Path.home() / '.kimi-code')


def config() -> dict:
    try:
        return tomllib.loads((kimi_home() / 'config.toml').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _toml(value):
    """Serialize the provider/model subset as TOML inline values without another dependency."""
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(k)} = {_toml(v)}' for k, v in value.items() if v is not None) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_toml(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=True)


class KimiCode(Agent):
    id = 'kimi'
    name = 'Kimi Code'
    command = 'kimi'
    vendor = 'Moonshot AI'
    install = 'npm install -g @moonshot-ai/kimi-code'
    signin = 'Run `kimi login` in a terminal, then start this agent again.'
    docs = 'https://www.kimi.com/code/docs/en/kimi-code-cli/guides/getting-started.html'
    family = 'kimi'
    takes_effort = True
    efforts = ('low', 'high', 'max')

    supports_login = True

    def login(self, exe: str) -> None:
        from ..hostenv import login_terminal

        # Authenticate the original account, not a helper’s isolated copy of its credentials.
        login_terminal([exe, "login"], {"KIMI_CODE_HOME": str(kimi_home())})

    def config_model(self):
        return config().get('default_model') or None

    def models(self, path, run=True):
        return [{'id': key, 'name': row.get('display_name') or key, 'note': 'from your Kimi Code configuration'}
                for key, row in config().get('models', {}).items()]

    def session_command(self, exe, workdir, mcp_url, token, model):
        original = kimi_home()
        private = workdir / '.kimi-code'
        private.mkdir(mode=0o700, exist_ok=True)
        data = config()
        selected, _, effort = (model or data.get('default_model') or '').partition('#')
        kept = {k: data[k] for k in ('providers', 'models', 'thinking') if k in data}
        kept.update(default_model=selected, telemetry=False, auto_session_title=False,
                    builtin_product_skills=False, merge_all_available_skills=False,
                    tools={'enabled': ['mcp__decomp__*']})
        if effort:
            if effort not in self.efforts:
                raise ValueError('Choose a supported Kimi effort: low, high or max.')
            kept['thinking'] = dict(kept.get('thinking') or {}, effort=effort)
        # Copy only referenced credentials, never user plugins, hooks, MCP servers or instructions.
        for provider in kept.get('providers', {}).values():
            key = (provider.get('oauth') or {}).get('key', '')
            if not key:
                continue
            relative = Path(key)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Kimi OAuth key must be a relative credential reference.')
            for rel in (relative, Path('credentials') / (relative.name + '.json')):
                source, target = original / rel, private / rel
                if source.is_file():
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
        target = private / 'config.toml'
        target.write_text('\n'.join(f'{k} = {_toml(v)}' for k, v in kept.items()) + '\n', encoding='utf-8')
        target.chmod(0o600)
        mcp = private / 'mcp.json'
        mcp.write_text(json.dumps({'mcpServers': {'decomp': {'url': mcp_url, 'bearerTokenEnvVar': TOKEN_ENV,
                                                          'toolTimeoutMs': 180000}}}), encoding='utf-8')
        mcp.chmod(0o600)
        profile = private / 'decomp-agent.md'
        profile.write_text('---\nname: decomp-helper\ndescription: Match code using decompdecomp tools.\n'
                           'tools: ["mcp__decomp__*"]\nsubagents: []\n---\n'
                           'You are a decompilation assistant. Use only the decomp MCP tools.\n', encoding='utf-8')
        empty = private / 'empty-skills'
        empty.mkdir(exist_ok=True)
        return [exe, '--output-format', 'stream-json', '--skills-dir', str(empty), '--agent-file', str(profile)] + (['-m', selected] if selected else []), {
            'KIMI_CODE_HOME': str(private), TOKEN_ENV: token, 'KIMI_DISABLE_TELEMETRY': '1',
            'KIMI_CODE_TRUST_WORKSPACE': '1', 'KIMI_LOOP_MAX_STEPS_PER_TURN': '100', 'KIMI_MODEL_NAME': ''}

    def launch_command(self, cmd):
        if Path(cmd[0]).name.lower() in ('node', 'node.exe'):
            node, entry, *args = cmd
        else:
            entry, *args = cmd
            entry = str(Path(entry).resolve())
            own_node = Path(cmd[0]).parent / ('node.exe' if os.name == 'nt' else 'node')
            node = str(own_node) if own_node.is_file() else _node()
        # cli_argv can prepend Node while retaining npm's extensionless symlink.
        entry = str(Path(entry).resolve())
        if Path(entry).suffix not in ('.mjs', '.cjs', '.js'):
            raise RuntimeError('Install the current Kimi Code CLI with npm install -g @moonshot-ai/kimi-code; the legacy Python kimi-cli is not supported.')
        if not node:
            raise RuntimeError('Kimi Code needs Node.js 22.19 or later. Install Node.js and reinstall Kimi Code.')
        return [node, '-e', STDIN_LAUNCHER, entry, *args]

    def parse(self, event, state):
        out = []
        if event.get('role') == 'assistant':
            content = event.get('content')
            if isinstance(content, str) and content.strip():
                out.append(('text', content.strip()))
            elif isinstance(content, list):
                for block in content:
                    if block.get('type') == 'text' and block.get('text'):
                        out.append(('text', block['text']))
            for call in event.get('tool_calls') or []:
                fn = call.get('function') or {}
                out.append(('tool', f"{tool_name(fn.get('name', ''))}({args_brief(fn.get('arguments'))})"))
        if event.get('error'):
            out.append(('error', str(event['error'])[:SAID_CHARS]))
        # Only actual reported request usage counts; context occupancy is not token spend.
        if isinstance(event.get('usage'), dict):
            usage = event['usage']
            out.append(usage_item(input=number(usage, 'input_tokens'), output=number(usage, 'output_tokens'),
                                  cache_read=number(usage, 'cache_read_tokens'), cache_write=number(usage, 'cache_write_tokens'), turns=1))
        return out

    def fatal(self, error):
        return any(s in error.lower() for s in ('model not found', 'unknown model', 'unsupported effort', 'unsupported option',
                                               'no provider configured', 'no default model', 'kimi login'))


KIMI = KimiCode()
