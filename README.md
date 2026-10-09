# Qibocal MCP

A minimal stdio FastMCP bridge and a runnable Pydantic AI calibration agent for
Qibocal. Execute a protocol, inspect its report images and fitted results, then
explicitly decide whether to update the platform. Run locally or through
SSH/SLURM without an HTTP service.

This is a single-platform draft for a trusted agent, **not a hardware safety
layer**. Supply hardware limits and an execution budget; acquisitions can affect
hardware even when fitted updates are disabled.

## Installation

```sh
uv sync --extra agent
# Kaleido needs Chrome to render Plotly reports. Skip if Chrome is installed.
uv run plotly_get_chrome -y
```

Python 3.12 or 3.13, platform definitions, hardware drivers and Chrome are
required. `devenv shell` provides Python 3.12 and Nix-managed Chrome, configures
Kaleido to use it and syncs all extras; no `plotly_get_chrome` step is needed
inside that environment. Only the machine running the agent needs the agent
extra and model credentials.

## Quick launch

```sh
export QIBOLAB_PLATFORMS=/path/to/your/platforms
uv run qibocal-mcp --platform YOUR_PLATFORM --output runs
```

Use that command as an MCP client's stdio subprocess, with this repository as
its working directory. Alternatively, let the agent start its own server:

```sh
export OPENAI_API_KEY=...
uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
  "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
   than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
   Run at most two protocols. Inspect plots before accepting an update.
   Stop if the evidence is insufficient."
```

Replace the platform and target with your own and verify the limits are safe.
Persistent defaults can be managed with `uv run qibocal-mcp-config`; objectives
and API keys are never stored in that configuration.

### Copilot subscriptions and OpenWebUI

The same agent extra supports both providers:

```sh
# Copilot: reuse an existing GitHub CLI OAuth login with Copilot access.
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
uv run qibocal-agent --model github-copilot:claude-haiku-4.5 \
  --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"

# OpenWebUI: use its API key and an exact model ID from /api/models.
export OPENWEBUI_API_KEY=...
uv run qibocal-agent --model openwebui:YOUR_MODEL_ID \
  --model-base-url http://localhost:3000/api \
  --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"
```

Choose a vision- and tool-capable model. Copilot model availability depends on
your subscription; its native adapter requires Chat Completions support.
OpenWebUI uses Chat Completions explicitly, not OpenAI's Responses API.
See the [model-provider guide](docs/guides/model-providers.rst) for credentials,
model discovery, endpoint defaults and persistent configuration.

## Documentation

Start at [docs/index.rst](docs/index.rst) for installation, practical tutorials,
configuration, networking, safety, artifacts, CLI/MCP reference and development.
The [configuration guide](docs/guides/configuration.rst) documents
`qibocal-mcp.json`; the [SSH/SLURM tutorial](docs/tutorials/ssh-slurm.rst)
covers remote execution. The [installation guide](docs/installation.rst)
also explains the pinned Qibocal API.

Build the Sphinx site locally:

```sh
uv sync --extra agent --extra docs
uv run sphinx-build -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` in a browser. The site uses native
reStructuredText and Sphinx's built-in Alabaster theme.
