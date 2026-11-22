"""Bounded LLM/tool bridge. Run python team_chat.py to talk to the builder."""
import argparse
from copy import deepcopy
import json
import os

from llm import chat_llm
from export_sets import showdown_set
from models import build_pool, FORMAT_ID
from search import name_of
from team_pipeline import ARCHETYPE_GUIDANCE, NOVELTY_GUIDANCE, catalog, request_schema, run_request
from team_styles import archetype_set_roles, set_features, speed_mode

MAX_TOOL_CALLS = 6
MAX_MESSAGES = 20
MAX_HISTORY_CHARS = 24000

# Derived from the approved master prompt: interpretation -> validation ->
# search -> evidence -> explanation. Tools expose data, never executable code.
SYSTEM_PROMPT = """You are the conversational interface to a Pokemon Showdown team builder.
The only supported format is Gen 9 Doubles Ubers. Use the tools to interact with
the program. To build a team you MUST call build_team with a structured request.
Never invent a team, set ID, score, or tool result. Never claim a build succeeded
without a built result. Get available species or exact set IDs with get_pool;
get_profiles explains the supported engines and their required support.
If a user specifies an exact set variant by role, use get_pool to find an
available set that matches the requested speed mode, moves, and other details.
Only pin a listed ID. Leave set selection to the builder for general requests.

Separate immutable required members (objects with name and optional set_id),
excluded Pokemon, one team engine, play_style, and bounded preferences.
Preserve every explicit required member, exclusion and pin, including those in
earlier conversation turns unless the user explicitly changes them. Submit
contradictions unchanged so code can explain the conflict. Do not silently
relax constraints, replace unavailable Pokemon, or invent numerical weights.
For a follow-up, latest explicit edits take precedence over the previous receipt.
Use update_team when offered, sending only the requested changes. Example: after offense moderate, "change
offense preference to high, keep everything else" MUST set preferences.offense
to high while preserving other preferences, required/excluded members and engine.
Required means must include; prioritize means a soft preference. Use the
documented defaults for unspecified fields: engine none, play_style balance,
empty required/excluded/prioritize/preferences, novelty_budget null.
Straightforward/easy means complexity low, not offense low. Defensive flexibility
means balance and resilience moderate.
For ambiguous requirements ask one focused question. Unsupported formats, type
constraints, custom sets or multiple engines need clarification; do not omit them
or pretend they are supported. Never execute or suggest executing model code.

The program displays successful builds in this order: team plan and member
roles, Showdown import, then limitations and tradeoffs. Use present_team when
offered to select exact evidence labels for the member roles. Failed builds
need a focused question, never automatic changes. Keep replies concise.
""" + '\n' + NOVELTY_GUIDANCE + '\n' + ARCHETYPE_GUIDANCE

COMPACT_SYSTEM_PROMPT = """You build Gen 9 Doubles Ubers teams with the program tools.
For a new team call build_team. Always fill engine. Use the requested mechanic
(rain, sun, trick_room, etc.) or none when the user gives no mechanic. Put
Pokemon the user must have in required and Pokemon they want to avoid in
excluded. Use prioritize for soft preferences. Leave other unspecified fields
out. Straightforward means complexity low.
Example: "Build a sun team with Koraidon and no Pelipper"
calls build_team with {"request":{"engine":"sun","required":[{"name":"Koraidon"}],
"excluded":["Pelipper"]}}.
If update_team is offered, send only the latest changes and preserve other
constraints. Use get_pool to verify any requested set variant before pinning
set_id. Use get_profiles to look up team styles. Ask a focused question about
unsupported or ambiguous constraints. Never invent Pokemon, sets or tool results.
""" + NOVELTY_GUIDANCE + '\n' + ARCHETYPE_GUIDANCE

PRESENTATION_PROMPT = """Call present_team once. For each member, choose one
exact capability label from the tool's allowed values. Prefer a concrete role
that serves this team's plan or helps a named teammate. Examples:
- On rain, choose "Provides rain" for a Drizzle setter over a generic attack label.
- On a team that needs protection from spread moves, choose
  "Support through Wide Guard" over "Setup through Iron Defense" when both are offered.
- For a speed-control role, choose "Speed control through Icy Wind" when offered.
These are examples of how to choose. Use only the actual members and labels
in the current tool schema. Return the tool call with no prose. The program
renders usage, mechanics, teammate benefits, import text, and limitations."""

