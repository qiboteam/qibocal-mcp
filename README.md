# Qibocal MCP

A minimal FastMCP server and a runnable Pydantic AI calibration agent. Qibocal is
pinned to commit `87abd3350612b72e64b09db115ee0cf22886b507` of
[qiboteam/qibocal#1716](https://github.com/qiboteam/qibocal/pull/1716);
this draft uses that commit's API, not the PR description's older examples.

## Setup

```sh
uv sync --extra agent
# Kaleido needs Chrome to render Plotly reports. Skip if Chrome is installed.
uv run plotly_get_chrome -y
```

Alternatively, `devenv shell` sets up Python 3.12 and syncs all extras, following
Qibolab's Python/uv setup. Chrome must still be available for image export.
Configure `QIBOLAB_PLATFORMS` and install any required hardware drivers for your
platform. A platform name is required; Qibocal's test-only `mock` platform is not
included in its installed package.

## Server

```sh
export QIBOLAB_PLATFORMS=/path/to/your/platforms
uv run qibocal-mcp --platform YOUR_PLATFORM --output runs
```

Connect an MCP client over **stdio**, using the command above with the repository
as its working directory. There are exactly two tools:

| Tool | Behavior |
| --- | --- |
| `execute_protocol(protocol, parameters, targets)` | Binds a built-in protocol and calls `Executor.__call__()`, with automatic updates disabled. Returns a run handle, fitted results, report tables and images. |
| `update_platform(run_id)` | Calls `Executor.update(completed)` explicitly. Updates the live platform and saves a snapshot, without overwriting the source platform. |

Read the `qibocal://context` resource for platform qubit IDs and protocol parameter
definitions, including units. Reports are returned as **MCP image content**, not
just file paths, so a vision-capable agent can actually inspect them. Data,
results, run metadata and interactive HTML/PNG plots are also saved in
`runs/<run_id>/`; accepted updates write `platform/parameters.json` and
`platform/calibration.json` beneath that run.

For example, `execute_protocol` accepts:

```json
{
  "protocol": "qubit_spectroscopy",
  "parameters": {"freq_width": 20000000, "freq_step": 200000, "nshots": 100},
  "targets": [0]
}
```

Use the IDs reported by your platform. Multi-qubit targets are JSON arrays of
IDs, such as `[[0, 1]]`. Acquisition settings `nshots` and `relaxation_time` default
to platform settings when omitted.

## Agent

```sh
export OPENAI_API_KEY=...
uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
  "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
   than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
   Run at most two protocols. Inspect the plots before accepting an update."
```

The agent starts one MCP subprocess, reads its context, and lets the model plan,
execute, inspect and decide whether to update or run another experiment.
There is no hard-coded calibration sequence, fit acceptance threshold or workflow
engine. Select a **vision-capable** Pydantic AI model; the optional `agent` extra
includes the OpenAI provider (install other provider extras yourself if needed).
Put hardware limits, an execution budget and stopping criteria in the objective.

This is a local, single-platform draft for a trusted agent, not a production
hardware safety layer. Calls are serialized. Run handles exist only until the
server exits; saved artifacts are not automatically resumed, and platform updates
are not transactional. Automatic *fitted* updates are disabled, but a protocol's
acquisition may itself alter hardware state. Errors are exposed as MCP errors;
do not blindly retry acquisitions or failed/partially applied updates.
Source platform files remain untouched; promoting a saved snapshot is a manual
operation. No model API call is made by the server itself.
