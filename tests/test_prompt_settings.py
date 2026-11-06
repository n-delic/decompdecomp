import pytest

from decompnet.app import prompt_settings, tools
from decompnet.app.agents import Manager, Session
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_global_agent_inheritance_validation_reset_and_restart(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    manager = Manager(net, tmp_path, 'http://localhost/mcp')
    session = Session('1','codex','reader','game','reader',{})
    manager.sessions['1'] = session
    prompt_settings.save_global(net, {'initial_rom':'Global {title} {count}', 'instructions':'Global instructions {literal}'})
    assert prompt_settings.for_session(net,session)['instructions'] == 'Global instructions {literal}'
    manager.set_prompts('1', {'instructions':'Agent instructions'})
    assert manager.app_instructions(session) == 'Agent instructions'
    assert prompt_settings.for_session(net,session)['initial_rom'] == 'Global {title} {count}'
    path = net.store.one('PRAGMA database_list')['file']
    reopened = RomNetwork(path,tmp_path)
    assert prompt_settings.global_overrides(reopened)['instructions'] == 'Global instructions {literal}'
    manager.set_prompts('1',{})
    assert manager.app_instructions(session) == 'Global instructions {literal}'
    prompt_settings.save_global(net,{})
    assert manager.app_instructions(session) == tools.INSTRUCTIONS
    for overrides in ({'unknown':'x'}, {'initial_rom':'{title.__class__}'}, {'initial_rom':'{bad}'},
                      {'initial_rom':'{'}, {'initial_rom':'{count:10000}'}, {'instructions':123}):
        with pytest.raises(NetworkError):
            prompt_settings.validate(overrides)
    assert prompt_settings.validate(prompt_settings.defaults()) == prompt_settings.defaults()


@pytest.mark.parametrize('mode,key', [('rom','initial_rom'),('fork','initial_project'),('readable','initial_readable')])
def test_templates_expand_by_mode_and_session_snapshot_is_stable(tmp_path,monkeypatch,mode,key):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    manager = Manager(net,tmp_path,'http://localhost/mcp')
    session = Session('1','codex','reader','game','reader',{'per_session':3,'task':'readable' if mode=='readable' else ''})
    manager.sessions['1'] = session
    ov={'title':'Synthetic game','kind':'fork' if mode=='fork' else 'rom','platform':'n64',
        'platform_name':'Nintendo 64','compiler':{'toolchain':'gcc-2.7.2-kmc'}}
    monkeypatch.setattr(net,'overview',lambda _:ov)
    prompt_settings.save_global(net,{key:'Global {title}; {count} functions.'})
    assert manager._prompt(session) == 'Global Synthetic game; 3 functions.'
    manager.set_prompts('1',{key:'Agent {title}; {count}.','instructions':'Agent system'})
    manager._session_begin(session)
    manager.set_prompts('1',{key:'Changed for next session','instructions':'New system'})
    assert manager._prompt(session) == 'Agent Synthetic game; 3.'
    assert manager.app_instructions(session) == 'Agent system'
    manager._session_begin(session)
    assert manager._prompt(session) == 'Changed for next session'
    assert manager.app_instructions(session) == 'New system'


def test_prompt_profiles_separate_efficiency_cohorts(tmp_path):
    from decompnet.app.session_efficiency import context
    net, _ = _game(tmp_path, [('f',10,'open')])
    before = context(net,'game',{})
    prompt_settings.save_global(net,{'instructions':'New experiment'})
    after = context(net,'game',{})
    assert before['cohort'] != after['cohort'] and before['prompt_profile'] != after['prompt_profile']
