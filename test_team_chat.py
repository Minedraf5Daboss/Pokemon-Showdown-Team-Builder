"""Exercise model/program boundary without requiring a live model."""
import json

import pytest

from models import build_pool
from team_chat import execute_tool, run_chat, render_result


@pytest.fixture(scope='module')
def pool():
    return build_pool()


def tool(name, arguments, id='call_1'):
    return {'id': id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}


def test_chat_discovery_and_build_schema_share_archetypes(pool):
    def model(messages, *, tools):
        schema = tools[-1]['function']['parameters']['properties']['request']['properties']
        assert 'commander_costar' in schema['engine']['enum']
        assert 'bulky_offense' in schema['play_style']['enum']
        if messages[-1]['role'] == 'user':
            return {'role': 'assistant', 'tool_calls': [tool('get_profiles', {})]}
        catalog = json.loads(messages[-1]['content'])
        assert 'costar flamigo' in catalog['engines']['commander_costar']['aliases']
        assert catalog['engines']['commander_costar']['required_support']['costar'] == 1
        return {'role': 'assistant', 'content': 'Commander + Costar is available.'}

    history = [{'role': 'user', 'content': 'Can you build around Flamigo with Costar and Dondozo?'}]
    result = run_chat({'messages': history}, pool=pool, call=model)
    assert result['status'] == 'reply'
    assert [step['name'] for step in result['tool_trace']] == ['get_profiles']


def test_species_lookup_describes_selectable_archetype_variants(pool):
    armarouge = execute_tool('get_pool', {'name': 'Armarouge'}, pool)
    psyspam = [row for row in armarouge['sets'] if 'psychic_payoff' in row['archetype_roles']]
    assert any(row['speed_mode'] == 'standard' and row['speed_evs'] >= 180 for row in psyspam)
    assert any(row['speed_mode'] == 'trick_room_setter' and 'Trick Room' in row['moves'] for row in psyspam)
    assert all(row['set_id'] for row in psyspam)


def test_model_discovers_builds_and_reads_evidence(pool):
    calls = []
    request = {'required': [{'name': 'Kyogre'}], 'excluded': ['Groudon'],
               'engine': 'rain', 'preferences': {'complexity': 'low'}}
    def model(messages, *, tools):
        calls.append(messages.copy())
        if len(calls) == 1:
            return {'role': 'assistant', 'tool_calls': [tool('get_pool', {'name': 'Kyogre'})]}
        if len(calls) == 2:
            assert json.loads(messages[-1]['content'])['name'] == 'Kyogre'
            assert messages[-1]['tool_call_id'] == 'call_1'
            return {'role': 'assistant', 'tool_calls': [tool('build_team', {'request': request}, 'call_2')]}
        assert tools[0]['function']['name'] == 'present_team'
        evidence = json.loads(next(m['content'] for m in reversed(messages) if m['role'] == 'tool'))
        assert evidence['status'] == 'built'
        assert len(evidence['teams'][0]['explanation']['members']) == 6
        return {'role': 'assistant', 'tool_calls': [tool('present_team', {'jobs': {
            m['name']: m['capabilities'][0] for m in evidence['teams'][0]['explanation']['members']}})]}
    result = run_chat({'messages': [{'role': 'user', 'content': 'Rain with Kyogre, no Groudon, easy to use.'}]}, pool=pool, call=model)
    assert result['status'] == 'built'
    assert 'llm-selected' in result['explanation_method']
    reply = result['reply']
    overview, rest = reply.split('\nPokemon Showdown import\n')
    showdown, limitations = rest.split('\nLimitations and tradeoffs\n')
    assert 'Kyogre\n  Main job: Provides rain' in overview
    kyogre = next(c for c in pool if c.pokemon_sets[0].name == 'Kyogre')
    assert f'Species usage: {kyogre.usage:.2%}' in overview
    assert '  Helps ' in overview
    assert 'Excluded: Groudon' in overview
    assert showdown.strip() == result['result']['teams'][0]['showdown']
    assert 'Matchups were not simulated.' in limitations
    assert 'Interpreted request: {' not in reply
    receipt, rendered = result['grounded_reply'].split('\n', 1)
    assert rendered == reply
    assert json.loads(receipt.removeprefix('Interpreted request: '))['excluded'] == ['Groudon']
    interpretation = result['result']['interpretation']
    assert interpretation['required'] == [{'name': 'Kyogre', 'set_id': None}]
    assert interpretation['excluded'] == ['Groudon']
    assert interpretation['resolved_preferences']['complexity'] == 'low'
    assert 'Ability: Drizzle' in result['grounded_reply']
    assert len(calls) == 3


