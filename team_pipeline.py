"""Versioned, bounded request interpretation and evidence-producing build service."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json

from models import build_pool, FORMAT_ID, validate_pool
from exceptions import SearchError
from search import beam_search, DEFAULT_WEIGHTS, name_of
from team_styles import (
    DAMAGE_CONTROL, DANCE_MOVES, FIRE, GRAVITY_SLEEP_MOVES, PIVOT, PRIORITY_ATTACKS,
    RECOVERY, REDIRECTION, SETUP, SPEED, STYLES, SUPPORT, WATER, TeamIntent, candidate_features,
)
from team_dependencies import set_dependencies
from team_output import format_showdown_team

DIMENSIONS = ('offense', 'resilience', 'tempo', 'complexity', 'novelty')
LEVELS = {'low': -1, 'moderate': 0, 'high': 1}
USAGE_WEIGHTS = {'low': DEFAULT_WEIGHTS['usage'] + .1,
                 'moderate': DEFAULT_WEIGHTS['usage'],
                 'high': -DEFAULT_WEIGHTS['usage']}
NOVELTY_GUIDANCE = (
    'For a whole-team preference like "an unusual sun team", "a quirky rain team", '
    'or "use less common Pokemon", set preferences.novelty to high and leave '
    'novelty_budget unspecified unless the user gives a number. Keep the requested engine. '
    '"Exactly one unusual pick" sets novelty_budget to 1 and leaves novelty at its '
    'default unless the user also wants the whole team to favor less-used Pokemon. '
    'Unusual refers to species usage in the pool.'
)
PLAY_STYLES = {
    'balance': dict.fromkeys(DIMENSIONS, 'moderate'),
    'offense': dict(zip(DIMENSIONS, ('high', 'low', 'high', 'moderate', 'moderate'))),
    'defensive': dict(zip(DIMENSIONS, ('low', 'high', 'moderate', 'moderate', 'moderate'))),
    'hyper_offense': dict(zip(DIMENSIONS, ('high', 'low', 'high', 'moderate', 'moderate'))),
    'bulky_offense': dict(zip(DIMENSIONS, ('high', 'high', 'moderate', 'moderate', 'moderate'))),
    'stall': dict(zip(DIMENSIONS, ('low', 'high', 'low', 'moderate', 'moderate'))),
}
ENGINE_NAMES = tuple(name for name, style in STYLES.items() if style.kind == 'engine')
PROFILE_REQUIREMENTS = {name: style.requirements for name, style in STYLES.items() if style.kind == 'play_style'}
ARCHETYPE_GUIDANCE = (
    'Use the canonical engine and play_style values for explicit archetype requests. An engine can be '
    'combined with a play_style. TailRoom, SunRoom and RainRoom each represent one supported hybrid engine. '
    'Ask about other combinations of multiple engines. Use none only when no engine is requested or the user removes it.'
)
ENGINE_OPTIONS = {
    'rain': ['Drizzle or Rain Dance setter', 'Water attacks', 'Swift Swim', 'Thunder or Hurricane'],
    'sun': ['Drought or Orichalcum Pulse setter', 'Fire attacks', 'Chlorophyll', 'Solar Power'],
    'trick_room': ['Trick Room setter', 'Low-speed attacker', 'Redirection or Fake Out support'],
}


def label_schema(names, description):
    aliases = (f'{name}: {", ".join(STYLES[name].aliases)}' for name in names if name in STYLES)
    return {'type': 'string', 'enum': list(names), 'description': description + ' Aliases: ' + '; '.join(aliases)}


def request_schema():
    """Machine-readable labels only; numerical scoring remains code-owned."""
    names = {'type': 'array', 'items': {'type': 'string'}}
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'required': {'type': 'array', 'items': {'type': 'object',
            'additionalProperties': False, 'required': ['name'], 'properties': {
                'name': {'type': 'string'}, 'set_id': {'type': ['string', 'null']}}}},
        'excluded': deepcopy(names), 'prioritize': deepcopy(names),
        'engine': label_schema(('none', *ENGINE_NAMES), 'One team mechanic or named hybrid.'),
        'play_style': label_schema(PLAY_STYLES, 'Overall approach. Hyper offense, bulky offense and stall have team composition requirements.'),
        'preferences': {'type': 'object', 'additionalProperties': False,
            'properties': {d: {'type': 'string', 'enum': list(LEVELS)} for d in DIMENSIONS}},
        'novelty_budget': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 6,
                           'description': 'Exact count of unusual species. Use only for a requested count.'},
    }}


def catalog():
    """Shared strategy contract, consumed by validation, search and explanations."""
    return {'schema_version': 1, 'format': FORMAT_ID, 'request_schema': request_schema(), 'levels': list(LEVELS),
            'dimensions': list(DIMENSIONS), 'play_styles': deepcopy(PLAY_STYLES),
            'play_style_details': {name: {'aliases': list(STYLES[name].aliases),
                                          'objective': STYLES[name].excels_at,
                                          'required_support': dict(requirements),
                                          'tradeoff': STYLES[name].watch_out_for,
                                          'sources': list(STYLES[name].sources)}
                                   for name, requirements in PROFILE_REQUIREMENTS.items()},
            'baseline_weights': dict(DEFAULT_WEIGHTS),
            'engines': {name: {
                'aliases': list(STYLES[name].aliases),
                'sources': list(STYLES[name].sources),
                'objective': STYLES[name].excels_at,
                'required_support': dict(STYLES[name].requirements),
                'rewarded_roles': [f for f, _ in STYLES[name].requirements],
                'compatible_combinations': [list(f for f, _ in STYLES[name].requirements)],
                'optional_sets': ENGINE_OPTIONS.get(name, ['Available variants with ' + f for f, _ in STYLES[name].requirements]),
                'special_handling': STYLES[name].watch_out_for,
                'completion_evidence': 'Selected sets must meet every required_support feature count.',
            } for name in ENGINE_NAMES},
            'defaults': {'engine': 'none', 'play_style': 'balance', 'required': [],
                         'excluded': [], 'preferences': {}, 'prioritize': [], 'novelty_budget': None},
            'novelty_definition': 'High novelty favors less-used species across the pool. An exact novelty budget counts species in the bottom quartile (including ties).',
            'request_fields': ['required', 'excluded', 'engine', 'play_style', 'preferences', 'prioritize', 'novelty_budget'],
            'required_member_schema': {'name': 'Pokemon name', 'set_id': 'optional exact set ID'}}


class RequestIssue(ValueError):
    def __init__(self, message, status='conflict', question='Which named constraint would you like to change?'):
        super().__init__(message)
        self.status, self.question = status, question


def validate_request(raw, pool):
    spec = catalog()
    if not isinstance(raw, dict) or set(raw) - set(spec['request_fields']):
        raise RequestIssue('Request must be an object containing only documented fields.', 'needs_clarification')
    config = deepcopy(spec['defaults'])
    config.update(deepcopy(raw))
    config['defaults_applied'] = [k for k in spec['defaults'] if k not in raw]
    if not isinstance(config['engine'], str) or config['engine'] not in ('none', *ENGINE_NAMES):
        raise RequestIssue('Choose one supported engine.', 'needs_clarification', 'Which engine in /v1/profiles do you want?')
    if not isinstance(config['play_style'], str) or config['play_style'] not in PLAY_STYLES:
        raise RequestIssue('Unknown play style.', 'needs_clarification',
                           'Choose ' + ', '.join(PLAY_STYLES) + '.')
    if config['play_style'] == 'hyper_offense' and config['engine'] == 'hard_trick_room':
        raise RequestIssue('Hyper offense requires fast attackers, while hard Trick Room requires a slow core.',
                           question='Which style should the team prioritize?')
    prefs = config['preferences']
    if not isinstance(prefs, dict) or set(prefs) - set(DIMENSIONS) or any(not isinstance(v, str) or v not in LEVELS for v in prefs.values()):
        raise RequestIssue('Preferences must select low, moderate, or high for documented dimensions.', 'needs_clarification')
    config['resolved_preferences'] = {**PLAY_STYLES[config['play_style']], **prefs}
    names = {name_of(c).casefold(): name_of(c) for c in pool}
    def canonical(name):
        if not isinstance(name, str) or name.strip().casefold() not in names:
            raise RequestIssue(f'Pokemon not available in current pool: {name}', question='Choose an available Pokemon from /v1/pool, or update the pool.')
        return names[name.strip().casefold()]
    for field in ('required', 'excluded', 'prioritize'):
        if not isinstance(config[field], list):
            raise RequestIssue(f'{field} must be a list.', 'needs_clarification')
    required = {}
    for member in config['required']:
        if not isinstance(member, dict) or set(member) - {'name', 'set_id'} or 'name' not in member:
            raise RequestIssue('Required members need name and optional set_id.', 'needs_clarification')
        name = canonical(member['name'])
        pin = member.get('set_id')
        if pin is not None and (not isinstance(pin, str) or not any(s.set_id == pin for c in pool if name_of(c) == name for s in c.pokemon_sets)):
            raise RequestIssue(f'Unavailable set_id for {name}', question=f'Choose a listed set_id for {name} or remove that pin.')
        if name in required and required[name] != pin:
            raise RequestIssue(f'Conflicting pins for {name}')
        required[name] = pin
    config['required'] = [{'name': n, 'set_id': p} for n, p in required.items()]
    for field in ('excluded', 'prioritize'):
        config[field] = list(dict.fromkeys(canonical(n) for n in config[field]))
    overlap = set(required) & set(config['excluded'])
    if overlap:
        raise RequestIssue('Required and excluded: ' + ', '.join(sorted(overlap)), question='Remove these Pokemon from either required or excluded.')
    if len(required) > 6:
        raise RequestIssue('More than six required members.', question='Remove required members until at most six remain.')
    budget = config['novelty_budget']
    if budget is not None and (type(budget) is not int or not 0 <= budget <= 6):
        raise RequestIssue('novelty_budget must be null or an integer from 0 to 6.', 'needs_clarification')
    config['weights'] = dict(DEFAULT_WEIGHTS)
    config['weights']['role'] += {
        'balance': 0, 'offense': .1, 'defensive': .2,
        'hyper_offense': .1, 'bulky_offense': .1, 'stall': .2,
    }[config['play_style']]
    # A negative coefficient reverses the usage ranking while leaving the other
    # score components in place.
    config['weights']['usage'] = USAGE_WEIGHTS[config['resolved_preferences']['novelty']]
    return config


def interpret(prompt, call=None):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise RequestIssue('Provide a nonempty prompt of at most 12000 characters.', 'needs_clarification')
    if call is None:
        from llm import call_llm
        call = call_llm
    system = ('Return ONLY a JSON object with request and questions (list of focused questions). '
              'request uses the catalog schema. Preserve all explicit requirements, exclusions and set pins. '
              'Never substitute unavailable Pokemon. Ask a question for incompatible engines or uncertain constraints. '
              'Straightforward means complexity low, not offense low. Defensive flexibility means balance and resilience moderate. '
              + NOVELTY_GUIDANCE + ' ' + ARCHETYPE_GUIDANCE + ' Unspecified fields may use documented defaults. '
              'Do not invent numeric weights. Request schema: ' + json.dumps(request_schema()))
    try:
        response = call(system, prompt)
    except Exception as exc:
        raise RequestIssue('LLM provider unavailable; check provider configuration or submit a structured request.',
                           'service_error', 'Retry after configuring the provider, or use a request envelope.') from exc
    try:
        result = json.loads(response)
    except (ValueError, TypeError) as exc:
        raise RequestIssue('Interpreter returned invalid JSON; submit a structured request or retry.', 'needs_clarification') from exc
    if not isinstance(result, dict) or set(result) != {'request', 'questions'} or not isinstance(result['questions'], list) or any(not isinstance(q, str) for q in result['questions']):
        raise RequestIssue('Interpreter returned an invalid envelope.', 'needs_clarification')
    if result['questions']:
        raise RequestIssue('The request needs clarification.', 'needs_clarification', ' '.join(result['questions']))
    return result['request']


def preference_terms(candidate, config, unusual):
    f = candidate_features(candidate)
    p = {k: LEVELS[v] for k, v in config['resolved_preferences'].items()}
    # Explicit proxies, not matchup or damage-calculation claims. Each term is bounded.
    return {'offense': .2 * p['offense'] * bool(f['attacker'] and not f['setup']),
            'resilience': .2 * p['resilience'] * bool(f['defensive'] or f['sustain']),
            'tempo': .2 * p['tempo'] * bool(f['speed_control'] or f['support']),
            'complexity': .2 * p['complexity'] * bool(f['setup'] or f['partner_manipulation'] or set_dependencies(candidate.pokemon_sets[0]).requires),
            'novelty': .2 * p['novelty'] * (name_of(candidate).casefold() in unusual),
            'prioritize': .2 * (name_of(candidate) in config['prioritize'])}


def explain(team, config, unusual):
    engine = STYLES.get(config['engine'])
    profile = STYLES['hyper_offense' if config['play_style'] == 'offense' else config['play_style']]
    profile_requirements = PROFILE_REQUIREMENTS.get(config['play_style'], ())
    features = {name_of(c): candidate_features(c) for c in team.members}
    counts = sum(features.values(), Counter())
    rows = []
    for i, c in enumerate(team.members):
        s = c.pokemon_sets[0]
        dep = set_dependencies(s)
        benefits = []
        for other in team.members:
            if other is c:
                continue
            for capability in sorted(dep.provides & set_dependencies(other.pokemon_sets[0]).requires):
                benefits.append({'teammate': name_of(other), 'how': f'Provides {capability}, required by its selected set.'})
            if engine:
                for provider, recipient, how in engine.benefits:
                    if features[s.name][provider] and features[name_of(other)][recipient]:
                        benefits.append({'teammate': name_of(other), 'how': how,
                                         'evidence': {'provider_feature': provider, 'recipient_feature': recipient,
                                                      'recipient_set_id': other.pokemon_sets[0].set_id}})
        jobs = []
        field_and_partner_roles = dep.provides - {'boosted ally'}
        if field_and_partner_roles:
            jobs.append('Provides ' + ', '.join(sorted(field_and_partner_roles)))
        for label, moves in [('Speed control', SPEED), ('Recovery', RECOVERY), ('Redirection', REDIRECTION),
                             ('Switches out', PIVOT), ('Priority damage', PRIORITY_ATTACKS),
                             ('Supports allies', {'Coaching', 'Beat Up'}), ('Controls accuracy', {'Gravity'}),
                             ('Sleep pressure', GRAVITY_SLEEP_MOVES), ('Dance trigger', DANCE_MOVES - SETUP),
                             ('Support', (SUPPORT | DAMAGE_CONTROL) - SPEED - REDIRECTION - PIVOT - {'Coaching'}),
                             ('Setup', SETUP)]:
            hits = sorted(set(s.moves) & moves)
            if hits:
                jobs.append(label + ' through ' + ', '.join(hits))
        if features[s.name]['attacker']:
            jobs.append('Attacks with an offensive EV spread')
        if 'boosted ally' in dep.provides:
            jobs.append('Can gain stat boosts')
        if not jobs:
            jobs.append('Uses its selected moves')
        limitations = list(s.team_notes)
        if config['engine'] in {'rain', 'rainroom'} and set(s.moves) & FIRE:
            limitations.append('Rain weakens this set\'s Fire attacks while active.')
        if config['engine'] in {'sun', 'sunroom'} and set(s.moves) & WATER:
            limitations.append('Sun weakens this set\'s Water attacks while active.')
        if engine and any(key == 'trick_room' for key, _ in engine.requirements) and features[s.name]['fast_attacker']:
            limitations.append('This speed-invested set may move later under Trick Room; choose its field timing carefully.')
        required = any(r['name'] == s.name for r in config['required'])
        rows.append({'name': s.name, 'species_usage': c.usage, 'main_job': jobs[0], 'capabilities': jobs,
                     'mechanics_evidence': {'set_id': s.set_id, 'moves': list(s.moves), 'ability': s.ability, 'item': s.held_Item,
                                            'evs': asdict(s.evs), 'sources': s.source_urls, 'evidence_status': s.evidence_status},
                     'teammate_benefits': benefits,
                     'limitations': limitations + ([f"Needs support: {', '.join(sorted(dep.requires - dep.provides))}"] if dep.requires - dep.provides else []) + ['Availability of support does not guarantee it stays active; no matchup simulation was performed.'],
                     'selection_evidence': {'reason': 'Required by user' if required else 'Ranked by recorded incremental score contributions',
                                            'score': team.addition_breakdowns[i],
                                            'preference_terms': preference_terms(c, config, unusual)},
                     'unusual': s.name.casefold() in unusual})
    game_plan = engine.excels_at if engine else profile.excels_at
    tradeoff = engine.watch_out_for if engine else 'Profile scores use set features and usage proxies; they do not establish matchup strength.'
    if profile_requirements:
        if engine:
            game_plan += ' ' + profile.excels_at
        tradeoff = ' '.join((engine.watch_out_for, profile.watch_out_for)) if engine else profile.watch_out_for
    return {'game_plan': game_plan,
            'request_satisfaction': {'required': config['required'], 'excluded_absent': all(name_of(c) not in config['excluded'] for c in team.members),
                                     'profile_feature_counts': dict(counts),
                                     'prioritized_selected': [name_of(c) for c in team.members if name_of(c) in config['prioritize']],
                                     'engine_evidence': {f: {'observed': counts[f], 'minimum': n} for f, n in (engine.requirements if engine else ())},
                                     'play_style_evidence': {f: {'observed': counts[f], 'minimum': n} for f, n in profile_requirements},
                                     'novelty_count': sum(r['unusual'] for r in rows)},
            'tradeoff': tradeoff +
                        (' Low complexity is a preference; required engine support still takes precedence.' if config['resolved_preferences']['complexity'] == 'low' else ''),
            'members': rows, 'explanation_method': 'deterministic evidence renderer (available without an explanation LLM)'}


def run_request(payload, *, pool=None, call=None, beam_width=100):
    """Single service entry point for CLI, HTTP and future tool adapters."""
    pool = build_pool() if pool is None else list(pool)
    try:
        if not isinstance(payload, dict) or set(payload) - {'request', 'prompt'} or len(payload) != 1:
            raise RequestIssue('Provide exactly one of request or prompt.', 'needs_clarification')
        raw = interpret(payload['prompt'], call) if 'prompt' in payload else payload['request']
        config = validate_request(raw, pool)
        if not pool:
            raise RequestIssue('Candidate pool is empty.', question='Populate the candidate pool and retry.')
        validate_pool(pool)
        cutoff = sorted(c.usage for c in pool)[max(0, (len(pool) - 1) // 4)]
        unusual = frozenset(name_of(c).casefold() for c in pool if c.usage <= cutoff)
        config['novelty_usage_cutoff'] = cutoff
        config['format'] = FORMAT_ID
        requested = tuple(name for name in (config['engine'], config['play_style'])
                          if name in ENGINE_NAMES or name in PROFILE_REQUIREMENTS)
        teams = beam_search(pool, required_members={r['name']: r['set_id'] for r in config['required']},
                            excluded_pokemon=config['excluded'], weights=config['weights'],
                            style_intent=TeamIntent(requested=requested) if requested else None,
                            preference_score=lambda c: sum(preference_terms(c, config, unusual).values()),
                            unusual_species=unusual, novelty_budget=config['novelty_budget'], beam_width=beam_width, top_k=1)
        return {'schema_version': 1, 'status': 'built', 'interpretation': config,
                'teams': [{'showdown': format_showdown_team(t), 'score': t.breakdown,
                           'explanation': explain(t, config, unusual)} for t in teams]}
    except RequestIssue as exc:
        return {'schema_version': 1, 'status': exc.status, 'message': str(exc), 'question': exc.question}
    except (ValueError, SearchError) as exc:
        exhausted = 'survived the beam' in str(exc)
        return {'schema_version': 1, 'status': 'search_exhausted' if exhausted else 'conflict',
                'message': str(exc), 'question': 'Try a wider search or expand the pool; constraints were not relaxed.' if exhausted else 'Change the indicated constraint or available sets and retry.'}
