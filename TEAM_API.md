# Team pipeline and live tests

The service builds Gen 9 Doubles Ubers teams from the current local candidate
pool. It does not download data or run continuous refresh jobs. Future tool
adapters can call `team_pipeline.run_request`, `team_chat.run_chat`, or the HTTP endpoints. Updating
the pool remains a separate operation; each request loads one pool snapshot.

## Local test without an LLM

From PowerShell in `C:\dev\showdown-builder`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scratch_runner.py --request-file examples/rain_request.json
```

Expect `status: built`, an interpretation receipt, Showdown import text, score
contributions and explanations. Check Kyogre is included, Groudon absent,
rain evidence meets its minimums, complexity is low, and novelty_count is 1.
Use `scratch_runner.py --list-styles` to see the catalog or `--style "TailRoom"`
to request a build without a model. Use `--prompt` or `--request-file` for the
API response format and evidence.

## Team archetypes

[The runtime catalog](data/team_archetypes.json) defines aliases, required
mechanics, game plans, tradeoffs, and research sources. It feeds the chat tools,
structured build requests, the legacy CLI, and corpus labeling. Search checks
the selected moves, abilities, items, and spreads before returning a match.

An `engine` describes the team's main mechanic. A `play_style` describes its
overall approach. For example, use
`{"request":{"engine":"tailroom","play_style":"bulky_offense"}}` to ask for a
bulky team with options under both Tailwind and Trick Room. In chat, ask for
“a TailRoom team with bulky offense.” SunRoom and RainRoom also have dedicated
engine keys. The API accepts one engine per request.

Other options include pivot control, redirection setup, priority offense,
PaoNite, Grassy Terrain, Coaching, Blizzard spam, Aurora Veil, Beat Up,
Commander + Costar, Dancer, and Gravity Sleep. Hyper offense, bulky offense,
and stall have their own play-style requirements. The engine and play-style
checks report the feature counts used as evidence.

The builder targets Gen 9 Doubles Ubers. The catalog draws from [Smogon Doubles
Ubers teams](https://www.smogon.com/forums/threads/doubles-ubers.3712864/),
[VGC Guide's speed-control guide](https://www.vgcguide.com/speed-control), and
team reports such as [Jayden Gan's Kyogre and Calyrex-Ice
report](https://victoryroad.pro/2025/07/25/jayden-gan-singapore/). Sources from
other doubles formats illustrate mechanics. The local Doubles Ubers candidate
pool supplies the actual sets.

Use the runtime catalog for new archetype knowledge. It takes effect when the
application next starts and keeps the model's choices aligned with the builder's
checks. If held-out chat prompts show recurring interpretation errors, use
fine-tuning to improve the mapping from player language to supported requests.
The shipped corpus snapshot predates the new rules. To prepare examples, run
`python build_team_corpus.py` to refresh the labels and review them before
exporting. `python export_team_training.py --task intent` exports classifier
labels. Examples for the conversational model should use the actual
`build_team` and `update_team` tool schemas with reviewed prompts and arguments.

## Natural-language test

Configure a provider using LLM_SETUP.md, then run:

```powershell
.\.venv\Scripts\python.exe scratch_runner.py --prompt "Build a straightforward rain team with some defensive flexibility and one unusual pick."
```

Inspect the receipt: rain, balance, low complexity, moderate offense and
resilience, novelty_budget 1. The interpretation is model-dependent: if it
asks a focused question, resubmit with the answer included. Explicit required
members, exclusions and pins are best supplied through structured requests
for tool clients. The LLM does not choose raw weights or mutate validated
constraints. Explanations are rendered deterministically from evidence, so
they remain available without a second model call.

## HTTP endpoints for AI/tool clients

```powershell
.\.venv\Scripts\python.exe team_api.py --serve --port 8765
```

In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/v1/profiles
Invoke-RestMethod http://127.0.0.1:8765/v1/pool
Invoke-RestMethod http://127.0.0.1:8765/v1/tools
$body = Get-Content -Raw examples/rain_request.json
$result = Invoke-RestMethod http://127.0.0.1:8765/v1/build -Method Post -ContentType 'application/json' -Body $body
$result | ConvertTo-Json -Depth 30
$result.teams[0].showdown
```

`POST /v1/build` accepts exactly one of `{"request": {...}}` or
`{"prompt": "..."}`. `/v1/profiles` publishes the supported fields, levels,
engine definitions, archetype aliases, defaults and novelty definition. `/v1/pool` publishes
available species and exact set IDs. Required members are objects with `name`
and optional `set_id`; excluded and prioritize are lists of names.

For an explicit variant request such as “Use a fast Expanding Force Armarouge,”
the chat's `get_pool` tool lists each matching species' set IDs, speed mode,
Speed EVs and IVs, and archetype roles. Use a listed `set_id` to request the
same build in a structured request. The human-readable sets are in
[the Showdown import file](data/smogon/candidate_sets.txt).

