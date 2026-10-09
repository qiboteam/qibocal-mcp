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

### Managed environments and platform repositories

SSH agents without an explicit `--server-python` provision a managed environment
named `qibocal-mcp` by default. Local agents and standalone servers manage an
environment only with `--venv NAME_OR_PATH`. For example, on shared cluster
storage:

```sh
uv run qibocal-agent --no-config --model openai:gpt-4.1 \
  --platform YOUR_PLATFORM --ssh ACCESS_NODE --partition PARTITION \
  --venv /shared/YOUR_USER/qibocal-mcp/env --workdir /shared/calibration-work \
  --platform-repo 'https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms' \
  --dependency 'YOUR_DRIVER_PACKAGE==VERSION' \
  --output /shared/calibration-runs "YOUR_BOUNDED_OBJECTIVE"
```

Replace all placeholders with approved paths, trusted repositories/requirements
and reviewed hardware limits. The remote host needs `python3` 3.12/3.13, Git and
`pip`/`ensurepip`, `uv` or external `pip>=22.3`; no remote checkout, preinstalled
server or agent extra is needed. A stdlib bootstrap transfers the launching
installation's server source and metadata (checkout or installed distribution),
not configuration/model keys, and installs server dependencies and pinned
Qibocal. Wheel-installed agents are supported without a local checkout.
The model provider is resolved before deployment or hardware.
Chrome and system drivers remain site-managed; Python driver
packages can be supplied through repeatable `--dependency`.

Named environments live under the execution host's
`$XDG_CACHE_HOME/qibocal-mcp/envs/NAME` (or `~/.cache/qibocal-mcp/envs/NAME`),
separate from notebook `qibocal/envs`. Explicit absolute or `./`/`../` paths are
resolved on that host; only `--venv` expands `~` there. Environments are reused,
successful dependency state is fingerprinted, including nested local `-r`/`-c`
files, root packaging metadata and Git `HEAD`. Changed source/dependencies/
requirements reconcile installation. Managed environments check dependencies
and server importability before launch. Failures stop startup; existing broken or
custom environments are never automatically deleted/recreated. Installation is
additive: removed configured dependencies are not automatically uninstalled.
Managed environments honor the source/distribution's Qibocal pin; conflicts
are errors. The pin is a PEP 508 direct Git dependency, so pip and built
distributions honor the same commit as uv.

Repositories can be URLs, Git SSH sources or deployment-host local paths, with
optional URL-encoded `#ref=REF&subdirectory=RELATIVE_PATH` fragments. Quote shell
strings containing `&`; subdirectories cannot traverse or escape the checkout,
including through symlinks. A ref can be a branch, tag or commit; platform
folders default to the repository root.
Clones are cached per URL/ref and reused **without automatic pull/fetch/reset**;
an omitted ref captures the default branch on first clone. Pin commits for
reproducibility, or choose a new ref/review and maintain the cache to refresh.
Explicit `--platforms` paths come first, repository roots in configured order
next; without explicit paths, inherited execution-host `QIBOLAB_PLATFORMS`
comes last. With neither option, that variable is untouched.

Packaged repository roots are installed editable. Requirements are read from
each checkout/subdirectory root and only the first matching selected platform.
Local projects and includes are rebased relative to their original requirements
file in cached copies; unrelated platforms are not scanned.
They resolve together with `--dependency`,
in one resolver request, not as precedence overrides. Trust all sources:
metadata/requirements may execute code. Remote Git authentication is independent
of laptop SSH login; its
`SSH_AUTH_SOCK` is not automatically forwarded. Never put credentials in URLs
or configuration.

Managed provisioning is POSIX-only, with Python 3.12/3.13. Existing `--venv`
paths require `pyvenv.cfg` and executable `bin/python`. Installed package changes
trigger reconciliation, and Qibocal's actual Git commit is verified.
Use separate environments for concurrent deployments with different settings;
provisioning locks do not cover active server sessions.

Explicit `--server-python` bypasses environment/core-server installation and
must already contain the server; it cannot coexist with `--venv`. Standalone
`--worker-python` also cannot coexist with `--venv`. Repository/dependency options
without a managed environment intentionally modify the selected existing
interpreter; prefer `--venv` for isolation. Repository/dependency options with
a distinct standalone `--worker-python` provision dependencies into
both gateway and worker interpreters; both must already contain the server.
The agent uses the same installed interpreter for workers. Repeatable CLI
repository/dependency lists replace stored lists, like `--slurm-arg`; config
set/get use JSON arrays
and unset restores defaults. The agent passes resolved settings with `--no-config`;
remote server and worker configuration is not read.

For SLURM, environment, working directory, cached source/clones, platforms and
output must be shared at identical absolute paths on access/compute nodes.
Configure remote `XDG_CACHE_HOME` on shared storage too: `--venv` does not move
the cache. No automatic node-local staging/provisioning occurs. See the
[SSH/SLURM tutorial](docs/tutorials/ssh-slurm.rst) for managed and externally
managed examples and the [configuration guide](docs/guides/configuration.rst)
for full environment/dependency semantics.

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
