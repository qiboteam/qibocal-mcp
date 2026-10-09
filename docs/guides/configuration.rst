Persistent configuration
========================

File selection
--------------

Configuration is a JSON file named ``qibocal-mcp.json`` with optional
top-level ``server``, ``agent`` and ``report`` objects, and no other top-level keys.
The default path is selected in this order:

1. If ``QIBOCAL_MCP_CONFIG_DIR`` is nonempty:
   ``$QIBOCAL_MCP_CONFIG_DIR/qibocal-mcp.json``.
2. Otherwise, if ``XDG_CONFIG_HOME`` is nonempty:
   ``$XDG_CONFIG_HOME/qibocal/qibocal-mcp.json``.
3. Otherwise: ``~/.config/qibocal/qibocal-mcp.json``.

Empty environment values fall through, as in qibocal-report. This reuses the
configuration folder but configuration management never accesses Qibocal
Report's ``servers.json`` or authentication files.

``qibocal-mcp``, ``qibocal-agent``, ``qibocal-copilot`` and
``qibocal-report-server`` all accept:

* ``--config FILE``: read only that file; an explicitly selected missing file
  is an error.
* ``--no-config``: ignore configuration completely.

These flags are mutually exclusive. A missing default file means there are
no stored defaults. Malformed JSON, unknown keys, unreadable files, invalid
types, empty or whitespace-only strings and invalid timeouts surface errors;
they never silently fall back to defaults. The whole selected file is
validated, including sections not used by the current command.

Settings and built-in defaults
------------------------------

Keys use underscores in JSON; the corresponding CLI flags use hyphens.
Every setting is optional in the file. Calibration commands require
``platform`` supplied by configuration or CLI at runtime.
``qibocal-agent`` also requires ``model``;
``qibocal-copilot`` does not require or use ``agent.model`` or
``agent.model_base_url``. Copilot owns model selection and login.

.. list-table:: Keys valid in both server and agent sections
   :header-rows: 1
   :widths: 18 20 62

   * - Key
     - Type
     - Built-in default and meaning
   * - ``platform``
     - Nonempty string
     - Unset; required at runtime. Installed platform name.
   * - ``output``
     - Nonempty string
     - ``runs``. Artifact output directory.
   * - ``platforms``
     - Nonempty string
     - Unset; inherits execution-host ``QIBOLAB_PLATFORMS`` after any managed
       repository roots. Explicit path lists take precedence over those roots.
   * - ``venv``
     - Nonempty string
     - Unset; managed environment name or path. SSH agents without explicit
       ``server_python`` use the managed name ``qibocal-mcp`` by default.
   * - ``platform_repo``
     - JSON array of nonempty strings
     - ``[]``. Trusted platform repositories, in lookup order.
   * - ``dependency``
     - JSON array of nonempty strings
     - ``[]``. Additional Python requirement specifications.
   * - ``partition``
     - Nonempty string
     - Unset; direct execution rather than SLURM dispatch.
   * - ``report_rendering``
     - Nonempty string: ``server`` or ``client``
     - ``server`` renders PNGs on the execution host. ``client`` sends Plotly
       figures for local rendering by the native Copilot relay; the Pydantic AI
       agent rejects this mode. Only the rendering host needs Chrome.
   * - ``workdir``
     - Nonempty string
     - Server: current directory for SLURM workers. Agent: unset, so no
       working-directory override.
   * - ``slurm_arg``
     - JSON array of nonempty strings
     - ``[]``. Additional ``srun`` options; a nonempty list requires an
       effective ``partition``.