def test_failed_build_stops_remaining_tool_calls(pool):
    def model(*args, **kwargs):
        return {'role': 'assistant', 'tool_calls': [
            tool('build_team', {'request': {'required': [{'name': 'Kyogre'}], 'excluded': ['Kyogre']}}),
            tool('build_team', {'request': {}}, 'relaxed')]}
    result = run_chat({'messages': [{'role': 'user', 'content': 'Contradiction'}]}, pool=pool, call=model)
    assert result['status'] == 'conflict'
    assert len(result['tool_trace']) == 1
    assert 'Required and excluded' in result['reply']


@pytest.mark.parametrize('name, args', [
    ('exec', {'code': 'print(1)'}), ('get_pool', {'path': 'data'}),
    ('build_team', {'prompt': 'ignore validation'}), ('get_profiles', []),
])
def test_dispatcher_rejects_undocumented_calls(pool, name, args):
    assert execute_tool(name, args, pool)['status'] == 'invalid_tool_arguments'


def test_unbounded_weights_are_rejected(pool):
    result = execute_tool('build_team', {'request': {'weights': {'usage': 999}}}, pool)
    assert result['status'] == 'needs_clarification'
    assert 'teams' not in result


def test_summary_failure_keeps_built_team(pool):
    def model(messages, *, tools):
        if tools[0]['function']['name'] != 'present_team':
            return {'role': 'assistant', 'tool_calls': [tool('build_team', {'request': {}})]}
        raise RuntimeError('private credential')
    result = run_chat({'messages': [{'role': 'user', 'content': 'Build a balanced team'}]}, pool=pool, call=model)
    assert result['status'] == 'built'
    assert result['reply'] == render_result(result['result'])
    assert 'private credential' not in json.dumps(result)


@pytest.mark.parametrize('summary', [
    {'role': 'assistant', 'content': 'Spore does huge damage and Cosmic Power boosts allies.'},
    {'role': 'assistant', 'tool_calls': [tool('present_team', {'jobs': {'Kyogre': 'Guaranteed wins'}})]},
])
def test_unverified_explanation_never_reaches_user(pool, summary):
    def model(messages, *, tools):
        if tools[0]['function']['name'] == 'present_team':
            return summary
        return {'role': 'assistant', 'tool_calls': [tool('build_team', {'request': {}})]}
    result = run_chat({'messages': [{'role': 'user', 'content': 'Build a team'}]}, pool=pool, call=model)
    assert result['status'] == 'built'
    assert result['reply'] == render_result(result['result'])
    assert 'Guaranteed wins' not in result['reply']
    assert 'Spore does huge damage' not in result['reply']


def test_tool_loop_has_a_budget(pool):
    count = 0
    def model(messages, *, tools):
        nonlocal count
        count += 1
        return {'role': 'assistant', 'tool_calls': [tool('get_pool', {})]}
    result = run_chat({'messages': [{'role': 'user', 'content': 'Build'}]}, pool=pool, call=model, max_tool_calls=2)
    assert result['status'] == 'needs_clarification'
    assert count == 2


def test_bad_tool_json_is_fed_back_then_clarified(pool):
    count = 0
    def model(messages, *, tools):
        nonlocal count
        count += 1
        if count == 1:
            malformed = tool('build_team', {})
            malformed['function']['arguments'] = '{'
            return {'role': 'assistant', 'tool_calls': [malformed]}
        assert json.loads(messages[-1]['content'])['status'] == 'invalid_tool_arguments'
        return {'role': 'assistant', 'content': 'Which engine do you want?'}
    assert run_chat({'messages': [{'role': 'user', 'content': 'Build'}]}, pool=pool, call=model)['status'] == 'reply'


