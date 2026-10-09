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

## SSH and SLURM

The same agent can run at any of the three locations in Qibocal's
[interactive infrastructure](https://qibo.science/qibocal/stable/tutorials/interactive.html).
Use your SSH configuration for the access-node host, authentication and any
`ProxyJump`. No HTTP port or tunnel is needed: MCP messages, including plot
images, travel over SSH's stdio.

| Agent location | Execution path | Options |
| --- | --- | --- |
| Laptop/local machine | SSH to access node, then one `srun` job per operation | `--ssh ACCESS_NODE --partition PARTITION` |
| Access node | One `srun` job per operation | `--partition PARTITION` |
| Allocated compute node | Direct execution with one persistent hardware connection | Neither option |

**Local agent, remote queue:**

```sh
uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
  --ssh ACCESS_NODE --partition PARTITION \
  --server-python /shared/qibocal-mcp-servers/.venv/bin/python \
  --workdir /shared/qibocal-mcp-servers \
  --platforms /shared/qibolab-platforms \
  --output /shared/calibration-runs \
  --slurm-arg=--account=YOUR_ACCOUNT --slurm-arg=--time=00:15:00 \
  --timeout 7200 \
  "Calibrate qubit 0's drive frequency. Run at most two protocols,
   respecting the hardware's allowed amplitudes and frequency range."
```

**Agent on the access node:** use the same command without `--ssh`; omit
`--server-python` when the agent and workers use the same shared Python environment.
**Agent on an allocated compute node:** omit `--ssh`, `--partition` and
`--slurm-arg`; this keeps the original direct mode and does not submit nested jobs.
Acquire the allocation through your site's usual procedure before running it.

Install this project on the access/compute nodes (`uv sync` suffices for the
server; only the machine running the agent needs `--extra agent` and model
credentials). The server Python environment, working directory, platform
definitions and output directory must be accessible at the **same paths on the
access and compute nodes**. Chrome and instrument drivers must be available on
compute nodes. `--platforms` supplies the remote `QIBOLAB_PLATFORMS`; other driver
settings must be configured on the access node and are exported to SLURM.
The laptop's environment and model credentials are not forwarded over SSH.
SSH uses noninteractive authentication (`BatchMode=yes`); configure keys or an
SSH agent beforehand.

Queued mode starts a hardware-free MCP gateway on the access node. Reading
`qibocal://context` submits an initial discovery job. Each `execute_protocol`
submits a fresh job, connects, acquires/fits/reports, disconnects and releases its
allocation. An accepted `update_platform` also runs in a separate job: it restores
the saved execution, applies the update, and saves a platform snapshot. Subsequent
jobs load the latest **accepted** snapshot, including both Qibolab parameters and
Qibocal calibration; rejected fits never become the next job's platform state.
There is no allocation held while the model inspects reports.

Queued artifacts live under `OUTPUT/sessions/<session_id>/<run_id>/`, on the
cluster, not the laptop. Images and results are returned over MCP; copying the
remaining files is a separate operation. `--timeout` includes queue waiting and
execution time; `--slurm-arg` may be repeated for scheduling options such as
account, time, constraint, reservation or hardware resources. Do not use it to
redirect stdin/stdout/stderr, change the task count, or detach the job.
Queue/subprocess failures are surfaced to the model rather than treated as
successful experiments. Cancellation terminates the local `srun` controller;
SLURM/site signal handling governs how quickly its allocation is released.

Other MCP clients can also run a queued gateway directly:

```sh
uv run qibocal-mcp --platform YOUR_PLATFORM --partition PARTITION \
  --platforms /shared/qibolab-platforms --output /shared/calibration-runs
```

This is a single-platform draft for a trusted agent, not a production hardware
safety layer. Calls are serialized. Run handles exist only until the direct
server or queue gateway exits; resuming an entire agent session is not supported.
Platform updates are not transactional; once an update is attempted, retries of
that run are blocked, even after failure, to avoid repeating a partial update.
Automatic *fitted* updates are disabled, but a protocol's
acquisition may itself alter hardware state. Errors are exposed as MCP errors;
do not blindly retry acquisitions or failed/partially applied updates.
Source platform files remain untouched; promoting a saved snapshot is a manual
operation. No model API call is made by the server itself.