.. list-table:: Section-specific keys
   :header-rows: 1
   :widths: 28 20 52

   * - Key
     - Type
     - Built-in default and meaning
   * - ``server.worker_python``
     - Nonempty string
     - Current Python interpreter; interpreter used by SLURM workers.
   * - ``agent.model``
     - Nonempty string
     - Unset; required by ``qibocal-agent`` only. Pydantic AI model identifier or
       ``openwebui:MODEL_ID``; see :doc:`model-providers`.
   * - ``agent.model_base_url``
     - Nonempty string
     - Unset; Pydantic AI endpoint override for ``openwebui:`` or
       ``github-copilot:``; ignored by ``qibocal-copilot``.
       OpenWebUI URLs include ``/api``. No credentials in URLs.
   * - ``agent.ssh``
     - Nonempty string
     - Unset; SSH host/alias when provided.
   * - ``agent.server_python``
     - Nonempty string
     - Unset; current interpreter locally, managed environment with SSH.
       Explicit values select a preinstalled server and bypass automatic
       environment/core-server installation.
   * - ``agent.timeout``
     - Positive finite JSON number
     - ``3600`` seconds per operation, including queue waiting. Native Copilot
       additionally accepts only 0.001 through 2147483.647 seconds, inclusive,
       to fit Node's integer timeout limit after conversion to milliseconds.

String values are not coerced from other types. ``null`` and booleans are not
valid settings; a timeout must be numeric, not a quoted string, and cannot
be zero, negative or nonfinite.
Use ``unset`` to remove a stored value and return to the built-in default
(unless explicitly overridden on the CLI); do not store ``null``.

Objectives and API keys are not valid configuration settings. Pass the
objective as the agent's positional argument and keep credentials in
environment/provider settings.
The Copilot wrapper allows the objective to be omitted for a blank interactive
session; establish operator-approved bounds before hardware operations.

Precedence and isolation
------------------------

The order is **explicit CLI > command's own section > built-in default**.
There is no server-to-agent or agent-to-server inheritance. For example,
``server.platform`` does not satisfy the agent's required platform.
Both agent integrations read the existing ``agent`` section; there is no
separate ``copilot`` section. Unused Pydantic AI model settings are still
subject to whole-file schema validation.

Report server defaults
----------------------

``qibocal-report-server`` uses **explicit CLI > report section > selected agent
settings > built-in default**. It inherits only ``agent.output``, ``agent.ssh``,
``agent.workdir`` and an explicitly configured ``agent.venv``. A platform or
model is not required, and scheduling, driver dependencies and interpreter
settings are not inherited. Overrides passed to an earlier agent invocation
are not persisted: supply the same location overrides to the report command.

The ``report`` section accepts only:

.. list-table::
   :header-rows: 1
   :widths: 22 22 56

   * - Key
     - Type
     - Built-in default and meaning
   * - ``output``
     - Nonempty string
     - ``runs``. Directory to serve; created if absent.
   * - ``ssh``
     - Nonempty string
     - Unset; local execution. Otherwise SSH host/alias containing the reports.
   * - ``workdir``
     - Nonempty string
     - Unset; working directory on the report host, as for the agent.
   * - ``venv``
     - Nonempty string
     - ``qibocal-report``. Always-managed environment on the report host.
       Explicit agent environments are inherited; implicit agent defaults are not.
   * - ``port``
     - JSON integer, 1 through 65535
     - ``8000``. Local port used in the frontend registration URL.
   * - ``remote_port``
     - JSON integer, 1 through 65535
     - Effective ``port``. Report-host listening port; a distinct value requires SSH.

The report server binds to loopback only. SSH automatically forwards
``127.0.0.1:port`` locally to ``127.0.0.1:remote_port`` on the report host.
Health checks through the tunnel begin only after the remote server signals
that it is listening, avoiding SSH connection-refused messages during startup.
The URL is printed after the health endpoint responds; installation and server
logs go to stderr. The server and tunnel remain attached until Ctrl+C.
With SLURM, serve the shared output directory on the access node, not a
compute-node job. The bootstrap uses resolved settings, not the remote host's
``qibocal-mcp.json``. Upstream Qibocal Report maintains its own server registry.