SHARED_LIMITATION = ('Availability of support does not guarantee it stays active; '
                     'no matchup simulation was performed.')


def tool_definitions(previous_request=None, *, compact=False):
    def tool(name, description, parameters):
        return {'type': 'function', 'function': {
            'name': name, 'description': description, 'parameters': parameters}}
    request = request_schema()
    if compact and previous_request is None:
        properties = request['properties']
        request['properties'] = {'engine': properties['engine'], **properties}
        request['required'] = ['engine']
    build_tool = tool('build_team', 'Validate and build a team. Preserve ALL explicit constraints. Returns team and evidence or a conflict/question.',
                      {'type': 'object', 'properties': {'request': request},
                       'required': ['request'], 'additionalProperties': False})
    if previous_request is not None:
        build_tool = tool('update_team',
            'Apply ONLY changes requested in the LATEST user message to the previous request. Code preserves all other fields and merges preferences. Do not copy the previous request into changes.',
            {'type': 'object', 'properties': {'changes': request},
             'required': ['changes'], 'additionalProperties': False})
    return [
        tool('get_profiles', 'Read strategy objectives, support requirements, defaults and preference definitions.',
             {'type': 'object', 'properties': {}, 'additionalProperties': False}),
        tool('get_pool', 'List available Pokemon; supply name to inspect exact set IDs, speed modes, roles, and moves.',
             {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'additionalProperties': False}),
        build_tool,
    ]


def pool_catalog(pool, name=None):
    if name is None:
        return {'format': FORMAT_ID, 'pokemon': [name_of(c) for c in pool]}
    if not isinstance(name, str) or not name.strip():
        return {'status': 'invalid_tool_arguments', 'message': 'name must be a nonempty string.'}
    matches = [c for c in pool if name_of(c).casefold() == name.strip().casefold()]
    if not matches:
        return {'status': 'conflict', 'message': f'Pokemon not available in current pool: {name}'}
    sets = []
    for s in matches[0].pokemon_sets:
        pokemon = showdown_set(s)
        features = set_features(pokemon)
        mode = speed_mode(pokemon, features)
        sets.append({'set_id': s.set_id, 'label': s.label, 'moves': list(s.moves),
                     'ability': s.ability, 'item': s.held_Item, 'nature': s.nature,
                     'speed_mode': mode, 'speed_evs': pokemon['evs']['spe'], 'speed_ivs': pokemon['ivs']['spe'],
                     'archetype_roles': sorted(archetype_set_roles(features, mode)),
                     'scenarios': list(s.scenario_tags), 'evidence_status': s.evidence_status,
                     'team_notes': list(s.team_notes)})
    return {'name': name_of(matches[0]), 'species_usage': matches[0].usage, 'sets': sets}


def execute_tool(name, arguments, pool, previous_request=None):
    """Allowlisted dispatcher. build_team cannot re-enter prompt interpretation."""
    if not isinstance(arguments, dict):
        return {'status': 'invalid_tool_arguments', 'message': 'Arguments must be an object.'}
    if name == 'get_profiles' and not arguments:
        spec = catalog()
        return {
            'format': spec['format'], 'defaults': spec['defaults'], 'play_styles': spec['play_styles'],
            'play_style_details': {name: {k: p[k] for k in ('aliases', 'objective', 'required_support')}
                                   for name, p in spec['play_style_details'].items()},
            'engines': {name: {k: p[k] for k in ('aliases', 'objective', 'required_support')}
                        for name, p in spec['engines'].items()},
            'novelty_definition': spec['novelty_definition'],
        }
    if name == 'get_pool' and not set(arguments) - {'name'}:
        return pool_catalog(pool, arguments.get('name'))
    if name == 'build_team' and previous_request is None and set(arguments) == {'request'}:
        return run_request({'request': arguments['request']}, pool=pool)
    if name == 'update_team' and previous_request is not None and set(arguments) == {'changes'}:
        changes = arguments['changes']
        if not isinstance(changes, dict):
            return {'status': 'invalid_tool_arguments', 'message': 'changes must be an object.'}
        updated = deepcopy(previous_request)
        updated.update(deepcopy(changes))
        if isinstance(changes.get('preferences'), dict) and isinstance(previous_request.get('preferences', {}), dict):
            updated['preferences'] = {**previous_request.get('preferences', {}), **changes['preferences']}
        return run_request({'request': updated}, pool=pool)
    return {'status': 'invalid_tool_arguments', 'message': 'Unknown tool or undocumented arguments.'}