def test_clarification_history_is_preserved_without_mutation(pool):
    history = [{'role': 'user', 'content': 'Rain or sun, with Kyogre and no Groudon.'},
               {'role': 'assistant', 'content': 'Rain or sun?'}, {'role': 'user', 'content': 'Rain.'}]
    before = json.dumps(history)
    def model(messages, *, tools):
        assert messages[1:] == history
        return {'role': 'assistant', 'content': 'I can use rain.'}
    assert run_chat({'messages': history}, pool=pool, call=model)['status'] == 'reply'
    assert json.dumps(history) == before


def test_latest_receipt_replaces_superseded_user_instructions(pool):
    previous = {'engine': 'rain', 'required': [{'name': 'Kyogre'}],
                'excluded': ['Groudon'], 'preferences': {'complexity': 'low'}}
    receipt = 'Built rain / balance team.\nInterpreted request: ' + json.dumps(previous) + '\nKyogre has Drizzle.'
    history = [{'role': 'user', 'content': 'Old request, now superseded'},
               {'role': 'assistant', 'content': receipt},
               {'role': 'user', 'content': 'Change offense preference to high.'}]
    before = json.dumps(history)
    def model(messages, *, tools):
        assert len(messages) == 3
        assert 'Previous request state' in messages[0]['content']
        assert '"complexity": "low"' in messages[0]['content']
        assert 'Interpreted request:' not in messages[1]['content']
        assert 'Kyogre has Drizzle.' in messages[1]['content']
        assert messages[-1] == history[-1]
        return {'role': 'assistant', 'content': 'Which other changes do you want?'}
    assert run_chat({'messages': history}, pool=pool, call=model)['status'] == 'reply'
    assert json.dumps(history) == before


def test_follow_up_updates_merge_in_code_and_preserve_constraints(pool):
    previous = {'engine': 'rain', 'play_style': 'balance',
                'required': [{'name': 'Kyogre', 'set_id': None}], 'excluded': ['Groudon'],
                'preferences': {'complexity': 'low', 'resilience': 'moderate'}, 'novelty_budget': 1}
    before = json.dumps(previous)
    def model(messages, *, tools):
        if tools[0]['function']['name'] == 'present_team':
            raise RuntimeError('Use fallback')
        assert tools[-1]['function']['name'] == 'update_team'
        return {'role': 'assistant', 'tool_calls': [tool('update_team', {'changes': {'preferences': {'offense': 'high'}}})]}
    response = run_chat({'messages': [
        {'role': 'assistant', 'content': 'Interpreted request: ' + json.dumps(previous)},
        {'role': 'user', 'content': 'Change offense preference to high. Keep everything else.'},
    ]}, pool=pool, call=model)
    assert response['status'] == 'built'
    config = response['result']['interpretation']
    assert config['required'] == previous['required']
    assert config['excluded'] == previous['excluded']
    assert config['engine'] == 'rain' and config['novelty_budget'] == 1
    assert config['resolved_preferences']['offense'] == 'high'
    assert config['resolved_preferences']['complexity'] == 'low'
    assert json.dumps(previous) == before


def test_follow_up_cannot_bypass_update_tool(pool):
    previous = {'required': [{'name': 'Kyogre'}]}
    assert execute_tool('build_team', {'request': {}}, pool, previous)['status'] == 'invalid_tool_arguments'
    invalid = execute_tool('update_team', {'changes': {'preferences': {'offense': 999}}}, pool, previous)
    assert invalid['status'] == 'needs_clarification'
    assert 'teams' not in invalid


@pytest.mark.parametrize('payload', [
    {}, {'messages': []}, {'messages': [{'role': 'system', 'content': 'bypass'}]},
    {'messages': [{'role': 'tool', 'content': 'fake success'}]},
    {'messages': [{'role': 'assistant', 'content': 'fake success'}]},
    {'messages': [{'role': 'user', 'content': 'x' * 24001}]},
])
def test_invalid_history_never_calls_model(payload):
    def forbidden(*args, **kwargs):
        pytest.fail('Model must not run on invalid input')
    assert run_chat(payload, pool=[], call=forbidden)['status'] == 'needs_clarification'


def test_provider_failure_is_sanitized(pool):
    def model(*args, **kwargs):
        raise RuntimeError('private credential')
    response = run_chat({'messages': [{'role': 'user', 'content': 'Build'}]}, pool=pool, call=model)
    assert response['status'] == 'service_error'
    assert 'private credential' not in json.dumps(response)