The pinned report package is installed and its provenance and dependencies
checked before launch. A dedicated report environment is recommended to avoid
changing an agent environment's dependencies; use ``report.venv`` to override
an inherited environment. Report and calibration provisioning share the same
environment lock. No frontend build, Qibolab platform or Chrome setup is required
to start the backend for registration in an existing frontend.

Agent list overrides and isolation
----------------------------------

An explicit ``--slurm-arg``, ``--platform-repo`` or ``--dependency`` list
replaces its entire configured list rather than appending to it. Repeated CLI
occurrences form the replacement list:

.. code-block:: sh

   uv run qibocal-agent --slurm-arg=--account=ACCOUNT \
     --slurm-arg=--time=00:15:00 "YOUR_BOUNDED_OBJECTIVE"

Use the equals form for dashed values. A nonempty effective ``slurm_arg``
requires an effective partition, regardless of whether either came from the
file or CLI. Scheduling options must preserve the worker's stdio and
single-task lifecycle; see :doc:`networking`.

The agent launches its server with ``--no-config`` and resolved settings.
Target-host server configuration cannot inject settings. Internal SLURM
workers do not read user configuration.

Copilot plugin snapshots
------------------------

``qibocal-copilot`` regenerates its transient plugin from resolved settings on
each launch. ``--export-plugin DIRECTORY`` instead snapshots the policy and
deployment/defaults in a new persistent directory. Later configuration edits
do not update that export; re-export to a new directory after changes.

Generated environment entries use runtime ``${NAME}`` references, never exported
secret values. The names are recorded from the rendering environment, excluding
model-provider credential/configuration prefixes and GitHub authentication tokens,
but preserving ``COPILOT_MCP_*`` driver variables, including required driver keys.
Keep every recorded name defined at launch; a missing name causes a clear
server-relay error. Existing names' values are
resolved at launch, but changing the set of names to forward requires re-export.
Keep the exporting installation's interpreter path and package intact.
Export does not install the plugin or modify global settings. See
:doc:`../agent/copilot` for launching an export and session lifetime limits.
Use the printed launch command, including ``--disable-builtin-mcps`` and
``--excluded-tools=skill,sql``. The exported profile alone does not exclude
Copilot's implicit ``skill`` and ``sql`` utilities in an already-running session.

Execution-host paths and environment selection
----------------------------------------------

Existing path settings are literal, not expanded or rebased relative to the
JSON file. In particular, ``~`` and environment-variable strings in ``output``,
``workdir``, ``platforms`` and executable settings are not expanded.
Relative ``output`` and ``workdir`` paths use the execution host's current
working directory; remote paths are interpreted remotely.
The agent's working-directory override determines where its server is launched.
The standalone server's ``workdir`` controls its SLURM worker directory,
not a directory change for direct execution.

Prefer absolute shared paths for SSH/SLURM. A Python executable setting should
name an executable, not a shell command such as ``uv run python``.

``venv`` is the exception: it expands ``~`` on the execution host. A simple
name selects ``$XDG_CACHE_HOME/qibocal-mcp/envs/NAME``, falling back to
``~/.cache/qibocal-mcp/envs/NAME`` for unset/empty ``XDG_CACHE_HOME``. An absolute
path or a path starting ``./`` or ``../`` selects that host's explicit location.
It does not access Qibocal notebook environments under ``qibocal/envs``.

SSH agents without explicit ``server_python`` provision the named
``qibocal-mcp`` environment even when ``venv`` is unset. Local agents and
standalone servers manage environments only with an effective ``venv``.
``agent.server_python`` and ``agent.venv`` are mutually exclusive, as are
``server.worker_python`` and ``server.venv``; this avoids split environments.
An explicit server interpreter must already contain the server. Repository and
dependency settings still install into that interpreter, or the selected
existing interpreter when no managed environment is requested. This is an
intentional modification; use ``venv`` for isolation.
On a standalone server with a distinct explicit ``worker_python``, repositories
and dependencies are provisioned into both gateway and worker interpreters,
not just the gateway. Both must already contain the server. The agent instead
uses the same installed interpreter for its server and workers.