def render_result(result, jobs=None):
    """Authoritative, grounded text also used when the model cannot summarize."""
    if result['status'] != 'built':
        return '\n'.join(filter(None, (result['message'], result.get('question'))))
    config = result['interpretation']
    style = config['play_style'].replace('_', ' ').capitalize()
    engine = config['engine'].replace('_', ' ').title()
    title = f'{engine} team ({style.lower()})' if config['engine'] != 'none' else f'{style} team'
    lines = [f'{title} | Gen 9 Doubles Ubers']
    for team in result['teams']:
        explanation = team['explanation']
        checks = explanation['request_satisfaction']
        lines.extend(['', f"Game plan: {explanation['game_plan']}"])
        if config['required']:
            required = [f"{m['name']} ({m['set_id']})" if m['set_id'] else m['name']
                        for m in config['required']]
            lines.append('Required members: ' + ', '.join(required))
        if config['excluded'] and checks['excluded_absent']:
            lines.append('Excluded: ' + ', '.join(config['excluded']))
        if config['prioritize']:
            selected = ', '.join(checks['prioritized_selected']) or 'none'
            lines.append(f'Priority picks selected: {selected}')
        if config['preferences']:
            preferences = ', '.join(f'{name} {level}' for name, level in config['preferences'].items())
            lines.append(f'Interpreted preferences: {preferences}')
        if config['novelty_budget'] is not None or checks['novelty_count']:
            lines.append(f"Unusual picks: {checks['novelty_count']} (bottom quarter of pool usage)")
        lines.extend(['', 'Team overview'])
        for member in explanation['members']:
            mechanics = member['mechanics_evidence']
            job = jobs[member['name']] if jobs else member['main_job']
            lines.extend(['', member['name'], f"  Main job: {job.rstrip('.')}."])
            usage = member.get('species_usage')
            if usage is not None:
                unusual = ' (unusual pick)' if member['unusual'] else ''
                lines.append(f'  Species usage: {usage:.2%}{unusual}')
            lines.append(f"  Ability: {mechanics['ability']} | Item: {mechanics['item'] or 'None'}")
            other_roles = [label.rstrip('.') for label in member['capabilities'] if label != job]
            if other_roles:
                lines.append('  Also offers: ' + ' / '.join(other_roles))
            lines.extend(f"  Helps {b['teammate']}: {b['how']}" for b in member['teammate_benefits'])
            lines.append(f"  Why selected: {member['selection_evidence']['reason'].rstrip('.')}.")
        lines.extend(['', 'Pokemon Showdown import', '', team['showdown'], '',
                      'Limitations and tradeoffs', f"Team: {explanation['tradeoff']}"])
        for member in explanation['members']:
            notes = dict.fromkeys(note for note in member['limitations'] if note != SHARED_LIMITATION)
            if notes:
                lines.extend(['', member['name'], *(f'  - {note}' for note in notes)])
        lines.extend(['', 'Field support can be disrupted. Matchups were not simulated.'])
    return '\n'.join(lines)


def _presentation_tool(result):
    members = result['teams'][0]['explanation']['members']
    jobs = {'type': 'object', 'additionalProperties': False,
            'required': [m['name'] for m in members],
            'properties': {m['name']: {'type': 'string', 'enum': m['capabilities']} for m in members}}
    return {'type': 'function', 'function': {
        'name': 'present_team',
        'description': 'Choose the most relevant main job for EACH selected member from its exact evidence labels. Code renders the explanation.',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'required': ['jobs'], 'properties': {'jobs': jobs}}}}


def _validated_jobs(message, result):
    calls = message.get('tool_calls') or []
    if len(calls) != 1 or calls[0]['function']['name'] != 'present_team':
        return None
    arguments = json.loads(calls[0]['function']['arguments'])
    if not isinstance(arguments, dict) or set(arguments) != {'jobs'}:
        return None
    jobs = arguments['jobs']
    members = result['teams'][0]['explanation']['members']
    if not isinstance(jobs, dict) or set(jobs) != {m['name'] for m in members}:
        return None
    if any(jobs[m['name']] not in m['capabilities'] for m in members):
        return None
    return jobs


