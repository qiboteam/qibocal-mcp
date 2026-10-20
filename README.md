# Qibocal MCP servers

Qibocal calibration for AI agents, as two Python packages:

- [**`qibocal-mcp`**](packages/mcp) — the MCP servers. One command exposes
  Qibocal calibration tools and the reusable `calibrate` prompt over stdio.
  Install it where the instruments are reachable, or on a SLURM access node
  to dispatch one job per operation.
- [**`qibocal-agent`**](packages/agent) — the orchestration. It reaches those
  servers from anywhere: start the MCP from your laptop through SSH, run an
  autonomous calibration agent, browse reports, and store your defaults.
  Its base installation is light: FastMCP only, no Qibocal or hardware stack.

This is a single-platform draft, **not a hardware safety layer**. Supply
approved hardware limits, an execution budget and stopping criteria;
acquisitions can affect hardware even when fitted updates are disabled.

## Calibrate interactively

Install the project on your computer (`git clone` this repository, then
`uv sync`), and tell Qibocal about your setup once:

```sh
.venv/bin/qibocal-mcp-config set agent.platform YOUR_PLATFORM
.venv/bin/qibocal-mcp-config set agent.platforms_dir '~/path/to/platforms'
.venv/bin/qibocal-mcp-config set agent.ssh ACCESS_NODE
.venv/bin/qibocal-mcp-config set agent.partition PARTITION
.venv/bin/qibocal-mcp-config set agent.dependency '["YOUR_DRIVER_PACKAGE"]'
```

Then register the server in any MCP assistant (Copilot CLI, Claude, Cursor,
OpenCode, ...), for Copilot in `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "qibocal": {
      "command": "/full/path/to/qibocal-mcp-servers/.venv/bin/qibocal-mcp-remote"
    }
  }
}
```

It launches the server on the cluster, preparing its Python environment and
your dependencies automatically. In the conversation, ask the assistant to
calibrate starting from the `calibrate` tool, and state your objective and
hardware limits. Several setups can be stored side by side and switched
within the conversation.

The [interactive tutorial](docs/tutorials/interactive.rst) walks through all
of this with a simulator, from SSH keys to named configurations. To run the
server directly next to the hardware instead, see the
[MCP server docs](docs/mcp/index.rst).

## Run the autonomous agent

With the same configuration, add a model and its credentials, then give the
agent an objective:

```sh
.venv/bin/qibocal-mcp-config set agent.model openai:gpt-4.1
export OPENAI_API_KEY=...

.venv/bin/qibocal-agent \
  "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
   than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
   Run at most two protocols. Inspect plots before accepting an update.
   Stop on errors or insufficient evidence."
```

Replace the target and the illustrative limits with operator-approved values.
The agent plans, executes one protocol at a time, inspects the report images
and explicitly accepts or rejects fitted updates. See the
[autonomous agent tutorial](docs/tutorials/autonomous.rst).

## Browse calibration reports

```sh
qibocal-report-server            # serves ~/.cache/qibocal/agent/runs/default locally
qibocal-report-server --ssh ACCESS_NODE --output /shared/calibration/runs
```

Starts a [Qibocal Report](https://github.com/qiboteam/qibocal-report) backend
at the output location (through a private SSH tunnel when remote) and prints
the URL to register in your local frontend. Both execution modes collect a
standard Qibocal report per session under `OUTPUT/sessions/<session_id>/`.

## Documentation and development

The [documentation](docs/index.rst) has one track per package —
[MCP servers](docs/mcp/index.rst) and
[orchestration/agent](docs/agent/index.rst) — followed by advanced guides and
reference. The repository is a uv workspace; `uv sync` installs both packages
with their test and development dependencies, and each package has its own
test suite under `packages/*/tests`. Build the docs with:

```sh
uv sync --group docs
uv run sphinx-build -W --keep-going -b html docs docs/_build/html
```

See [development](docs/development.rst) for the source layout, invariants and
the pinned Qibocal API.
