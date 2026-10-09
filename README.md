# qibocal-agent

An autonomous calibration agent for Qibocal. Give it a bounded objective: the
agent chooses experiments, inspects report images and fitted results, and decides
whether to accept platform updates. Run locally or through SSH/SLURM.

The agent manages its own stdio MCP subprocess. The included standalone MCP
server is a byproduct for other clients, not a prerequisite to using the agent.

This is a single-platform draft, **not a hardware safety layer**. Supply approved
hardware limits, an execution budget and stopping criteria; acquisitions can
affect hardware even when fitted updates are disabled.

## Agent

### Minimal installation and first run

For a standard local setup, use a checkout of this repository with `uv`,
Python 3.12/3.13, Chrome, and your site's working Qibolab platform and drivers.
Assume `QIBOLAB_PLATFORMS` already points to your platform definitions.

```sh
uv sync --extra agent
export OPENAI_API_KEY=...
uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
  "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
   than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
   Run at most two protocols. Inspect plots before accepting an update.
   Stop on errors or insufficient evidence."
```

Replace the platform, target and illustrative limits with operator-approved
values. Skip the export if your provider credentials are already configured.
`uv` installs Qibocal and the agent dependencies; you do not need to install or
start an MCP server separately. See the [agent track](docs/agent/index.rst) for
the [first-run guide](docs/getting-started.rst) and calibration tutorials.

### Advanced installation and customization

Use the [advanced installation instructions](docs/installation.rst#advanced-installation-and-customization)
only for what your setup needs:

- Missing platform paths or Chrome, alternative installation with `devenv`,
  and execution-host requirements: [installation](docs/installation.rst).
- Copilot subscriptions, OpenWebUI or other model providers:
  [model providers](docs/guides/model-providers.rst).
- Managed environments, trusted platform repositories, Python driver
  dependencies and persistent defaults:
  [configuration](docs/guides/configuration.rst).
- Remote hardware and shared-storage constraints:
  [SSH/SLURM tutorial](docs/tutorials/ssh-slurm.rst).

SSH launches without `--server-python` automatically provision the remote
server and its Python dependencies; no remote checkout or agent installation
is needed. Local launches use the current interpreter unless `--venv` is
supplied. Chrome and system-level drivers remain site-managed.

## Standalone MCP server

This is a separate track for connecting another trusted MCP client to Qibocal.
It needs neither the agent extra nor model credentials.

```sh
uv sync
uv run qibocal-mcp --platform YOUR_PLATFORM --output runs
```

With your platform paths, drivers and Chrome available, configure your MCP
client to launch that command as its stdio subprocess from this checkout.
Running the server alone in a terminal does not initiate calibration, and
there is no HTTP URL. Read the [MCP track](docs/mcp/index.rst) for its own
installation, first tool call and queued gateway setup.

## Documentation and development

The [documentation index](docs/index.rst) presents the agent first and the MCP
server separately, followed by shared advanced guides and references.
The distribution is named `qibocal-agent`; the `qibocal_mcp` module,
`qibocal-mcp` and `qibocal-mcp-config` commands, configuration filenames and
execution caches retain their existing names.

Build the Sphinx site locally:

```sh
uv sync --extra agent --extra docs
uv run sphinx-build -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. See [development](docs/development.rst) for
the source layout, existing tests and pinned Qibocal API.
