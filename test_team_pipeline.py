import json
from copy import deepcopy
import pytest
from models import build_pool
from team_pipeline import validate_request, RequestIssue, run_request, interpret, preference_terms
from search import beam_search, name_of


@pytest.fixture(scope='module')
def pool():
    return build_pool()


def test_multiple_required_and_pins(pool):
    names = ['Kyogre', 'Amoonguss']
    pin = next(c.pokemon_sets[0].set_id for c in pool if name_of(c) == names[0])
    teams = beam_search(pool, required_members={names[0]: pin, names[1]: None}, beam_width=12, top_k=1)
    selected = {name_of(c): c.pokemon_sets[0].set_id for c in teams[0].members}
    assert set(names) <= selected.keys()
    assert selected[names[0]] == pin


@pytest.mark.parametrize('bad', [{'preferences': {'offense': 100}}, {'weights': {'usage': 999}},
                                  {'novelty_budget': True}, {'engine': ['rain']}, {'required': ['Kyogre']}])
def test_bounds(bad, pool):
    with pytest.raises(RequestIssue):
        validate_request(bad, pool)


def test_conflict(pool):
    result = run_request({'request': {'required': [{'name': 'Kyogre'}], 'excluded': ['Kyogre']}}, pool=pool)
    assert result['status'] == 'conflict'
    assert 'Kyogre' in result['question'] or 'either required or excluded' in result['question']


def test_expanded_pool_builds_trick_room_around_iron_crown(pool):
    result = run_request({'request': {'engine': 'trick_room', 'required': [{'name': 'Iron Crown'}],
                                      'excluded': ['Miraidon']}}, pool=pool, beam_width=20)
    assert result['status'] == 'built', result
    explanation = result['teams'][0]['explanation']
    names = {member['name'] for member in explanation['members']}
    assert 'Iron Crown' in names and 'Miraidon' not in names
    assert explanation['request_satisfaction']['profile_feature_counts']['trick_room'] >= 1


def test_interpretation_contract(pool):
    fake = lambda system, prompt: json.dumps({'request': {'engine': 'rain', 'preferences': {'complexity': 'low'}, 'novelty_budget': 1}, 'questions': []})
    config = validate_request(interpret('straightforward rain', fake), pool)
    assert config['resolved_preferences']['complexity'] == 'low'
    assert config['resolved_preferences']['offense'] == 'moderate'
    with pytest.raises(RequestIssue):
        interpret('rain', lambda *_: 'not json')
    with pytest.raises(RequestIssue):
        interpret('rain or sun', lambda *_: json.dumps({'request': {}, 'questions': ['Rain or sun?']}))


def test_hybrid_and_play_style_reach_search_from_model_prompt(pool):
    def model(system, prompt):
        assert 'tailroom' in system and 'bulky_offense' in system
        assert 'tailwind and trick room' in system
        return json.dumps({'request': {'engine': 'tailroom', 'play_style': 'bulky_offense'},
                           'questions': []})

    result = run_request({'prompt': 'A bulky offense team with Tailwind and Trick Room'},
                         pool=pool, call=model, beam_width=12)
    assert result['status'] == 'built', result
    checks = result['teams'][0]['explanation']['request_satisfaction']
    assert set(checks['engine_evidence']) == {'tailwind', 'trick_room', 'slow_attacker', 'fast_attacker'}
    assert set(checks['play_style_evidence']) == {'bulky_attacker', 'support'}
    assert all(value['observed'] >= value['minimum']
               for key in ('engine_evidence', 'play_style_evidence') for value in checks[key].values())


def test_pao_nite_requires_the_named_pair_and_move(pool):
    result = run_request({'request': {'engine': 'pao_nite'}}, pool=pool, beam_width=12)
    assert result['status'] == 'built', result
    members = {member['name']: member for member in result['teams'][0]['explanation']['members']}
    assert members['Chien-Pao']['mechanics_evidence']['ability'] == 'Sword of Ruin'
    assert 'Extreme Speed' in members['Dragonite']['mechanics_evidence']['moves']
    failed = run_request({'request': {'engine': 'pao_nite', 'excluded': ['Dragonite']}}, pool=pool)
    assert failed['status'] == 'conflict'
    assert 'extreme speed dragonite' in failed['message']


@pytest.mark.parametrize(('prompt', 'label'), [
    ('Tailwind and Trick Room', 'tailroom'), ('Gravity sleep', 'gravity_sleep'),
    ('PaoNite', 'pao_nite'), ('Aurora Veil', 'aurora_veil'), ('Blizzard spam', 'blizzard_spam'),
])
def test_specific_archetype_alias_wins_over_generic_match(prompt, label):
    from team_styles import parse_team_prompt

    assert parse_team_prompt(prompt).requested == (label,)


