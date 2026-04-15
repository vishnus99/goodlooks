# GoodLooks CLI

Local to-do manager with a polished terminal experience.

## Install (editable for development)

```bash
python3 -m pip install -e .
```

## Run

```bash
goodlooks --help
```

## Interactive Features

Get recommendation steps for a task:

```bash
goodlooks recommend --id 2
```

Run recommendations with a local Ollama model (no OpenAI key required):

```bash
# start/manage Ollama directly from GoodLooks
goodlooks ollama start
goodlooks ollama status
# one-time: pull a local model (still required)
ollama pull llama3.1

# use Ollama provider for the recommender
export GOODLOOKS_RECOMMENDER_PROVIDER=ollama
export GOODLOOKS_LLM_MODEL=llama3.1
# optional if Ollama is on a different host/port
# export OLLAMA_BASE_URL=http://127.0.0.1:11434

goodlooks recommend --id 2
```

Provider controls:

```bash
# default provider is openai
export GOODLOOKS_RECOMMENDER_PROVIDER=openai

# force non-LLM fallback recommender
export GOODLOOKS_RECOMMENDER_BACKEND=heuristic
```

Recommender settings are resolved in this order: environment variables -> config file -> defaults.
Config file path: `~/.config/goodlooks/recommender.json` (or `$XDG_CONFIG_HOME/goodlooks/recommender.json`).

Generate this file interactively:

```bash
goodlooks setup
```

Example config:

```json
{
  "backend": "langchain",
  "provider": "auto",
  "model": "llama3.1",
  "timeout_sec": 8,
  "ollama_base_url": "http://127.0.0.1:11434"
}
```

Validate recommender setup:

```bash
goodlooks doctor
goodlooks doctor --fix
goodlooks doctor --json
```

Show a board-like status snapshot directly in the terminal (pending grouped by urgency + recent completed):

```bash
goodlooks status
```

Optional shortcut:

```bash
alias gl='goodlooks'
```