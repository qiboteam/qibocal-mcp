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

## Use the MCP interactively

On the machine that reaches the hardware (a lab workstation or an allocated
compute node), install `qibocal-mcp` and register it in any MCP client
(Copilot CLI, Claude, Cursor, OpenCode, ...):

```json
{
  "mcpServers": {
    "qibocal": {
      "command": "qibocal-mcp",
      "args": ["--platform", "YOUR_PLATFORM", "--output", "runs"]
    }
  }
}
```

On a SLURM access node, add `--partition PARTITION`: every operation becomes
its own `srun` job, and no allocation is held while results are inspected.

From your own machine, with the hardware behind SSH, install `qibocal-agent`
locally and use `qibocal-mcp-remote` as the command instead — it launches the
server on the access node (provisioning its Python environment automatically)
and carries the same stdio interface:

```json
{
  "mcpServers": {
    "qibocal": {
      "command": "qibocal-mcp-remote",
      "args": ["--platform", "YOUR_PLATFORM", "--ssh", "ACCESS_NODE",
               "--partition", "PARTITION"]
    }
  }
}
```

In the conversation, load the server's `calibrate` prompt (or let the model
call the `calibrate` tool — same content, for interfaces that ignore MCP
prompts) and state your objective and hardware limits. The nine tools cover
context discovery, protocol execution with report images, explicit platform
updates and edits, session switching and append-only notes.

## Run the autonomous agent

```sh
pip install "qibocal-agent[agent]"   # or: uv sync, in this checkout
export OPENAI_API_KEY=...

qibocal-mcp-config set agent.platform YOUR_PLATFORM
qibocal-mcp-config set agent.model openai:gpt-4.1
# Only if the hardware is behind SSH / SLURM:
qibocal-mcp-config set agent.ssh ACCESS_NODE
qibocal-mcp-config set agent.partition PARTITION

qibocal-agent \
  "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
   than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
   Run at most two protocols. Inspect plots before accepting an update.
   Stop on errors or insufficient evidence."
```

Replace the platform, target and illustrative limits with operator-approved
values. The agent discovers the platform context, plans, executes one protocol
at a time, inspects the returned report images and explicitly accepts or
rejects fitted updates. Settings can equally be given as CLI flags or kept as
named entries (`agents.NAME.*`) for several setups side by side; see the
[configuration guide](docs/guides/configuration.rst). Managed remote
environments, trusted platform repositories and scheduler options are advanced
features with working defaults.

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
