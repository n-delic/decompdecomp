"""Editable prompt templates: built-in, global, then per-helper overrides."""
import json
import logging
from string import Formatter

from ..network import NetworkError

KEY = 'agent-prompt-overrides'
FIELDS = {'initial_rom': {'title','count','budget','compiler_guide'},
          'initial_project': {'title','count','budget','what','guides'},
          'initial_readable': {'title','count','names'}, 'instructions': set()}
LABELS = {'initial_rom':'Initial prompt — ROM work', 'initial_project':'Initial prompt — imported projects',
          'initial_readable':'Initial prompt — polishing', 'instructions':'App instructions'}


def defaults():
    from . import tools
    return dict(initial_rom=tools.WORK_PROMPT, initial_project=tools.FORK_PROMPT,
                initial_readable=tools.READABLE_PROMPT, instructions=tools.INSTRUCTIONS)


def validate(value):
    if not isinstance(value, dict) or set(value) - FIELDS.keys():
        raise NetworkError('Choose initial_rom, initial_project, initial_readable or instructions overrides.')
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > 100000:
            raise NetworkError('Each prompt must be text of at most 100,000 characters.')
        if key == 'instructions':
            continue  # This is literal text, never a format template.
        try:
            for _, name, spec, conversion in Formatter().parse(text):
                if name is not None and (name not in FIELDS[key] or spec or conversion):
                    raise ValueError('Unsupported placeholder')
        except ValueError as error:
            raise NetworkError(f'{LABELS[key]}: use only ' + ', '.join('{'+f+'}' for f in sorted(FIELDS[key]))
                               + '. Write literal braces as {{ and }}.') from error
    return dict(value)


def global_overrides(net):
    row = net.store.one('SELECT value FROM settings WHERE key=?', KEY)
    return json.loads(row['value']) if row else {}


def usable(value, whose):
    """The saved overrides that still validate, each field on its own. A template saved before a placeholder was
    removed would fail in .format() when a session starts and stop the helper: that field falls back to what it
    inherits, with a warning, and the other fields (a helper's own app instructions, say) stay in effect."""
    if not isinstance(value, dict):
        return {}
    kept = {}
    for key, text in value.items():
        try:
            kept.update(validate({key: text}))
        except NetworkError as error:
            logging.getLogger(__name__).warning('Ignoring the invalid %s prompt override %s: %s', whose, key, error)
    return kept


def effective(net, options):
    inherited = defaults() | usable(global_overrides(net), 'global')
    return inherited | usable(options.get('prompts') or {}, 'helper')


def for_session(net, session):
    snapshot = getattr(session, 'prompt_config', None)
    return snapshot if snapshot is not None else effective(net, session.options)


def save_global(net, value):
    value = validate(value)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (KEY, json.dumps(value)))


def key_for(net, session):
    return 'initial_readable' if session.options.get('task') == 'readable' else 'initial_project' if net.fork_slug(session.slug) else 'initial_rom'


def view(net, session=None):
    builtin = defaults()
    inherited = builtin | usable(global_overrides(net), 'global') if session else builtin
    overrides = session.options.get('prompts') or {} if session else global_overrides(net)
    keys = [key_for(net, session), 'instructions'] if session else list(FIELDS)
    return {'helper':session.id if session else None, 'overrides':overrides,
            'fields':[{'key':key, 'label':LABELS[key], 'inherited':inherited[key],
                       'value':overrides.get(key, inherited[key]), 'custom':key in overrides,
                       'placeholders':sorted(FIELDS[key])} for key in keys]}
