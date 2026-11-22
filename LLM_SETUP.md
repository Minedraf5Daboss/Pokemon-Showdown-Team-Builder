# Model setup

The project defaults to **local Ollama with `llama3.1:8b`**. You can load a
different Ollama model, connect to a compatible server, or use a hosted model.
Settings apply to programs launched from the same PowerShell session, including
`team_chat.py`, `scratch_runner.py --prompt`, and the local HTTP server. The
app reads environment variables directly.

## Ollama on Windows

1. Install [Ollama](https://ollama.com/download/windows) and start the app.
2. If not already downloaded, run `ollama pull llama3.1:8b`.
3. In PowerShell, from the project directory, run:

```powershell
.\.venv\Scripts\python.exe llm.py
.\.venv\Scripts\python.exe team_chat.py
```

The first command makes a hello-world call. The second opens a conversation:
ask for a team, answer clarifying questions, or request changes. `/reset` starts
over and `/quit` exits. A build starts with the game plan and each Pokémon’s
role, species usage, and teammate benefits. The Showdown import follows in
its own section, with limitations and tradeoffs at the end. The model chooses
main jobs from the builder’s evidence, and the program formats the response.

Follow-up changes use a bounded update tool: the model supplies the changes and
code preserves the remaining request fields. For example, after a build, try
`Change offense preference to high. Keep everything else.`

For one prompt:

```powershell
.\.venv\Scripts\python.exe team_chat.py --prompt "Build a straightforward rain team with Kyogre, without Groudon, and one unusual pick."
```

Run the command as shown for readable text. Add `--json` for the full
machine-readable result, interpretation receipt, and tool trace.
`scratch_runner.py --prompt`
remains the single-request interpretation interface; use `team_chat.py` for
tool use and follow-up turns. Local timeouts default to 120 seconds per attempt.
If the server is not running, start the Ollama app or run `ollama serve`.

## Smaller local models

Use the model's exact name from `ollama list`. The following settings use
[Llama 3.2 3B](https://ollama.com/library/llama3.2:3b) with a 4,096-token
context and the compact chat prompt:

```powershell
ollama pull llama3.2:3b
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "llama3.2:3b"
$env:LLM_CHAT_MODE = "compact"
$env:LLM_CONTEXT_TOKENS = "4096"
$env:LLM_MAX_OUTPUT_TOKENS = "512"
.\.venv\Scripts\python.exe team_chat.py --prompt "Build a rain team with Kyogre. Exclude Groudon."
```

For a smaller download, try [Llama 3.2 1B](https://ollama.com/library/llama3.2:1b)
or [Qwen 2.5 0.5B](https://ollama.com/library/qwen2.5:0.5b). Pull it with
Ollama and set `LLM_MODEL` to its full tag. Very small models can miss
constraints, especially in long conversations or complex requests. Inspect
the interpretation with `--json`. A [structured request](TEAM_API.md)
lets you specify exact constraints and build without a model.

Compact mode keeps discovery, builds, and follow-up edits. It uses a shorter
instruction prompt and renders team roles directly from the builder's evidence,
saving a separate model call on successful builds.

## Hosted providers

Set `LLM_PROVIDER`, `LLM_MODEL`, and the matching API key in the same
PowerShell session. Use the model ID exactly as your provider lists it.

| `LLM_PROVIDER` | API key variable | API documentation |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) |
| `anthropic` | `ANTHROPIC_API_KEY` | [Claude's OpenAI-compatible endpoint](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk) |
| `gemini` | `GEMINI_API_KEY` | [Gemini's OpenAI-compatible endpoint](https://ai.google.dev/gemini-api/docs/openai) |
| `groq` | `GROQ_API_KEY` | [Groq's OpenAI-compatible endpoint](https://console.groq.com/docs/openai) |
| `openrouter` | `OPENROUTER_API_KEY` | [OpenRouter's model and API reference](https://openrouter.ai/docs/api_reference/overview) |

```powershell
$env:LLM_PROVIDER = "openai"
$env:LLM_MODEL = "gpt-4.1-mini"
# Set OPENAI_API_KEY in this PowerShell session before running these commands.
.\.venv\Scripts\python.exe llm.py
.\.venv\Scripts\python.exe team_chat.py
```

OpenAI also accepts the existing `OPENAI_MODEL` setting, which defaults to
`gpt-4.1-mini`. When both model variables are set, `LLM_MODEL` takes priority.
OpenAI's standalone text call uses Responses. The hosted chat presets use
Chat Completions. Choose a model with Chat Completions tool support or set
`LLM_TOOL_PROTOCOL=prompt`.

## Custom endpoints

Use `LLM_PROVIDER=compatible` for [LM Studio](https://lmstudio.ai/docs/developer/openai-compat)
or another local or hosted endpoint that implements OpenAI Chat Completions.
Set `LLM_API_KEY` if the endpoint requires a key.

```powershell
$env:LLM_PROVIDER = "compatible"
$env:LLM_BASE_URL = "http://localhost:1234/v1"
$env:LLM_MODEL = "your-server-model-id"
.\.venv\Scripts\python.exe llm.py
.\.venv\Scripts\python.exe team_chat.py
```

## Chat settings

| Setting | Values and defaults |
| --- | --- |
| `LLM_TOOL_PROTOCOL` | `json` uses [Ollama's schema-constrained actions](https://docs.ollama.com/capabilities/structured-outputs) and defaults on for `local` and `ollama`. `native` sends standard function tools and defaults on for hosted presets. `prompt` uses ordinary chat messages containing JSON actions and defaults on for `compatible`. |
| `LLM_CHAT_MODE` | `full` by default. `compact` shortens the prompt and skips the presentation call. |
| `LLM_CONTEXT_TOKENS` | Ollama context size in `json` mode. Defaults to 16,384. Configure the context window in your model server for other modes. |
| `LLM_MAX_OUTPUT_TOKENS` | Output limit. Defaults to 1,800 for chat. The standalone text call uses the provider default unless set. |
| `LLM_TIMEOUT_SECONDS` | Timeout per attempt. Defaults to 120 seconds for `local`, `ollama`, and `compatible`, and 30 seconds for hosted presets. |

`local` retains the existing Ollama configuration, including the base URL
`http://localhost:11434/v1`. With `ollama`, the base URL may also be specified
as `http://localhost:11434`. An explicit `local` and `native` configuration
continues to work with another compatible server. Each call stays with the
configured provider if it fails.