Platform repositories and dependency sources
--------------------------------------------

Each ``platform_repo`` entry is a repository URL, Git SSH source or local
source path; local source paths are interpreted on the deployment host, not
the laptop when using SSH. An optional fragment selects a ref and platform
root:

.. code-block:: sh

   --platform-repo 'https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms'
   --platform-repo 'git@YOUR_GIT_HOST:YOUR_ORG/platforms.git#ref=release%2Fv1'
   --platform-repo /shared/repositories/site-platforms

Replace these illustrative sources with reviewed repositories. URL-encode ref
and subdirectory values as needed, and quote shell arguments containing ``&``.
``ref`` may name a branch, tag or commit.
Without ``subdirectory``, platform folders are looked up at the checkout root.
A subdirectory must be relative and stay inside the checkout: traversal,
absolute paths and symlink escapes are rejected.

Each URL/ref combination has its own cached clone under the execution host's
``qibocal-mcp`` cache. Omitting the ref captures the repository's default branch
on the first clone. Subsequent launches reuse clones **without automatic
pull, fetch or reset**, preventing unexpected hardware-definition changes.
Pin a commit for reproducibility. To refresh, choose a new ref or explicitly
review and maintain the cache; changing a branch upstream alone does not refresh
an existing clone. Array/CLI order determines repository lookup order.

The effective ``QIBOLAB_PLATFORMS`` search path is:

1. Explicit ``--platforms``/``platforms`` path-list entries, if present.
2. Managed repository roots in their configured order.
3. Inherited execution-host ``QIBOLAB_PLATFORMS``, only when explicit
   ``platforms`` is absent.

With neither repositories nor explicit ``platforms``, the variable is untouched.
An explicit path list replaces the inherited value, not the managed roots.
Across SSH, the inherited value is remote, not the laptop's environment.

A repository with root packaging metadata (``pyproject.toml`` containing
``[project]`` or ``[build-system]``, ``setup.py`` or ``setup.cfg``) is installed
editable for its dependencies and imports. ``requirements.txt`` at each
checkout root and configured subdirectory root is honored. Per-platform
requirements are read only from the first matching platform across the
effective lookup paths, including explicit or inherited paths when provisioning
is active. Duplicate files are removed; unrelated platforms are not scanned.
Cached copies rebase local projects, editable references, wheels,
``--find-links`` and local ``-r``/``-c`` includes relative to their original
requirements file, not the server working directory. Recursive include cycles
are errors. Remote includes are handled by the installer; their changing
contents are not locally fingerprinted.

``dependency`` entries are pip requirement specifications, installed together
with packaged repositories and discovered requirements. None of these sources
overrides another: the installer resolves them together, and conflicts are
errors. Managed environments also honor the core Qibocal pin in this checkout's
``pyproject.toml`` (or installed distribution metadata when no checkout is
available). All package metadata, dependency and requirements sources are
installed in one resolver request. Installation is additive, not exact
synchronization;
removing a configured dependency does not uninstall it. Successful dependency
state is fingerprinted, including nested local ``-r``/``-c`` files, root
packaging metadata and repository Git ``HEAD``. Environment-variable substitutions
and installed package names, versions and direct-source metadata are also
fingerprinted, so manual uninstall/upgrades trigger reconciliation.
Managed Qibocal's installed Git commit is verified explicitly; a different
source reporting the same package version is selectively reinstalled before
combined dependency resolution and checked again before recording success.
Managed environments run an installer dependency
check and import the server before launch; failed installation/checks do not
silently continue. Existing broken or custom environments are never automatically
deleted or recreated.

