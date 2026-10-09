# qibocal-agent

An autonomous calibration agent for Qibocal. Give it a bounded objective: the
agent chooses experiments, inspects report images and fitted results, and decides
whether to accept platform updates. Run locally or through SSH/SLURM.

Choose the one-shot Pydantic AI `qibocal-agent`, its reusable Python session API,
or interactive GitHub Copilot CLI via `qibocal-copilot`. Both integrations share
calibration policy and local/SSH/SLURM deployment, and manage their own stdio MCP
subprocess. The standalone MCP server is not a prerequisite.

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
- Pydantic AI Copilot subscriptions, OpenWebUI or other model providers:
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

### Interactive GitHub Copilot CLI

Install GitHub Copilot CLI separately, make `copilot` available on `PATH`, and
authenticate through Copilot. Only the core Python package is needed, not the
`agent` extra:

```sh
uv sync
uv run qibocal-copilot --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"
```

Omit the objective for a blank interactive session; establish operator-approved
bounds before discovery or acquisition. Pass native options after `--`, for
example `-- --model MODEL_ID`. Copilot owns login, subscription and model
selection; `agent.model` and `agent.model_base_url` are not used.
Native Copilot stages MCP report PNGs on the client host for its `view` tool,
including with SSH/SLURM. Choose an image-capable model and stop if images
cannot actually be inspected. `view` is a general reader, not an enforced
image-only tool; native path permissions remain in effect.

The wrapper generates a transient calibration plugin on every launch without
changing global settings or installing it. For a persistent deployment snapshot:

```sh
uv run qibocal-copilot --platform YOUR_PLATFORM --export-plugin ./qibocal-plugin
copilot --plugin-dir ./qibocal-plugin \
  --agent qibocal-calibration:qibocal-calibration \
  --disable-builtin-mcps --excluded-tools=skill,sql
```

Export refuses existing directories and needs no login, hardware access or
Copilot binary. The profile picker displays `qibocal-calibration`; direct launch
uses the qualified ID above. Exports reference environment variable names rather
than values; keep those names defined and re-export if the name set changes.
Use the printed launch command: tool restrictions require both the profile and
session flags. Selecting the exported profile in an already-running session
without those flags can expose Copilot's implicit `skill` and `sql` utilities.
Read the [Copilot guide](docs/agent/copilot.rst) for permissions, image support,
interpreter requirements and session-resume boundaries.
The [agent track](docs/agent/index.rst) also shows reusable Python multi-turn use.

## Browse calibration reports

Start a [Qibocal Report](https://github.com/qiboteam/qibocal-report) server
at the agent's configured output location:

```sh
uv run qibocal-report-server
```

The command inherits `agent.output`, `agent.ssh`, `agent.workdir` and an
explicit `agent.venv` from your configuration. Override them with CLI flags or
the optional `report` configuration section; `--config FILE` and `--no-config`
work as for the agent. Without configuration it serves `runs` locally, installing
the report package in a dedicated managed environment named `qibocal-report`.
No platform, model or hardware dependencies are required by this command.

For remote reports, it starts the server on the SSH host and opens a private
local port forward:

```sh
uv run qibocal-report-server --ssh ACCESS_NODE --output /shared/calibration/runs \
  --port 8000 --remote-port 9000
```

Once ready, it prints `http://127.0.0.1:8000` to register in the frontend on
your local machine. Keep the command running; Ctrl+C stops the server and
tunnel. With SLURM, the access node must be able to read the shared report
directory; the report server itself is not a SLURM job.
See the [CLI reference](docs/reference/cli.rst#qibocal-report-server).

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