Response schema version is 1. Domain outcomes use HTTP 200 with `status`:
`built`, `needs_clarification`, `conflict`, or `search_exhausted`. The latter
does not prove impossibility. Non-built outcomes include a message and a
focused question or retry instruction. Malformed HTTP JSON gets 400; oversized
bodies get 413; provider/data failures get 503. The server binds to loopback
only and processes one request at a time. It is a local development interface,
not an authenticated public deployment. Stop it with Ctrl+C.

## Conversational model and tool bridge

`team_chat.py` and `POST /v1/chat` implement the master-prompt flow through three
allowlisted tools: `get_profiles`, `get_pool`, and `build_team`. `GET /v1/tools`
publishes their schemas. External clients can map discovery to `/v1/profiles`
and `/v1/pool`, and submit the `build_team` arguments directly to `/v1/build`.
The in-process bridge uses one pool snapshot for the entire turn.
The server uses the model and protocol selected in the [model setup guide](LLM_SETUP.md).
On a follow-up with a previous receipt, `update_team(changes)` replaces the
build tool. The model submits only the latest requested changes; code keeps
all other fields and merges individual preference dimensions. Lists in changes
replace their previous lists, so changing required/excluded members needs the
complete revised list. To reset preferences, set the dimensions back to their
documented levels; an empty preferences patch preserves the previous levels.
This contextual tool is managed inside `/v1/chat`; external structured clients
can submit a complete updated request to `/v1/build`.

```powershell
$body = @{ messages = @(
    @{ role = 'user'; content = 'Build a straightforward rain team with Kyogre, without Groudon, and one unusual pick.' }
) } | ConvertTo-Json -Depth 10
$chat = Invoke-RestMethod http://127.0.0.1:8765/v1/chat -Method Post -ContentType 'application/json' -Body $body
$chat.reply
$chat.result.teams[0].showdown
$chat.result.interpretation
$chat.tool_trace
```

For follow-ups, send the earlier user/assistant text messages plus the new user
message. Use `grounded_reply` as assistant history after a build; it contains
the actual interpretation receipt. The server holds no conversation state.
The bridge uses the latest receipt as the previous request state and keeps the
last team's evidence available, avoiding replay of superseded build prompts.
It accepts 1–20 messages totaling at most 24000 characters, ending in a user
message. System messages, tool messages, and tool-call fields from HTTP clients
are rejected. Start a new conversation by sending only the new user message.

Responses contain `reply` and `tool_trace`, plus the authoritative
pipeline `result` and `grounded_reply` after a build attempt. After a build,
`reply` shows the team overview, Showdown import, and limitations in order.
The model can choose a main job for each
member only from its exact capability labels using an internal `present_team`
tool. Code validates every label and renders the explanation; unverified prose
is discarded. `result` contains the validated receipt, Showdown team, and full evidence.
Inspect the receipt: syntactically valid interpretation can still misunderstand
natural language. The assistant asks about unsupported formats, engine
combinations, type constraints and custom sets.
Evidence remains heuristic and does not establish matchup strength.
Clients should use `result.status == built` to identify an actual team. A plain
`status: reply` is conversational text or a question, not a completed build.

Each turn executes at most six program tool calls and stops on its first build
outcome, followed by at most one presentation-selection call.
Compact chat mode renders roles directly from the builder's evidence.
Conflicts/questions are returned verbatim; there is no automatic retry with
relaxed constraints. A failed model presentation preserves the built team and uses
the evidence renderer. No model output is evaluated as Python or shell code.
The chat route uses the same loopback-only server and HTTP error rules as build.

## Scoring and evidence limits

Composition is baseline weights, engine completion/reward, play-style profile,
then bounded preference terms. Each preference contribution is at most 0.2 in
magnitude per selected member; play-style weight and novelty usage adjustments
are code-owned. Offense uses offensive EV investment without setup; resilience
uses defensive/sustain features; tempo uses support/speed-control features;
complexity uses setup and partner dependencies. These are declared heuristics,
not battle simulations or predictions of player skill. Balance uses neutral
dimension levels. Low complexity penalizes dependency-heavy choices but cannot
remove required engine support.

Ask for a quirky or less common team to set `preferences.novelty` to `high`.
The builder reverses the usual usage score so lower-usage species rank higher
on that component, and gives a bonus to species in the bottom usage quartile.
Synergy, role coverage, and team constraints still apply. Use `novelty_budget`
for an exact number of bottom-quartile species, including ties. A required
member can fill that budget. Both settings measure species usage.

Selection evidence is an incremental beam-search score, not a counterfactual
proof that a member is uniquely optimal. Capability statements use selected
moves/abilities/items and dependency rules, not scoring bonuses. Team benefits
are conditional on support remaining active. Legal pool validation, field
support checks and species constraints do not establish matchup strength.

For an intentional conflict, include Kyogre in both required and excluded.
Expect `conflict` and an instruction to remove it from one list. For a bounds
test, use `{"request":{"preferences":{"offense":999}}}`; expect
`needs_clarification`, with no team built.