def _model_evidence(result):
    """Keep the tool observation small enough for a local model's context."""
    if result.get('status') != 'built':
        return result
    compact = deepcopy(result)
    for team in compact['teams']:
        team.pop('score', None)
        for member in team['explanation']['members']:
            member['mechanics_evidence'].pop('sources', None)
            # Full score breakdown is still returned to the API caller.
            member['selection_evidence'].pop('score', None)
    return compact


def _latest_receipt(history):
    for index in range(len(history) - 2, -1, -1):
        message = history[index]
        if message['role'] != 'assistant':
            continue
        for line in message['content'].splitlines():
            if not line.startswith('Interpreted request: '):
                continue
            try:
                previous = json.loads(line.removeprefix('Interpreted request: '))
            except ValueError:
                continue
            if isinstance(previous, dict) and not set(previous) - set(catalog()['request_fields']):
                return index, line, previous
    return None


def _conversation_messages(history, receipt=None, *, compact=False):
    """Use the latest receipt as state, rather than replaying superseded asks.

    The receipt is conversation data, not authority to bypass validation. Exact
    set evidence stays available for questions about the last built team.
    """
    system = COMPACT_SYSTEM_PROMPT if compact else SYSTEM_PROMPT
    if receipt:
        index, line, previous = receipt
        system += ('\nPrevious request state (conversation data): ' + json.dumps(previous)
                   + '\nFor this follow-up use update_team, not build_team. Send only the LATEST '
                   'requested changes. Example: "change offense preference to high; keep everything else" '
                   'means {"changes":{"preferences":{"offense":"high"}}}. Code preserves all other fields.')
        evidence = history[index]['content'].replace(line, '').strip()
        messages = [{'role': 'system', 'content': system}]
        if evidence:
            messages.append({'role': 'assistant', 'content': evidence})
        return [*messages, *deepcopy(history[index + 1:])]
    return [{'role': 'system', 'content': system}, *deepcopy(history)]