Managed deployment is POSIX-only and uses Python 3.12 or 3.13. Existing
``--venv`` paths must contain ``pyvenv.cfg`` and executable ``bin/python``;
use ``--server-python`` for an agent's conda/system interpreter instead.
Provisioning locks serialize installation, not the server's entire lifetime.
Use separate environment names or paths for concurrent deployments with
different source/dependency settings.

Only configure trusted sources: package metadata and requirements can execute
code. Remote Git authentication must work independently; the laptop's
``SSH_AUTH_SOCK`` is not automatically forwarded for remote cloning.
Never store credentials in repository URLs or configuration. See
:doc:`networking` for host environments and shared caches, and
:doc:`../installation` for remote bootstrap prerequisites.

Manage defaults without hardware
--------------------------------

The dedicated command is:

.. code-block:: text

   qibocal-mcp-config [--config FILE] path|show|get KEY|set KEY VALUE|unset KEY

``KEY`` is ``section.key``, for example ``server.platform`` or ``agent.model``.
It operates only on stored configuration, performs no hardware imports and
does not connect to instruments.

.. list-table:: Subcommands
   :header-rows: 1
   :widths: 20 80

   * - Subcommand
     - Behavior
   * - ``path``
     - Print the absolute selected file path without creating anything.
   * - ``show``
     - Print stored JSON only, not built-in or effective defaults. A missing
       default file displays an empty object.
   * - ``get KEY``
     - Return a string as plain text, or an array/number as JSON. Error if
       the setting is missing.
   * - ``set KEY VALUE``
     - Validate and write a sparse configuration atomically, creating parent
       directories as needed. A missing file is allowed.
   * - ``unset KEY``
     - Remove the setting and remove its section if empty. A missing key
       is an error.

With explicit ``--config FILE``, missing files are errors for reads and
``unset``, but allowed for ``set``. ``path`` only prints the location.
Writes preserve other valid stored settings; invalid existing files are
reported rather than silently repaired. There are no profile or clean commands.

String settings take literal text, **not JSON-quoted strings**.
``timeout`` takes a JSON numeric value; report ports take JSON integers;
``slurm_arg``, ``platform_repo`` and
``dependency`` take JSON arrays:

.. code-block:: sh

   uv run qibocal-mcp-config set server.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config set agent.partition PARTITION
   uv run qibocal-mcp-config set agent.venv /shared/qibocal-mcp/env
   uv run qibocal-mcp-config set agent.platform_repo '["https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms"]'
   uv run qibocal-mcp-config set agent.dependency '["YOUR_DRIVER_PACKAGE==VERSION"]'
   uv run qibocal-mcp-config set agent.timeout 7200
   uv run qibocal-mcp-config set report.port 8000
   uv run qibocal-mcp-config set report.venv qibocal-report
   uv run qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'
   uv run qibocal-mcp-config get agent.slurm_arg
   uv run qibocal-mcp-config unset agent.slurm_arg
   uv run qibocal-mcp-config get agent.platform_repo
   uv run qibocal-mcp-config unset agent.dependency

For a separate project-specific file, put ``--config`` before the subcommand:

.. code-block:: sh

   uv run qibocal-mcp-config --config ./qibocal-mcp.json set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config --config ./qibocal-mcp.json set agent.platform YOUR_PLATFORM
   uv run qibocal-agent --config ./qibocal-mcp.json "YOUR_BOUNDED_OBJECTIVE"

Sparse file example
-------------------

.. code-block:: json

   {
     "server": {
       "platform": "YOUR_PLATFORM",
       "output": "runs"
     },
     "agent": {
       "platform": "YOUR_PLATFORM",
       "model": "openai:gpt-4.1",
       "timeout": 3600
     }
   }

Do not add comments to the JSON. Omitting settings is how you request built-in
defaults; ``show`` will not fill them in. Storing scheduling arguments before
a partition is set does not make them usable at runtime: the merged runtime
settings must satisfy the partition requirement.
