# Pokémon Showdown Team Builder

A Python tool for building competitive Pokémon Showdown teams for **Gen 9 Doubles Ubers**. Give it a strategy, Pokémon to include or exclude, and preferences such as how complex the team should be. It returns a six-member team you can import into Showdown, along with an explanation of the choices.

**Project status:** First development pass. I'm moving into bug fixing and checking whether the generated teams satisfy the request and make sense in play.

## Example build

Build a straightforward rain team with Kyogre, no Groudon, and exactly one lower-usage pick. The [example request](examples/rain_request.json) captures those constraints as JSON.

A run against the current local catalog returned **Kyogre, Tornadus, Miraidon, Incineroar, Calyrex-Ice, and Zebstrika**. The result confirmed that Groudon was excluded and counted exactly one lower-usage pick, two rain setters, and one teammate that benefits from rain. The response includes a Showdown import block for the full team.

## How it works

1. **Interpret the request.** The CLI and local HTTP API accept explicit constraints. Optional model-backed chat maps a natural-language request or follow-up to the same constraints.
2. **Search for a team.** Beam search considers specific movesets, scores candidates for usage, teammate fit, and role coverage, and checks mechanics such as weather support and Trick Room.
3. **Show the evidence.** The result includes the interpreted request, the selected team's import text, and the mechanics and scoring evidence used to explain its choices.

The optional language model interprets what the player wants. Python validates the request, selects the team, and produces the evidence. Scoring measures the selected team's fit against those rules and heuristics.

## Run locally

The current local catalog contains **7,704 movesets across 535 species and forms**, with variants for roles such as fast attackers, Trick Room setters, and minimum-Speed support. The CLI, API, and test suite use the local candidate pool and supporting data. A fresh clone requires the two data directories listed below to run builds. You can browse the supported strategies in the [archetype catalog](data/team_archetypes.json).

With Python 3.10 or later and the existing data at `data/smogon/` and `data/team_corpus/`, install the dependencies and run:

```powershell
python -m pip install -r requirements-dev.txt
python scratch_runner.py --list-styles
python scratch_runner.py --request-file examples/rain_request.json
python -m pytest -q
```

The JSON request runs without a model. To find a specific moveset, run `python scratch_runner.py --list-sets Armarouge`. Use its `set_id` in the structured request to pin that variant. The chat can also look up movesets by role, such as a fast Armarouge with Expanding Force, using the catalog's moves, Speed EVs and IVs, and role tags. See the [team API guide](TEAM_API.md) for the request format.

## Build teams with a model

### Default Ollama setup

The builder defaults to Ollama running **Llama 3.1 8B** locally. You can describe a team in plain language and revise it in the same conversation. The local setup needs no cloud API key.

1. Install and launch [Ollama for Windows](https://ollama.com/download/windows). Keep it running in the background.
2. Open a new PowerShell window and download the [Llama 3.1 8B model](https://ollama.com/library/llama3.1:8b). The model download is about 4.9 GB.

   ```powershell
   ollama pull llama3.1:8b
   ollama list
   ```

   Check that `llama3.1:8b` appears in the list.

3. In PowerShell, open the project folder and install the Python dependencies. For team builds, use a checkout with the local data described above. You can skip creating the virtual environment if the project already has one.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Check the connection to Ollama, then start the builder's interactive chat.

   ```powershell
   .\.venv\Scripts\python.exe llm.py
   .\.venv\Scripts\python.exe team_chat.py
   ```

   The first command sends a test prompt to the model. In the builder chat, try **“Build a straightforward rain team with Kyogre, without Groudon, and one unusual pick.”** After a team is returned, try **“Change offense preference to high. Keep everything else.”** The chat prints the game plan, Pokémon roles, and import text you can paste into the Pokémon Showdown Teambuilder. Type `/reset` to start over or `/quit` to exit.

For a single request from PowerShell, run:

```powershell
.\.venv\Scripts\python.exe team_chat.py --prompt "Build a straightforward rain team with Kyogre, without Groudon, and one unusual pick."
```

If the connection test fails, check that Ollama is running on port 11434 and that the model appears in `ollama list`. Open Ollama from the Start menu if needed.

Add `--json` to a single prompt to inspect the interpreted constraints and tool calls alongside the team.

### Smaller Ollama models

For a shorter chat prompt and a smaller context window, try Llama 3.2 3B in compact mode. Run these commands from the project folder in the same PowerShell session. Compact mode supports team builds and follow-up edits and uses the builder's evidence to describe team roles.

```powershell
ollama pull llama3.2:3b
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "llama3.2:3b"
$env:LLM_CHAT_MODE = "compact"
$env:LLM_CONTEXT_TOKENS = "4096"
$env:LLM_MAX_OUTPUT_TOKENS = "512"
.\.venv\Scripts\python.exe team_chat.py
```

### Compatible servers and hosted models

Use `LLM_PROVIDER=compatible` for LM Studio or another server that implements the OpenAI Chat Completions API. Set `LLM_BASE_URL` and `LLM_MODEL` to match that server. For OpenAI, Anthropic, Gemini, Groq, or OpenRouter, select the matching `LLM_PROVIDER` and set the model ID and provider API key in PowerShell. The same settings apply to the chat CLI and the local API. See the [model setup guide](LLM_SETUP.md) for provider-specific settings and other small models.

## Data sources

This is an independent fan project. The local data pipeline uses [Smogon usage statistics](https://www.smogon.com/stats/) and references [Pokémon Showdown's validator](https://github.com/smogon/pokemon-showdown) for legality checks.

After regenerating set recommendations, run `python refresh_pool.py --sets-only` to update the local movesets when species coverage and usage evidence stay the same.