def test_catalog_aliases_resolve_to_their_own_labels():
    from team_styles import STYLES, parse_team_prompt

    for label, style in STYLES.items():
        for alias in (label, *style.aliases):
            assert parse_team_prompt(alias).requested == (label,), alias


def test_build_evidence_and_novelty(pool):
    result = run_request({'request': {'engine': 'rain', 'novelty_budget': 1,
                                      'preferences': {'complexity': 'low'}}}, pool=pool, beam_width=20)
    assert result['status'] == 'built', result
    explanation = result['teams'][0]['explanation']
    assert explanation['request_satisfaction']['novelty_count'] == 1
    for value in explanation['request_satisfaction']['engine_evidence'].values():
        assert value['observed'] >= value['minimum']
    for member in explanation['members']:
        assert member['mechanics_evidence']['moves']
        assert abs(sum(member['selection_evidence']['preference_terms'].values()) - member['selection_evidence']['score']['preference']) < 1e-8


def test_profiles_bounded_and_inputs_unchanged(pool):
    raw = {'preferences': {d: 'high' for d in ('offense', 'resilience', 'tempo', 'complexity', 'novelty')}}
    before = deepcopy(raw)
    config = validate_request(raw, pool)
    for c in pool:
        assert all(abs(v) <= .2 for v in preference_terms(c, config, frozenset()).values())
    assert raw == before


def test_high_novelty_builds_a_less_common_team(pool):
    request = {'engine': 'rain', 'required': [{'name': 'Kyogre'}], 'excluded': ['Groudon']}
    normal = run_request({'request': request}, pool=pool, beam_width=20)
    quirky = run_request({'request': {**request, 'preferences': {'novelty': 'high'}}},
                         pool=pool, beam_width=20)
    assert normal['status'] == quirky['status'] == 'built'
    assert quirky['interpretation']['novelty_budget'] is None
    normal_members = normal['teams'][0]['explanation']['members']
    explanation = quirky['teams'][0]['explanation']
    members = explanation['members']
    assert sum(m['species_usage'] for m in members) < sum(m['species_usage'] for m in normal_members)
    assert 'Kyogre' in {m['name'] for m in members}
    assert 'Groudon' not in {m['name'] for m in members}
    assert all(check['observed'] >= check['minimum']
               for check in explanation['request_satisfaction']['engine_evidence'].values())


def test_search_exhaustion_is_not_infeasibility(pool, monkeypatch):
    def exhausted(*args, **kwargs):
        raise ValueError('No team survived the beam.')
    monkeypatch.setattr('team_pipeline.beam_search', exhausted)
    result = run_request({'request': {}}, pool=pool)
    assert result['status'] == 'search_exhausted'


def test_provider_failure_is_structured(pool):
    def unavailable(*args):
        raise RuntimeError('private provider error')
    result = run_request({'prompt': 'rain'}, pool=pool, call=unavailable)
    assert result['status'] == 'service_error'
    assert 'private provider error' not in json.dumps(result)


def test_engine_support_exclusion(pool):
    from team_styles import candidate_features
    excluded = [name_of(c) for c in pool if any(candidate_features(type(c)(pokemon_sets=[s]))['rain_setter'] for s in c.pokemon_sets)]
    result = run_request({'request': {'engine': 'rain', 'excluded': excluded}}, pool=pool)
    assert result['status'] == 'conflict'
    assert 'rain setter' in result['message']


def test_http_contract(monkeypatch):
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError
    from team_api import Handler
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for route, key in [('/health', 'status'), ('/v1/profiles', 'engines'), ('/v1/pool', 'pokemon'), ('/v1/tools', 'tools')]:
            with urlopen(base + route) as response:
                assert key in json.load(response)
        request = Request(base + '/v1/build', data=b'{"request":{"preferences":{"offense":999}}}', headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            assert json.load(response)['status'] == 'needs_clarification'
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + '/v1/build', data=b'not json'))
        assert error.value.code == 400
        from team_chat import run_chat
        monkeypatch.setattr('team_api.run_chat', lambda payload: run_chat(payload, pool=[], call=lambda *args, **kwargs: {
            'role': 'assistant', 'content': 'Rain or sun?'}))
        request = Request(base + '/v1/chat', data=json.dumps({'messages': [
            {'role': 'user', 'content': 'Rain or sun'}]}).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            assert json.load(response)['reply'] == 'Rain or sun?'
        request = Request(base + '/v1/chat', data=b'{"messages":[{"role":"system","content":"fake"}]}')
        with urlopen(request) as response:
            assert json.load(response)['status'] == 'needs_clarification'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