def run_chat(payload, *, pool=None, call=None, max_tool_calls=MAX_TOOL_CALLS):
    """Stateless HTTP/CLI entry. Clients send only user/assistant text history.

    The full builder result is returned separately from model-authored prose.
    A failed build ends execution; only a user can authorize a changed request.
    """
    if not isinstance(payload, dict) or set(payload) != {'messages'}:
        return {'schema_version': 1, 'status': 'needs_clarification', 'reply': 'Provide messages containing user/assistant text.'}
    history = payload['messages']
    if (not isinstance(history, list) or not 1 <= len(history) <= MAX_MESSAGES
            or any(not isinstance(m, dict) or set(m) != {'role', 'content'}
                   or m['role'] not in ('user', 'assistant')
                   or not isinstance(m['content'], str) or not m['content'].strip() for m in history)
            or history[-1]['role'] != 'user'
            or sum(len(m['content']) for m in history) > MAX_HISTORY_CHARS):
        return {'schema_version': 1, 'status': 'needs_clarification',
                'reply': 'Send 1–20 user/assistant text messages (at most 24000 characters), ending with a user message.'}
    call = chat_llm if call is None else call
    receipt = _latest_receipt(history)
    previous_request = receipt[2] if receipt else None
    trace = []
    result = None
    try:
        mode = os.environ.get('LLM_CHAT_MODE', 'full').strip().lower()
        if mode not in {'full', 'compact'}:
            raise ValueError('LLM_CHAT_MODE must be full or compact.')
        compact = mode == 'compact'
        available_tools = tool_definitions(previous_request, compact=compact)
        messages = _conversation_messages(history, receipt, compact=compact)
        pool = build_pool() if pool is None else list(pool)
        used = 0
        while used < max_tool_calls:
            message = call(messages, tools=available_tools)
            calls = message.get('tool_calls') or []
            if not calls:
                reply = (message.get('content') or '').strip()
                if not reply:
                    raise ValueError('Empty reply')
                return {'schema_version': 1, 'status': 'reply', 'reply': reply, 'tool_trace': trace}
            # A batch is counted against the same budget. Never run a partial
            # build after a previous build or a failed constraint validation.
            if len(calls) > max_tool_calls - used:
                break
            messages.append(message)
            for index, tool_call in enumerate(calls):
                used += 1
                function = tool_call.get('function', {})
                name = function.get('name')
                try:
                    arguments = json.loads(function.get('arguments', ''))
                except (ValueError, TypeError):
                    arguments = None
                observation = execute_tool(name, arguments, pool, previous_request)
                trace.append({'name': name, 'arguments': arguments, 'status': observation.get('status', 'ok')})
                build_finished = (name in ('build_team', 'update_team')
                                  and observation.get('status') != 'invalid_tool_arguments')
                if not build_finished or not compact:
                    messages.append({'role': 'tool', 'tool_call_id': tool_call.get('id', ''),
                                     'content': json.dumps(_model_evidence(observation), allow_nan=False)})
                if build_finished:
                    result = observation
                    if compact:
                        break
                    # Stop tool execution on the first build outcome. Replies
                    # for pending calls keep the provider's transcript valid.
                    for pending in calls[index + 1:]:
                        messages.append({'role': 'tool', 'tool_call_id': pending.get('id', ''),
                                         'content': '{"status":"skipped","message":"Build already completed; ask the user before changes."}'})
                    break
            if result is not None:
                break
        if result is None:
            return {'schema_version': 1, 'status': 'needs_clarification', 'tool_trace': trace,
                    'reply': 'The assistant reached its tool limit without building. Please specify the engine and any required/excluded Pokemon.'}
        # Conflicts are shown verbatim; the model cannot obscure or relax them.
        jobs = None
        method = 'deterministic evidence renderer'
        if result['status'] == 'built' and not compact:
            messages.append({'role': 'user', 'content': PRESENTATION_PROMPT})
            try:
                final = call(messages, tools=[_presentation_tool(result)])
                jobs = _validated_jobs(final, result)
                if jobs:
                    method = 'llm-selected evidence labels, deterministic explanation renderer'
            except Exception:
                pass  # A successful build survives a summary provider failure.
        reply = grounded = render_result(result, jobs)
        if result['status'] == 'built':
            config = result['interpretation']
            receipt = {key: config[key] for key in catalog()['request_fields']}
            grounded = 'Interpreted request: ' + json.dumps(receipt) + '\n' + reply
        return {'schema_version': 1, 'status': result['status'], 'reply': reply,
                'result': result, 'grounded_reply': grounded, 'explanation_method': method, 'tool_trace': trace}
    except Exception:
        return {'schema_version': 1, 'status': 'service_error', 'tool_trace': trace,
                'reply': 'The configured model or team data is unavailable. Check your setup in LLM_SETUP.md and retry.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt', help='Run one chat turn and exit')
    parser.add_argument('--json', action='store_true', help='Return the complete JSON result for a single prompt')
    args = parser.parse_args(argv)
    if args.json and args.prompt is None:
        parser.error('--json requires --prompt')
    history = []
    if args.prompt is None:
        print('Team builder chat. Type /reset to start over, /quit to exit.', flush=True)
    while True:
        try:
            prompt = args.prompt if args.prompt is not None else input('You: ')
        except (EOFError, KeyboardInterrupt):
            return 0
        if prompt.strip() == '/quit':
            return 0
        if prompt.strip() == '/reset':
            history.clear()
            continue
        if not prompt.strip():
            if args.prompt is not None:
                parser.error('--prompt must not be empty')
            continue
        if len(history) >= MAX_MESSAGES - 1 or sum(len(m['content']) for m in history) + len(prompt) > MAX_HISTORY_CHARS:
            print('Conversation limit reached. Use /reset and restate your constraints.')
            if args.prompt is not None:
                return 2
            continue
        history.append({'role': 'user', 'content': prompt})
        response = run_chat({'messages': history})
        text = response.get('grounded_reply', response['reply'])
        if args.json:
            print(json.dumps(response, indent=2))
        else:
            print('\n' + response['reply'], flush=True)
        if args.prompt is not None:
            return 0 if response['status'] in ('built', 'reply') else 2
        # Store the receipt and actual team rather than an unsupported claim
        # from the model as context for subsequent user edits.
        history.append({'role': 'assistant', 'content': text})


if __name__ == '__main__':
    raise SystemExit(main())
